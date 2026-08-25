import datetime as dt

import pytest

from engine.judgment.ledger import (
    CLARIFY_CONFIDENCE_GATE,
    MONEY_ACTION_CONFIDENCE_GATE,
    Ledger,
    ReviewQueueError,
)
from engine.schemas import Invoice

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-901", debtor_id="D-01", amount_inr=40000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


def test_audit_entry_exists_before_action_is_returned():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("invoice_triaged", "INV-901", {}, NOW)
    ledger.process_event("outreach_sent", "INV-901", {}, NOW)
    action = ledger.process_event("extraction_received", "INV-901", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    action = ledger.process_event("mandate_offer_requested", "INV-901", {}, NOW)
    assert action is not None
    assert action.kind == "mandate_offer"
    matching = [a for a in ledger.audit if a.detail.get("action_id") == action.id]
    assert len(matching) == 1, "every returned action must have a matching audit entry"


def test_mandate_amount_always_equals_ledger_invoice_amount_never_llm_number():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(amount_inr=40000))
    ledger.process_event("invoice_triaged", "INV-901", {}, NOW)
    # extraction (stand-in for a real LLM call) claims a WRONG amount
    ledger.process_event("extraction_received", "INV-901", {"amount_inr": 999999}, NOW)
    action = ledger.process_event("mandate_offer_requested", "INV-901", {}, NOW)
    # the mandate_offer action must use the LEDGER's invoice amount, never the extraction's
    assert action.kind == "mandate_offer"
    assert action.params["amount_inr"] == 40000


def test_tier0_reserve_recovers_with_zero_touches():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-902"))
    ledger.register_reserve("INV-902", True)
    action = ledger.process_event("payment_failed", "INV-902", {}, NOW)
    assert action.kind == "mandate_execute"
    assert action.params["source"] == "reserve"
    assert ledger.entities["INV-902"].state == "KEPT"
    assert ledger.entities["INV-902"].touches == []


def test_dispute_produces_evidence_packet_and_stops_the_ladder():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-903"))
    action = ledger.process_event("dispute_raised", "INV-903", {}, NOW)
    assert action.kind == "evidence_packet"
    assert ledger.entities["INV-903"].state == "DISPUTED"
    # a follow-up event must not produce any new outbound action
    next_action = ledger.process_event("promise_broken", "INV-903", {}, NOW + dt.timedelta(days=1))
    assert next_action is None


def test_kept_promise_improves_trust():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-904", debtor_id="D-77"))
    ledger.process_event("extraction_received", "INV-904", {"amount_inr": 40000}, NOW)
    before = ledger._trust_for("D-77", NOW)
    ledger.process_event("promise_kept", "INV-904", {}, NOW)
    after = ledger.trust["D-77"]
    assert after.alpha == before.alpha + 1


def test_a_gentle_nudge_is_a_real_bounds_checked_action():
    """Packet P2 logged that an `outreach_sent` against an ENGAGED entity
    produced no Action at all, so the runner could send no gentle nudge without
    inventing one outside the gate. The ledger now emits one: a `message`
    action at stage "gentle", audited before it is returned and counted as a
    touch like every other outbound contact."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-906", debtor_id="D-80"))
    ledger.process_event("invoice_triaged", "INV-906", {}, NOW)

    action = ledger.process_event("outreach_sent", "INV-906", {"stage": "gentle"}, NOW)
    assert action is not None, "a gentle nudge must be an Action, not a runner-side improvisation"
    assert action.kind == "message"
    assert action.params["stage"] == "gentle"
    assert action.bounds_checked is True
    assert ledger.entities["INV-906"].state == "ENGAGED"

    # audited BEFORE it was handed back (law 3), and counted as a touch (law 4)
    assert [a for a in ledger.audit if a.detail.get("action_id") == action.id]
    assert ledger.entities["INV-906"].touches == [NOW]
    assert ledger.touches_by_debtor["D-80"] == [NOW]


def test_outreach_at_an_escalation_stage_is_a_firm_message():
    """A second outreach beat against an entity that is already escalating
    changes no state — but it is still a touch someone asked for, so it still
    has to come back as a bounds-checked Action rather than nothing."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-907", debtor_id="D-81"))
    ledger.process_event("extraction_received", "INV-907", {"amount_inr": 40000}, NOW)
    ledger.process_event("promise_broken", "INV-907", {}, NOW)
    assert ledger.entities["INV-907"].state == "ESCALATE_1"

    later = NOW + dt.timedelta(days=8)  # fresh touch window
    action = ledger.process_event("outreach_sent", "INV-907", {"stage": "firm"}, later)
    assert action is not None
    assert (action.kind, action.params["stage"]) == ("message", "firm")

    # ...and the same holds one rung further up the ladder
    ledger.process_event("promise_broken", "INV-907", {}, later)
    assert ledger.entities["INV-907"].state == "ESCALATE_2"
    even_later = later + dt.timedelta(days=8)
    action = ledger.process_event("outreach_sent", "INV-907", {"stage": "firm"}, even_later)
    assert (action.kind, action.params["stage"]) == ("message", "firm")


