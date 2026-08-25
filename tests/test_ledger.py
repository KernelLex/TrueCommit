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


def test_broken_promise_hurts_trust_and_escalates():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-905", debtor_id="D-78"))
    ledger.process_event("extraction_received", "INV-905", {"amount_inr": 40000}, NOW)
    before = ledger._trust_for("D-78", NOW)
    ledger.process_event("promise_broken", "INV-905", {}, NOW)
    after = ledger.trust["D-78"]
    assert after.beta == before.beta + 1
    assert ledger.entities["INV-905"].state == "ESCALATE_1"
