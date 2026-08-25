"""Tests for engine/action/razorpay_client.py — Phase C real client.

NO live API calls anywhere in this file: every RazorpayClient under test is
built with an httpx.MockTransport, and RAZORPAY_TEST_KEY_ID / _SECRET are
passed explicitly rather than read from .env, so this suite never touches
the network and never needs real keys.
"""

import json

import httpx
import pytest

from engine.action.razorpay_client import (
    RazorpayClient,
    RazorpayError,
    _to_paise,
)

TEST_KEY_ID = "rzp_test_fakekey123"
TEST_KEY_SECRET = "fakesecret"


def _mock_client(handler) -> RazorpayClient:
    transport = httpx.MockTransport(handler)
    return RazorpayClient(key_id=TEST_KEY_ID, key_secret=TEST_KEY_SECRET, transport=transport)


def _json_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=body)


# -- paise conversion --------------------------------------------------


def test_paise_conversion():
    assert _to_paise(40000) == 4000000  # Rs.40,000 -> 40,00,000 paise
    assert _to_paise(1) == 100
    assert _to_paise(0) == 0


# -- key-prefix refusal (hard rule 1) -----------------------------------


def test_live_key_refused_before_any_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _json_response(200, {})

    with pytest.raises(RazorpayError):
        RazorpayClient(
            key_id="rzp_live_shouldneverwork",
            key_secret="whatever",
            transport=httpx.MockTransport(handler),
        )
    assert calls == []  # constructor must refuse before any HTTP call is possible


def test_missing_keys_refused():
    with pytest.raises(RazorpayError):
        RazorpayClient(key_id="", key_secret="")


# -- payment link happy path --------------------------------------------


def test_create_payment_link_happy_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _json_response(200, {
            "id": "plink_TEST123",
            "short_url": "https://rzp.io/rzp/testlink",
            "status": "created",
        })

    client = _mock_client(handler)
    result = client.create_payment_link(
        amount_inr=40000,
        description="INV-001 demo invoice",
        customer={"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
    )

    assert captured["url"].endswith("/payment_links")
    assert captured["body"]["amount"] == 4000000
    assert captured["body"]["currency"] == "INR"
    assert captured["body"]["customer"]["name"] == "Acme Traders"
    assert result["id"] == "plink_TEST123"
    assert result["short_url"] == "https://rzp.io/rzp/testlink"


# -- invoice happy path --------------------------------------------------


def test_create_invoice_happy_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _json_response(200, {"id": "inv_TEST123", "short_url": "https://rzp.io/rzp/testinv"})

    client = _mock_client(handler)
    result = client.create_invoice(
        amount_inr=40000,
        description="Aug supplies",
        customer={"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        due_date="2026-09-01",
    )

    assert captured["body"]["type"] == "invoice"
    assert len(captured["body"]["line_items"]) == 1
    assert captured["body"]["line_items"][0]["amount"] == 4000000
    assert "expire_by" in captured["body"]
    assert result["id"] == "inv_TEST123"


# -- customer happy path --------------------------------------------------


def test_create_customer_happy_path():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _json_response(200, {"id": "cust_TEST123"})

    client = _mock_client(handler)
    result = client.create_customer(name="Acme Traders", contact="+919000000001", email="acme@example.com")

    assert captured["body"]["fail_existing"] == "0"
    assert result["id"] == "cust_TEST123"


# -- mandate registration link payload shape -----------------------------


def test_mandate_registration_link_upi_payload_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _json_response(200, {
            "id": "inv_MANDATETEST",
            "short_url": "https://rzp.io/rzp/mandatelink",
            "auth_link_status": "issued",
        })

    client = _mock_client(handler)
    result = client.create_mandate_registration_link(
        max_amount_inr=100000,
        description="Promise Keeper mandate registration",
        customer={"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        method="upi",
    )

    assert captured["url"].endswith("/subscription_registration/auth_links")
    body = captured["body"]
    assert body["amount"] >= 100  # UPI Autopay registration minimum initial charge
    sub_reg = body["subscription_registration"]
    assert sub_reg["method"] == "upi"
    assert sub_reg["frequency"] == "as_presented"
    assert sub_reg["max_amount"] == 10000000  # Rs.1,00,000 -> paise
    assert result["short_url"] == "https://rzp.io/rzp/mandatelink"


def test_mandate_registration_link_emandate_payload_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _json_response(200, {"id": "inv_EMANDATETEST", "short_url": "https://rzp.io/rzp/emandatelink"})

    client = _mock_client(handler)
    client.create_mandate_registration_link(
        max_amount_inr=50000,
        description="eMandate registration",
        customer={"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        method="emandate",
    )

    sub_reg = captured["body"]["subscription_registration"]
    assert sub_reg["method"] == "emandate"
    assert sub_reg["auth_type"] == "netbanking"
    assert "frequency" not in sub_reg


def test_mandate_registration_link_rejects_unknown_method():
    client = _mock_client(lambda request: _json_response(200, {}))
    with pytest.raises(RazorpayError):
        client.create_mandate_registration_link(
            max_amount_inr=1000, description="x", customer={}, method="carrier_pigeon",
        )


# -- simulated mandate lifecycle -----------------------------------------


def test_execute_mandate_is_simulated_and_makes_no_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _json_response(200, {})

    client = _mock_client(handler)
    result = client.execute_mandate(token_id="token_TEST", amount_inr=40000, customer_id="cust_TEST")

    assert calls == []  # no network call for a simulated action
    assert result["simulated"] is True
    assert "reason" in result and result["reason"]
    assert result["amount"] == 4000000
    assert result["token_id"] == "token_TEST"


def test_revoke_mandate_is_simulated_and_makes_no_request():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return _json_response(200, {})

    client = _mock_client(handler)
    result = client.revoke_mandate(token_id="token_TEST")

    assert calls == []
    assert result["simulated"] is True
    assert "reason" in result and result["reason"]
    assert result["id"] == "token_TEST"


# -- error handling --------------------------------------------------


def test_razorpay_error_raised_on_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(400, {
            "error": {
                "code": "BAD_REQUEST_ERROR",
                "description": "Order amount less than minimum amount allowed",
            }
        })

    client = _mock_client(handler)
    with pytest.raises(RazorpayError) as excinfo:
        client.create_payment_link(amount_inr=1, description="x", customer={})

    assert excinfo.value.status_code == 400
    assert "minimum amount" in excinfo.value.description


# -- module-level convenience functions delegate to a default client -----


def test_module_level_functions_exist_and_are_callable_attrs():
    import engine.action.razorpay_client as module

    for name in (
        "create_payment_link", "create_invoice", "create_customer",
        "create_mandate_registration_link", "execute_mandate", "revoke_mandate",
    ):
        assert callable(getattr(module, name))


def test_constructing_with_no_keys_configured_raises_cleanly(monkeypatch):
    # engine.action.razorpay_client is already imported at the top of this file
    # (and by every other test module) with no keys forced into scope at
    # import time - that alone proves import never requires keys. This test
    # proves the other half: only *constructing* a client requires them, and a
    # genuinely empty environment fails with RazorpayError, not a crash or a
    # silent no-op. load_dotenv is stubbed out so this doesn't depend on
    # whether a real .env happens to exist on disk in this repo.
    import engine.action.razorpay_client as module

    monkeypatch.setattr(module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("RAZORPAY_TEST_KEY_ID", raising=False)
    monkeypatch.delenv("RAZORPAY_TEST_KEY_SECRET", raising=False)

    with pytest.raises(RazorpayError):
        module.RazorpayClient()
