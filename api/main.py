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

from engine import config as agent_config
from engine.integration.runner import WorldRunner
from engine.perception import cache as perception_cache
from engine.perception.providers.ollama import get_fallback_events

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


def _runtime_model_name() -> str | None:
    """The model actually driving THIS process's perception provider, if
    the provider has one. `OllamaProvider` stores it on the instance;
    `AnthropicProvider` uses `engine.perception.client.MODEL` (a module
    constant, not an attribute) instead; `heuristic`/`oracle` have none."""
    provider = runner.provider
    model = getattr(provider, "model", None)
    if model is not None:
        return model
    if provider.name == "anthropic":
        from engine.perception.client import MODEL

        return MODEL
    return None


@app.get("/config")
def get_config() -> dict:
    """Packet P6: the tunable-parameters surface for the five mesh agents
    (master doc §7), shown live in System Health. Read-only — this app
    never accepts a config write over HTTP; `config/agents.yaml` is a file
    you edit, and `engine.config.load_config()` is memoised per path, so a
    running process needs a restart to pick up an edit.

    `effective` is the precedence-resolved value (explicit arg > env, if
    set > yaml, if present > builtin default — see `engine/config.py`) for
    every `perception.*`/`sentinel.*` field. `live_status` is what THIS
    process's already-constructed `WorldRunner` actually picked. For
    `sentinel.*` and `perception.cache_enabled` those two agree by
    construction (both are genuinely wired). For `perception.provider` /
    `ollama_model` / `ollama_base_url`, `live_status` may NOT reflect
    `effective` if the yaml has been edited without also setting the
    matching env var — this packet resolves and reports that precedence
    but does not (yet) rewire the live `WorldRunner`'s provider selection;
    see `engine/config.py`'s module docstring for exactly why.
    """
    cfg = agent_config.load_config()
    provider_val, provider_src = agent_config.effective_perception_provider(cfg)
    model_val, model_src = agent_config.effective_ollama_model(cfg)
    base_url_val, base_url_src = agent_config.effective_ollama_base_url(cfg)
    cache_val, cache_src = agent_config.effective_cache_enabled(cfg)

    return {
        "config": json.loads(cfg.model_dump_json()),
        "effective": {
            "perception": {
                "provider": {"value": provider_val, "source": provider_src},
                "ollama_model": {"value": model_val, "source": model_src},
                "ollama_base_url": {"value": base_url_val, "source": base_url_src},
                "cache_enabled": {"value": cache_val, "source": cache_src},
            },
            "sentinel": agent_config.sentinel_kwargs(cfg),
        },
        "bounds": agent_config.bounds_snapshot(),
        "wiring_notes": {
            "sentinel": "wired -- engine.config.build_sentinel() constructs a real Sentinel from these values",
            "perception_cache_enabled": (
                "wired -- engine/perception/cache.py's enabled() consults this yaml when "
                "PK_PERCEPTION_CACHE is unset"
            ),
            "perception_provider_model_base_url": (
                "resolved + reported here only -- the live WorldRunner in this process still "
                "selects its provider via env-var-or-default (see this route's own docstring)"
            ),
            "auditor": "not wired until the Auditor packet (Day 7, master doc §7.3) -- placeholders only",
            "judgment": "no tunables by design (CLAUDE.md law 4) -- bounds are hard constants in "
                        "state_machine.py, shown above for reference only",
        },
        "live_status": {
            "runtime_provider": runner.provider_name,
            "runtime_model": _runtime_model_name(),
            "ollama_fallback_events": len(get_fallback_events()),
            "sentinel_dead_letter_count": len(runner.sentinel.dead_letter),
            "cache_stats": perception_cache.stats(),
        },
    }
