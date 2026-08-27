"""Track A: IVR — press 1/2 on a live call for a real mandate or a real
payment link (2026-08-27).

WHAT THESE TESTS ACTUALLY DEFEND
---------------------------------
Same shape of risk as test_telephony.py / test_telegram_dispatch.py: this is
a real-world-side-effect feature (a real phone rings, a real Razorpay
TEST-mode object can be created), so no test here makes a real network call
— Twilio's own dispatch (`telephony.place_ivr_call`) and every Razorpay call
(`razorpay_client.create_mandate_via_subscription` / `create_payment_link`)
are mocked throughout. The load-bearing claims:

  * `Ledger.ivr_select()` re-runs `check_bounds()` for real at keypress time
    — a mandate refusal, a renegotiation cap, or an exhausted touch budget
    blocks the selection exactly like it blocks every other channel, and
    NOTHING about being triggered by IVR gets its own softer rule;
  * `Ledger.ivr_available_options()` is a pure preview: it never spends a
    touch, never creates an Action, never writes an audit entry;
  * the two Twilio-facing webhooks (`/telephony/ivr-menu`,
    `/telephony/ivr-response`) ALWAYS return valid TwiML, never an HTTP
    error, for every case tried here (unknown entity, no options available,
    an invalid digit, a blocked selection, a Razorpay failure) — Twilio has
    no way to speak an HTTP status code to the person on the phone;
  * the real Razorpay call only happens when the gate allows, is audited
    whether it succeeds or fails, and is NEVER attempted for a blocked
    selection or an invalid digit;
  * the operator-facing trigger (`POST /entities/{id}/call-ivr-now`) only
    places a real call when `PK_REAL_TELEPHONY=1`, a working credential, and
    a real operator-submitted contact all hold — the same three-condition
    shape `WorldRunner.real_telephony_contact` shares with
    `_should_go_real_telephony`'s non-"is this manual" conditions.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.action import razorpay_client, telephony
from engine.action.razorpay_client import RazorpayError
from engine.integration.runner import WorldRunner
from engine.judgment.ledger import Ledger, ReviewQueueError
from engine.schemas import Invoice

NOW = dt.datetime(2026, 8, 26, 9, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-961", debtor_id="D-96", amount_inr=48000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


# ---------------------------------------------------------------------------
# 1. Ledger.ivr_available_options / ivr_select — isolated, no API, no network
# ---------------------------------------------------------------------------


def test_available_options_are_both_true_for_a_fresh_entity():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    options = ledger.ivr_available_options("INV-961", NOW)
    assert options == {"mandate_offer": True, "link": True}


def test_available_options_never_spends_a_touch_or_writes_an_audit_entry():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    audit_before = len(ledger.audit)
    touches_before = len(ledger.touches_by_debtor.get("D-96", []))
    ledger.ivr_available_options("INV-961", NOW)
    ledger.ivr_available_options("INV-961", NOW)
    assert len(ledger.audit) == audit_before
    assert len(ledger.touches_by_debtor.get("D-96", [])) == touches_before


def test_available_options_reflect_mandate_refused():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    entity = ledger.entities["INV-961"].model_copy()
    entity.mandate_refused = True
    ledger.entities["INV-961"] = entity
    options = ledger.ivr_available_options("INV-961", NOW)
    assert options["mandate_offer"] is False
    assert options["link"] is True, "a mandate refusal must not block the unrelated link option"


def test_available_options_unknown_entity_is_all_false():
    ledger = Ledger()
    assert ledger.ivr_available_options("INV-does-not-exist", NOW) == {"mandate_offer": False, "link": False}


def test_ivr_select_allowed_returns_a_real_bounds_checked_action():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(amount_inr=48000))
    out = ledger.ivr_select("INV-961", "mandate_offer", NOW)
    assert out["blocked"] is False
    assert out["block_reason"] is None
    action = out["action"]
    assert action is not None
    assert action.kind == "mandate_offer"
    assert action.params["amount_inr"] == 48000
    assert action.bounds_checked is True


def test_ivr_select_is_audited_before_it_is_returned():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    out = ledger.ivr_select("INV-961", "link", NOW)
    matching = [a for a in ledger.audit if a.detail.get("action_id") == out["action"].id]
    assert len(matching) == 1


def test_ivr_select_spends_a_real_touch():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    before = len(ledger.touches_by_debtor.get("D-96", []))
    ledger.ivr_select("INV-961", "link", NOW)
    after = len(ledger.touches_by_debtor.get("D-96", []))
    assert after == before + 1


def test_ivr_select_moves_no_state():
    """Mirrors manual_reminder's own shape exactly: a debtor's live keypress
    is a contact event, not the agent deciding anything, so it never moves
    the entity's escalation state on its own."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    state_before = ledger.entities["INV-961"].state
    ledger.ivr_select("INV-961", "mandate_offer", NOW)
    assert ledger.entities["INV-961"].state == state_before


