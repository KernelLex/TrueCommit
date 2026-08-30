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
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from engine import config as agent_config
from engine.action import razorpay_client, telegram_bot, telephony, tts
from engine.action.contacts import ContactError
from engine.action.evidence import render_card
from engine.action.razorpay_client import RazorpayError
from engine.integration import day_story
from engine.integration.runner import WorldRunner
from engine.judgment import acceptance, state_machine
from engine.judgment.ledger import (
    CLARIFY_CONFIDENCE_GATE,
    MANUAL_REMINDER_CHANNELS,
    MONEY_ACTION_CONFIDENCE_GATE,
    ReviewQueueError,
)
from engine.perception import cache as perception_cache
from engine.perception.providers.ollama import get_fallback_events

ROOT = Path(__file__).resolve().parent.parent
MAX_ADVANCE_DAYS = 365

UNINJECTABLE_EVENTS = {state_machine.HUMAN_RESOLUTION_EVENT}
"""Event types `POST /events` refuses. `human_resolution` is the one event that
can move a terminal state (state_machine.HUMAN_RESOLUTION_EVENT), so the
general-purpose manual-injection route must not be a second door onto it: the
ONLY way to fire it is `POST /entities/{id}/resolve-handoff`, which additionally
refuses any entity that is not an open handoff or dispute."""

load_dotenv()
# Loaded HERE, once, at module import — before the first WorldRunner is ever
# constructed. Without this, `WorldRunner.__init__`'s `_real_telephony_enabled()`
# / `_real_razorpay_enabled()` / `_real_tts_enabled()` / `_real_telegram_enabled()`
# (engine/integration/runner.py) read raw `os.environ`, which is empty for
# anything that only lives in `.env` on a process nobody has otherwise loaded
# it into — so the real-dispatch opt-in flags would silently resolve to their
# defaults regardless of `.env`, while `engine/action/telephony.py`'s own
# `is_configured()`/`_credentials()` (which each call `load_dotenv()` lazily,
# on every use) would still find the real credentials. That mismatch is real
# and was hit live: `PK_REAL_TELEPHONY=1` in `.env` had no effect on a freshly
# started server because nothing had loaded it yet (found 2026-08-28 — see
# tracking/BUILD_LOG.md). `load_dotenv()`'s default `override=False` means
# this is a no-op if the shell already exported these vars some other way.
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

VOICE_NOTE_URL_PREFIX = "/voice-notes"
"""Where the generated MP3s are served from (packet P14). The dashboard reaches
them at `/api/voice-notes/<file>.mp3` through the Vite dev proxy. The directory
is created at import time because `StaticFiles` refuses to mount a path that
does not exist, and a fresh checkout has generated nothing yet."""

tts.VOICE_NOTE_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    VOICE_NOTE_URL_PREFIX,
    StaticFiles(directory=tts.VOICE_NOTE_DIR),
    name="voice-notes",
)


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


def _entity_row(entity) -> dict:
    """EntityState (judgment layer, untouched) plus two read-only fields this
    composition layer adds on top: `invoice_due`, the real invoice's due date
    when this entity is a real invoice (`runner.invoices`), else null (packet
    P13's Demo Console date picker); and `contact` (packet P15), the resolved
    `{"name", "contact", "email", "source"}` block — a real operator-submitted
    contact if one was ever submitted for this entity's debtor, else the exact
    synthetic demo fallback. Neither mutates or replaces anything EntityState
    itself reports; both merge in a sibling fact the judgment layer doesn't
    carry."""
    row = json.loads(entity.model_dump_json())
    invoice = runner.invoices.get(entity.entity_id)
    row["invoice_due"] = invoice.due.isoformat() if invoice else None
    row["contact"] = runner.resolve_contact(entity.entity_id)
    return row


@app.get("/entities")
def list_entities() -> list[dict]:
    """Read-only listing of every EntityState the ledger knows about — feeds
    the dashboard's funnel (state -> at-risk/in-recovery/recovered bucket)
    and the entity-timeline picker (P4)."""
    return [_entity_row(e) for e in ledger.entities.values()]


@app.get("/entities/{entity_id}")
def get_entity(entity_id: str) -> dict:
    entity = ledger.entities.get(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="unknown entity")
    return _entity_row(entity)


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


# ---------------------------------------------------------------------------
# Contacts: real debtor/customer identity (packet P15)
#
# NO REAL CALL, SMS OR WHATSAPP MESSAGE IS EVER PLACED HERE OR ANYWHERE ELSE
# IN THIS PROJECT. There is no telephony/SMS-gateway/WhatsApp-Business
# credential of any kind. Submitting a real name + phone number for a debtor
# only ever affects two things: (1) what the audit trail / dashboard displays
# from here on for that debtor, and (2) the `customer.contact` field actually
# sent to the REAL Razorpay TEST API when a real payment link/mandate is
# created (`PK_REAL_RAZORPAY=1`, or the demo-console button below) — Razorpay's
# sandbox genuinely reads that field.
#
# Every dispatch point in `WorldRunner` (voice, SMS, message, the real
# Razorpay call) reads WHO to contact through exactly one function,
# `WorldRunner.resolve_contact()`, which returns a real submitted contact when
# one exists for the entity's debtor and the exact synthetic demo fallback
# otherwise — labelled `source: "operator_submitted"` / `"demo_fallback"` so
# nothing downstream can present one as the other.
# ---------------------------------------------------------------------------


