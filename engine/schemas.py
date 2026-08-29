"""Pydantic contracts every layer communicates through (BUILD.md §2).

Field additions beyond BUILD.md's literal listing (Invoice.delivery_confirmed /
payment_failed_attempt / enach_familiar, InvoiceCause) are logged in
tracking/DECISIONS.md — they fill gaps BUILD.md's day-tasks require but the
schema sketch didn't spell out (e.g. triage needs a delivery flag).
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

# Module-qualified (dt.date / dt.datetime) rather than `from datetime import
# date, datetime` — Python 3.14 (PEP 649) evaluates annotations lazily against
# the class namespace, and Extraction.date / Promise.due below would shadow a
# bare `date` import with the field's own default, breaking `date | None`.

# ---------------------------------------------------------------------------
# Shared literals
# ---------------------------------------------------------------------------

ExtractionLevel = Literal["L1", "L2", "L3", "L4", "L5"]
CartDropStage = Literal["summary", "address", "payment"]
CartCauseType = Literal["friction", "price_shock", "trust", "timing", "comparison", "unknown"]

DebitFailureReason = Literal[
    "insufficient_funds", "bank_downtime", "mandate_revoked",
    "account_closed_frozen", "amount_exceeds_limit",
]
"""NACH/eMandate return-reason taxonomy for a bounced mandate execution
(packet: "debit-failure taxonomy", 2026-08-30). A failed debit is NOT
automatically a broken promise — see `engine/judgment/state_machine.py`'s
`mandate_execute_failed` handling and `tracking/AI_JUDGMENT.md` for the full
per-reason trust/retry argument:
  insufficient_funds    -- timing problem, not willingness. Pending-neutral
                           trust, re-presented later at a trust-derived date;
                           a trust-derived SHRUNK tranche once the retry is
                           exhausted (never the full mandate-execute amount —
                           law 2 stays intact, only the fallback LINK shrinks).
  bank_downtime          -- not the debtor's fault. Zero trust impact, does
                           NOT spend the one allowed retry, silent same-
                           channel retry.
  mandate_revoked        -- a genuine willingness signal. Full trust penalty,
                           skips the AT_RISK grace and escalates immediately.
  account_closed_frozen  -- the rail itself is dead. No retry regardless of
                           budget, immediate fallback to a payment link at
                           the full amount, no trust penalty (not a
                           willingness signal), and no future mandate offer
                           (mirrors the post-refusal bound).
  amount_exceeds_limit   -- a structural/config mismatch, not willingness.
                           Same treatment as insufficient_funds.
"""

# Invoice root-cause taxonomy for Scene 1 triage (BUILD.md Day 3: "5 causes
# defined" — not enumerated in BUILD.md's schema sketch, so fixed here;
# see tracking/DECISIONS.md).
InvoiceCauseType = Literal[
    "payment_failed",   # technical failure (bounce/gateway/insufficient funds), debtor intended to pay
    "delivery_dispute",  # debtor disputes whether goods/services were delivered correctly
    "cashflow_delay",   # genuine behavioral delay, debtor intends to pay but is cash-constrained
    "dispute",          # debtor formally disputes the invoice/amount/contract terms
    "non_responsive",   # debtor has gone silent, no stated reason
]

InvoiceStatus = Literal["open", "overdue", "paid", "disputed", "closed"]
PromiseStatus = Literal["pending", "kept", "broken", "at_risk", "renegotiated", "disputed"]
MessageDirection = Literal["in", "out"]
MessageChannel = Literal["wa", "email", "sms"]
"""`sms` added in packet P14 (the reminder subsystem). It is a real CHANNEL —
the text that rides it is genuinely generated — but no SMS gateway credential
exists in this project, so every dispatched SMS record carries
`send_status: "simulated_no_sms_provider"`. See engine/action/tts.py."""

ActionKind = Literal[
    "link", "mandate_offer", "mandate_execute", "message", "voice",
    "sms", "evidence_packet", "human_handoff",
    "mandate_pre_debit_notice", "mandate_post_debit_notice",
]
"""`sms` is a new outbound KIND (packet P14) and rides the same touch cap as
every other outbound kind. Calls stay under `voice`: P14 upgraded what a
`voice` action PRODUCES (a real gTTS-generated MP3 instead of a text line on a
"voice_note" rail), it did not rename or split the kind.

