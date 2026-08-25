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

from engine.perception import assembly
from engine.perception.cart_cause import _CartCauseOut
from engine.perception.client import MODEL, call_structured, load_prompt
from engine.perception.extractor import _ExtractOut
from engine.perception.providers import PerceptionProvider
from engine.perception.triage import _TriageOut
from engine.schemas import Cart, CartCause, Extraction, Invoice, InvoiceCause, Message


class AnthropicProvider(PerceptionProvider):
    name = "anthropic"

    def identity(self) -> str:
        # Model id AND prompt/assembly wording participate in the cache
        # fingerprint: results from a different model — or from a different
        # version of the prompt — must never be served as if they were this
        # one's. (Before P7 the prompt text was NOT fingerprinted, so editing
        # a prompt silently re-served answers computed under the old one.)
        return f"{self.name}:{MODEL}:prompts@{assembly.prompt_fingerprint()}"

    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        user_content = assembly.extract_user_content(message, thread_messages)
        out = call_structured(load_prompt("extract"), user_content, _ExtractOut)
        return Extraction(
            message_id=message.id, level=out.level, amount_inr=out.amount_inr,
            date=out.date, condition=out.condition, confidence=out.confidence,
        )

    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        user_content = assembly.triage_user_content(invoice, thread_messages)
        out = call_structured(load_prompt("triage"), user_content, _TriageOut)
        return InvoiceCause(
            invoice_id=invoice.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence
        )

    def _cart_cause(self, cart: Cart) -> CartCause:
        user_content = assembly.cart_cause_user_content(cart)
        out = call_structured(load_prompt("cart_cause"), user_content, _CartCauseOut)
        return CartCause(
            cart_id=cart.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence
        )


def build() -> AnthropicProvider:
    return AnthropicProvider()