class ContactIn(BaseModel):
    name: str
    phone: str


@app.post("/entities/{entity_id}/contact")
def submit_contact(entity_id: str, body: ContactIn) -> dict:
    """Record a real name + phone for the debtor/customer behind `entity_id`.

    Applies to every sibling entity that shares this debtor (`_contact_key()`
    scopes by debtor_id, same precedent as the per-debtor touch cap) — the
    response's `also_applies_to` names them, so the UI can say "this also
    applies to INV-004, INV-009" rather than let an operator believe they only
    just updated the one invoice they were looking at.

    A malformed phone or an empty name is a 422 with the validation message,
    before anything is stored. Unknown to both `runner.invoices` and
    `runner.threads`/the ledger's own entities is a 404 — there is nothing to
    attach a contact to.
    """
    if (
        entity_id not in runner.invoices
        and entity_id not in ledger.entities
        and entity_id not in runner.threads
    ):
        raise HTTPException(status_code=404, detail="unknown entity")

    key = runner._contact_key(entity_id)
    try:
        contact = runner.contacts.submit(key, body.name, body.phone, runner.now())
    except ContactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    also_applies_to = sorted(
        eid for eid in runner.invoices if eid != entity_id and runner._contact_key(eid) == key
    )
    runner.audit_manual(
        entity_id, "operator submitted a real contact record for outreach",
        {
            "contact_key": key, "name": contact.name, "phone": contact.phone,
            "also_applies_to": also_applies_to,
        },
    )
    return {
        "entity_id": entity_id,
        "contact_key": key,
        "contact": json.loads(contact.model_dump_json()),
        "also_applies_to": also_applies_to,
    }


@app.get("/contacts")
def list_contacts() -> list[dict]:
    """One row per entity the ledger knows about (invoice-backed + any cart
    already registered by day 1's cart beat), each carrying its resolved
    contact — real submitted or demo fallback — so the dashboard's Contacts
    panel can render every row without a second round-trip per entity.
    Read-only: resolves nothing new and decides nothing."""
    rows = []
    for entity_id, entity in ledger.entities.items():
        resolved = runner.resolve_contact(entity_id)
        rows.append({
            "entity_id": entity_id,
            "debtor_id": ledger.debtor_of.get(entity_id),
            "invoice_amount_inr": entity.invoice_amount_inr,
            "state": entity.state,
            "contact_name": resolved["name"],
            "contact_phone": resolved["contact"],
            "contact_source": resolved["source"],
            "telegram_chat_id": resolved["telegram_chat_id"],
        })
    return rows


class TelegramLinkIn(BaseModel):
    chat_id: str


@app.get("/telegram/updates")
def telegram_updates() -> dict:
    """Read the bot's own inbox (packet P17) so an operator can find the real
    `chat_id` of whoever just messaged it — the one-time opt-in Telegram
    requires before the bot can message someone back. Read-only, decides
    nothing; returns a clean empty list (not an error) when no bot token is
    configured, since this is a discovery convenience, not a required route.
    """
    if not telegram_bot.is_configured():
        return {"configured": False, "chats": []}
    try:
        chats = telegram_bot.get_recent_chats()
    except telegram_bot.TelegramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"configured": True, "chats": chats}


@app.post("/entities/{entity_id}/contact/telegram")
def link_telegram(entity_id: str, body: TelegramLinkIn) -> dict:
    """Attach a discovered Telegram `chat_id` to the contact already on file
    for this entity's debtor (packet P17). Additive to name/phone, never a
    substitute for them — 422 if no contact was ever submitted for this
    entity yet (submit name/phone first via `POST /entities/{id}/contact`)."""
    key = runner._contact_key(entity_id)
    try:
        contact = runner.contacts.link_telegram(key, body.chat_id, runner.now())
    except ContactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    runner.audit_manual(
        entity_id, "operator linked a real Telegram chat to this contact",
        {"contact_key": key, "chat_id": body.chat_id},
    )
    return {"entity_id": entity_id, "contact_key": key, "contact": json.loads(contact.model_dump_json())}


# ---------------------------------------------------------------------------
# Reminders: real voice + real SMS (packet P14)
#
# READ THE CONTRAST WITH THE DEMO CONSOLE BELOW, because it is the whole point
# of this pair of routes. `/create-mandate-now` (P13) is an ungated human
# console: it deliberately skips `check_bounds()` because a person clicking once
# to inspect a real Razorpay object is not the runaway-cost risk the bounds were
# built for. `/remind-now` is the OPPOSITE: it sends a real message to a
# (notional) debtor, so it competes for that debtor's real weekly touch budget
# and is refusable by exactly the same bound an autonomous nudge is. It calls
# `Ledger.manual_reminder()`, which runs `_gate()` at click time and can hand
# back `{"blocked": true, "block_reason": ...}` — the same 200-with-a-refusal
# shape `approve_held` established in P9, so the dashboard reuses one UI for
# "the guardrail said no" instead of inventing a second.
#
# REAL vs SIMULATED, stated in the payload and not only in a comment:
#   REAL      the generated MP3 (gTTS, playable) and the SMS text.
#   SIMULATED the delivery. No phone is dialled, no handset is reached — there
#             is no telephony/SMS credential in this project. Every record
#             carries `dial_status` / `send_status` saying exactly that.
# ---------------------------------------------------------------------------


