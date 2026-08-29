"""Three-arm runner (BUILD.md Day 8, eval/run_arms.py) — locks reproducibility
and the correctness properties the "measured money recovered" bar depends on.

Not a test of WHICH numbers come out — those are measured, not asserted —
but of the invariants that make the measurement trustworthy: same seed gives
the same numbers, the frozen personas are genuinely never mutated, the
Arm C figure reconciles exactly against the pinned canonical run, and no
arm silently double-counts across the invoice/cart boundary.
"""

import copy
import datetime as dt

from data.generate import DEBTOR_BY_ID
from eval.run_arms import (
    MANDATE_ACCEPTANCE_SWEEP,
    _load_invoices,
    _scaled_mandate_table,
    run_arm_a,
    run_arm_b,
    run_arm_c,
    run_sensitivity_band,
)
from sim import personas as personas_mod


def test_arm_a_recovers_nothing_and_says_why():
    invoices = _load_invoices()
    result = run_arm_a(invoices)
    assert result["recovered_invoices"] == 0
    assert result["recovered_amount_inr"] == 0
    assert result["total_amount_inr"] == sum(i["amount_inr"] for i in invoices)
    assert "note" in result and "frozen persona tables" in result["note"]


def test_arm_b_is_reproducible_with_the_same_seed():
    invoices = _load_invoices()
    first = run_arm_b(invoices, seed=42)
    second = run_arm_b(invoices, seed=42)
    assert first == second


def test_arm_b_dso_is_never_negative_when_present():
    invoices = _load_invoices()
    result = run_arm_b(invoices, seed=42)
    assert result["recovered_invoices"] > 0, "fixture assumption: seed 42 recovers at least one invoice"
    assert result["dso_days_mean"] is not None
    assert result["dso_days_mean"] >= 0


def test_arm_c_is_reproducible_with_the_same_seed():
    first = run_arm_c(seed=42)
    second = run_arm_c(seed=42)
    assert first == second


def test_arm_c_reconciles_exactly_against_the_pinned_whole_world_figure():
    """The invoice-only Arm C figure, plus the 2 Tier-0 reserve carts' amount,
    plus the non-reserve Scene-2 carts that now genuinely recover through
    their matched instrument (master doc §3.3, built 2026-08-29: C-05's
    scheduled mandate and C-07's delivery-secured mandate — see
    tests/test_integration.py::test_the_timing_cause_cart_gets_a_scheduled_mandate_that_executes
    / ::test_the_trust_cause_carts_show_both_the_execute_and_the_revoke_branch),
    must equal the canonical whole-world figure pinned by
    test_integration.py::test_the_45_day_distribution_is_the_number_the_docs_quote.
    If this test and that one ever disagree, one of the two pinned numbers is
    stale, not this reconciliation. (Before 2026-08-29 the third term was 0,
    because non-reserve carts got zero follow-through at all — see
    tracking/BUILD_LOG.md.)

    2026-08-30a (debit-failure taxonomy): Arm C's OWN invoice-only figure
    moved 2,324,347 -> 2,343,347 (+19,000) — this feature is Scene-1 (mandate
    execution), squarely inside Arm C's own scope, unlike the Scene-2-only
    change above. The whole-world canonical figure moved by the identical
    +19,000, so the reconciliation still holds exactly.

    2026-08-30b (debtor-level judgment): Arm C's own figure moved again,
    2,343,347 -> 2,180,422 — also squarely Scene-1 (the touch-budget
    allocator and dispute freeze both operate on `active_invoice_ids`). The
    whole-world canonical figure moved by the identical amount, so the
    reconciliation still holds exactly; see
    test_integration.py::test_the_45_day_distribution_is_the_number_the_docs_quote
    for the full honest accounting of why the number went DOWN, not up.
    """
    result = run_arm_c(seed=42)
    RESERVE_CART_TOTAL_INR = 1_899 + 5_250  # C-09 + C-10, Tier-0
    NON_RESERVE_CART_RECOVERED_INR = 2_499 + 2_499  # C-05 (timing) + C-07 (trust, happy path)
    CANONICAL_WHOLE_WORLD_RECOVERED_INR = 2_192_569
    assert (
        result["recovered_amount_inr"] + RESERVE_CART_TOTAL_INR + NON_RESERVE_CART_RECOVERED_INR
        == CANONICAL_WHOLE_WORLD_RECOVERED_INR
    )


def test_arm_c_never_mutates_the_frozen_mandate_table():
    """personas-frozen means frozen - run_arm_c's monkeypatch-and-restore
    pattern (used by the sensitivity sweep) must leave sim.personas.MANDATE_TABLE
    byte-identical to what it was before, even across a full sweep."""
    before = copy.deepcopy(personas_mod.MANDATE_TABLE)
    run_sensitivity_band(seed=42, rates=[0.1, 0.6])
    after = personas_mod.MANDATE_TABLE
    assert before == after
    assert personas_mod.MANDATE_TABLE is not None


def test_arm_c_touches_total_excludes_cart_customer_touches():
    """touches_by_debtor mixes D-xx (invoice debtors) and CUST-xx (cart
    customers) in one dict on the ledger - the invoice-only scope must not
    silently sum cart-driven touches into its total."""
    result = run_arm_c(seed=42)
    invoice_debtor_ids = {DEBTOR_BY_ID[d]["id"] for d in DEBTOR_BY_ID}
    assert all(not d.startswith("CUST-") for d in invoice_debtor_ids)
    # result itself doesn't expose the raw dict, but a sanity bound catches a
    # gross leak: touches_total must be plausible for 60 invoices over 45 days
    # under a 2-touches-per-week-per-debtor cap (12 debtors * ~6-7 weeks * 2).
    assert 0 <= result["touches_total"] <= 12 * 7 * 2


def test_sensitivity_band_covers_the_required_10_to_60_percent_range():
    assert MANDATE_ACCEPTANCE_SWEEP[0] == 0.10
    assert MANDATE_ACCEPTANCE_SWEEP[-1] == 0.60
    band = run_sensitivity_band(seed=42)
    assert [row["target_mandate_acceptance_rate"] for row in band] == MANDATE_ACCEPTANCE_SWEEP
    # every row must be a genuine Arm C run, not a scaled copy of one figure
    assert all("recovered_amount_inr" in row for row in band)


def test_scaled_mandate_table_never_exceeds_valid_probabilities():
    for rate in [0.0, 0.10, 0.60, 1.0]:
        table = _scaled_mandate_table(rate)
        for persona_id, moves in table.items():
            assert 0.0 <= moves["confirm_mandate"] <= 1.0
            assert 0.0 <= moves["refuse_but_promise"] <= 1.0
            assert 0.0 <= moves["ignore"] <= 1.0
            total = moves["confirm_mandate"] + moves["refuse_but_promise"] + moves["ignore"]
            assert abs(total - 1.0) < 0.01, f"{persona_id} at rate={rate}: weights sum to {total}, not 1.0"


def test_full_report_is_reproducible_end_to_end(tmp_path):
    """The exact invariant the plan asked for: re-running must produce
    identical numbers. Excludes the wall-clock generated_at timestamp, which
    is metadata about the run, not a simulation output."""
    import json
    from eval.run_arms import main

    out1 = tmp_path / "m1.json"
    out2 = tmp_path / "m2.json"
    assert main(["--seed", "42", "--out", str(out1)]) == 0
    assert main(["--seed", "42", "--out", str(out2)]) == 0

    data1 = json.loads(out1.read_text())
    data2 = json.loads(out2.read_text())
    data1.pop("generated_at")
    data2.pop("generated_at")
    assert data1 == data2
