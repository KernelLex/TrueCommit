"""Phase A can't call the real API (no ANTHROPIC_API_KEY yet), but everything
around the call is testable now: prompts load, modules import cleanly, and
the offline-without-a-key failure is a clean, typed error rather than a
crash somewhere deep in the SDK.
"""

import datetime as dt

import pytest

from engine.perception import cart_cause, client, extractor, triage
from engine.perception.cart_cause import _CartCauseOut
from engine.perception.extractor import _ExtractOut
from engine.perception.triage import _TriageOut
from engine.schemas import Cart, CartItem, Invoice, Message

PROMPT_NAMES = ["triage", "extract", "cart_cause", "verify", "draft"]


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client._client = None  # reset the module-level singleton between tests


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_all_prompts_load_and_are_substantial(name):
    text = client.load_prompt(name)
    assert len(text) > 200
    assert "JSON" in text


def test_extractor_raises_cleanly_without_api_key():
    msg = Message(id="M-1", thread_id="T-1", direction="in", channel="wa", text="will pay 1000 by Friday", ts=dt.datetime(2026, 8, 26))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        extractor.extract_promise(msg, [msg])


def test_triage_raises_cleanly_without_api_key():
    inv = Invoice(id="INV-001", debtor_id="D-01", amount_inr=40000, issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue", description="x")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        triage.triage_invoice(inv, [])


def test_cart_cause_raises_cleanly_without_api_key():
    cart = Cart(id="C-1", customer_id="CUST-1", amount_inr=2499, items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)], drop_stage="payment", drop_signals=[], ts=dt.datetime(2026, 8, 26))
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cart_cause.infer_cart_cause(cart)


def test_llm_output_schemas_never_carry_identifiers():
    """The LLM never assigns an invoice_id/message_id/cart_id — those come
    from the calling code (SEE/SPEAK, never SPEND — and never NAME either)."""
    for model, forbidden_field in [(_TriageOut, "invoice_id"), (_ExtractOut, "message_id"), (_CartCauseOut, "cart_id")]:
        assert forbidden_field not in model.model_fields