class RemindNowIn(BaseModel):
    """`channel` is the only thing the operator chooses about HOW it is sent.
    There is deliberately no `stage` field: `stage` is an input to
    `check_bounds()`, and a route that could pass `stage="legal"` would be a
    door onto legal communication the agent must never send (CLAUDE.md law 4).
    `custom_text` is content only — it is spoken/sent verbatim and is never
    parsed for an amount, a date, or anything else that could move state.

    `"message"` joined `"voice"`/`"sms"` in packet P15: a manual WhatsApp/email
    nudge on the entity's own thread channel, gated by the identical
    `check_bounds()` re-check pattern — see `MANUAL_REMINDER_CHANNELS`."""

    channel: Literal["voice", "sms", "message"]
    custom_text: str | None = None


def _manual_message_rows(entity_id: str) -> list[dict]:
    """Manual WhatsApp/email reminders (packet P15's third manual channel)
    dispatch through the SAME unchanged `elif kind == "message":` branch every
    autonomous nudge has used since Day 5 — that is the whole point of adding
    `"message"` to `MANUAL_REMINDER_CHANNELS` rather than inventing a new
    action kind. Because of that, they are NOT recorded in `runner.reminders`:
    that list's own contract (since packet P14) is "every voice/SMS reminder
    this world actually dispatched", and widening it to every message would
    blur autonomous and manual nudges together in a list nothing else expects
    to hold autonomous ones.

    Built here instead, additively, from `runner.actions` (every manual
    `message` Action carries `params["manual"] = True`) plus the matching
    `_send()` audit entry — which now carries `contact_name`/`contact_phone`/
    `contact_source` for every dispatched kind (packet P15) — so a manual
    WhatsApp/email reminder shows up in this history exactly like its voice/
    SMS siblings, without changing what `runner.reminders` means or touching
    `WorldRunner._dispatch`."""
    rows = []
    for action in runner.actions:
        if action.entity_id != entity_id or action.kind != "message" or not action.params.get("manual"):
            continue
        entry = next(
            (
                a for a in reversed(ledger.audit)
                if a.entity_id == entity_id and a.layer == "action"
                and a.detail.get("action_id") == action.id
            ),
            None,
        )
        detail = entry.detail if entry else {}
        rows.append({
            "action_id": action.id,
            "entity_id": entity_id,
            "channel": "message",
            "text": detail.get("text"),
            "manual": True,
            "stage": action.params.get("stage"),
            "reason": action.reason,
            "ts": action.ts.isoformat(),
            "day": None,
            "contact_name": detail.get("contact_name"),
            "contact_phone": detail.get("contact_phone"),
            "contact_source": detail.get("contact_source"),
            "rail": detail.get("rail"),
            "delivery_channel": detail.get("channel"),
            "whatsapp_status": detail.get("whatsapp_status"),
            "whatsapp_sid": detail.get("whatsapp_sid"),
            "telegram_status": detail.get("telegram_status"),
            "telegram_message_id": detail.get("telegram_message_id"),
        })
    return rows


@app.api_route("/telephony/twiml", methods=["GET", "POST"])
def telephony_twiml(text: str) -> Response:
    """The webhook Twilio's real Voice API fetches TwiML from (packet P16
    follow-up, 2026-08-27). Twilio TRIAL accounts reject inline `twiml=` on
    `Calls.create()` outright ("Invalid or disallowed parameters... trial
    accounts have limited parameter access" — confirmed live, see
    tracking/BUILD_LOG.md); only a `url=` Twilio can fetch from is accepted.
    `engine.action.telephony.place_call()` builds that URL as
    `{PUBLIC_BASE_URL}/telephony/twiml?text=<the reminder text>` — `text` is
    already fully decided by the time it reaches here (the ledger's template
    or an operator's own words); this route speaks it verbatim via Twilio's
    own `<Say>`, choosing no content of its own (CLAUDE.md law 1). Public by
    necessity (Twilio's servers must reach it, not just this backend's own
    caller) but read-only and stateless — it decides nothing and writes
    nothing to the ledger or audit trail.
    """
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Say voice="Polly.Aditi" language="hi-IN">{telephony._escape_xml(text)}</Say></Response>'
    )
    # Twilio's webhook fetcher specifically wants `text/xml` -
    # `application/xml` (also technically valid XML) was silently rejected as
    # unparseable in live testing (2026-08-27, tracking/BUILD_LOG.md), and
    # every official Twilio TwiML example uses this exact value.
    return Response(content=twiml, media_type="text/xml")


