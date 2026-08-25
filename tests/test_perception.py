"""Perception entry points: prompts load, the default (offline) path works
without any key, and the Anthropic path still fails clean when there is no key.

Since perception became provider-pluggable (see tests/test_providers.py), the
default provider is `heuristic` — pure Python, no key, no network — so
`extract_promise` and friends now WORK offline. The "no key" failure that used
to be the whole story is still asserted, but now only where it belongs: on the
`anthropic` provider, which is the one that actually costs money.
"""

import datetime as dt

import pytest

from engine.perception import cart_cause, client, extractor, triage
from engine.perception.cart_cause import _CartCauseOut
from engine.perception.extractor import _ExtractOut
from engine.perception.providers import get_provider, reset_instances
from engine.perception.triage import _TriageOut
from engine.schemas import Cart, CartItem, Invoice, Message

PROMPT_NAMES = ["triage", "extract", "cart_cause", "verify", "draft"]

MSG = Message(id="M-1", thread_id="T-1", direction="in", channel="wa",
              text="will pay 1000 by Friday", ts=dt.datetime(2026, 8, 26))
INVOICE = Invoice(id="INV-001", debtor_id="D-01", amount_inr=40000, issued=dt.date(2026, 7, 1),
                  due=dt.date(2026, 8, 13), status="overdue", description="x")
CART = Cart(id="C-1", customer_id="CUST-1", amount_inr=2499,
            items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)],
            drop_stage="payment", drop_signals=[], ts=dt.datetime(2026, 8, 26))


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("PK_PERCEPTION_PROVIDER", raising=False)
    monkeypatch.setenv("PK_PERCEPTION_CACHE_DIR", str(tmp_path / "cache"))
    client._client = None  # reset the module-level singleton between tests
    reset_instances()
    yield
    reset_instances()


@pytest.mark.parametrize("name", PROMPT_NAMES)
def test_all_prompts_load_and_are_substantial(name):
    text = client.load_prompt(name)
    assert len(text) > 200
    assert "JSON" in text


def test_extractor_works_offline_by_default():
    """Default provider is the offline heuristic — no key required."""
    extraction = extractor.extract_promise(MSG, [MSG])
    assert extraction.message_id == "M-1"
    assert extraction.level in {"L1", "L2", "L3", "L4", "L5"}


def test_triage_works_offline_by_default():
    assert triage.triage_invoice(INVOICE, []).invoice_id == "INV-001"


def test_cart_cause_works_offline_by_default():
    assert cart_cause.infer_cart_cause(CART).cart_id == "C-1"


def test_anthropic_extractor_raises_cleanly_without_api_key():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        extractor.extract_promise(MSG, [MSG], provider="anthropic")


def test_anthropic_triage_raises_cleanly_without_api_key():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        triage.triage_invoice(INVOICE, [], provider="anthropic")


def test_anthropic_cart_cause_raises_cleanly_without_api_key():
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        cart_cause.infer_cart_cause(CART, provider="anthropic")


def test_anthropic_provider_selectable_by_env_var(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "anthropic")
    assert get_provider().name == "anthropic"
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        extractor.extract_promise(MSG, [MSG])


def test_llm_output_schemas_never_carry_identifiers():
    """The LLM never assigns an invoice_id/message_id/cart_id — those come
    from the calling code (SEE/SPEAK, never SPEND — and never NAME either)."""
    for model, forbidden_field in [(_TriageOut, "invoice_id"), (_ExtractOut, "message_id"), (_CartCauseOut, "cart_id")]:
        assert forbidden_field not in model.model_fields
