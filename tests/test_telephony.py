"""Packet P16 — real phone call + real WhatsApp message via Twilio.

WHAT THESE TESTS ACTUALLY DEFEND
---------------------------------
This is the one place in the codebase where a real-world side effect on a
real human being becomes possible, so almost every test here is about the
GATE (`WorldRunner._should_go_real_telephony`) rather than about Twilio's own
API — no test in this file makes a real network call; every one mocks
`engine.action.telephony.is_configured` / `place_call` / `send_whatsapp`. The
load-bearing claims:

  * an AUTONOMOUS action can never go real, no matter how the other three
    gate conditions are set — this is what keeps the seeded 45-day run and
    every `pytest` run network-free even on a machine with real Twilio
    credentials sitting in `.env`;
  * a manual action only goes real when ALL FOUR conditions hold at once:
    manual, `PK_REAL_TELEPHONY` opt-in, a working credential, and a real
    (operator-submitted) contact — the synthetic demo number is never dialled
    for real;
  * a Twilio failure is audited and degrades to the existing simulated
    fields, never silently swallowed and never crashes the dispatch;
  * the seeded 45-day run is unaffected even with the opt-in flag on, because
    no autonomous action can ever pass the gate.
"""

import datetime as dt

import pytest

from engine.action import telephony
from engine.integration.runner import WorldRunner
from engine.schemas import Action

NOW = dt.datetime(2026, 8, 26, 9, 0)


@pytest.fixture
def world():
    return WorldRunner(real_razorpay=False, real_tts=False, real_telephony=True)


def _action(entity_id: str, kind: str, manual: bool) -> Action:
    params = {"stage": "firm"}
    if manual:
        params["manual"] = True
    return Action(
        id="A-TEST", entity_id=entity_id, kind=kind, params=params,
        reason="test", bounds_checked=True, ts=NOW,
    )


def _drive_to_escalate_2(world: WorldRunner, entity_id: str) -> None:
    world._emit("invoice_triaged", entity_id, {}, 0)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_1 (message, touch 1)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_2 (voice,   touch 2)


# ---------------------------------------------------------------------------
# 1. is_configured() / _e164 — pure logic, no network
# ---------------------------------------------------------------------------