# ---------------------------------------------------------------------------
# IVR: press 1/2 on a live call (Track A, 2026-08-27)
#
# Two Twilio-facing webhooks (public, read/decide-on-the-fly, stateless
# beyond the ledger's own audit trail) plus one operator-facing trigger.
# `/telephony/ivr-menu` is where `place_ivr_call` points Twilio at when the
# call connects; `/telephony/ivr-response` is where Twilio's `<Gather>` POSTs
# the pressed digit. Both always return valid TwiML, never an HTTP error —
# Twilio has no good way to surface a 4xx/5xx to the person on the phone, so
# an unknown entity or a blocked selection is spoken aloud honestly instead.
#
# The real Razorpay object is created HERE, directly and unconditionally —
# not through `runner.dispatch_action()`'s `_payment_instrument()`, which is
# the rate-limited, mostly-simulated path a 45-day automated run uses. This
# mirrors packet P13's `create-mandate-now` precedent exactly: a human (here,
# the debtor's own keypress) triggering a one-off real object is not the
# runaway-cost risk `check_bounds()`'s bounds exist to prevent — the bound
# that DOES apply (whether this debtor may be offered a mandate/link at all
# right now) is enforced first, by `Ledger.ivr_select()`, before any network
# call is attempted.
# ---------------------------------------------------------------------------

def _real_future_debit_date(days_ahead: int = 7) -> str:
    """A real Razorpay `start_at`/`debit_date` must be in the future relative
    to REAL wall-clock time. This project's simulated invoices are, by
    design, already overdue — `invoice.due` sits in the past relative to
    both the virtual clock and the real calendar (that is the whole premise
    of a recovery scenario) — so using that field directly for a REAL
    Razorpay call gets rejected outright: a live IVR call hit exactly this,
    "start_at cannot be lesser than the current time.", a genuine 400 (see
    tracking/BUILD_LOG.md, 2026-08-27). `days_ahead` mirrors the exact
    offset already verified live and accepted (tracking/BUILD_LOG.md's
    "mandate rail pivot" entry, `sub_TUM5ilVyr8rpZZ`, `start_at` 7 days out).
    Real wall-clock `date.today()` on purpose, never the simulated clock —
    a REAL debit commitment is not a virtual-day concept."""
    return (dt.date.today() + dt.timedelta(days=days_ahead)).isoformat()


_IVR_DIGIT_TO_KIND: dict[str, Literal["mandate_offer", "link"]] = {"1": "mandate_offer", "2": "link"}

_IVR_MENU_PROMPT = {
    "mandate_offer": "Press 1 to set up an automatic payment on a date you choose.",
    "link": "Press 2 to get a payment link on your phone instead.",
}


def _ivr_say(text: str) -> str:
    return f'<Say voice="Polly.Aditi" language="hi-IN">{telephony._escape_xml(text)}</Say>'


def _ivr_hangup_response(text: str) -> Response:
    twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{_ivr_say(text)}<Hangup/></Response>'
    return Response(content=twiml, media_type="text/xml")


@app.api_route("/telephony/ivr-menu", methods=["GET", "POST"])
def telephony_ivr_menu(entity_id: str) -> Response:
    """The opening menu Twilio fetches the moment an IVR call connects.
    Built fresh from `Ledger.ivr_available_options` every time — the menu
    never announces an instrument the bounds would refuse a moment later,
    though the real gate re-runs anyway at keypress time (see
    `/telephony/ivr-response`) because eight seconds of `<Gather>` timeout is
    still enough for the world to move on."""
    invoice = runner.invoices.get(entity_id)
    entity = runner.ledger.entities.get(entity_id)
    if invoice is None or entity is None:
        return _ivr_hangup_response("Sorry, we could not find your account. Goodbye.")

    options = ledger.ivr_available_options(entity_id, runner.now())
    offered = [kind for kind in ("mandate_offer", "link") if options.get(kind)]
    intro = f"Hi, this is Promise Keeper calling about Rs.{entity.invoice_amount_inr:,} that is overdue."

    if not offered:
        return _ivr_hangup_response(
            f"{intro} We are unable to offer a new payment option on this account right now. "
            "Please check your messages for updates. Goodbye."
        )

    try:
        base_url = telephony.public_base_url()
    except telephony.TelephonyError:
        # Can only happen if PUBLIC_BASE_URL was unset after the call was
        # already placed (place_ivr_call itself requires it first) — still
        # handled here rather than letting a Twilio-facing webhook 500.
        return _ivr_hangup_response("Sorry, this call cannot be completed right now. Goodbye.")
    action_url = f"{base_url}/telephony/ivr-response?entity_id={quote(entity_id)}"
    menu_text = intro + " " + " ".join(_IVR_MENU_PROMPT[kind] for kind in offered)
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response><Gather numDigits="1" action="{action_url}" method="POST" timeout="8">'
        f'{_ivr_say(menu_text)}'
        f'</Gather>{_ivr_say("We did not receive a response. Goodbye.")}</Response>'
    )
    return Response(content=twiml, media_type="text/xml")


