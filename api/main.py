"""FastAPI skeleton: events in, state out (BUILD.md §4.1). Wired to the real
judgment layer (engine/judgment/ledger.py) with the dataset loaded at
startup. Perception/Razorpay routes are NOT wired live yet — Phase B/C.
"""

import datetime as dt
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from engine.judgment.ledger import Ledger
from engine.schemas import Invoice

ROOT = Path(__file__).resolve().parent.parent
ledger = Ledger()


def _load_dataset() -> None:
    invoices = json.loads((ROOT / "data" / "invoices.json").read_text(encoding="utf-8"))
    for row in invoices:
        ledger.register_invoice(Invoice.model_validate(row))

    carts = json.loads((ROOT / "data" / "carts.json").read_text(encoding="utf-8"))
    for row in carts:
        if row["reserve_active"]:
            ledger.register_reserve(row["id"], True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load_dataset()
    yield


app = FastAPI(title="Promise Keeper API", version="0.1.0-phase-a", lifespan=lifespan)


class EventIn(BaseModel):
    type: str
    entity_id: str
    payload: dict = {}
    ts: dt.datetime | None = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "invoices_loaded": len(ledger.debtor_of), "reserves_active": len(ledger.reserve_active)}


@app.post("/events")
def post_event(event: EventIn) -> dict | None:
    now = event.ts or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    action = ledger.process_event(event.type, event.entity_id, event.payload, now)
    return json.loads(action.model_dump_json()) if action else None


@app.get("/entities/{entity_id}")
def get_entity(entity_id: str) -> dict:
    entity = ledger.entities.get(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="unknown entity")
    return json.loads(entity.model_dump_json())


@app.get("/entities/{entity_id}/audit")
def get_entity_audit(entity_id: str) -> list[dict]:
    return [json.loads(a.model_dump_json()) for a in ledger.audit if a.entity_id == entity_id]


@app.get("/audit")
def get_audit(limit: int = 100) -> list[dict]:
    return [json.loads(a.model_dump_json()) for a in ledger.audit[-limit:]]


@app.get("/trust/{debtor_id}")
def get_trust(debtor_id: str) -> dict:
    trust = ledger.trust.get(debtor_id)
    if trust is None:
        raise HTTPException(status_code=404, detail="no trust record yet")
    return json.loads(trust.model_dump_json())