def test_no_agent_nudge_at_the_merchant_review_and_handoff_rungs():
    """ESCALATE_3 is the formal/legal notice, which law 4 routes to the
    MERCHANT for review, and ESCALATE_4 is the ladder exhausting into a human.
    Neither is a moment for the agent to send another message of its own, so an
    outreach beat there yields no Action — and, unlike the old gentle-nudge
    gap, that silence is deliberate and stated in `_OUTREACH_ACTION`."""
    for state in ("ESCALATE_3", "ESCALATE_4"):
        ledger = Ledger()
        ledger.register_invoice(make_invoice(id=f"INV-94-{state}", debtor_id=f"D-{state}"))
        entity = ledger._entity(f"INV-94-{state}")
        entity.state = state
        ledger.entities[f"INV-94-{state}"] = entity

        assert ledger.process_event("outreach_sent", f"INV-94-{state}", {"stage": "formal"}, NOW) is None
        assert ledger.touches_by_debtor.get(f"D-{state}", []) == []


def test_the_touch_cap_is_shared_across_one_debtors_invoices():
    """The law is per DEBTOR. Two invoices belonging to D-90 use up the week's
    budget; a third invoice of the same debtor is blocked even though it has
    never been contacted — and the block is audited, not silently dropped."""
    ledger = Ledger()
    for n in (1, 2, 3):
        ledger.register_invoice(make_invoice(id=f"INV-91{n}", debtor_id="D-90"))
    for entity_id in ("INV-911", "INV-912", "INV-913"):
        ledger.process_event("invoice_triaged", entity_id, {}, NOW)

    first = ledger.process_event("outreach_sent", "INV-911", {"stage": "gentle"}, NOW)
    second = ledger.process_event("outreach_sent", "INV-912", {"stage": "gentle"}, NOW)
    third = ledger.process_event("outreach_sent", "INV-913", {"stage": "gentle"}, NOW)

    assert first is not None and second is not None
    assert third is None, "the debtor's third contact this week must be blocked"
    assert ledger.entities["INV-913"].touches == []
    assert len(ledger.touches_by_debtor["D-90"]) == 2

    blocked = [a for a in ledger.audit if a.summary == "action blocked: message"]
    assert len(blocked) == 1
    assert "max_touches_per_week" in blocked[0].detail["reason"]
    assert blocked[0].detail["debtor_id"] == "D-90"

    # ...and the budget rolls over into the next window
    later = NOW + dt.timedelta(days=8)
    assert ledger.process_event("outreach_sent", "INV-913", {"stage": "gentle"}, later) is not None


def test_a_different_debtor_has_a_separate_touch_budget():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-921", debtor_id="D-91"))
    ledger.register_invoice(make_invoice(id="INV-922", debtor_id="D-91"))
    ledger.register_invoice(make_invoice(id="INV-923", debtor_id="D-92"))
    for entity_id in ("INV-921", "INV-922", "INV-923"):
        ledger.process_event("invoice_triaged", entity_id, {}, NOW)
        ledger.process_event("outreach_sent", entity_id, {"stage": "gentle"}, NOW)

    assert len(ledger.touches_by_debtor["D-91"]) == 2
    assert len(ledger.touches_by_debtor["D-92"]) == 1
    assert ledger.entities["INV-923"].state == "ENGAGED"