@app.post("/telephony/ivr-response")
async def telephony_ivr_response(entity_id: str, request: Request) -> Response:
    """Where Twilio's `<Gather>` POSTs the digit the debtor pressed
    (`Digits`, form-encoded — Twilio's webhook convention). Re-runs
    `check_bounds()` for real via `Ledger.ivr_select()` before doing
    anything else: the menu the debtor just heard could be stale."""
    form = await request.form()
    digits = str(form.get("Digits", ""))
    kind = _IVR_DIGIT_TO_KIND.get(digits)
    if kind is None:
        return _ivr_hangup_response("Sorry, that was not a valid option. Goodbye.")

    invoice = runner.invoices.get(entity_id)
    if invoice is None or entity_id not in runner.ledger.entities:
        # A webhook Twilio is mid-call on must always get back valid TwiML,
        # never an HTTP error — so this checks everything `ivr_select` would
        # 404/422 on itself before calling it, rather than catching
        # `ReviewQueueError` and trying to translate it into speech.
        return _ivr_hangup_response("Sorry, we could not find your account. Goodbye.")

    outcome = ledger.ivr_select(entity_id, kind, runner.now())

    if outcome["blocked"]:
        return _ivr_hangup_response(
            "Sorry, that option is not available on this account right now. "
            "We will follow up by message. Goodbye."
        )
    runner.actions.append(outcome["action"])  # bounds-checked, audited, touch-counted — see Ledger.ivr_select

    resolved = runner.resolve_contact(entity_id)
    customer = {"name": resolved["name"], "contact": resolved["contact"], "email": resolved["email"]}
    description = f"Promise Keeper IVR selection for {entity_id}"

    try:
        if kind == "mandate_offer":
            debit_date = _real_future_debit_date()
            result = razorpay_client.create_mandate_via_subscription(
                invoice.amount_inr, description, customer, debit_date,
            )
            short_url = result["subscription"].get("short_url")
            audit_detail = {
                "kind": kind, "amount_inr": invoice.amount_inr, "customer": customer,
                "debit_date": debit_date, "plan_id": result["plan"].get("id"),
                "subscription_id": result["subscription"].get("id"), "short_url": short_url,
                "razorpay_mode": "test", "contact_source": resolved["source"],
            }
            message_text = (
                f"Your automatic payment for {entity_id}, Rs.{invoice.amount_inr:,}, is scheduled "
                f"for {debit_date}. Approve or manage it here: {short_url}"
            )
        else:
            result = razorpay_client.create_payment_link(invoice.amount_inr, description, customer)
            short_url = result.get("short_url")
            audit_detail = {
                "kind": kind, "amount_inr": invoice.amount_inr, "customer": customer,
                "payment_link_id": result.get("id"), "short_url": short_url,
                "razorpay_mode": "test", "contact_source": resolved["source"],
            }
            message_text = f"Payment link for {entity_id}, Rs.{invoice.amount_inr:,}: {short_url}"
    except RazorpayError as exc:
        runner.audit_manual(
            entity_id, f"IVR: {kind} creation FAILED (debtor selected on a live call)",
            {"kind": kind, "amount_inr": invoice.amount_inr, "customer": customer,
             "error": str(exc), "razorpay_status_code": exc.status_code, "razorpay_description": exc.description},
        )
        return _ivr_hangup_response(
            "Sorry, something went wrong setting that up. We will follow up by message. Goodbye."
        )

    runner.audit_manual(entity_id, f"IVR: {kind} created (debtor selected on a live call)", audit_detail)

    # The Razorpay object existing is not the same as the debtor HAVING the
    # link — earlier live testing found the spoken "check your messages"
    # promise was never backed by an actual send (tracking/BUILD_LOG.md,
    # 2026-08-28). Same real-dispatch gate as the call itself
    # (`real_telephony_contact`): opt-in, credential, real submitted contact.
    # Falls back to an honest spoken line, never a false promise, when a real
    # send isn't possible (e.g. this number never joined the Twilio WhatsApp
    # sandbox) or fails.
    real_contact = runner.real_telephony_contact(entity_id)
    if real_contact:
        try:
            wa_result = telephony.send_whatsapp(real_contact, message_text)
        except telephony.TelephonyError as exc:
            runner.audit_manual(
                entity_id, "IVR: real WhatsApp confirmation FAILED (link/mandate still created)",
                {"kind": kind, "contact": real_contact, "error": str(exc)},
            )
            confirm_text = "Your confirmation link could not be messaged to you — please contact support."
        else:
            runner.audit_manual(
                entity_id, "IVR: real WhatsApp confirmation sent",
                {"kind": kind, "contact": real_contact, "whatsapp_sid": wa_result["sid"]},
            )
            confirm_text = (
                "Your automatic payment has been set up. A confirmation message is on its way."
                if kind == "mandate_offer" else
                "A payment link has been sent to your WhatsApp."
            )
    else:
        confirm_text = (
            "Your automatic payment has been set up." if kind == "mandate_offer" else
            "Your payment link has been created."
        )
    return _ivr_hangup_response(confirm_text)


class CallIvrNowIn(BaseModel):
    contact_override: str | None = None