def test_is_configured_false_with_no_credentials(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(telephony, "load_dotenv", lambda: None)
    assert telephony.is_configured() is False


def test_is_configured_true_with_account_sid_and_auth_token(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.delenv("TWILIO_API_KEY_SID", raising=False)
    monkeypatch.delenv("TWILIO_API_KEY_SECRET", raising=False)
    monkeypatch.setattr(telephony, "load_dotenv", lambda: None)
    assert telephony.is_configured() is True


def test_is_configured_true_with_api_key_sid_and_secret_plus_account_sid(monkeypatch):
    """The credential shape Twilio's 'API keys & tokens' console page hands
    out (SK... + secret) — needs the Account SID alongside it too, unlike the
    Auth Token shape, since an API key alone doesn't say which account it
    belongs to."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxxx")
    monkeypatch.setenv("TWILIO_API_KEY_SID", "SKxxxx")
    monkeypatch.setenv("TWILIO_API_KEY_SECRET", "secret")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(telephony, "load_dotenv", lambda: None)
    assert telephony.is_configured() is True


def test_is_configured_false_with_an_api_key_but_no_account_sid(monkeypatch):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.setenv("TWILIO_API_KEY_SID", "SKxxxx")
    monkeypatch.setenv("TWILIO_API_KEY_SECRET", "secret")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(telephony, "load_dotenv", lambda: None)
    assert telephony.is_configured() is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("9611550053", "+919611550053"),
        ("+919611550053", "+919611550053"),
        ("+91 96115-50053", "+919611550053"),
    ],
)
def test_e164_normalizes_bare_indian_numbers(raw, expected):
    assert telephony._e164(raw) == expected


# ---------------------------------------------------------------------------
# 2. The gate: WorldRunner._should_go_real_telephony
# ---------------------------------------------------------------------------


def test_gate_refuses_an_autonomous_action_even_with_everything_else_on(world, monkeypatch):
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    action = _action("INV-001", "voice", manual=False)
    assert world._should_go_real_telephony(action, "INV-001") is False


def test_gate_refuses_without_the_explicit_opt_in_flag(monkeypatch):
    world = WorldRunner(real_razorpay=False, real_tts=False, real_telephony=False)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    action = _action("INV-001", "voice", manual=True)
    assert world._should_go_real_telephony(action, "INV-001") is False


def test_gate_refuses_without_a_working_credential(world, monkeypatch):
    monkeypatch.setattr(telephony, "is_configured", lambda: False)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    action = _action("INV-001", "voice", manual=True)
    assert world._should_go_real_telephony(action, "INV-001") is False


def test_gate_refuses_the_synthetic_demo_contact_even_with_everything_else_on(world, monkeypatch):
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    # no contact ever submitted -> resolve_contact falls back to the demo constant
    action = _action("INV-001", "voice", manual=True)
    assert world._should_go_real_telephony(action, "INV-001") is False


def test_gate_allows_only_when_all_four_conditions_hold(world, monkeypatch):
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    action = _action("INV-001", "voice", manual=True)
    assert world._should_go_real_telephony(action, "INV-001") is True
    # same four conditions, message kind
    assert world._should_go_real_telephony(_action("INV-001", "message", manual=True), "INV-001") is True


# ---------------------------------------------------------------------------
# 3. Dispatch actually calls telephony.place_call / send_whatsapp when (and
#    only when) the gate allows it
# ---------------------------------------------------------------------------


def test_manual_voice_places_a_real_call_when_the_gate_allows_it(world, monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(
        telephony, "place_call",
        lambda to, text: calls.append((to, text)) or {"sid": "CAxxxx", "status": "queued", "to": to, "from": "+15005550006"},
    )
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)

    out = world.ledger.manual_reminder("INV-001", "voice", world.now())
    world.dispatch_action(out["action"])

    assert len(calls) == 1
    to, text = calls[0]
    assert to == "9611550053"
    record = world.reminders[-1]
    assert record["dial_status"] == "real_call_placed"
    assert record["call_sid"] == "CAxxxx"


def test_manual_voice_stays_simulated_when_the_gate_refuses(world, monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(telephony, "place_call", lambda *a, **k: calls.append(1))
    # No contact submitted -> demo fallback -> gate refuses even with opt-in on
    out = world.ledger.manual_reminder("INV-001", "voice", world.now())
    world.dispatch_action(out["action"])

    assert calls == [], "place_call must never be invoked when the gate refuses"
    record = world.reminders[-1]
    assert record["dial_status"] == "simulated_no_telephony_provider"
    assert "call_sid" not in record


def test_a_real_call_failure_is_audited_and_falls_back_to_the_simulated_fields(world, monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)

    def _boom(to, text):
        raise telephony.TelephonyError("The number +919611550053 is unverified for trial accounts.")

    monkeypatch.setattr(telephony, "place_call", _boom)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)

    audit_before = len(world.ledger.audit)
    out = world.ledger.manual_reminder("INV-001", "voice", world.now())
    world.dispatch_action(out["action"])  # must not raise

    record = world.reminders[-1]
    assert record["dial_status"] == "real_call_failed"
    assert "unverified" in record["dial_error"]
    assert len(world.ledger.audit) > audit_before + 1, "the failure itself must be audited, not swallowed"


def test_manual_whatsapp_message_sends_for_real_when_the_gate_allows_it(world, monkeypatch):
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        telephony, "send_whatsapp",
        lambda to, text: sent.append((to, text)) or {"sid": "SMxxxx", "status": "queued", "to": f"whatsapp:{to}", "from": "whatsapp:+14155238886"},
    )
    entity_id = "INV-001"
    assert world.channel_of.get(entity_id, "wa") == "wa", "fixture assumption: INV-001 is a WhatsApp-channel entity"
    world.contacts.submit(world._contact_key(entity_id), "Amogh", "9611550053", NOW)

    out = world.ledger.manual_reminder(entity_id, "message", world.now())
    world.dispatch_action(out["action"])

    assert len(sent) == 1
    entries = [a for a in world.ledger.audit if a.entity_id == entity_id and a.detail.get("action_id") == out["action"].id]
    assert entries[-1].detail["whatsapp_status"] == "real_message_sent"
    assert entries[-1].detail["whatsapp_sid"] == "SMxxxx"


def test_manual_whatsapp_message_stays_simulated_without_the_opt_in_flag(monkeypatch):
    world = WorldRunner(real_razorpay=False, real_tts=False, real_telephony=False)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(telephony, "send_whatsapp", lambda *a, **k: sent.append(1))
    entity_id = "INV-001"
    world.contacts.submit(world._contact_key(entity_id), "Amogh", "9611550053", NOW)

    out = world.ledger.manual_reminder(entity_id, "message", world.now())
    world.dispatch_action(out["action"])

    assert sent == [], "send_whatsapp must never be invoked without the explicit PK_REAL_TELEPHONY opt-in"


# ---------------------------------------------------------------------------
# 4. The seeded 45-day run stays network-free even with the opt-in flag on —
#    the autonomous-only restriction is what makes this true, not luck.
# ---------------------------------------------------------------------------


def test_the_full_seeded_run_never_attempts_a_real_call_or_whatsapp_send_even_with_the_flag_on(monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)

    def _fail(*a, **k):
        raise AssertionError("a real telephony call was attempted during a fully-autonomous run")

    monkeypatch.setattr(telephony, "place_call", _fail)
    monkeypatch.setattr(telephony, "send_whatsapp", _fail)

    world = WorldRunner(seed=42, real_razorpay=False, real_tts=False, real_telephony=True)
    # submit a real contact for every debtor so the gate's 4th condition can
    # never be the thing blocking it either -- only "manual" is left standing.
    for entity_id, invoice in world.invoices.items():
        key = world._contact_key(entity_id)
        if world.contacts.get(key) is None:
            world.contacts.submit(key, "Test Contact", "9611550053", NOW)
    world.advance(45)  # must not raise
