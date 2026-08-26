"""Packet P17 — real Telegram messages (text + audio), the channel that
replaced the Twilio-WhatsApp real-send path as this project's actual
real-message demo path (see tracking/DECISIONS.md, 2026-08-27, for why
WhatsApp hit a real wall at every provider tried).

WHAT THESE TESTS ACTUALLY DEFEND
---------------------------------
Same shape of risk as `test_telephony.py`, and the same answer: every test
here mocks `engine.action.telegram_bot` — no test makes a real network call.
The load-bearing claims:

  * an AUTONOMOUS action can never reach a real Telegram send, regardless of
    config — same safety property `_should_go_real_telephony` has, applied to
    `_should_go_real_telegram`;
  * a manual send only goes real when ALL FOUR conditions hold: manual,
    `PK_REAL_TELEGRAM` opt-in, a working bot token, and a real `chat_id` on
    file for this entity's debtor;
  * THE SPECIFIC REGRESSION this file exists to pin: a real Telegram send can
    succeed at the dispatch layer while the API response body silently omits
    it, because `api/main.py::_manual_message_rows()` explicitly whitelists
    which `detail` keys it surfaces — found live 2026-08-27 when a real
    Telegram message was confirmed delivered (a real `message_id` came back
    from Telegram's API) but `POST /remind-now`'s response showed no
    `telegram_status` at all. The dispatch-layer code was correct throughout;
    the bug was one missing pair of dict keys in the API's read-back
    function. `test_remind_now_surfaces_the_real_telegram_send_in_its_response`
    below fails without that fix and passes with it.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.action import telegram_bot
from engine.integration.runner import WorldRunner
from engine.judgment import state_machine as sm
from engine.schemas import Action

NOW = dt.datetime(2026, 8, 26, 9, 0)


@pytest.fixture
def world():
    return WorldRunner(real_razorpay=False, real_tts=False, real_telegram=True)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _action(entity_id: str, kind: str, manual: bool) -> Action:
    params = {"stage": "firm"}
    if manual:
        params["manual"] = True
    return Action(
        id="A-TEST", entity_id=entity_id, kind=kind, params=params,
        reason="test", bounds_checked=True, ts=NOW,
    )


# ---------------------------------------------------------------------------
# 1. The gate: WorldRunner._should_go_real_telegram
# ---------------------------------------------------------------------------


def test_gate_refuses_an_autonomous_action_even_with_everything_else_on(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)
    action = _action("INV-001", "message", manual=False)
    assert world._should_go_real_telegram(action, "INV-001") is False


def test_gate_refuses_without_the_explicit_opt_in_flag(monkeypatch):
    world = WorldRunner(real_razorpay=False, real_tts=False, real_telegram=False)
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)
    action = _action("INV-001", "message", manual=True)
    assert world._should_go_real_telegram(action, "INV-001") is False


def test_gate_refuses_without_a_working_bot_token(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: False)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)
    action = _action("INV-001", "message", manual=True)
    assert world._should_go_real_telegram(action, "INV-001") is False


def test_gate_refuses_when_no_chat_id_is_linked_even_with_a_real_contact(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    # name/phone submitted, but no Telegram opt-in linked
    action = _action("INV-001", "message", manual=True)
    assert world._should_go_real_telegram(action, "INV-001") is False


def test_gate_allows_only_when_all_four_conditions_hold(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)
    assert world._should_go_real_telegram(_action("INV-001", "message", manual=True), "INV-001") is True
    assert world._should_go_real_telegram(_action("INV-001", "voice", manual=True), "INV-001") is True


# ---------------------------------------------------------------------------
# 2. Dispatch actually calls telegram_bot.send_message / send_voice
# ---------------------------------------------------------------------------


def test_manual_message_sends_a_real_telegram_text_when_the_gate_allows_it(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        telegram_bot, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or {"message_id": 42, "chat_id": chat_id},
    )
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)

    out = world.ledger.manual_reminder("INV-001", "message", world.now())
    world.dispatch_action(out["action"])

    assert len(sent) == 1
    assert sent[0][0] == "8327566456"
    entries = [
        a for a in world.ledger.audit
        if a.entity_id == "INV-001" and a.detail.get("action_id") == out["action"].id
    ]
    assert entries[-1].detail["telegram_status"] == "real_message_sent"
    assert entries[-1].detail["telegram_message_id"] == 42


def test_manual_message_stays_simulated_only_without_a_linked_chat_id(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(telegram_bot, "send_message", lambda *a, **k: sent.append(1))
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    # no link_telegram call

    out = world.ledger.manual_reminder("INV-001", "message", world.now())
    world.dispatch_action(out["action"])

    assert sent == [], "send_message must never be invoked without a linked chat_id"


def test_a_real_telegram_send_failure_is_audited_and_falls_back_cleanly(world, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)

    def _boom(chat_id, text):
        raise telegram_bot.TelegramError("Forbidden: bot was blocked by the user")

    monkeypatch.setattr(telegram_bot, "send_message", _boom)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)

    audit_before = len(world.ledger.audit)
    out = world.ledger.manual_reminder("INV-001", "message", world.now())
    world.dispatch_action(out["action"])  # must not raise

    entries = [
        a for a in world.ledger.audit
        if a.entity_id == "INV-001" and a.detail.get("action_id") == out["action"].id
    ]
    assert entries[-1].detail["telegram_status"] == "real_send_failed"
    assert "blocked by the user" in entries[-1].detail["telegram_error"]
    assert len(world.ledger.audit) > audit_before + 1, "the failure itself must be audited, not swallowed"


def test_manual_voice_also_sends_the_real_audio_via_telegram(monkeypatch):
    # real_tts=True: Telegram audio-sending only happens after a successful
    # local audio generation (see _dispatch_voice) - the shared `world`
    # fixture defaults real_tts=False, which would short-circuit before ever
    # reaching the Telegram branch this test exists to check.
    world = WorldRunner(real_razorpay=False, real_tts=True, real_telegram=True)

    class _FakeTTS:
        def __init__(self, text, lang):
            pass

        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"\xff\xf3\x84\xc4" + b"\x00" * 64)

    monkeypatch.setattr("gtts.gTTS", _FakeTTS)
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    sent = []
    monkeypatch.setattr(
        telegram_bot, "send_voice",
        lambda chat_id, path, caption=None: sent.append((chat_id, path)) or {"message_id": 7, "chat_id": chat_id},
    )
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    world.contacts.link_telegram(world._contact_key("INV-001"), "8327566456", NOW)

    out = world.ledger.manual_reminder("INV-001", "voice", world.now())
    world.dispatch_action(out["action"])

    assert len(sent) == 1
    assert sent[0][0] == "8327566456"
    record = world.reminders[-1]
    assert record["telegram_status"] == "real_message_sent"
    assert record["telegram_message_id"] == 7
    assert record["audio_generation"] == "ok", "the local MP3 is still generated regardless of Telegram delivery"


# ---------------------------------------------------------------------------
# 3. THE regression: the API must surface a real Telegram send, not swallow it
# ---------------------------------------------------------------------------


def test_remind_now_surfaces_the_real_telegram_send_in_its_response(client, monkeypatch):
    """Found live 2026-08-27: `telegram_bot.send_message` genuinely succeeded
    (confirmed by a real Telegram message_id) but `POST /remind-now`'s JSON
    response showed no `telegram_status` field at all — `_manual_message_rows`
    only whitelisted `whatsapp_status`/`whatsapp_sid`, never the Telegram
    equivalents. This test fails on the pre-fix code and passes after it."""
    from api.main import ledger, runner

    monkeypatch.setattr(runner, "real_telegram", True)
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)
    monkeypatch.setattr(
        telegram_bot, "send_message",
        lambda chat_id, text: {"message_id": 99, "chat_id": chat_id},
    )

    entity_id = next(
        eid for eid, e in ledger.entities.items()
        if e.state not in sm.TERMINAL_STATES
        and len(ledger.touches_by_debtor.get(ledger.debtor_of.get(eid, eid), [])) < sm.MAX_TOUCHES_PER_WEEK
    )
    client.post(f"/entities/{entity_id}/contact", json={"name": "Amogh", "phone": "9611550053"})
    client.post(f"/entities/{entity_id}/contact/telegram", json={"chat_id": "8327566456"})

    body = client.post(f"/entities/{entity_id}/remind-now", json={"channel": "message"}).json()
    assert body["blocked"] is False
    assert body["reminder"]["telegram_status"] == "real_message_sent"
    assert body["reminder"]["telegram_message_id"] == 99

    listed = client.get(f"/entities/{entity_id}/reminders").json()
    matching = [r for r in listed["reminders"] if r["action_id"] == body["action"]["id"]]
    assert len(matching) == 1
    assert matching[0]["telegram_status"] == "real_message_sent"
    assert matching[0]["telegram_message_id"] == 99


# ---------------------------------------------------------------------------
# 4. The seeded 45-day run stays network-free even with the opt-in flag on
# ---------------------------------------------------------------------------


def test_the_full_seeded_run_never_attempts_a_real_telegram_send_even_with_the_flag_on(monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    monkeypatch.setattr(telegram_bot, "is_configured", lambda: True)

    def _fail(*a, **k):
        raise AssertionError("a real Telegram send was attempted during a fully-autonomous run")

    monkeypatch.setattr(telegram_bot, "send_message", _fail)
    monkeypatch.setattr(telegram_bot, "send_voice", _fail)

    world = WorldRunner(seed=42, real_razorpay=False, real_tts=False, real_telegram=True)
    for entity_id in world.invoices:
        key = world._contact_key(entity_id)
        if world.contacts.get(key) is None:
            world.contacts.submit(key, "Test Contact", "9611550053", NOW)
        world.contacts.link_telegram(key, "8327566456", NOW)
    world.advance(45)  # must not raise
