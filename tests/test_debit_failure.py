"""Debit-failure taxonomy (2026-08-30, master doc's recovery-hierarchy
reasoning extended to WHY a mandate execution bounced — see
engine/schemas.py's `DebitFailureReason`).

CRITICAL MODELING POINT under test throughout: a failed debit is NOT
automatically a broken promise. Before this feature, `mandate_execute_failed`
applied a full trust penalty uniformly on an exhausted retry, regardless of
reason — a real bug, since insufficient_funds/bank_downtime/
account_closed_frozen/amount_exceeds_limit are none of them a willingness
signal. Only `mandate_revoked` is. These tests pin that distinction from
three angles: the state_machine transition (pure), the ledger's trust delta
(process_event), and the dispatched action's amount/copy.
"""

import datetime as dt

import pytest

from engine.judgment import state_machine as sm
from engine.judgment import trust
from engine.judgment.ledger import Ledger
from engine.schemas import Invoice, TrustState

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def entity(**overrides) -> sm.EntityState:
    base = sm.EntityState(entity_id="INV-TEST", state="MANDATED", invoice_amount_inr=40000)
    return base.model_copy(update=overrides)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-950", debtor_id="D-DEBIT", amount_inr=40000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


def _to_mandated(ledger: Ledger, entity_id: str, amount: int = 40000) -> None:
    """Drives a freshly-registered invoice to MANDATED via the ordinary
    extraction -> offer path, exactly like a real L1+ promise would."""
    ledger.process_event("extraction_received", entity_id, {"amount_inr": amount, "invoice_amount_inr": amount}, NOW)
    action = ledger.process_event("mandate_offer_requested", entity_id, {}, NOW)
    assert action is not None and action.kind == "mandate_offer"


# ---------------------------------------------------------------------------
# 1. state_machine.transition() — pure routing per reason
# ---------------------------------------------------------------------------