def test_a_mandate_offer_spends_the_same_debtor_budget_as_a_message():
    """A touch is a touch whatever instrument rides on it — otherwise the cap
    could be dodged by switching from a message to a link."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-931", debtor_id="D-93"))
    ledger.register_invoice(make_invoice(id="INV-932", debtor_id="D-93"))

    ledger.process_event("invoice_triaged", "INV-931", {}, NOW)
    ledger.process_event("outreach_sent", "INV-931", {"stage": "gentle"}, NOW)   # touch 1
    ledger.process_event("extraction_received", "INV-931", {"amount_inr": 40000}, NOW)
    offer = ledger.process_event("mandate_offer_requested", "INV-931", {}, NOW)  # touch 2
    assert offer is not None and offer.kind == "mandate_offer"
    assert len(ledger.touches_by_debtor["D-93"]) == 2

    ledger.process_event("invoice_triaged", "INV-932", {}, NOW)
    assert ledger.process_event("outreach_sent", "INV-932", {"stage": "gentle"}, NOW) is None


def test_tier0_reserve_recovery_spends_no_debtor_touch_budget():
    """Master doc §8.6: the whole claim is 'recovered, 0 touches'. It must stay
    true in the debtor-scoped counter too, or the Tier-0 beat would quietly
    consume the human's weekly budget."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="CART-01", debtor_id="C-01"))
    ledger.register_reserve("CART-01", True)
    ledger.process_event("payment_failed", "CART-01", {}, NOW)

    assert ledger.entities["CART-01"].state == "KEPT"
    assert ledger.touches_by_debtor.get("C-01", []) == []


def test_broken_promise_hurts_trust_and_escalates():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-905", debtor_id="D-78"))
    ledger.process_event("extraction_received", "INV-905", {"amount_inr": 40000}, NOW)
    before = ledger._trust_for("D-78", NOW)
    ledger.process_event("promise_broken", "INV-905", {}, NOW)
    after = ledger.trust["D-78"]
    assert after.beta == before.beta + 1
    assert ledger.entities["INV-905"].state == "ESCALATE_1"


# ---------------------------------------------------------------------------
# Packet P9 — confidence gates + the human side of the loop (master doc §2.3)
#
# The API-level half of these lives in tests/test_review_queue.py. These are
# the judgment-layer units: the gates belong to the LEDGER, so they have to
# hold whatever pushed the event in.
# ---------------------------------------------------------------------------


def _promised(ledger: Ledger, entity_id: str, confidence: float | None, amount: int = 40000) -> None:
    payload: dict = {"amount_inr": amount}
    if confidence is not None:
        payload["confidence"] = confidence
    ledger.process_event("invoice_triaged", entity_id, {}, NOW)
    ledger.process_event("extraction_received", entity_id, payload, NOW)


