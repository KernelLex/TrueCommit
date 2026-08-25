"""Root-cause triage (BUILD.md Day 3).

Classifies WHY an invoice is unpaid; it never chooses the recovery path — the
state machine does. Backend is resolved by
`engine.perception.providers.get_provider` (argument → `PK_PERCEPTION_PROVIDER`
→ `heuristic`); the Claude path lives in `providers/anthropic_provider.py`.
"""

from pydantic import BaseModel

from engine.perception.providers import get_provider
from engine.schemas import Invoice, InvoiceCause, InvoiceCauseType, Message


class _TriageOut(BaseModel):
    """LLM-constrained output shape — carries no invoice_id (see extractor)."""

    cause: InvoiceCauseType
    confidence: float
    evidence: list[str]


def triage_invoice(
    invoice: Invoice, thread_messages: list[Message], *, provider: str | None = None
) -> InvoiceCause:
    return get_provider(provider).triage(invoice, thread_messages)