def test_bank_downtime_changes_nothing_not_even_retry_count():
    e = entity(state="MANDATED", retry_count=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": "bank_downtime"}, NOW)
    assert result.state == "MANDATED"
    assert result.retry_count == 0
    assert result.mandate_refused is False
    assert result.escalate_stage == 0


def test_bank_downtime_does_not_disturb_an_existing_at_risk_retry():
    """A SECOND failure, this time infra-side, while already mid-retry must
    not burn the one allowed retry or move the entity off AT_RISK."""
    e = entity(state="AT_RISK", retry_count=1)
    result = sm.transition(e, "mandate_execute_failed", {"reason": "bank_downtime"}, NOW)
    assert result.state == "AT_RISK"
    assert result.retry_count == 1


def test_account_closed_frozen_goes_straight_to_linked_no_retry():
    e = entity(state="MANDATED", retry_count=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": "account_closed_frozen"}, NOW)
    assert result.state == "LINKED"
    assert result.mandate_refused is True
    assert result.escalate_stage == 0, "an infra fact, not an escalation"


def test_account_closed_frozen_bypasses_at_risk_even_with_retries_remaining():
    """"Do not retry" means never, not just once the budget is spent."""
    e = entity(state="MANDATED", retry_count=0)  # full retry budget still available
    result = sm.transition(e, "mandate_execute_failed", {"reason": "account_closed_frozen"}, NOW)
    assert result.state == "LINKED"
    assert result.retry_count == 0, "no retry attempt was ever made — the rail is dead"


def test_mandate_revoked_skips_at_risk_and_escalates_immediately():
    e = entity(state="MANDATED", retry_count=0, escalate_stage=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": "mandate_revoked"}, NOW)
    assert result.state == "ESCALATE_1"
    assert result.escalate_stage == 1
    assert result.mandate_refused is True
    assert result.retry_count == 0, "never entered AT_RISK — there is nothing to retry"


@pytest.mark.parametrize("reason", ["insufficient_funds", "amount_exceeds_limit"])
def test_timing_reasons_get_the_one_allowed_retry_first(reason: str):
    e = entity(state="MANDATED", retry_count=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": reason}, NOW)
    assert result.state == "AT_RISK"
    assert result.retry_count == 1
    assert result.mandate_refused is False


@pytest.mark.parametrize("reason", ["insufficient_funds", "amount_exceeds_limit"])
def test_timing_reasons_fall_back_to_a_link_once_the_retry_is_exhausted(reason: str):
    """Master doc §3.5's jump-back matrix, verbatim: "...same-day polite
    retry x1 -> payment link -> ladder resumes at current stage." Not an
    escalation — `escalate_stage` stays exactly where it was."""
    e = entity(state="AT_RISK", retry_count=sm.RETRY_ON_EXECUTION_FAILURE, escalate_stage=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": reason}, NOW)
    assert result.state == "LINKED"
    assert result.escalate_stage == 0, "a fallback link, not an escalation step"
    assert result.mandate_refused is False, "still not a willingness signal, even exhausted"


def test_no_reason_preserves_the_original_undifferentiated_behavior():
    """Backward compatibility: any caller (manual event injection, an old
    test) that fires this event without a `reason` payload must behave
    exactly as before this feature existed."""
    e = entity(state="MANDATED", retry_count=0)
    result = sm.transition(e, "mandate_execute_failed", {}, NOW)
    assert result.state == "AT_RISK"
    assert result.retry_count == 1

    exhausted = entity(state="AT_RISK", retry_count=sm.RETRY_ON_EXECUTION_FAILURE)
    result2 = sm.transition(exhausted, "mandate_execute_failed", {}, NOW)
    assert result2.state == "LINKED"


def test_unrecognized_reason_falls_back_to_the_same_default_as_no_reason():
    e = entity(state="MANDATED", retry_count=0)
    result = sm.transition(e, "mandate_execute_failed", {"reason": "not_a_real_code"}, NOW)
    assert result.state == "AT_RISK"
    assert result.retry_count == 1


# ---------------------------------------------------------------------------
# 2. Ledger.process_event() — trust delta per reason (the bug fix itself)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["insufficient_funds", "bank_downtime", "account_closed_frozen", "amount_exceeds_limit"])
def test_non_willingness_reasons_are_trust_pending_neutral_even_on_exhausted_retry(reason: str):
    """THE BUG THIS PACKET FIXES: before, ANY exhausted-retry
    mandate_execute_failed applied a full trust.update_broken() regardless of
    reason. None of these four reasons is a willingness signal, so beta must
    not move at all (decay-only), exactly like `update_refusal`."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")
    before = ledger._trust_for("D-DEBIT", NOW)

    # first failure
    ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": reason}, NOW)
    mid = ledger.trust["D-DEBIT"]
    assert mid.beta == pytest.approx(before.beta), f"{reason}: beta moved on the FIRST failure"

    if reason != "bank_downtime" and ledger.entities["INV-950"].state == "AT_RISK":
        # second, exhausted-retry failure for the timing reasons
        ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": reason}, NOW)
        after = ledger.trust["D-DEBIT"]
        assert after.beta == pytest.approx(before.beta), f"{reason}: beta moved on the EXHAUSTED retry"
        assert after.alpha == pytest.approx(before.alpha)


def test_mandate_revoked_is_the_one_reason_that_moves_trust():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")
    before = ledger._trust_for("D-DEBIT", NOW)

    ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": "mandate_revoked"}, NOW)
    after = ledger.trust["D-DEBIT"]
    assert after.beta == pytest.approx(before.beta + 1.0)
    assert after.alpha == pytest.approx(before.alpha)


def test_no_reason_preserves_the_original_trust_behavior_exactly():
    """The pre-existing behavior (state != AT_RISK -> broken, else neutral)
    for any caller that doesn't supply a reason."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")
    before = ledger._trust_for("D-DEBIT", NOW)

    ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000}, NOW)
    mid = ledger.trust["D-DEBIT"]
    assert mid.beta == pytest.approx(before.beta), "first failure (-> AT_RISK) stays neutral, as before"

    ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000}, NOW)
    after = ledger.trust["D-DEBIT"]
    assert after.beta == pytest.approx(before.beta + 1.0), "exhausted retry, no reason -> the original broken hit"


# ---------------------------------------------------------------------------
# 3. Dispatched action per reason — distinct, audited, and law-2-safe
# ---------------------------------------------------------------------------


def test_account_closed_frozen_dispatches_a_full_amount_link_and_bars_future_mandates():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")

    action = ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": "account_closed_frozen"}, NOW)
    assert action is not None and action.kind == "link"
    assert action.params["amount_inr"] == 40000
    assert "account_closed_frozen" in action.reason
    assert ledger.entities["INV-950"].mandate_refused is True

    # switching instrument permanently: even a fresh promise, well outside the
    # touch-cap window, can never re-offer a MANDATE again (it may still be
    # bound-blocked as a link by the cap — that's a different bound, not what
    # this test is pinning)
    later = NOW + dt.timedelta(days=8)
    ledger.process_event("extraction_received", "INV-950", {"amount_inr": 40000, "invoice_amount_inr": 40000}, later)
    retry_offer = ledger.process_event("mandate_offer_requested", "INV-950", {}, later)
    assert retry_offer is not None
    assert retry_offer.kind == "link", "no mandate re-offer after the rail died"


def test_mandate_revoked_escalates_instead_of_dispatching_a_link():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")

    action = ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": "mandate_revoked"}, NOW)
    assert action is not None
    assert action.kind == "message"  # ESCALATE_1's action, not a link
    assert ledger.entities["INV-950"].state == "ESCALATE_1"


@pytest.mark.parametrize("reason", ["insufficient_funds", "amount_exceeds_limit"])
def test_timing_reason_exhausted_retry_shrinks_the_fallback_link_amount(reason: str):
    """The mandate itself never shrinks (law 2) — only the fallback LINK
    that follows an exhausted timing-reason retry."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")

    first = ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": reason}, NOW)
    assert first is not None and first.kind == "mandate_execute"
    assert first.params["amount_inr"] == 40000, "the retry attempt itself stays at the full ledger amount"

    second = ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": reason}, NOW)
    assert second is not None and second.kind == "link"
    assert second.params["amount_inr"] < 40000, "the fallback link is a trust-derived SHRUNK tranche"
    assert second.params["instrument"] == "shrunk_tranche"
    assert second.params["original_amount_inr"] == 40000
    assert reason in second.reason


def test_bank_downtime_dispatches_no_action_at_all():
    """Not a real attempt — no state change, so `process_event` correctly
    returns None (the generic "nothing changed" no-op path)."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")

    action = ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": "bank_downtime"}, NOW)
    assert action is None
    assert ledger.entities["INV-950"].state == "MANDATED"