def test_a_money_action_under_the_confidence_gate_is_held_not_emitted():
    """Master doc §2.3: "any extraction that would trigger a MONEY action at
    conf < 0.9 -> held for human approve-click"."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-940", debtor_id="D-94"))
    _promised(ledger, "INV-940", confidence=0.82)

    action = ledger.process_event("mandate_offer_requested", "INV-940", {}, NOW)
    assert action is None, "the money action must not be emitted"

    assert len(ledger.held_actions) == 1
    held = ledger.held_actions[0]
    assert held.action.kind == "mandate_offer"
    assert held.action.params["amount_inr"] == 40000  # law 2: the LEDGER's amount, held or not
    assert held.action.bounds_checked is False
    assert held.reason == "confidence 0.82 < 0.90 money gate"
    assert held.status == "pending" and held.sendable is True

    # nothing was sent, so nothing was spent
    assert ledger.entities["INV-940"].touches == []
    assert ledger.touches_by_debtor.get("D-94", []) == []

    # ...and the hold reached the trail BEFORE the queue held it (law 3)
    entry = next(a for a in ledger.audit if a.summary == "action held for human approval")
    assert entry.detail["held_id"] == held.id
    assert "action_id" not in entry.detail, "a held action must never look bounds-checked in the trail"


def test_a_money_action_at_or_above_the_gate_is_emitted_unsupervised():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-941", debtor_id="D-94A"))
    _promised(ledger, "INV-941", confidence=MONEY_ACTION_CONFIDENCE_GATE)
    action = ledger.process_event("mandate_offer_requested", "INV-941", {}, NOW)
    assert action is not None and action.kind == "mandate_offer"
    assert ledger.held_actions == []


def test_an_extraction_that_states_no_confidence_is_not_gated():
    """Deliberate, and documented in ledger.py's docstring: the gate compares a
    number, and "we were told nothing" is not evidence of low confidence. Every
    producer inside the system supplies one; this is the manual-injection case.
    Pinned as a test so it stays a decision rather than becoming a surprise."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-942", debtor_id="D-94B"))
    _promised(ledger, "INV-942", confidence=None)
    assert ledger.process_event("mandate_offer_requested", "INV-942", {}, NOW).kind == "mandate_offer"
    assert ledger.held_actions == []


def test_a_stale_confidence_never_carries_over_to_a_later_extraction():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-943", debtor_id="D-94C"))
    _promised(ledger, "INV-943", confidence=0.80)
    assert ledger.extraction_confidence["INV-943"] == 0.80
    ledger.process_event("extraction_received", "INV-943", {"amount_inr": 40000}, NOW)
    assert "INV-943" not in ledger.extraction_confidence


def test_the_money_gate_also_covers_the_link_the_mandate_falls_back_to():
    """At MANDATED both candidates move money — the offer, and the link the
    ladder falls back to when a bound refuses the offer. A low-confidence read
    must not be able to reach the wire through the fallback door."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-944", debtor_id="D-94D", amount_inr=150_000))
    _promised(ledger, "INV-944", confidence=0.80, amount=150_000)
    assert ledger.process_event("mandate_offer_requested", "INV-944", {}, NOW) is None

    held = ledger.held_actions[-1]
    assert held.action.kind == "link", "the mandate is over the cap, so the LINK is what was held"
    assert "mandate_amount_cap" in next(
        a.detail["reason"] for a in ledger.audit if a.summary == "action blocked: mandate_offer"
    )


def test_sub_075_gets_exactly_one_clarifying_question_then_the_queue():
    """Master doc §2.3, first two bullets. The agent asks ONCE; the second
    ambiguous read is a human's problem, not a second question."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-945", debtor_id="D-95"))
    ledger.process_event("invoice_triaged", "INV-945", {}, NOW)

    first = ledger.process_event(
        "extraction_received", "INV-945", {"amount_inr": 40000, "confidence": 0.62}, NOW
    )
    assert first is not None
    assert (first.kind, first.params["stage"]) == ("message", "clarify")
    assert first.reason == "confidence 0.62 < 0.75 clarify gate"
    assert ledger.clarify_count["INV-945"] == 1
    assert ledger.held_actions == []

    later = NOW + dt.timedelta(days=8)  # fresh touch window, so ONLY the clarify cap can stop it
    second = ledger.process_event(
        "extraction_received", "INV-945", {"amount_inr": 40000, "confidence": 0.58}, later
    )
    assert second is None, "the agent must never ask a second clarifying question by itself"
    assert ledger.clarify_count["INV-945"] == 1

    held = ledger.held_actions[-1]
    assert held.action.params["stage"] == "clarify"
    assert held.reason.startswith("still ambiguous after clarification")


