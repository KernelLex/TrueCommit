import datetime as dt

import pytest

from engine.action.evidence import build_evidence_packet, render_card
from engine.action.messenger import Messenger
from engine.action.sentinel import MAX_RETRIES, Sentinel
from engine.action import razorpay_client
from engine.schemas import Action, Invoice, Message

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_action(kind="mandate_offer", **overrides) -> Action:
    base = dict(id="A-1", entity_id="INV-001", kind=kind, params={"amount_inr": 40000}, reason="test", bounds_checked=True, ts=NOW)
    base.update(overrides)
    return Action(**base)


def make_invoice(**overrides) -> Invoice:
    base = dict(id="INV-001", debtor_id="D-01", amount_inr=40000, issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="disputed", description="x", delivery_confirmed=True)
    base.update(overrides)
    return Invoice(**base)


# -- messenger --------------------------------------------------------------


def test_messenger_derives_rail_from_action_kind():
    m = Messenger()
    qm = m.send(make_action(kind="mandate_offer"), "wa", "text")
    assert qm.rail == "mandate_link"
    qm2 = m.send(make_action(kind="link", id="A-2"), "wa", "text")
    assert qm2.rail == "plain_link"
    qm3 = m.send(make_action(kind="voice", id="A-3"), "wa", "text")
    assert qm3.rail == "voice_note"


def test_messenger_can_override_rail_for_wa_native_payment():
    m = Messenger()
    qm = m.send(make_action(kind="link"), "wa", "text", rail="wa_native_payment")
    assert qm.rail == "wa_native_payment"


def test_messenger_tracks_per_entity():
    m = Messenger()
    m.send(make_action(entity_id="INV-001"), "wa", "a")
    m.send(make_action(entity_id="INV-002", id="A-2"), "wa", "b")
    assert len(m.for_entity("INV-001")) == 1
    assert len(m.for_entity("INV-002")) == 1


# -- sentinel -----------------------------------------------------------


def test_sentinel_retries_up_to_max_then_dead_letters():
    s = Sentinel()
    outcomes = []
    for _ in range(MAX_RETRIES + 1):
        outcomes.append(s.record_send_attempt("A-1", "INV-001", "mandate_offer", False, NOW, "err"))
    assert outcomes == ["retry"] * MAX_RETRIES + ["dead_letter"]
    assert len(s.dead_letter) == 1
    assert s.dead_letter[0].attempts == MAX_RETRIES + 1


def test_sentinel_success_resets_attempts():
    s = Sentinel()
    s.record_send_attempt("A-1", "INV-001", "mandate_offer", False, NOW, "err")
    s.record_send_attempt("A-1", "INV-001", "mandate_offer", False, NOW, "err")
    result = s.record_send_attempt("A-1", "INV-001", "mandate_offer", True, NOW)
    assert result == "ok"
    assert "A-1" not in s.attempts


def test_sentinel_circuit_breaker_opens_after_threshold():
    s = Sentinel()
    for i in range(10):
        s.record_send_attempt(f"A-{i}", "INV-001", "message", False, NOW, "err")
    assert s.should_pause_outbound() is True


def test_sentinel_circuit_breaker_closes_on_success():
    s = Sentinel()
    for i in range(6):
        s.record_send_attempt(f"A-{i}", "INV-001", "message", False, NOW, "err")
    assert s.should_pause_outbound() is True
    s.record_send_attempt("A-recover", "INV-001", "message", True, NOW)
    assert s.should_pause_outbound() is False


def test_sentinel_link_timeout_never_assumes_delivery():
    s = Sentinel()
    s.track_link_sent("A-1", NOW)
    assert s.link_timed_out("A-1", NOW + dt.timedelta(hours=1)) is False
    assert s.link_timed_out("A-1", NOW + dt.timedelta(hours=49)) is True


def test_sentinel_link_opened_cancels_timeout():
    s = Sentinel()
    s.track_link_sent("A-1", NOW)
    s.mark_link_opened("A-1")
    assert s.link_timed_out("A-1", NOW + dt.timedelta(hours=49)) is False


# -- evidence -----------------------------------------------------------


def test_evidence_packet_uses_ledger_facts_not_llm_amount():
    inv = make_invoice(amount_inr=215000, delivery_confirmed=False)
    msgs = [Message(id="M-1", thread_id="T-1", direction="in", channel="wa", text="damaged panels", ts=NOW)]
    packet = build_evidence_packet(inv, msgs, NOW)
    assert packet.amount_inr == 215000  # from the invoice record, never invented
    assert packet.delivery_confirmed is False
    assert "M-1" not in packet.summary  # summary is a placeholder, not fabricated from the thread


def test_evidence_packet_excerpt_capped_and_card_renders():
    inv = make_invoice()
    msgs = [Message(id=f"M-{i}", thread_id="T-1", direction="in", channel="wa", text=f"msg {i}", ts=NOW) for i in range(10)]
    packet = build_evidence_packet(inv, msgs, NOW)
    assert len(packet.thread_excerpt) == 6
    assert packet.thread_excerpt[-1].text == "msg 9"
    card = render_card(packet)
    assert "DISPUTE" in card and "INV-001" in card


# -- razorpay_client (interface-only until Phase C) --------------------


@pytest.mark.parametrize("fn,args", [
    (razorpay_client.create_payment_link, (1000, "x", {})),
    (razorpay_client.create_invoice, (1000, "x", {}, "2026-09-01")),
    (razorpay_client.create_mandate_order, (1000, "2026-09-01", {}, "scheduled")),
    (razorpay_client.execute_mandate, ("M-1",)),
    (razorpay_client.revoke_mandate, ("M-1",)),
])
def test_razorpay_client_stubs_raise_until_configured(fn, args):
    with pytest.raises(NotImplementedError):
        fn(*args)