def test_every_debit_failure_writes_an_audit_entry_before_any_action():
    """Law 3: every action writes to the audit log BEFORE it executes — and
    even the reason itself (bank_downtime, which emits no Action at all)
    still gets a judgment-layer audit entry recording what happened."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    _to_mandated(ledger, "INV-950")
    before_len = len(ledger.audit)

    ledger.process_event("mandate_execute_failed", "INV-950", {"amount_inr": 40000, "reason": "bank_downtime"}, NOW)
    new_entries = ledger.audit[before_len:]
    assert any(e.layer == "judgment" and "mandate_execute_failed" in e.detail.get("event", "") for e in new_entries)


# ---------------------------------------------------------------------------
# 4. trust.py — the trust-derived retry delay / shrunk tranche, pure functions
# ---------------------------------------------------------------------------


def test_retry_delay_and_shrink_at_the_prior():
    prior = trust.new_trust("D-X", NOW)
    assert trust.mean(prior) == pytest.approx(0.5)
    assert trust.derive_retry_delay_days(prior) == 4
    assert trust.derive_shrunk_tranche_inr(prior, 40000) == 30000


def test_higher_trust_means_shorter_delay_and_a_bigger_ask():
    high = TrustState(debtor_id="D-X", alpha=20.0, beta=2.0, last_update=NOW)
    assert trust.derive_retry_delay_days(high) == 2
    assert trust.derive_shrunk_tranche_inr(high, 40000) == 38182


def test_lower_trust_means_longer_delay_and_a_smaller_ask():
    low = TrustState(debtor_id="D-X", alpha=2.0, beta=20.0, last_update=NOW)
    assert trust.derive_retry_delay_days(low) == 5
    assert trust.derive_shrunk_tranche_inr(low, 40000) == 21818


def test_shrunk_tranche_never_exceeds_the_original_or_drops_to_zero():
    floor_trust = TrustState(debtor_id="D-X", alpha=1.0, beta=1000.0, last_update=NOW)
    shrunk = trust.derive_shrunk_tranche_inr(floor_trust, 100)
    assert 1 <= shrunk <= 100
    ceiling_trust = TrustState(debtor_id="D-X", alpha=1000.0, beta=1.0, last_update=NOW)
    assert trust.derive_shrunk_tranche_inr(ceiling_trust, 100) == 100


# ---------------------------------------------------------------------------
# 5. WorldRunner integration — the real 45-day run genuinely exercises this
# ---------------------------------------------------------------------------


def test_the_real_45_day_run_genuinely_produces_debit_failures_with_reasons(monkeypatch):
    """As of 2026-08-30's debtor-level touch-budget allocation, this seeded
    run's 3 real Scene-1 mandate offers ALL get confirmed and execute
    cleanly (see tests/test_integration.py::
    test_the_only_refusals_left_are_ones_a_debtor_actually_made for the
    full honest accounting) — a genuinely measured fact, not a gap to paper
    over. That means the RUNNER's own persona-driven wiring
    (`WorldRunner._resolve_mandate_execution` -> `sim.personas.
    debit_failure_reason` -> `Ledger.process_event`) has no OTHER real-run
    coverage in this file (every other test above drives `Ledger` directly).
    Forcing exactly one execution to fail, deterministically, proves that
    integration still works end to end without waiting on this seed's own
    luck."""
    import engine.integration.runner as runner_mod
    from engine.integration.runner import WorldRunner

    world = WorldRunner(real_razorpay=False, real_tts=False)
    entity_id = world.active_invoice_ids[0]
    now = world.now()
    amount = world.invoices[entity_id].amount_inr
    world.ledger.process_event(
        "extraction_received", entity_id, {"amount_inr": amount, "confidence": 0.95, "level": "L1"}, now,
    )
    action = world.ledger.process_event("mandate_offer_requested", entity_id, {}, now)
    assert action is not None and action.kind == "mandate_offer"
    world.ledger.process_event("mandate_confirmed", entity_id, {"amount_inr": amount}, now)
    assert world.ledger.entities[entity_id].state == "MANDATED"

    monkeypatch.setattr(runner_mod, "mandate_executes", lambda rng, persona: False)
    monkeypatch.setattr(runner_mod, "debit_failure_reason", lambda rng, persona: "insufficient_funds")
    world._resolve_mandate_execution(world.day, entity_id)

    failures = [e for e in world.events if e.entity_id == entity_id and e.type == "mandate_execute_failed"]
    assert len(failures) == 1
    assert failures[0].payload["reason"] == "insufficient_funds"
    assert world.ledger.debit_failure_reason[entity_id] == "insufficient_funds"
    assert world.ledger.entities[entity_id].state == "AT_RISK"  # timing reason, first failure: the one allowed retry
