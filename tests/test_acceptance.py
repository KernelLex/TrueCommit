"""Mandate-acceptance learning (packet 4, 2026-08-31) — the second, portfolio-
level Beta posterior. See `engine/judgment/acceptance.py`'s module docstring
for why this is a different question from `trust.py`'s per-debtor posterior.
"""

import datetime as dt

from engine.judgment import acceptance
from engine.judgment.ledger import Ledger
from engine.schemas import Invoice

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-AL", debtor_id="D-AL", amount_inr=40000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


# ---------------------------------------------------------------------------
# 1. Pure Beta-posterior math, direct
# ---------------------------------------------------------------------------


def test_new_acceptance_is_the_prior():
    a = acceptance.new_acceptance(NOW)
    assert a.alpha == acceptance.PRIOR_ALPHA
    assert a.beta == acceptance.PRIOR_BETA
    assert a.debtor_id == acceptance.PORTFOLIO_ID
    assert acceptance.mean(a) == 0.5


def test_accepted_increases_alpha_by_one():
    a = acceptance.new_acceptance(NOW)
    a2 = acceptance.update_accepted(a, NOW)
    assert a2.alpha == acceptance.PRIOR_ALPHA + 1
    assert a2.beta == acceptance.PRIOR_BETA


def test_declined_increases_beta_by_one():
    a = acceptance.new_acceptance(NOW)
    a2 = acceptance.update_declined(a, NOW)
    assert a2.beta == acceptance.PRIOR_BETA + 1
    assert a2.alpha == acceptance.PRIOR_ALPHA


def test_no_decay_ever_applied_even_far_in_the_future():
    """Unlike trust.py's posterior, this one never decays — the whole point
    is a stable, cumulative read by the end of one bounded run."""
    a = acceptance.new_acceptance(NOW)
    a = acceptance.update_accepted(a, NOW)
    much_later = NOW + dt.timedelta(days=10_000)
    a2 = acceptance.update_declined(a, much_later)
    assert a2.alpha == acceptance.PRIOR_ALPHA + 1  # untouched by elapsed time
    assert a2.beta == acceptance.PRIOR_BETA + 1


def test_observations_derives_exact_counts_from_the_posterior():
    a = acceptance.new_acceptance(NOW)
    a = acceptance.update_accepted(a, NOW)
    a = acceptance.update_accepted(a, NOW)
    a = acceptance.update_declined(a, NOW)
    obs = acceptance.observations(a)
    assert obs == {"n_accepted": 2, "n_declined": 1, "n_total": 3}


def test_observations_is_zero_at_the_prior():
    assert acceptance.observations(acceptance.new_acceptance(NOW)) == {
        "n_accepted": 0, "n_declined": 0, "n_total": 0,
    }


# ---------------------------------------------------------------------------
# 2. Ledger wiring — event-type-keyed, not flag-keyed
# ---------------------------------------------------------------------------


def test_ledger_starts_at_the_prior_before_any_mandate_event():
    ledger = Ledger()
    state = ledger.current_mandate_acceptance(NOW)
    assert state.alpha == acceptance.PRIOR_ALPHA
    assert state.beta == acceptance.PRIOR_BETA


def test_mandate_confirmed_event_moves_alpha():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("extraction_received", "INV-AL", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-AL", {}, NOW)
    ledger.process_event("mandate_confirmed", "INV-AL", {"amount_inr": 40000}, NOW)
    state = ledger.current_mandate_acceptance(NOW)
    assert state.alpha == acceptance.PRIOR_ALPHA + 1
    assert state.beta == acceptance.PRIOR_BETA


def test_mandate_refused_event_moves_beta():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("extraction_received", "INV-AL", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-AL", {}, NOW)
    ledger.process_event("mandate_refused", "INV-AL", {"reason": "debtor declined auto-debit"}, NOW)
    state = ledger.current_mandate_acceptance(NOW)
    assert state.beta == acceptance.PRIOR_BETA + 1
    assert state.alpha == acceptance.PRIOR_ALPHA


def test_a_mandate_revoked_execution_failure_does_NOT_move_this_posterior():
    """The debtor already accepted the offer to reach execution at all — a
    revoke afterward is evidence about a DIFFERENT question (will an
    accepted mandate actually pay out), not about acceptance itself."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("extraction_received", "INV-AL", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-AL", {}, NOW)
    ledger.process_event("mandate_confirmed", "INV-AL", {"amount_inr": 40000}, NOW)
    before = ledger.current_mandate_acceptance(NOW)
    ledger.process_event("mandate_execute_failed", "INV-AL", {"amount_inr": 40000, "reason": "mandate_revoked"}, NOW)
    after = ledger.current_mandate_acceptance(NOW)
    assert after.alpha == before.alpha
    assert after.beta == before.beta


def test_this_posterior_is_independent_of_the_debtor_level_trust_posterior():
    """Different question, different table: confirming/refusing a mandate
    must not, by itself, move `Ledger.trust` (that stays governed by
    promise-kept/broken and the pending-neutral refusal rule)."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("extraction_received", "INV-AL", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-AL", {}, NOW)
    ledger.process_event("mandate_refused", "INV-AL", {"reason": "debtor declined auto-debit"}, NOW)
    debtor_trust = ledger.current_trust("D-AL", NOW)
    assert debtor_trust.alpha == 2.0 and debtor_trust.beta == 2.0  # pending-neutral, untouched

    acceptance_state = ledger.current_mandate_acceptance(NOW)
    assert acceptance_state.beta == acceptance.PRIOR_BETA + 1  # but THIS posterior did move