`mandate_pre_debit_notice` / `mandate_post_debit_notice` (added for the RBI
E-Mandate Framework's T-1 pre-debit and post-transaction notification
requirements) are DELIBERATELY NOT in `state_machine.OUTBOUND_KINDS` /
`ledger.TOUCH_COUNTED_KINDS` — they are mandatory disclosures about money
already committed or already moved, not discretionary outreach, so a
merchant's MAX_TOUCHES_PER_WEEK budget cannot lawfully suppress them. See
`Ledger.pre_debit_notice` / `Ledger.post_debit_notice`."""
AuditLayer = Literal["perception", "judgment", "action", "sentinel", "auditor"]


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


class Event(BaseModel):
    event_id: str
    type: str
    entity_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: dt.datetime


# ---------------------------------------------------------------------------
# Domain records (Scene 1: B2B, Scene 2: commerce)
# ---------------------------------------------------------------------------


class Invoice(BaseModel):
    id: str
    debtor_id: str
    amount_inr: int = Field(gt=0)
    issued: dt.date
    due: dt.date
    status: InvoiceStatus
    description: str

    delivery_confirmed: bool = False
    payment_failed_attempt: bool = False
    enach_familiar: bool = False
    """Whether this invoice's debtor is known to be comfortable approving
    eNACH/UPI Autopay mandates — gates the state machine's mandate-offer
    branch (master doc §3.2)."""


class CartItem(BaseModel):
    sku: str
    name: str
    qty: int = Field(gt=0)
    price_inr: int = Field(gt=0)


class Cart(BaseModel):
    id: str
    customer_id: str
    amount_inr: int = Field(gt=0)
    items: list[CartItem]
    drop_stage: CartDropStage
    drop_signals: list[str] = Field(default_factory=list)
    ts: dt.datetime
    reserve_active: bool = False


class Message(BaseModel):
    id: str
    thread_id: str
    direction: MessageDirection
    channel: MessageChannel
    text: str
    ts: dt.datetime


# ---------------------------------------------------------------------------
# Perception outputs (the ONLY LLM-produced contracts)
# ---------------------------------------------------------------------------


class Extraction(BaseModel):
    message_id: str
    level: ExtractionLevel
    amount_inr: int | None = None
    date: dt.date | None = None
    condition: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class CartCause(BaseModel):
    cart_id: str
    cause: CartCauseType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


class InvoiceCause(BaseModel):
    """Scene 1 analogue of CartCause — root-cause triage output (BUILD.md Day 3)."""

    invoice_id: str
    cause: InvoiceCauseType
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Judgment layer (ZERO LLM — these are only ever written by deterministic code)
# ---------------------------------------------------------------------------


class Promise(BaseModel):
    id: str
    debtor_id: str
    invoice_id: str
    amount_inr: int = Field(gt=0)
    due: dt.date
    status: PromiseStatus
    source_msg: str
    """message_id this promise was extracted from."""


class TrustState(BaseModel):
    debtor_id: str
    alpha: float = Field(gt=0)
    beta: float = Field(gt=0)
    last_update: dt.datetime


# ---------------------------------------------------------------------------
# Action + audit
# ---------------------------------------------------------------------------


class Action(BaseModel):
    id: str
    entity_id: str
    kind: ActionKind
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str
    bounds_checked: bool
    ts: dt.datetime


class AuditEntry(BaseModel):
    id: str
    entity_id: str
    layer: AuditLayer
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
    ts: dt.datetime


# ---------------------------------------------------------------------------
# Human-in-the-loop (master doc §2.3 confidence gates, §3.6 approval queue)
# ---------------------------------------------------------------------------

HeldStatus = Literal["pending", "approved", "rejected", "blocked", "handled"]


class HeldAction(BaseModel):
    """An Action the ledger DECIDED on and then deliberately did NOT emit,
    because master doc §2.3 requires a human click first.

    `action` is a real `Action` built by the ledger (nothing constructs an
    Action anywhere else) carrying `bounds_checked=False` — and that flag is
    the honest one: a held action has NOT passed the gate, because
    `check_bounds()` is re-run at APPROVAL time, not at creation time. A hold
    created on day 3 and approved on day 9 is measured against the debtor's
    day-9 touch budget, so a stale hold can never smuggle an action past a cap
    that has since been hit.

    `sendable=False` marks the one item in the queue that has no approve
    button at all: the formal-notice draft. CLAUDE.md law 4 says the agent
    never sends legal communication — not even on a human click. The merchant
    sends it themselves, outside the system, and marks the item handled.
    """

    id: str
    entity_id: str
    action: Action
    reason: str
    """Why it was held, in the merchant's words — e.g. "confidence 0.82 < 0.90
    money gate"."""
    ts: dt.datetime
    status: HeldStatus = "pending"
    sendable: bool = True
    label: str | None = None
    resolved_ts: dt.datetime | None = None
    resolution_note: str | None = None
    emitted_action_id: str | None = None
    """The id of the Action actually emitted when a human approved this — None
    unless `status == "approved"`."""