def test_a_clarifying_question_spends_the_debtors_touch_budget():
    """Clarify is not a loophole around bound #4 — an agent that could ask
    "just to confirm?" for free would have found one."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-946", debtor_id="D-95A"))
    ledger.register_invoice(make_invoice(id="INV-947", debtor_id="D-95A"))
    for entity_id in ("INV-946", "INV-947"):
        ledger.process_event("invoice_triaged", entity_id, {}, NOW)

    ledger.process_event("outreach_sent", "INV-946", {"stage": "gentle"}, NOW)   # touch 1
    clarify = ledger.process_event(
        "extraction_received", "INV-947", {"amount_inr": 40000, "confidence": 0.60}, NOW
    )                                                                            # touch 2
    assert clarify is not None and clarify.params["stage"] == "clarify"
    assert len(ledger.touches_by_debtor["D-95A"]) == 2
    assert ledger.process_event("outreach_sent", "INV-946", {"stage": "gentle"}, NOW) is None


def test_a_clarifying_question_blocked_by_the_cap_is_still_only_asked_once():
    """The cap is spent, so no question goes out — and the agent does not get
    to retry it later as its "one" question, because the attempt was made."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-948", debtor_id="D-95B"))
    ledger.register_invoice(make_invoice(id="INV-949", debtor_id="D-95B"))
    for entity_id in ("INV-948", "INV-949"):
        ledger.process_event("invoice_triaged", entity_id, {}, NOW)
        ledger.process_event("outreach_sent", entity_id, {"stage": "gentle"}, NOW)

    assert ledger.process_event(
        "extraction_received", "INV-948", {"amount_inr": 40000, "confidence": 0.60}, NOW
    ) is None
    assert ledger.clarify_count["INV-948"] == 1
    blocked = [a for a in ledger.audit if a.summary == "action blocked: message"]
    assert "max_touches_per_week" in blocked[-1].detail["reason"]


# -- the approve / reject / handled clicks ----------------------------------


def test_approval_re_runs_check_bounds_at_click_time_not_at_hold_time():
    """The stale-hold test. INV-950 and INV-951 share debtor D-95C, and the cap
    is per debtor: the budget is spent on the sibling invoice AFTER the hold is
    created, and the human's click is refused because of it."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-950", debtor_id="D-95C"))
    ledger.register_invoice(make_invoice(id="INV-951", debtor_id="D-95C"))
    _promised(ledger, "INV-950", confidence=0.82)
    ledger.process_event("mandate_offer_requested", "INV-950", {}, NOW)
    held = ledger.pending_held_actions()[0]
    assert ledger.touches_by_debtor.get("D-95C", []) == []

    ledger.process_event("invoice_triaged", "INV-951", {}, NOW)
    ledger.process_event("outreach_sent", "INV-951", {"stage": "gentle"}, NOW)
    ledger.process_event("outreach_sent", "INV-951", {"stage": "gentle"}, NOW + dt.timedelta(days=1))
    assert len(ledger.touches_by_debtor["D-95C"]) == 2

    outcome = ledger.approve_held(held.id, NOW + dt.timedelta(days=2))
    assert outcome["blocked"] is True and outcome["action"] is None
    assert "max_touches_per_week" in outcome["block_reason"]
    assert held.status == "blocked"

    summaries = [a.summary for a in ledger.audit]
    assert "human approved held action" in summaries          # audited BEFORE it acted
    assert "human-approved action blocked at click time" in summaries
    assert len(ledger.touches_by_debtor["D-95C"]) == 2, "a blocked approval spends nothing"


def test_an_approved_hold_is_emitted_bounds_checked_and_touch_counted():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-952", debtor_id="D-95D"))
    _promised(ledger, "INV-952", confidence=0.82)
    ledger.process_event("mandate_offer_requested", "INV-952", {}, NOW)
    held = ledger.pending_held_actions()[0]

    outcome = ledger.approve_held(held.id, NOW)
    action = outcome["action"]
    assert outcome["blocked"] is False
    assert action.kind == "mandate_offer" and action.bounds_checked is True
    assert action.params["amount_inr"] == 40000
    assert held.status == "approved" and held.emitted_action_id == action.id
    assert ledger.touches_by_debtor["D-95D"] == [NOW]
    assert [a for a in ledger.audit if a.detail.get("action_id") == action.id]


def test_approving_a_hold_on_an_entity_that_has_since_terminated_is_refused():
    """The other stale-hold shape, and the common one in a real run: the ladder
    moved on and handed the entity to a human while the hold sat in the queue."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-953", debtor_id="D-95E"))
    _promised(ledger, "INV-953", confidence=0.82)
    ledger.process_event("mandate_offer_requested", "INV-953", {}, NOW)
    held = ledger.pending_held_actions()[0]

    ledger.process_event("dispute_raised", "INV-953", {}, NOW + dt.timedelta(days=1))
    outcome = ledger.approve_held(held.id, NOW + dt.timedelta(days=2))
    assert outcome["blocked"] is True
    assert "terminal state DISPUTED" in outcome["block_reason"]


