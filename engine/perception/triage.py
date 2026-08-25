"""Root-cause triage — LLM call 1 of 4 (BUILD.md Day 3)."""

from pydantic import BaseModel

from engine.perception.client import call_structured, load_prompt
from engine.schemas import Invoice, InvoiceCause, InvoiceCauseType, Message


class _TriageOut(BaseModel):
    cause: InvoiceCauseType
    confidence: float
    evidence: list[str]


def triage_invoice(invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
    thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages) or "(no messages yet)"
    user_content = (
        f"Invoice {invoice.id}: Rs.{invoice.amount_inr:,}, status={invoice.status}, "
        f"issued {invoice.issued}, due {invoice.due}.\n"
        f"delivery_confirmed={invoice.delivery_confirmed}, "
        f"payment_failed_attempt={invoice.payment_failed_attempt}.\n\n"
        f"Thread so far:\n{thread_text}"
    )
    out = call_structured(load_prompt("triage"), user_content, _TriageOut)
    return InvoiceCause(invoice_id=invoice.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence)
