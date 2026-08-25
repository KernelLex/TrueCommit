import datetime as dt

from engine.judgment.ledger import Ledger
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