def test_rejecting_a_mandate_hold_falls_back_to_the_link_path():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-954", debtor_id="D-95F"))
    _promised(ledger, "INV-954", confidence=0.82)
    ledger.process_event("mandate_offer_requested", "INV-954", {}, NOW)
    held = ledger.pending_held_actions()[0]

    outcome = ledger.reject_held(held.id, NOW)
    fallback = outcome["action"]
    assert held.status == "rejected"
    assert fallback is not None and fallback.kind == "link" and fallback.bounds_checked is True
    assert ledger.touches_by_debtor["D-95F"] == [NOW]
    assert "human rejected held action" in [a.summary for a in ledger.audit]
    assert ledger.pending_held_actions() == [], "the fallback is not re-held"


def test_the_formal_notice_draft_enters_the_queue_with_no_way_to_send_it():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-955", debtor_id="D-95G"))
    entity = ledger._entity("INV-955")
    entity.state = "ESCALATE_2"
    entity.escalate_stage = 2
    ledger.entities["INV-955"] = entity

    action = ledger.process_event("promise_broken", "INV-955", {}, NOW)
    assert ledger.entities["INV-955"].state == "ESCALATE_3"
    assert action.kind == "human_handoff", "the blocked legal stage still routes to a human"

    draft = ledger.pending_held_actions()[0]
    assert draft.sendable is False and draft.label == "formal_notice_draft"
    assert draft.action.params["stage"] == "legal"
    assert "never sends legal communication" in draft.reason

    with pytest.raises(ReviewQueueError) as approve_exc:
        ledger.approve_held(draft.id, NOW)
    assert approve_exc.value.status_code == 403
    with pytest.raises(ReviewQueueError):
        ledger.reject_held(draft.id, NOW)

    handled = ledger.mark_held_handled(draft.id, NOW, note="couriered")
    assert handled.status == "handled" and handled.resolution_note == "couriered"
    assert "human marked held item handled outside the system" in [a.summary for a in ledger.audit]
    assert not [a for a in ledger.audit if a.summary.startswith("message: escalation stage ESCALATE_3")]


def test_a_queue_item_is_decided_exactly_once():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-956", debtor_id="D-95H"))
    _promised(ledger, "INV-956", confidence=0.82)
    ledger.process_event("mandate_offer_requested", "INV-956", {}, NOW)
    held = ledger.pending_held_actions()[0]

    ledger.approve_held(held.id, NOW)
    for verb in (ledger.approve_held, ledger.reject_held, ledger.mark_held_handled):
        with pytest.raises(ReviewQueueError) as exc:
            verb(held.id, NOW)
        assert exc.value.status_code == 409
    with pytest.raises(ReviewQueueError) as missing:
        ledger.approve_held("H-9999", NOW)
    assert missing.value.status_code == 404


# -- human_resolution + the kill-switch -------------------------------------