@app.post("/entities/{entity_id}/call-ivr-now")
def call_ivr_now(entity_id: str, body: CallIvrNowIn | None = None) -> dict:
    """The operator-facing trigger: place a REAL phone call that offers the
    debtor a live choice, gated exactly like every other real-dispatch path
    in this codebase (`WorldRunner.real_telephony_contact`) — opt-in via
    `PK_REAL_TELEPHONY=1`, a real Twilio credential, and a real
    operator-submitted contact (never the synthetic demo number). A request
    that fails any of those gates is refused with a clear reason and no call
    is placed — this route never falls back to a simulated call, unlike the
    autonomous voice reminder path, because there is nothing to simulate: an
    IVR call is only meaningful if a real person can really press a digit."""
    invoice = runner.invoices.get(entity_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="unknown entity (no invoice on record for it)")

    contact = body.contact_override if body and body.contact_override else runner.real_telephony_contact(entity_id)
    if not contact:
        reason = (
            "no real telephony contact available: requires PK_REAL_TELEPHONY=1, a configured "
            "Twilio credential, and a real operator-submitted contact for this debtor "
            "(POST /entities/{id}/contact) — or pass contact_override explicitly"
        )
        runner.audit_manual(entity_id, "IVR call not placed (gate refused)", {"reason": reason})
        return {"placed": False, "reason": reason, "call": None}

    try:
        result = telephony.place_ivr_call(contact, entity_id)
    except telephony.TelephonyError as exc:
        runner.audit_manual(
            entity_id, "IVR call FAILED (operator-triggered)",
            {"contact": contact, "error": str(exc)},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    runner.audit_manual(
        entity_id, "IVR call placed (operator-triggered, real Twilio call)",
        {"contact": contact, "call_sid": result["sid"], "call_status": result["status"]},
    )
    return {"placed": True, "reason": None, "call": result}


@app.post("/entities/{entity_id}/remind-now")
def remind_now(entity_id: str, body: RemindNowIn) -> dict:
    """Send a real voice, SMS, or WhatsApp/email reminder now — if the bounds
    allow it.

    Returns the same `{action, blocked, block_reason}` shape as the approve
    route. A refusal is HTTP 200 with `blocked: true`, not a 4xx: the request
    was valid and the system did exactly what it should — a stopping rule
    stopped it (P9 decision #7, same reasoning).
    """
    try:
        outcome = ledger.manual_reminder(
            entity_id, body.channel, runner.now(), body.custom_text
        )
    except ReviewQueueError as exc:
        raise _review_error(exc) from exc

    action = outcome["action"]
    record = None
    if action is not None:
        runner.dispatch_action(action)
        if body.channel == "message":
            record = next(
                (r for r in reversed(_manual_message_rows(entity_id)) if r["action_id"] == action.id), None,
            )
        else:
            record = next(
                (r for r in reversed(runner.reminders) if r["action_id"] == action.id), None
            )
    return {
        "entity_id": entity_id,
        "channel": body.channel,
        "action": json.loads(action.model_dump_json()) if action else None,
        "reminder": record,
        "blocked": outcome["blocked"],
        "block_reason": outcome["block_reason"],
    }


@app.get("/entities/{entity_id}/reminders")
def get_reminders(entity_id: str) -> dict:
    """Every reminder this entity has: the ones that went out, with their real
    content, and the ones a bound refused.

    Sent voice/SMS reminders come from `runner.reminders`, written at dispatch
    time; sent manual WhatsApp/email reminders come from `_manual_message_rows`
    above (same underlying dispatch, a different bookkeeping list — see that
    function's docstring for why). Blocked ones are read out of
    `ledger.gate_log` — the append-only record of what `_gate()` actually saw at
    the moment it refused — so the reason shown is the reason the system decided
    from, never today's recomputation of it (CLAUDE.md law 8). The blocked
    filter deliberately excludes AUTONOMOUS `message` blocks (an ordinary
    gentle/firm nudge refused by the touch cap): `message` is both an
    autonomous kind and, since packet P15, a manual-reminder channel, and only
    the manual attempts belong on this panel — `voice`/`sms` need no such
    carve-out because `sms` has no autonomous trigger and a blocked autonomous
    `voice` escalation has always been shown here (P14), which this leaves
    unchanged.
    """
    if entity_id not in ledger.entities and entity_id not in runner.threads:
        raise HTTPException(status_code=404, detail="unknown entity")

    sent = [
        {**row, "status": "sent"}
        for row in runner.reminders
        if row["entity_id"] == entity_id
    ] + [
        {**row, "status": "sent"}
        for row in _manual_message_rows(entity_id)
    ]
    blocked = [
        {
            "status": "blocked",
            "entity_id": record.entity_id,
            "channel": record.kind,
            "manual": bool(record.params.get("manual")),
            "text": record.params.get("custom_text"),
            "block_reason": record.reason,
            "ts": record.ts.isoformat(),
            "checks": [json.loads(c.model_dump_json()) for c in record.checks],
        }
        for record in ledger.gate_log
        if record.entity_id == entity_id
        and record.kind in MANUAL_REMINDER_CHANNELS
        and not record.allowed
        and (record.kind != "message" or record.params.get("manual"))
    ]
    rows = sorted([*sent, *blocked], key=lambda r: r["ts"])
    return {
        "entity_id": entity_id,
        "day": runner.day,
        "audio_url_prefix": VOICE_NOTE_URL_PREFIX,
        "real_tts": runner.real_tts,
        "honesty": {
            "real": (
                "the generated MP3 audio, the SMS text, and the WhatsApp/email text — all genuine "
                "content a human can play or read"
            ),
            "simulated": (
                "the delivery. No phone is dialled and no handset receives an SMS or a WhatsApp "
                "message: this project holds no telephony, SMS-gateway, or WhatsApp Business "
                "credential. Every voice/SMS record below says so in its dial_status / send_status "
                "field; WhatsApp/email messages ride the same simulated queue every message this "
                "system sends always has (see engine/action/messenger.py)."
            ),
        },
        "reminders": rows,
        "counts": {"sent": len(sent), "blocked": len(blocked)},
    }


# ---------------------------------------------------------------------------
# Demo Console (packet P13) — "Create Mandate Now"
#
# This is NOT an agent decision and does not pretend to be one. Every other
# money-moving path in this file (`/advance`, `/events`, the review-queue
# approve/reject routes) exists to let a HUMAN supervise what the AGENT
# decided — the Action always came from `ledger.process_event` /
# `ledger.approve_held` / `ledger.reject_held`, gated by `check_bounds()`
# before this module ever sees it. The route below is the opposite shape: a
# human picks an entity and clicks a button to watch a REAL Razorpay
# TEST-mode mandate get created immediately, for inspection. It never calls
# `ledger.process_event`, `check_bounds()`, or `Ledger._gate()` — those exist
# to bound what the agent decides across an automated 45-virtual-day run, and
# a person deliberately clicking once is not the runaway-cost risk that
# machinery was built to prevent (CLAUDE.md law 4's bounds are about the
# agent's own decisions, not a human's explicit one-off click).
#
# It still audits before returning anything to the caller (CLAUDE.md law 3
# applies to every action regardless of who triggered it) via
# `runner.audit_manual()`, and the summary always starts "manual demo:" so it
# can never be mistaken in the trail for "mandate_offer" or any other string
# this codebase uses for something the agent itself decided.
# ---------------------------------------------------------------------------


class CreateMandateNowIn(BaseModel):
    """All fields optional. Omitted customer fields fall back to the exact
    synthetic, non-routable demo contact `WorldRunner._real_razorpay_call`
    already uses elsewhere in this codebase (`DEMO_CUSTOMER_CONTACT` /
    `DEMO_CUSTOMER_EMAIL`) — never a second, different fake-data convention.
    There is deliberately no amount field: the amount always comes from the
    invoice record (CLAUDE.md law 2), so nothing in this request body can
    ever override it."""

    customer_name: str | None = None
    customer_contact: str | None = None
    customer_email: str | None = None
    debit_date: str | None = None


@app.post("/entities/{entity_id}/create-mandate-now")
def create_mandate_now(entity_id: str, body: CreateMandateNowIn | None = None) -> dict:
    """The Demo Console's one button: create a REAL Razorpay TEST-mode
    mandate registration for `entity_id` right now, via
    `create_mandate_via_subscription` (packet P12's primary, live-verified
    mandate rail — see `engine/action/razorpay_client.py`).

    The amount and description are copied verbatim from `runner.invoices`
    (never invented, never taken from the request body — CLAUDE.md law 2).
    `debit_date` defaults to 7 real calendar days from today when omitted —
    NOT the invoice's own `due` date, which is always in the past relative to
    real wall-clock time (every invoice in this dataset is deliberately
    overdue) and gets rejected outright by Razorpay's real API ("start_at
    cannot be lesser than the current time.", hit live via the IVR call path
    — see tracking/BUILD_LOG.md 2026-08-27, `_real_future_debit_date`). Any
    omitted customer field falls back to the same synthetic demo contact used
    everywhere else in this codebase.

    A `RazorpayError` (missing/invalid `.env` keys, a real API rejection) is
    audited as a failed attempt and surfaces here as a clean 502 carrying
    Razorpay's own error description — never a stack trace, and never a
    fabricated `short_url`. A malformed `debit_date` is a 422 before any
    network call is attempted.
    """
    invoice = runner.invoices.get(entity_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="unknown entity (no invoice on record for it)")

    body = body or CreateMandateNowIn()
    # packet P15: the middle rung of the fallback chain is now a REAL submitted
    # contact when one exists for this debtor, still falling back to the exact
    # synthetic demo values when none was ever submitted (`resolve_contact()`
    # returns those byte-identically) — the explicit request-body override
    # above it is untouched.
    resolved = runner.resolve_contact(entity_id)
    customer = {
        "name": body.customer_name or resolved["name"],
        "contact": body.customer_contact or resolved["contact"],
        "email": body.customer_email or resolved["email"],
    }
    debit_date = body.debit_date or _real_future_debit_date()
    try:
        dt.datetime.strptime(debit_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"debit_date must be 'YYYY-MM-DD', got {debit_date!r}"
        ) from exc

    description = f"Promise Keeper demo console mandate for {entity_id}"

    # The Razorpay call has to happen before we have a plan_id/subscription_id/
    # short_url to put in the audit entry — there is no way to log ids that
    # don't exist yet. What CLAUDE.md law 3 protects is honoured here the same
    # way `WorldRunner._real_razorpay_call` already honours it for every other
    # real Razorpay call in this codebase: the audit write happens the instant
    # a result (success OR failure) exists, synchronously, before this route
    # returns anything to the caller — no path out of this function returns a
    # response without an audit entry for it already appended.
    try:
        result = razorpay_client.create_mandate_via_subscription(
            invoice.amount_inr, description, customer, debit_date,
        )
    except RazorpayError as exc:
        runner.audit_manual(
            entity_id,
            "manual demo: mandate creation FAILED (operator-triggered, no mandate was created)",
            {
                "amount_inr": invoice.amount_inr, "description": description,
                "customer": customer, "debit_date": debit_date,
                "error": str(exc), "razorpay_status_code": exc.status_code,
                "razorpay_description": exc.description,
            },
        )
        raise HTTPException(status_code=502, detail=exc.description or str(exc)) from exc

    plan = result["plan"]
    subscription = result["subscription"]
    runner.audit_manual(
        entity_id,
        "manual demo: mandate created by operator",
        {
            "amount_inr": invoice.amount_inr, "description": description,
            "customer": customer, "debit_date": debit_date,
            "plan_id": plan.get("id"), "subscription_id": subscription.get("id"),
            "short_url": subscription.get("short_url"), "razorpay_mode": "test",
            "contact_source": resolved["source"],
        },
    )
    # `contact_source` (packet P15) tells the dashboard whether the MIDDLE
    # fallback rung would have been a real submitted contact or the demo
    # constant, independent of whether the operator also typed an explicit
    # override in this one-off request body (`customer_used` already shows
    # exactly what was sent either way).
    return {
        "plan": plan, "subscription": subscription,
        "customer_used": customer, "contact_source": resolved["source"],
    }


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
            "auditor": agent_config.auditor_kwargs(cfg),
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
            "auditor": "wired -- engine.config.build_auditor() constructs a real Auditor from these "
                       "values; live rolling status is at GET /auditor",
            "judgment": "no tunables by design (CLAUDE.md law 4) -- bounds are hard constants in "
                        "state_machine.py, shown above for reference only",
        },
        "live_status": {
            "runtime_provider": runner.provider_name,
            "runtime_model": _runtime_model_name(),
            "ollama_fallback_events": len(get_fallback_events()),
            "sentinel_dead_letter_count": len(runner.sentinel.dead_letter),
            "cache_stats": perception_cache.stats(),
            "auditor_quarantined": ledger.auditor_quarantined,
        },
    }


