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
MessageChannel = Literal["wa", "email"]
ActionKind = Literal[
    "link", "mandate_offer", "mandate_execute", "message", "voice",
    "evidence_packet", "human_handoff",
]
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
