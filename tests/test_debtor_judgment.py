"""Debtor-level judgment (2026-08-30) — two mechanisms, tested directly:

1. Dispute freeze: "the system can dispute-freeze one invoice while still
   chasing four others from the same person. That is wrong." A dispute on
   ANY of a debtor's entities now blocks every outbound action on every
   OTHER entity of theirs until it resolves.
2. Touch-budget allocation: given more open invoices than a debtor's
   remaining weekly touch cap, `engine/judgment/allocation.py` picks a
   deterministic attempt ORDER by trust and invoice age (with a rotation
   term — see that module's own docstring for the starvation bug it fixes)
   rather than leaving the ledger's per-attempt bound to pick whichever
   entity happened to be iterated first.

Both mechanisms are ALSO exercised by the real 45-day run (see
tests/test_integration.py and tracking/BUILD_LOG.md 2026-08-30 for the full,
honest recovery-delta accounting) — these tests pin the mechanisms
themselves, deterministically, independent of that run's own stochastic
outcome.
"""

import datetime as dt

from engine.judgment import allocation
from engine.judgment.ledger import Ledger
from engine.judgment.state_machine import TERMINAL_STATES
from engine.schemas import Invoice, TrustState

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-901", debtor_id="D-DJ", amount_inr=40000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


# ---------------------------------------------------------------------------
# 1. Debtor-level dispute freeze
# ---------------------------------------------------------------------------


def _two_sibling_ledger() -> Ledger:
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-A", debtor_id="D-DJ", amount_inr=40000))
    ledger.register_invoice(make_invoice(id="INV-B", debtor_id="D-DJ", amount_inr=25000))
    return ledger


def test_a_dispute_on_one_invoice_freezes_a_sibling_outbound_action():
    ledger = _two_sibling_ledger()
    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    assert ledger.entities["INV-A"].state == "DISPUTED"

    # INV-B is untouched and would ordinarily sail through a gentle nudge —
    # the debtor-level freeze must refuse it anyway.
    blocked = ledger._gate(ledger.entities["INV-B"], "message", {"stage": "gentle"}, NOW)
    assert blocked.allowed is False
    assert "debtor-level dispute freeze" in blocked.reason
    assert "INV-A" in blocked.reason


def test_the_disputed_invoice_itself_is_unaffected_by_its_own_freeze():
    """The freeze blocks OTHER entities — DISPUTED is already a terminal
    state, so `check_bounds()`'s own terminal-state bound is what actually
    stops the disputed entity's own further outbound actions, same as ever."""
    ledger = _two_sibling_ledger()
    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    result = ledger._gate(ledger.entities["INV-A"], "message", {"stage": "gentle"}, NOW)
    assert result.allowed is False
    assert "terminal state" in result.reason  # not the debtor-freeze reason


def test_resolving_the_dispute_lifts_the_freeze():
    ledger = _two_sibling_ledger()
    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    assert ledger._gate(ledger.entities["INV-B"], "message", {"stage": "gentle"}, NOW).allowed is False

    ledger.process_event("human_resolution", "INV-A", {"resolution": "written_off"}, NOW)
    assert ledger.entities["INV-A"].state == "CLEAN_LOSS"
    assert ledger.disputed_entities_by_debtor.get("D-DJ") is None

    allowed = ledger._gate(ledger.entities["INV-B"], "message", {"stage": "gentle"}, NOW)
    assert allowed.allowed is True


