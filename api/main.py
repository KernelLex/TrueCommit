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
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from engine import config as agent_config
from engine.action.evidence import render_card
from engine.integration import day_story
from engine.integration.runner import WorldRunner
from engine.judgment import state_machine
from engine.judgment.ledger import (
    CLARIFY_CONFIDENCE_GATE,
    MONEY_ACTION_CONFIDENCE_GATE,
    ReviewQueueError,
)
from engine.perception import cache as perception_cache
from engine.perception.providers.ollama import get_fallback_events

MAX_ADVANCE_DAYS = 365

UNINJECTABLE_EVENTS = {state_machine.HUMAN_RESOLUTION_EVENT}
"""Event types `POST /events` refuses. `human_resolution` is the one event that
can move a terminal state (state_machine.HUMAN_RESOLUTION_EVENT), so the
general-purpose manual-injection route must not be a second door onto it: the
ONLY way to fire it is `POST /entities/{id}/resolve-handoff`, which additionally
refuses any entity that is not an open handoff or dispute."""

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
    through `ledger.process_event` -> `check_bounds()` -> audit-before-action.

    Packet P10 added ONE additive field, `stories`: `{day: [entity block, ...]}`
    for the day(s) this call just simulated, so the dashboard can jump straight
    to what happened instead of asking again. It is built by the SAME function
    `GET /day/{n}/story` serves, and there is a test that their beats are
    identical — a second code path here would be a second chance to disagree.
    Every pre-existing field is untouched.
    """
    days = (body or AdvanceIn()).days
    first_day = runner.day
    result = runner.advance(days)
    result["stories"] = day_story.build_stories(runner, range(first_day, runner.day))
    return result


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
    if event.type in UNINJECTABLE_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{event.type}' is the one event allowed to move a terminal state, so it is not "
                "injectable here. Use POST /entities/{entity_id}/resolve-handoff, which checks the "
                "entity really is an open handoff or dispute first."
            ),
        )
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


# ---------------------------------------------------------------------------
# Day Story (packet P10) — five READ-ONLY routes that surface data the pipeline
# already computed and audited. None of them decides, sends, mutates, or writes
# an audit entry; each one is a lens on `Ledger.audit`, `Ledger.gate_log`,
# `WorldRunner.threads` and `WorldRunner.day_snapshots`. Everything they return
# traces to a stored value, and where a value does not exist they return null
# plus a note saying so rather than a plausible-looking substitute
# (CLAUDE.md law 8). See engine/integration/day_story.py.
# ---------------------------------------------------------------------------


@app.get("/debtors")
def list_debtors() -> dict:
    """`{debtor_id: {name, trust_*, entities}}` — the names behind the entity
    ids, so a judge reads "Acme Traders", not "INV-001".

    Scene-2 cart customers have `name: null` and a `name_note` explaining why:
    `data/carts.json` stores a `customer_id` and no business name, and inventing
    one would be a fabrication sitting next to real money.
    """
    return day_story.debtor_directory(runner)


@app.get("/entities/{entity_id}/conversation")
def get_conversation(entity_id: str) -> dict:
    """The real thread for one entity — `WorldRunner.threads[entity_id]`, both
    directions, exactly the Messages the extractor read and the messenger sent.
    `origin` separates messages this run produced from the dataset's seed
    thread history it inherited."""
    if entity_id not in ledger.entities and entity_id not in runner.threads:
        raise HTTPException(status_code=404, detail="unknown entity")
    return day_story.conversation(runner, entity_id)


@app.get("/entities/{entity_id}/guardrail-checks")
def get_guardrail_checks(
    entity_id: str, action_kind: str | None = None, stage: str | None = None
) -> dict:
    """A READ-ONLY PREVIEW: "if this action were attempted right now, here is
    every bound and its result." With no `action_kind` it previews the action
    the ledger's own tables say this entity's next one would be.

    It is not the gate and cannot become one — it never calls `Ledger._gate()`,
    so it creates no Action, spends no touch, and writes nothing to the audit
    trail. The checks come from `check_bounds_detailed()`, which is proven
    unable to disagree with the real `check_bounds()`
    (`tests/test_state_machine.py::test_check_bounds_detailed_can_never_disagree_with_check_bounds`).
    """
    preview = day_story.guardrail_preview(runner, entity_id, action_kind, stage)
    if preview is None:
        raise HTTPException(status_code=404, detail="unknown entity")
    return preview


@app.get("/entities/{entity_id}/mandate-timeline")
def get_mandate_timeline(entity_id: str) -> dict:
    """The eMandate lifecycle end to end, reconstructed from the audit trail:
    offered -> registration link issued -> the debtor's response -> executed or
    failed -> revoked, each step carrying the audit entry it came from.

    Every step is labelled with its `nature`, and the labelling is the point.
    Registration links are REAL Razorpay TEST-mode objects when the run opted
    in; execution is SIMULATED in every run, because this sandbox account has
    UPI disabled and eMandate not enabled, so no token can be authorized to
    charge (tracking/TRACK_BAR.md §0). An entity with no mandate gets an empty
    `steps` list and a `status`, not a 404.
    """
    if entity_id not in ledger.entities and entity_id not in runner.threads:
        raise HTTPException(status_code=404, detail="unknown entity")
    return day_story.mandate_timeline(runner, entity_id)


@app.get("/day/{day}/story")
def get_day_story(day: int) -> dict:
    """What happened on one simulated day, per entity, as an ordered list of
    beats: the conversation as it was actually said, the decisions as they were
    actually audited, and the guardrail checklist recorded at the moment each
    decision was made.

    `day` is the runner's 0-based day INDEX — `advance(1)` simulates day 0 — so
    the day a judge just watched happen is `world_day - 1`. A day that has not
    been simulated yet returns an empty story with a `status`, not an error.
    """
    if day < 0:
        raise HTTPException(status_code=422, detail="day must be >= 0")
    return day_story.build_day_story(runner, day)


# ---------------------------------------------------------------------------
# Human-in-the-loop: the review queue (master doc §2.3 + §3.6)
#
# Everything below is a HUMAN acting. None of these routes decides anything:
# they translate a click into a call on the ledger, which audits it before it
# takes effect and re-runs `check_bounds()` on anything it is asked to send.
# No route in this file ever constructs an `Action` — approvals and rejections
# hand back an Action the LEDGER built, and the runner dispatches it down the
# same path a scheduled action takes.
# ---------------------------------------------------------------------------


class ResolutionIn(BaseModel):
    resolution: Literal["recovered", "written_off"]


class HandledIn(BaseModel):
    note: str | None = None


def _review_error(exc: ReviewQueueError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _held_row(held) -> dict:
    entity = ledger.entities.get(held.entity_id)
    row = json.loads(held.model_dump_json())
    row["entity_state"] = entity.state if entity else None
    row["debtor_id"] = ledger.debtor_of.get(held.entity_id)
    row["amount_inr"] = entity.invoice_amount_inr if entity else None
    row["paused"] = bool(ledger.paused.get(held.entity_id))
    row["extraction_confidence"] = ledger.extraction_confidence.get(held.entity_id)
    return row


def _evidence_for(entity_id: str) -> dict | None:
    packet = next(
        (p for p in reversed(runner.evidence_packets) if p.invoice_id == entity_id), None
    )
    if packet is None:
        return None
    return {"packet": json.loads(packet.model_dump_json()), "card": render_card(packet)}


def _handoff_row(entity_id: str) -> dict:
    entity = ledger.entities[entity_id]
    last = next(
        (a for a in reversed(runner.actions)
         if a.entity_id == entity_id and a.kind in ("human_handoff", "evidence_packet")),
        None,
    )
    return {
        "entity_id": entity_id,
        "state": entity.state,
        "debtor_id": ledger.debtor_of.get(entity_id),
        "amount_inr": entity.invoice_amount_inr,
        "escalate_stage": entity.escalate_stage,
        "paused": bool(ledger.paused.get(entity_id)),
        "reason": last.reason if last else None,
        "reason_detail": last.params.get("reason") if last else None,
        "evidence": _evidence_for(entity_id),
    }


@app.get("/review-queue")
def review_queue() -> dict:
    """Everything waiting on a human, in one read (master doc §3.6: an approval
    queue "only for (a) low-confidence money actions and (b) formal-notice
    stage", plus the handoffs and disputes the ladder has already stopped on).

    `held_actions` are decisions the ledger MADE and did not execute.
    `handoffs`/`disputes` are entities the ladder terminated at a human — they
    leave this list only when a human closes them via resolve-handoff.
    """
    handoffs = sorted(
        eid for eid, e in ledger.entities.items() if e.state == "HUMAN_HANDOFF"
    )
    disputes = sorted(eid for eid, e in ledger.entities.items() if e.state == "DISPUTED")
    pending = ledger.pending_held_actions()
    return {
        "day": runner.day,
        "gates": {
            "money_action_confidence": MONEY_ACTION_CONFIDENCE_GATE,
            "clarify_confidence": CLARIFY_CONFIDENCE_GATE,
        },
        "held_actions": [_held_row(h) for h in pending],
        "handoffs": [_handoff_row(eid) for eid in handoffs],
        "disputes": [_handoff_row(eid) for eid in disputes],
        "paused": [
            {
                "entity_id": eid,
                "state": ledger.entities[eid].state if eid in ledger.entities else None,
                "amount_inr": (ledger.entities[eid].invoice_amount_inr if eid in ledger.entities else None),
            }
            for eid in ledger.paused_entities()
        ],
        "resolved_held_actions": [
            _held_row(h) for h in ledger.held_actions if h.status != "pending"
        ],
        "counts": {
            "held_pending": len(pending),
            "held_resolved": len(ledger.held_actions) - len(pending),
            "handoffs": len(handoffs),
            "disputes": len(disputes),
            "paused": len(ledger.paused_entities()),
        },
    }


@app.post("/review-queue/{held_id}/approve")
def approve_held_action(held_id: str) -> dict:
    """The approve-click. `check_bounds()` re-runs HERE, against the debtor's
    touch budget as of now — so a hold that has gone stale while the debtor
    spent their weekly budget elsewhere is refused, audited, and reported back
    with the bound that refused it (`blocked: true`), not quietly sent."""
    try:
        outcome = ledger.approve_held(held_id, runner.now())
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc

    action = outcome["action"]
    if action is not None:
        runner.dispatch_action(action)
    return {
        "held": _held_row(outcome["held"]),
        "emitted": json.loads(action.model_dump_json()) if action else None,
        "blocked": outcome["blocked"],
        "block_reason": outcome["block_reason"],
    }


@app.post("/review-queue/{held_id}/reject")
def reject_held_action(held_id: str) -> dict:
    """The reject-click. The entity falls back the same way a bounds-block
    does: a rejected mandate offer tries the payment link (itself
    bounds-checked), and anything else simply resumes at its next ladder beat."""
    try:
        outcome = ledger.reject_held(held_id, runner.now())
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc

    fallback = outcome["action"]
    if fallback is not None:
        runner.dispatch_action(fallback)
    return {
        "held": _held_row(outcome["held"]),
        "fallback": json.loads(fallback.model_dump_json()) if fallback else None,
    }


@app.post("/review-queue/{held_id}/mark-handled")
def mark_held_handled(held_id: str, body: HandledIn | None = None) -> dict:
    """The only button a formal-notice draft has. It sends nothing — it records
    that a human dealt with the item outside the system."""
    try:
        held = ledger.mark_held_handled(held_id, runner.now(), (body or HandledIn()).note)
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc
    return {"held": _held_row(held), "emitted": None}


@app.post("/entities/{entity_id}/resolve-handoff")
def resolve_handoff(entity_id: str, body: ResolutionIn) -> dict:
    """Close an open handoff or dispute: "we recovered it" -> KEPT, "we wrote it
    off" -> CLEAN_LOSS. The only producer of `human_resolution` in the system."""
    try:
        entity = ledger.resolve_handoff(entity_id, body.resolution, runner.now())
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc
    return {
        "entity_id": entity_id,
        "resolution": body.resolution,
        "entity": json.loads(entity.model_dump_json()),
    }


@app.post("/entities/{entity_id}/pause")
def pause_entity(entity_id: str) -> dict:
    """The merchant kill-switch. A paused entity is skipped by the runner's
    outreach loop AND refused every outbound action inside the ledger's gate —
    pausing stops the thread, it does not merely hide it."""
    try:
        entity = ledger.set_paused(entity_id, True, runner.now())
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc
    return {"entity_id": entity_id, "paused": True, "state": entity.state}


@app.post("/entities/{entity_id}/unpause")
def unpause_entity(entity_id: str) -> dict:
    try:
        entity = ledger.set_paused(entity_id, False, runner.now())
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc
    return {"entity_id": entity_id, "paused": False, "state": entity.state}


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