def test_ivr_select_blocked_after_mandate_refused():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    entity = ledger.entities["INV-961"].model_copy()
    entity.mandate_refused = True
    ledger.entities["INV-961"] = entity

    out = ledger.ivr_select("INV-961", "mandate_offer", NOW)
    assert out["blocked"] is True
    assert out["action"] is None
    assert "NEVER" in out["block_reason"] or "refus" in out["block_reason"].lower()


def test_ivr_select_blocked_does_not_spend_a_touch():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    entity = ledger.entities["INV-961"].model_copy()
    entity.mandate_refused = True
    ledger.entities["INV-961"] = entity

    before = len(ledger.touches_by_debtor.get("D-96", []))
    ledger.ivr_select("INV-961", "mandate_offer", NOW)
    after = len(ledger.touches_by_debtor.get("D-96", []))
    assert after == before


def test_ivr_select_blocked_by_an_exhausted_touch_cap():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.touches_by_debtor["D-96"] = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=2)]

    out = ledger.ivr_select("INV-961", "link", NOW)
    assert out["blocked"] is True
    assert "max_touches_per_week" in out["block_reason"]


def test_ivr_select_rejects_an_unknown_kind():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    with pytest.raises(ReviewQueueError):
        ledger.ivr_select("INV-961", "voice", NOW)


def test_ivr_select_rejects_an_unknown_entity():
    ledger = Ledger()
    with pytest.raises(ReviewQueueError):
        ledger.ivr_select("INV-does-not-exist", "link", NOW)


# ---------------------------------------------------------------------------
# 2. API webhooks: /telephony/ivr-menu, /telephony/ivr-response
#
# Each test gets a FRESH day-0 WorldRunner via the `client` fixture — the
# app's lifespan rebuilds `runner`/`ledger` on every TestClient context
# (api/main.py's `_reset_world`), so tests never see another test's state.
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_ivr_menu_offers_both_options_for_a_fresh_entity(client):
    r = client.get("/telephony/ivr-menu", params={"entity_id": "INV-001"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/xml")
    assert "<Gather" in r.text
    assert "Press 1" in r.text and "Press 2" in r.text


def test_ivr_menu_unknown_entity_hangs_up_gracefully(client):
    r = client.get("/telephony/ivr-menu", params={"entity_id": "INV-does-not-exist"})
    assert r.status_code == 200
    assert "<Gather" not in r.text
    assert "<Hangup" in r.text
    assert "could not find your account" in r.text


def test_ivr_menu_offers_nothing_once_mandate_refused_and_touch_cap_full(client, monkeypatch):
    from api.main import ledger as live_ledger

    entity = live_ledger.entities["INV-001"].model_copy()
    entity.mandate_refused = True
    live_ledger.entities["INV-001"] = entity
    debtor_id = live_ledger._debtor_id("INV-001")
    live_ledger.touches_by_debtor[debtor_id] = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=2)]

    r = client.get("/telephony/ivr-menu", params={"entity_id": "INV-001"})
    assert r.status_code == 200
    assert "<Gather" not in r.text
    assert "<Hangup" in r.text
    assert "unable to offer" in r.text