@app.get("/auditor")
def get_auditor_status() -> dict:
    """Live Auditor status (master doc §7.3's "live accuracy widget") —
    sample count, rolling agreement rate, quarantine state, and the full
    drift log. Read-only; the Auditor samples itself as extractions happen
    (`WorldRunner._audit_extraction`), nothing here triggers a sample."""
    return runner.auditor.status()


def _load_break_even() -> dict | None:
    """`eval/run_arms.py`'s `break_even_touch_efficiency` figure, read from
    `metrics.json` at request time rather than recomputed here — computing
    it live would mean re-running the 6-point sensitivity sweep (six extra
    45-day `WorldRunner` simulations) on every dashboard poll. `metrics.json`
    is a committed, regenerated-on-demand artifact (`python -m
    eval.run_arms`), so this can go stale relative to the LIVE learned
    posterior below if code changes since the last regeneration — the
    dashboard meter labels it accordingly rather than implying it is live."""
    path = ROOT / "metrics.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("break_even_touch_efficiency")


@app.get("/acceptance")
def get_mandate_acceptance() -> dict:
    """Packet 4 (2026-08-31): the LIVE, learned, portfolio-wide mandate-
    acceptance Beta posterior (`engine/judgment/acceptance.py`) alongside the
    Tier-2, offline-computed break-even threshold (`eval/run_arms.py`) it is
    meant to be read against. Two genuinely different things reported side
    by side, labelled as such: `learned` updates every time a mandate is
    confirmed or refused in THIS running process; `break_even` is a snapshot
    of the last `python -m eval.run_arms` run, not recomputed per request."""
    now = runner.now()
    state = ledger.current_mandate_acceptance(now)
    return {
        "learned": {
            "alpha": state.alpha,
            "beta": state.beta,
            "mean": acceptance.mean(state),
            **acceptance.observations(state),
            "last_update": state.last_update,
        },
        "break_even": _load_break_even(),
        "note": (
            "`learned` is this process's own live posterior (Tier 2, simulated "
            "personas — see CLAUDE.md law 8). `break_even` is a snapshot from "
            "the last `python -m eval.run_arms` run, not recomputed live."
        ),
    }