def test_human_resolution_closes_a_handoff_both_ways():
    for resolution, expected, promise_status in (
        ("recovered", "KEPT", "kept"), ("written_off", "CLEAN_LOSS", "broken"),
    ):
        ledger = Ledger()
        entity_id = f"INV-96-{resolution}"
        debtor_id = f"D-96-{resolution}"
        ledger.register_invoice(make_invoice(id=entity_id, debtor_id=debtor_id))
        ledger.process_event("extraction_received", entity_id, {"amount_inr": 40000}, NOW)
        ledger.process_event("escalation_exhausted", entity_id, {}, NOW)
        assert ledger.entities[entity_id].state == "HUMAN_HANDOFF"
        trust_before = ledger.trust[debtor_id].model_copy()

        entity = ledger.resolve_handoff(entity_id, resolution, NOW + dt.timedelta(days=1))
        assert entity.state == expected

        # audited before it moved anything
        entry = next(a for a in ledger.audit if a.summary == f"human resolution: {resolution}")
        assert entry.detail["from_state"] == "HUMAN_HANDOFF"
        # no promise is left hanging (law 5 on the promise ledger too)
        assert {p.status for p in ledger.promises.values()} == {promise_status}
        # ...and trust does NOT move: an admin click is not debtor behaviour
        after = ledger.trust[debtor_id]
        assert (after.alpha, after.beta) == (trust_before.alpha, trust_before.beta)


def test_human_resolution_refuses_anything_that_is_not_an_open_handoff():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-962", debtor_id="D-96B"))
    ledger.process_event("invoice_triaged", "INV-962", {}, NOW)

    with pytest.raises(ReviewQueueError) as live:
        ledger.resolve_handoff("INV-962", "recovered", NOW)
    assert live.value.status_code == 409

    with pytest.raises(ReviewQueueError) as unknown:
        ledger.resolve_handoff("NOPE", "recovered", NOW)
    assert unknown.value.status_code == 404

    ledger.process_event("promise_kept", "INV-962", {}, NOW)
    with pytest.raises(ReviewQueueError):
        ledger.resolve_handoff("INV-962", "written_off", NOW)  # KEPT stays immutable

    with pytest.raises(ReviewQueueError) as bad:
        ledger.resolve_handoff("INV-962", "partially", NOW)
    assert bad.value.status_code == 422


def test_a_paused_thread_blocks_every_outbound_action_and_says_why():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-970", debtor_id="D-97"))
    ledger.process_event("invoice_triaged", "INV-970", {}, NOW)
    ledger.set_paused("INV-970", True, NOW)

    assert ledger.process_event("outreach_sent", "INV-970", {"stage": "gentle"}, NOW) is None
    _promised(ledger, "INV-970", confidence=0.95)  # high confidence: only the pause can stop it
    assert ledger.process_event("mandate_offer_requested", "INV-970", {}, NOW) is None

    blocks = [a for a in ledger.audit if a.summary.startswith("action blocked")]
    assert blocks and all(
        b.detail["reason"] == "thread paused by merchant (kill-switch)" for b in blocks
    )
    assert ledger.touches_by_debtor.get("D-97", []) == []
    assert ledger.paused_entities() == ["INV-970"]

    # ...and unpausing hands the thread straight back to the ladder, at the
    # rung it had reached (MANDATED -> a refusal now falls to the link path).
    ledger.set_paused("INV-970", False, NOW)
    resumed = ledger.process_event("mandate_refused", "INV-970", {}, NOW)
    assert resumed is not None and resumed.kind == "link"


def test_pausing_outranks_even_the_tier0_reserve_debit():
    """The kill-switch means "move no money on this thread" — and a reserve
    capture is money moving, however few touches it costs."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="CART-99", debtor_id="C-99"))
    ledger.register_reserve("CART-99", True)
    ledger.set_paused("CART-99", True, NOW)

    assert ledger.process_event("payment_failed", "CART-99", {}, NOW) is None
    assert ledger.entities["CART-99"].state != "KEPT"
    assert "action blocked: mandate_execute" in [a.summary for a in ledger.audit]


def test_pause_refuses_an_entity_the_ledger_has_never_seen():
    ledger = Ledger()
    with pytest.raises(ReviewQueueError) as exc:
        ledger.set_paused("INV-NOPE", True, NOW)
    assert exc.value.status_code == 404


def test_the_gate_constants_are_the_master_docs_numbers():
    assert (MONEY_ACTION_CONFIDENCE_GATE, CLARIFY_CONFIDENCE_GATE) == (0.90, 0.75)