def test_ivr_response_digit_1_creates_a_real_mandate_via_the_direct_unconditional_path(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        razorpay_client, "create_mandate_via_subscription",
        lambda *a, **k: calls.append(a) or {
            "plan": {"id": "plan_TEST"},
            "subscription": {"id": "sub_TEST", "short_url": "https://rzp.io/rzp/TEST"},
        },
    )
    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-001"}, data={"Digits": "1"})
    assert r.status_code == 200
    assert len(calls) == 1, "the mandate call must be made exactly once, directly, not through the rate-limited autonomous path"
    assert "automatic payment has been set up" in r.text

    from api.main import ledger as live_ledger
    entries = [a for a in live_ledger.audit if a.entity_id == "INV-001" and "IVR: mandate_offer created" in a.summary]
    assert len(entries) == 1
    assert entries[0].detail["short_url"] == "https://rzp.io/rzp/TEST"


def test_ivr_response_digit_2_creates_a_real_payment_link(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        razorpay_client, "create_payment_link",
        lambda *a, **k: calls.append(a) or {"id": "plink_TEST", "short_url": "https://rzp.io/rzp/LINK"},
    )
    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-001"}, data={"Digits": "2"})
    assert r.status_code == 200
    assert len(calls) == 1
    assert "payment link has been sent" in r.text


def test_ivr_response_invalid_digit_never_calls_razorpay(client, monkeypatch):
    calls = []
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(razorpay_client, "create_payment_link", lambda *a, **k: calls.append(1))

    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-001"}, data={"Digits": "9"})
    assert r.status_code == 200
    assert "<Hangup" in r.text
    assert calls == []


def test_ivr_response_blocked_selection_never_calls_razorpay(client, monkeypatch):
    from api.main import ledger as live_ledger

    entity = live_ledger.entities["INV-001"].model_copy()
    entity.mandate_refused = True
    live_ledger.entities["INV-001"] = entity

    calls = []
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", lambda *a, **k: calls.append(1))

    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-001"}, data={"Digits": "1"})
    assert r.status_code == 200
    assert "<Hangup" in r.text
    assert "not available" in r.text
    assert calls == [], "a blocked IVR selection must never reach a real Razorpay call"


def test_ivr_response_razorpay_failure_is_audited_and_hangs_up_gracefully(client, monkeypatch):
    def _boom(*a, **k):
        raise RazorpayError("bad request", status_code=400, description="Recurring digits in customer contact are disallowed")

    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _boom)
    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-001"}, data={"Digits": "1"})
    assert r.status_code == 200
    assert "<Hangup" in r.text
    assert "something went wrong" in r.text

    from api.main import ledger as live_ledger
    entries = [a for a in live_ledger.audit if a.entity_id == "INV-001" and "FAILED" in a.summary]
    assert len(entries) == 1


def test_ivr_response_unknown_entity_hangs_up_without_touching_the_ledger(client, monkeypatch):
    calls = []
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", lambda *a, **k: calls.append(1))
    r = client.post("/telephony/ivr-response", params={"entity_id": "INV-does-not-exist"}, data={"Digits": "1"})
    assert r.status_code == 200
    assert "<Hangup" in r.text
    assert calls == []


# ---------------------------------------------------------------------------
# 3. POST /entities/{id}/call-ivr-now — the operator-facing trigger
# ---------------------------------------------------------------------------


def test_call_ivr_now_refuses_without_the_opt_in_flag(client, monkeypatch):
    from api.main import runner as live_runner

    # Explicit, not assumed: WorldRunner's real_telephony default falls back
    # to the PK_REAL_TELEPHONY env var (see runner.py __init__), which this
    # dev machine's own .env may well have set to 1 from earlier real-call
    # work — a test asserting "opt-in OFF" behavior must force it off itself.
    live_runner.real_telephony = False
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(telephony, "place_ivr_call", lambda *a, **k: calls.append(1))
    client.post("/entities/INV-001/contact", json={"name": "Amogh", "phone": "9611550053"})

    r = client.post("/entities/INV-001/call-ivr-now")
    assert r.status_code == 200
    body = r.json()
    assert body["placed"] is False
    assert calls == []


