"""Escalation state machine + hard bounds — ZERO LLM (BUILD.md Day 5,
master doc §3.4). Every bound below is enforced here, only here, and cannot
be prompted around: nothing upstream (perception, drafting) can construct an
Action that skips check_bounds().

STATES vs the master doc's 3-terminal-state framing (Part 6 Q2: KEPT / CLEAN
LOSS / HUMAN_HANDOFF): DISPUTED is tracked as its own state (BUILD.md's Day-5
pytest explicitly checks for `state == "DISPUTED"`), but it is a HUMAN_HANDOFF
variant in every practical sense — dispute -> evidence packet -> human, no
further outbound actions, one-way. TERMINAL_STATES below includes it; pitch
material rolls it into the HUMAN_HANDOFF bucket. See tracking/DECISIONS.md.

Termination guarantee (CLAUDE.md law #5, "nothing loops forever"): escalation
is capped at ESCALATE_4 — the next failure event forces HUMAN_HANDOFF rather
than a 5th stage. A hard step-count safety valve (HARD_STEP_CAP) forces
HUMAN_HANDOFF regardless of event content if an entity somehow processes an
unreasonable number of events without resolving — a backstop, not the normal
path, but it makes "no infinite loop" true by construction, not by hoping the
event stream is well-behaved.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# HARD BOUNDS — master doc §3.4 / CLAUDE.md §3 law 4. Named constants only;
# nothing here is ever computed from an LLM output.
# ---------------------------------------------------------------------------

MAX_TOUCHES_PER_WEEK = 2
TOUCH_WINDOW_DAYS = 7
RENEGOTIATION_CAP = 2
MANDATE_AMOUNT_CAP = 100_000
RETRY_ON_EXECUTION_FAILURE = 1
MAX_ESCALATE_STAGE = 4
HARD_STEP_CAP = 60  # termination backstop; see module docstring

State = Literal[
    "NEW", "TRIAGED", "ENGAGED", "PROMISED", "MANDATED", "LINKED", "AT_RISK",
    "ESCALATE_1", "ESCALATE_2", "ESCALATE_3", "ESCALATE_4",
    "KEPT", "CLEAN_LOSS", "HUMAN_HANDOFF", "DISPUTED",
]
TERMINAL_STATES: set[State] = {"KEPT", "CLEAN_LOSS", "HUMAN_HANDOFF", "DISPUTED"}
ESCALATE_STATES: list[State] = ["ESCALATE_1", "ESCALATE_2", "ESCALATE_3", "ESCALATE_4"]

TouchKind = Literal["link", "mandate_offer", "message", "voice"]
# Actions that go TO the debtor/customer and are what "no further outbound
# actions" (BUILD.md Day-5 dispute test) means to block. evidence_packet /
# human_handoff are the terminal-state's own resolution artifacts, not
# further outreach, so they're exempt from the terminal-state block below.
OUTBOUND_KINDS = {"message", "link", "mandate_offer", "mandate_execute", "voice"}


class EntityState(BaseModel):
    entity_id: str
    state: State = "NEW"
    escalate_stage: int = 0
    renegotiation_count: int = 0
    retry_count: int = 0
    mandate_refused: bool = False
    touches: list[dt.datetime] = Field(default_factory=list)
    invoice_amount_inr: int | None = None
    step_count: int = 0


class BoundsResult(BaseModel):
    allowed: bool
    reason: str


def check_bounds(entity: EntityState, action_kind: str, params: dict[str, Any], now: dt.datetime) -> BoundsResult:
    """The single gate every action passes through before it executes
    (CLAUDE.md law #4). Pure predicate — never mutates `entity`."""
    if entity.state in TERMINAL_STATES and action_kind in OUTBOUND_KINDS:
        return BoundsResult(allowed=False, reason=f"entity in terminal state {entity.state}, no further outbound actions")

    if action_kind == "mandate_offer":
        if entity.mandate_refused:
            return BoundsResult(allowed=False, reason="post-refusal re-offer of mandate = NEVER")
        if entity.renegotiation_count > RENEGOTIATION_CAP:
            return BoundsResult(allowed=False, reason=f"renegotiation_cap ({RENEGOTIATION_CAP}) exceeded, no more mandate offers")
        amount = params.get("amount_inr")
        if amount is not None and amount > MANDATE_AMOUNT_CAP:
            return BoundsResult(allowed=False, reason=f"mandate_amount_cap (Rs.{MANDATE_AMOUNT_CAP:,}) exceeded, falls back to partial + link")

    if action_kind in ("mandate_offer", "mandate_execute"):
        amount = params.get("amount_inr")
        if amount is not None and entity.invoice_amount_inr is not None and amount != entity.invoice_amount_inr:
            return BoundsResult(allowed=False, reason="mandate amount must equal ledger invoice amount exactly, no invented numbers")

    if action_kind == "mandate_execute" and entity.retry_count > RETRY_ON_EXECUTION_FAILURE:
        return BoundsResult(allowed=False, reason=f"retry_on_execution_failure ({RETRY_ON_EXECUTION_FAILURE}) exceeded, falls to link/ladder/human")

    if action_kind in ("message", "link", "mandate_offer", "voice"):
        if params.get("stage") == "legal":
            return BoundsResult(allowed=False, reason="legal-stage notices go to the merchant for review; the agent never sends legal communication itself")
        recent = [t for t in entity.touches if (now - t).days < TOUCH_WINDOW_DAYS]
        if len(recent) >= MAX_TOUCHES_PER_WEEK:
            return BoundsResult(allowed=False, reason=f"max_touches_per_week ({MAX_TOUCHES_PER_WEEK}) exceeded")

    return BoundsResult(allowed=True, reason="ok")


def transition(entity: EntityState, event_type: str, payload: dict[str, Any], now: dt.datetime) -> EntityState:
    """Pure function: (state, event) -> next state. Never raises on an
    unrecognized event_type — an unknown event is a no-op besides the step
    count, so a malformed/unexpected input can never wedge the machine."""
    entity = entity.model_copy(deep=True)
    entity.step_count += 1

    if entity.state in TERMINAL_STATES:
        return entity  # dead end reached; nothing moves it further

    if event_type == "dispute_raised":
        entity.state = "DISPUTED"
        return entity

    if event_type == "invoice_triaged" and entity.state == "NEW":
        entity.state = "TRIAGED"
    elif event_type == "outreach_sent" and entity.state in ("TRIAGED", "ENGAGED", *ESCALATE_STATES, "AT_RISK"):
        if entity.state == "TRIAGED":
            entity.state = "ENGAGED"
    elif event_type == "extraction_received":
        entity.invoice_amount_inr = payload.get("invoice_amount_inr", entity.invoice_amount_inr)
        entity.state = "PROMISED"
    elif event_type == "mandate_offer_requested" and entity.state == "PROMISED":
        entity.state = "MANDATED"
    elif event_type == "mandate_refused":
        entity.mandate_refused = True
        entity.state = "LINKED"
    elif event_type == "mandate_confirmed" and entity.state == "MANDATED":
        pass  # stays MANDATED, awaiting execution
    elif event_type == "mandate_execute_success":
        entity.state = "KEPT"
    elif event_type == "mandate_execute_failed":
        if entity.retry_count < RETRY_ON_EXECUTION_FAILURE:
            entity.retry_count += 1
            entity.state = "AT_RISK"
        else:
            entity.state = "LINKED"
            entity = _escalate(entity)
    elif event_type == "promise_kept":
        entity.state = "KEPT"
    elif event_type == "promise_broken":
        entity.renegotiation_count += 1
        entity = _escalate(entity)
    elif event_type == "delivery_rejected":
        entity.state = "CLEAN_LOSS"  # Scene 2 delivery-secured mandate revoke branch, master doc §3.3
    elif event_type == "escalation_exhausted":
        entity.state = "HUMAN_HANDOFF"

    if entity.step_count > HARD_STEP_CAP and entity.state not in TERMINAL_STATES:
        entity.state = "HUMAN_HANDOFF"

    return entity


def _escalate(entity: EntityState) -> EntityState:
    # Read the current stage from `state` itself when already escalating,
    # rather than trusting `escalate_stage` alone — keeps this correct even
    # if a caller ever constructs/restores an EntityState where the two
    # fields aren't already in lockstep (they normally only move together,
    # via this function).
    current_stage = ESCALATE_STATES.index(entity.state) + 1 if entity.state in ESCALATE_STATES else entity.escalate_stage
    next_stage = current_stage + 1
    entity.escalate_stage = next_stage
    if next_stage > MAX_ESCALATE_STAGE:
        entity.state = "HUMAN_HANDOFF"
    else:
        entity.state = ESCALATE_STATES[next_stage - 1]
    return entity
