"""Anthropic (Claude) provider — the original perception path, unchanged.

This is a straight lift of what `extractor.py` / `triage.py` / `cart_cause.py`
did before providers existed: same prompts (`engine/perception/prompts/*.md`),
same schema-constrained call (`client.call_structured` →
`client.messages.parse(output_format=...)`), same model. Nothing about the
Claude path was rewritten to fit the interface — it was moved behind it.

It stays fully blocked on `ANTHROPIC_API_KEY`: with no key,
`get_provider("anthropic").extract(...)` raises the same clean RuntimeError
`client._get_client()` has always raised. Selecting this provider is the only
thing that costs money, which is exactly the point of making it a choice.
"""

from engine.perception.cart_cause import _CartCauseOut
from engine.perception.client import MODEL, call_structured, load_prompt
from engine.perception.extractor import _ExtractOut
from engine.perception.providers import PerceptionProvider
from engine.perception.triage import _TriageOut
from engine.schemas import Cart, CartCause, Extraction, Invoice, InvoiceCause, Message


class AnthropicProvider(PerceptionProvider):
    name = "anthropic"

    def identity(self) -> str:
        # Model id participates in the cache fingerprint: results from a
        # different model must never be served as if they were this one's.
        return f"{self.name}:{MODEL}"

    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages)
        user_content = (
            f"Thread so far:\n{thread_text}\n\n"
            f'Extract the commitment from the LAST message above: "{message.text}"'
        )
        out = call_structured(load_prompt("extract"), user_content, _ExtractOut)
        return Extraction(
            message_id=message.id, level=out.level, amount_inr=out.amount_inr,
            date=out.date, condition=out.condition, confidence=out.confidence,
        )

    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages) or "(no messages yet)"
        user_content = (
            f"Invoice {invoice.id}: Rs.{invoice.amount_inr:,}, status={invoice.status}, "
            f"issued {invoice.issued}, due {invoice.due}.\n"
            f"delivery_confirmed={invoice.delivery_confirmed}, "
            f"payment_failed_attempt={invoice.payment_failed_attempt}.\n\n"
            f"Thread so far:\n{thread_text}"
        )
        out = call_structured(load_prompt("triage"), user_content, _TriageOut)
        return InvoiceCause(
            invoice_id=invoice.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence
        )

    def _cart_cause(self, cart: Cart) -> CartCause:
        user_content = (
            f"Cart {cart.id}: Rs.{cart.amount_inr:,}, drop_stage={cart.drop_stage}, "
            f"drop_signals={cart.drop_signals}, reserve_active={cart.reserve_active}."
        )
        out = call_structured(load_prompt("cart_cause"), user_content, _CartCauseOut)
        return CartCause(
            cart_id=cart.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence
        )


def build() -> AnthropicProvider:
    return AnthropicProvider()