def test_a_second_independent_dispute_keeps_the_ladder_frozen_after_the_first_resolves():
    """A debtor with TWO separately disputed invoices must stay frozen until
    BOTH resolve — closing one must not silently unfreeze the other's
    siblings."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(id="INV-A", debtor_id="D-DJ", amount_inr=40000))
    ledger.register_invoice(make_invoice(id="INV-B", debtor_id="D-DJ", amount_inr=25000))
    ledger.register_invoice(make_invoice(id="INV-C", debtor_id="D-DJ", amount_inr=15000))

    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    ledger.process_event("dispute_raised", "INV-B", {}, NOW)
    assert ledger.disputed_entities_by_debtor["D-DJ"] == {"INV-A", "INV-B"}

    ledger.process_event("human_resolution", "INV-A", {"resolution": "recovered"}, NOW)
    assert ledger.disputed_entities_by_debtor["D-DJ"] == {"INV-B"}
    still_frozen = ledger._gate(ledger.entities["INV-C"], "message", {"stage": "gentle"}, NOW)
    assert still_frozen.allowed is False

    ledger.process_event("human_resolution", "INV-B", {"resolution": "written_off"}, NOW)
    assert "D-DJ" not in ledger.disputed_entities_by_debtor
    now_allowed = ledger._gate(ledger.entities["INV-C"], "message", {"stage": "gentle"}, NOW)
    assert now_allowed.allowed is True


def test_the_freeze_blocks_every_outbound_kind_not_just_messages():
    ledger = _two_sibling_ledger()
    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    for kind, params in [
        ("mandate_offer", {"amount_inr": 25000}),
        ("link", {"amount_inr": 25000}),
        ("voice", {"stage": "firm"}),
        ("mandate_execute", {"amount_inr": 25000}),
    ]:
        result = ledger._gate(ledger.entities["INV-B"], kind, params, NOW)
        assert result.allowed is False, f"{kind} should be blocked by the debtor-level freeze"
        assert "debtor-level dispute freeze" in result.reason


def test_the_freeze_never_blocks_a_non_outbound_kind():
    """evidence_packet/human_handoff are resolution artifacts, not outreach —
    a debtor-level freeze on siblings has no reason to touch them, and the
    disputed entity's OWN evidence_packet already bypasses `_gate()` entirely
    (dispatched via `_emit_action` directly)."""
    ledger = _two_sibling_ledger()
    ledger.process_event("dispute_raised", "INV-A", {}, NOW)
    result = ledger._gate(ledger.entities["INV-B"], "evidence_packet", {}, NOW)
    assert result.allowed is True


# ---------------------------------------------------------------------------
# 2. Debtor-level mandate-refusal posture (the other half of "negotiation
#    posture lifted to the debtor")
# ---------------------------------------------------------------------------


def test_a_debit_failure_bar_on_one_invoice_blocks_a_mandate_offer_on_a_sibling():
    ledger = _two_sibling_ledger()
    ledger.process_event("extraction_received", "INV-A", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-A", {}, NOW)
    ledger.process_event("mandate_execute_failed", "INV-A", {"amount_inr": 40000, "reason": "mandate_revoked"}, NOW)
    assert ledger.entities["INV-A"].mandate_refused is True
    assert ledger.debtor_mandate_refused["D-DJ"] is True

    # INV-B never itself refused anything — the debtor-level fact bars it anyway
    blocked = ledger._gate(ledger.entities["INV-B"], "mandate_offer", {"amount_inr": 25000}, NOW)
    assert blocked.allowed is False
    assert "debtor-level" in blocked.reason


def test_debtor_level_refusal_does_not_block_a_siblings_plain_link():
    """Negotiation posture about MANDATES specifically — a link is a
    different instrument and stays available. Checked a week later, on
    purpose: INV-A's own mandate_offer + its account_closed_frozen fallback
    link already spent this debtor's weekly touch cap at NOW — an unrelated,
    ordinary bound this test is not the one to re-prove."""
    ledger = _two_sibling_ledger()
    ledger.process_event("extraction_received", "INV-A", {"amount_inr": 40000, "invoice_amount_inr": 40000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-A", {}, NOW)
    ledger.process_event("mandate_execute_failed", "INV-A", {"amount_inr": 40000, "reason": "account_closed_frozen"}, NOW)

    later = NOW + dt.timedelta(days=8)
    allowed = ledger._gate(ledger.entities["INV-B"], "link", {"amount_inr": 25000}, later)
    assert allowed.allowed is True


# ---------------------------------------------------------------------------
# 3. check_bounds() backward compatibility (new optional parameter)
# ---------------------------------------------------------------------------


def test_check_bounds_defaults_debtor_mandate_refused_to_false():
    from engine.judgment import state_machine as sm

    entity = sm.EntityState(entity_id="INV-X", state="MANDATED", invoice_amount_inr=40000)
    # No 5th/6th argument at all — every caller written before this feature
    # must be completely unaffected.
    result = sm.check_bounds(entity, "mandate_offer", {"amount_inr": 40000}, NOW)
    assert result.allowed is True


def test_check_bounds_detailed_agrees_with_check_bounds_on_debtor_mandate_refused():
    """The same invariant `test_state_machine.py` already proves for every
    other bound, spot-checked for the new parameter specifically."""
    from engine.judgment import state_machine as sm

    entity = sm.EntityState(entity_id="INV-X", state="MANDATED", invoice_amount_inr=40000)
    params = {"amount_inr": 40000}
    for flag in (False, True):
        result = sm.check_bounds(entity, "mandate_offer", params, NOW, debtor_mandate_refused=flag)
        checks = sm.check_bounds_detailed(entity, "mandate_offer", params, NOW, debtor_mandate_refused=flag)
        assert result.allowed == all(c.passed for c in checks)


# ---------------------------------------------------------------------------
# 4. Touch-budget allocation (engine/judgment/allocation.py) — pure functions
# ---------------------------------------------------------------------------


def _trust(alpha: float, beta: float) -> TrustState:
    return TrustState(debtor_id="D-X", alpha=alpha, beta=beta, last_update=NOW)


def test_higher_age_wins_at_equal_trust_and_touches():
    prior = _trust(2.0, 2.0)
    ranked = allocation.rank_by_priority(
        ["INV-OLD", "INV-NEW"],
        age_days_by_entity={"INV-OLD": 50, "INV-NEW": 5},
        debtor_trust=prior,
    )
    assert ranked == ["INV-OLD", "INV-NEW"]


def test_rotation_prevents_permanent_exclusion_of_the_younger_invoice():
    """The exact bug found and fixed 2026-08-30 (see allocation.py's module
    docstring): without the rotation term, the older invoice would win
    EVERY round forever. With it, after enough rounds the younger one's
    unpenalized score overtakes."""
    prior = _trust(2.0, 2.0)
    age_days = {"INV-OLD": 50, "INV-NEW": 5}

    touches = {"INV-OLD": 0, "INV-NEW": 0}
    winners = []
    for _ in range(10):
        ranked = allocation.rank_by_priority(
            ["INV-NEW", "INV-OLD"], age_days, prior, touches_so_far_by_entity=touches,
        )
        winner = ranked[0]
        winners.append(winner)
        touches[winner] += 1

    assert "INV-NEW" in winners, "the younger invoice must win at least once across 10 rounds"
    assert winners[0] == "INV-OLD", "the genuinely older invoice still wins the FIRST round"


def test_rank_by_priority_never_drops_or_duplicates_an_entity():
    prior = _trust(3.0, 5.0)
    entity_ids = ["INV-A", "INV-B", "INV-C", "INV-D"]
    ranked = allocation.rank_by_priority(
        entity_ids, {"INV-A": 10, "INV-B": 40, "INV-C": 0, "INV-D": 60}, prior,
    )
    assert sorted(ranked) == sorted(entity_ids)
    assert len(ranked) == len(set(ranked)) == len(entity_ids)


def test_rank_by_priority_is_deterministic():
    prior = _trust(2.0, 2.0)
    args = (["INV-A", "INV-B", "INV-C"], {"INV-A": 10, "INV-B": 10, "INV-C": 10}, prior)
    first = allocation.rank_by_priority(*args)
    second = allocation.rank_by_priority(*args)
    assert first == second, "identical inputs must produce an identical order (CLAUDE.md law 6)"


def test_tied_scores_break_by_input_order_not_arbitrarily():
    prior = _trust(2.0, 2.0)
    # identical age -> identical score -> the stable sort must preserve input order
    ranked = allocation.rank_by_priority(["INV-A", "INV-B"], {"INV-A": 10, "INV-B": 10}, prior)
    assert ranked == ["INV-A", "INV-B"]
    ranked_reversed_input = allocation.rank_by_priority(["INV-B", "INV-A"], {"INV-A": 10, "INV-B": 10}, prior)
    assert ranked_reversed_input == ["INV-B", "INV-A"]


def test_allocate_touch_budget_is_a_truncated_slice_of_the_full_ranking():
    prior = _trust(2.0, 2.0)
    age_days = {"INV-A": 50, "INV-B": 20, "INV-C": 5}
    full = allocation.rank_by_priority(["INV-A", "INV-B", "INV-C"], age_days, prior)
    top2 = allocation.allocate_touch_budget(["INV-A", "INV-B", "INV-C"], age_days, prior, budget=2)
    assert top2 == full[:2]


def test_allocate_touch_budget_returns_everyone_when_budget_covers_them_all():
    prior = _trust(2.0, 2.0)
    entity_ids = ["INV-A", "INV-B"]
    result = allocation.allocate_touch_budget(entity_ids, {"INV-A": 1, "INV-B": 1}, prior, budget=5)
    assert result == entity_ids


def test_score_is_monotonic_in_trust_and_age_and_anti_monotonic_in_touches():
    low_trust = allocation.score_invoice_for_touch(0.2, 10)
    high_trust = allocation.score_invoice_for_touch(0.8, 10)
    assert high_trust > low_trust

    young = allocation.score_invoice_for_touch(0.5, 5)
    old = allocation.score_invoice_for_touch(0.5, 55)
    assert old > young

    fresh = allocation.score_invoice_for_touch(0.5, 30, touches_so_far=0)
    touched = allocation.score_invoice_for_touch(0.5, 30, touches_so_far=2)
    assert fresh > touched


# ---------------------------------------------------------------------------
# 5. WorldRunner integration — the allocator actually changes attempt order
# ---------------------------------------------------------------------------


def test_the_real_run_attempts_a_debtors_invoices_in_priority_order_not_alphabetical():
    """D-02 (INV-006/007/063/064/065) is the real instance this reasoning
    was built against (tracking/BUILD_LOG.md 2026-08-30): INV-064 (due
    2026-07-02, by far the oldest) is attempted before the alphabetically-
    earlier INV-006/007 on day 0's beat."""
    from engine.integration.runner import WorldRunner

    world = WorldRunner(real_razorpay=False, real_tts=False)
    world.advance(1)
    day0_attempts = [
        a.entity_id for a in world.ledger.audit
        if a.ts.date() == world._ts(0).date()
        and a.entity_id in ("INV-006", "INV-007", "INV-061", "INV-062", "INV-063", "INV-064", "INV-065")
        and a.summary.startswith("outreach_sent:")
    ]
    assert day0_attempts, "no D-02/D-01 outreach at all on day 0 — nothing to check order against"
    assert day0_attempts.index("INV-064") < day0_attempts.index("INV-006"), (
        "the oldest invoice must be attempted before the alphabetically-earlier one"
    )


def test_every_cart_and_invoice_still_reaches_a_terminal_state_in_45_days():
    """CLAUDE.md law 5, re-checked after debtor-level judgment: dispute-
    frozen invoices must still terminate via the idle sweep, not sit open
    forever just because their debtor is frozen."""
    from engine.integration.runner import WorldRunner

    world = WorldRunner(real_razorpay=False, real_tts=False)
    world.advance(45)
    unresolved = {
        eid: e.state for eid, e in world.ledger.entities.items()
        if e.state not in TERMINAL_STATES
    }
    assert unresolved == {}
