"""FastAPI: events in, state out (BUILD.md §4.1) — and, since packet P2, the
TIME-WARP driver (master doc §4.2).

The app owns a module-level `WorldRunner` (engine/integration/runner.py): the
virtual clock, the real Ledger, the Messenger, the Sentinel, the seeded rng and
the perception provider. `POST /advance` runs virtual days through the REAL
pipeline, which is what makes "press Advance-Day and money moves on screen"
true rather than staged.

`ledger` stays as an alias to `runner.ledger`, so every pre-existing route
(and every pre-existing test) keeps working unchanged, and `POST /events`
remains available for manual event injection in tests and demos.
"""

import datetime as dt
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine.integration.runner import WorldRunner

MAX_ADVANCE_DAYS = 365

runner = WorldRunner()
ledger = runner.ledger


def _reset_world() -> None:
    """Rebuild the world from the dataset on every startup. Rebinding both
    globals (rather than mutating in place) keeps each app lifespan — and so
    each TestClient context — a clean, reproducible day 0."""
    global runner, ledger
    runner = WorldRunner()
    ledger = runner.ledger


@asynccontextmanager
async def lifespan(_: FastAPI):
    _reset_world()
    yield


app = FastAPI(title="Promise Keeper API", version="0.2.0-phase-b", lifespan=lifespan)


class EventIn(BaseModel):
    type: str
    entity_id: str
    payload: dict = {}
    ts: dt.datetime | None = None


class AdvanceIn(BaseModel):
    days: int = Field(default=1, ge=1, le=MAX_ADVANCE_DAYS)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "invoices_loaded": len(runner.invoices), "reserves_active": len(ledger.reserve_active)}


@app.post("/advance")
def advance(body: AdvanceIn | None = None) -> dict:
    """The TIME-WARP button. `{"days": 1}` = "Advance 1 Day ▶",
    `{"days": 45}` = "Run to Day 45 ⏩". Every state change it produces went
    through `ledger.process_event` -> `check_bounds()` -> audit-before-action."""
    return runner.advance((body or AdvanceIn()).days)


@app.get("/world")
def world() -> dict:
    """Clock + provider + counts — the System Health screen's header."""
    return runner.world_summary()


@app.get("/funnel")
def funnel() -> dict:
    """The funnel/₹-recovered read model, without advancing anything."""
    return runner.funnel_summary()


@app.post("/events")
def post_event(event: EventIn) -> dict | None:
    now = event.ts or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    action = ledger.process_event(event.type, event.entity_id, event.payload, now)
    return json.loads(action.model_dump_json()) if action else None


@app.get("/entities")
def list_entities() -> list[dict]:
    """Read-only listing of every EntityState the ledger knows about — feeds
    the dashboard's funnel (state -> at-risk/in-recovery/recovered bucket)
    and the entity-timeline picker (P4)."""
    return [json.loads(e.model_dump_json()) for e in ledger.entities.values()]


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


@app.get("/trust")
def list_trust() -> list[dict]:
    """Read-only listing of every TrustState — feeds the dashboard's trust
    curves screen (P4). No decay is applied here (that only happens on the
    ordinary event-driven read paths); this is a snapshot of last-updated
    posteriors."""
    return [json.loads(t.model_dump_json()) for t in ledger.trust.values()]


@app.get("/trust/{debtor_id}")
def get_trust(debtor_id: str) -> dict:
    trust = ledger.trust.get(debtor_id)
    if trust is None:
        raise HTTPException(status_code=404, detail="no trust record yet")
    return json.loads(trust.model_dump_json())