def test_call_ivr_now_refuses_without_a_real_submitted_contact(client, monkeypatch):
    from api.main import runner as live_runner

    live_runner.real_telephony = True
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(telephony, "place_ivr_call", lambda *a, **k: calls.append(1))

    r = client.post("/entities/INV-001/call-ivr-now")
    assert r.status_code == 200
    assert r.json()["placed"] is False
    assert calls == []


def test_call_ivr_now_places_a_real_call_when_all_gates_hold(client, monkeypatch):
    from api.main import runner as live_runner

    live_runner.real_telephony = True
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    calls = []
    monkeypatch.setattr(
        telephony, "place_ivr_call",
        lambda to, entity_id: calls.append((to, entity_id)) or {
            "sid": "CAxxxx", "status": "queued", "to": to, "from": "+15005550006",
            "twiml_url": "https://example.com/telephony/ivr-menu?entity_id=INV-001",
        },
    )
    client.post("/entities/INV-001/contact", json={"name": "Amogh", "phone": "9611550053"})

    r = client.post("/entities/INV-001/call-ivr-now")
    assert r.status_code == 200
    body = r.json()
    assert body["placed"] is True
    assert body["call"]["sid"] == "CAxxxx"
    assert len(calls) == 1
    assert calls[0][1] == "INV-001"

    from api.main import ledger as live_ledger
    entries = [a for a in live_ledger.audit if a.entity_id == "INV-001" and "IVR call placed" in a.summary]
    assert len(entries) == 1


def test_call_ivr_now_failure_is_audited_and_returns_502(client, monkeypatch):
    from api.main import runner as live_runner

    live_runner.real_telephony = True
    monkeypatch.setattr(telephony, "is_configured", lambda: True)

    def _boom(to, entity_id):
        raise telephony.TelephonyError("The number +919611550053 is unverified for trial accounts.")

    monkeypatch.setattr(telephony, "place_ivr_call", _boom)
    client.post("/entities/INV-001/contact", json={"name": "Amogh", "phone": "9611550053"})

    r = client.post("/entities/INV-001/call-ivr-now")
    assert r.status_code == 502

    from api.main import ledger as live_ledger
    entries = [a for a in live_ledger.audit if a.entity_id == "INV-001" and "FAILED" in a.summary]
    assert len(entries) == 1


def test_call_ivr_now_unknown_entity_is_404(client):
    r = client.post("/entities/INV-does-not-exist/call-ivr-now")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. WorldRunner.real_telephony_contact — the reusable gate
# ---------------------------------------------------------------------------


def test_real_telephony_contact_requires_all_three_conditions(monkeypatch):
    world = WorldRunner(real_razorpay=False, real_tts=False, real_telephony=False)
    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    world.contacts.submit(world._contact_key("INV-001"), "Amogh", "9611550053", NOW)
    assert world.real_telephony_contact("INV-001") is None, "opt-in flag is off"

    world.real_telephony = True
    monkeypatch.setattr(telephony, "is_configured", lambda: False)
    assert world.real_telephony_contact("INV-001") is None, "no working credential"

    monkeypatch.setattr(telephony, "is_configured", lambda: True)
    fresh = WorldRunner(real_razorpay=False, real_tts=False, real_telephony=True)
    assert fresh.real_telephony_contact("INV-001") is None, "no real submitted contact"

    assert world.real_telephony_contact("INV-001") == "9611550053"


# ---------------------------------------------------------------------------
# 5. The seeded 45-day run never touches any of this — nothing in the
#    autonomous event loop calls ivr_select / place_ivr_call at all.
# ---------------------------------------------------------------------------


def test_the_full_seeded_run_never_calls_ivr_select_or_place_ivr_call(monkeypatch):
    def _fail(*a, **k):
        raise AssertionError("IVR machinery was reached during a fully-autonomous run")

    monkeypatch.setattr(telephony, "place_ivr_call", _fail)

    world = WorldRunner(seed=42, real_razorpay=False, real_tts=False)
    original = world.ledger.ivr_select
    calls = []
    world.ledger.ivr_select = lambda *a, **k: calls.append(1) or original(*a, **k)

    world.advance(45)  # must not raise
    assert calls == [], "nothing in the autonomous ladder should ever call ivr_select"
