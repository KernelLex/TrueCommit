"""Meta's direct WhatsApp Cloud API (engine/action/whatsapp_meta.py, packet 6
— live channel demo path). No test here makes a real network call: every one
mocks `httpx.post` directly, the same discipline `test_razorpay_client.py`
already uses for a REST client of this exact shape (lazy credential load,
one exception type, non-2xx -> raised error).
"""

import datetime as dt

import httpx
import pytest

from engine.action import whatsapp_meta
from engine.integration.runner import WorldRunner

NOW = dt.datetime(2026, 8, 26, 9, 0)


# ---------------------------------------------------------------------------
# 1. is_configured() / _credentials() — pure logic, no network
# ---------------------------------------------------------------------------


def test_is_configured_false_with_no_credentials(monkeypatch):
    monkeypatch.delenv("META_WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_WHATSAPP_PHONE_NUMBER_ID", raising=False)
    monkeypatch.setattr(whatsapp_meta, "load_dotenv", lambda *a, **k: None)
    assert whatsapp_meta.is_configured() is False


def test_is_configured_false_with_only_one_of_the_two_required(monkeypatch):
    monkeypatch.setattr(whatsapp_meta, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("META_WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.delenv("META_WHATSAPP_PHONE_NUMBER_ID", raising=False)
    assert whatsapp_meta.is_configured() is False


def test_is_configured_true_with_both_present(monkeypatch):
    monkeypatch.setattr(whatsapp_meta, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("META_WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_WHATSAPP_PHONE_NUMBER_ID", "123456")
    assert whatsapp_meta.is_configured() is True


# ---------------------------------------------------------------------------
# 2. _e164_digits — Meta wants digits only, no leading '+', unlike Twilio
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9611550053", "919611550053"),  # bare 10-digit -> assume +91, drop the +
        ("+919611550053", "919611550053"),
        ("+1 415-555-0100", "14155550100"),
        ("91 96115 50053", "919611550053"),
    ],
)
def test_e164_digits_normalizes_and_strips_the_plus(raw, expected):
    assert whatsapp_meta._e164_digits(raw) == expected


# ---------------------------------------------------------------------------
# 3. send_text / send_template — mocked httpx.post, no real network call
# ---------------------------------------------------------------------------


def _fake_response(status_code: int, body: dict) -> httpx.Response:
    return httpx.Response(status_code, json=body, request=httpx.Request("POST", "https://graph.facebook.com/x"))


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(whatsapp_meta, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("META_WHATSAPP_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_WHATSAPP_PHONE_NUMBER_ID", "123456")


def test_send_text_posts_a_free_form_text_payload(monkeypatch):
    calls = []

    def _fake_post(url, headers, json, timeout):
        calls.append((url, headers, json))
        return _fake_response(200, {"messages": [{"id": "wamid.TEST"}]})

    monkeypatch.setattr(whatsapp_meta.httpx, "post", _fake_post)
    result = whatsapp_meta.send_text("9611550053", "Rs.40,000 is due, please pay.")

    assert len(calls) == 1
    url, headers, payload = calls[0]
    assert "123456/messages" in url
    assert headers["Authorization"] == "Bearer tok"
    assert payload["type"] == "text"
    assert payload["text"]["body"] == "Rs.40,000 is due, please pay."
    assert payload["to"] == "919611550053"
    assert result == {"message_id": "wamid.TEST", "to": "919611550053"}


def test_send_template_posts_a_template_payload_not_free_form_text(monkeypatch):
    calls = []

    def _fake_post(url, headers, json, timeout):
        calls.append(json)
        return _fake_response(200, {"messages": [{"id": "wamid.TEMPLATE"}]})

    monkeypatch.setattr(whatsapp_meta.httpx, "post", _fake_post)
    result = whatsapp_meta.send_template("9611550053")

    payload = calls[0]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "hello_world"
    assert "text" not in payload
    assert result["template"] == "hello_world"


def test_a_non_2xx_response_raises_whatsapp_error_with_metas_own_message(monkeypatch):
    def _fake_post(url, headers, json, timeout):
        return _fake_response(
            403,
            {"error": {"message": "(#131047) Message failed to send because more than 24 hours have passed "
                                   "since the customer last replied to this number.", "code": 131047}},
        )

    monkeypatch.setattr(whatsapp_meta.httpx, "post", _fake_post)
    with pytest.raises(whatsapp_meta.WhatsAppError, match="24 hours"):
        whatsapp_meta.send_text("9611550053", "hello")


def test_a_network_error_raises_whatsapp_error_not_the_raw_httpx_exception(monkeypatch):
    def _fake_post(url, headers, json, timeout):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(whatsapp_meta.httpx, "post", _fake_post)
    with pytest.raises(whatsapp_meta.WhatsAppError):
        whatsapp_meta.send_text("9611550053", "hello")


def test_send_text_raises_without_credentials(monkeypatch):
    monkeypatch.delenv("META_WHATSAPP_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_WHATSAPP_PHONE_NUMBER_ID", raising=False)
    with pytest.raises(whatsapp_meta.WhatsAppError, match="not set"):
        whatsapp_meta.send_text("9611550053", "hello")


# ---------------------------------------------------------------------------
# 4. The autonomous 45-day run must never reach this channel at all
# ---------------------------------------------------------------------------


def test_the_full_seeded_run_never_attempts_a_real_meta_whatsapp_send_even_with_the_flag_on(monkeypatch):
    """Mirrors test_telephony.py's own equivalent test exactly. `real_
    whatsapp_meta_contact()` is only ever called from api/main.py's manual,
    webhook-driven IVR confirmation route — nothing in WorldRunner's own
    autonomous ladder/outreach code calls it — but this is the cheap,
    structural proof the rest of this project always adds for a real-
    dispatch channel, rather than trusting that by inspection alone."""
    monkeypatch.setattr(whatsapp_meta, "is_configured", lambda: True)

    def _fail(*a, **k):
        raise AssertionError("a real Meta WhatsApp send was attempted during a fully-autonomous run")

    monkeypatch.setattr(whatsapp_meta, "send_text", _fail)
    monkeypatch.setattr(whatsapp_meta, "send_template", _fail)

    world = WorldRunner(seed=42, real_razorpay=False, real_tts=False, real_whatsapp_meta=True)
    for entity_id in world.invoices:
        key = world._contact_key(entity_id)
        if world.contacts.get(key) is None:
            world.contacts.submit(key, "Test Contact", "9611550053", NOW)
    world.advance(45)  # must not raise
