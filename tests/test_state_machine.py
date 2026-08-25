"""BUILD.md Day 5 acceptance criteria:
- every bound has a test that tries to violate it and fails
- dispute event from ANY state -> DISPUTED, no further outbound actions
- 1000 random event sequences all terminate in KEPT / CLEAN_LOSS / HUMAN_HANDOFF
  (DISPUTED counts as the terminal bucket here too — see state_machine.py's
  module docstring on why it's tracked separately from HUMAN_HANDOFF but is
  a HUMAN_HANDOFF variant in every practical sense)
"""

import datetime as dt
import random

import pytest

from engine.judgment import state_machine as sm

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)

NON_TERMINAL_STATES = [s for s in sm.State.__args__ if s not in sm.TERMINAL_STATES]
ALL_EVENT_TYPES = [
    "invoice_triaged", "outreach_sent", "extraction_received", "mandate_offer_requested",
    "mandate_confirmed", "mandate_refused", "mandate_execute_success", "mandate_execute_failed",
    "promise_kept", "promise_broken", "dispute_raised", "delivery_rejected", "escalation_exhausted",
    "something_unrecognized",
]


def entity(**overrides) -> sm.EntityState:
    base = sm.EntityState(entity_id="INV-TEST")
    return base.model_copy(update=overrides)


# ---------------------------------------------------------------------------
# 1. Each of the 8 hard bounds — try to violate it, assert it's blocked
# ---------------------------------------------------------------------------


def test_bound_max_touches_per_week():
    e = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=3)])
    result = sm.check_bounds(e, "message", {"stage": "firm"}, NOW)
    assert not result.allowed
    assert "max_touches_per_week" in result.reason


def test_bound_max_touches_per_week_resets_outside_window():
    e = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=10), NOW - dt.timedelta(days=9)])
    result = sm.check_bounds(e, "message", {"stage": "firm"}, NOW)
    assert result.allowed  # both touches are outside the 7-day window


def test_bound_renegotiation_cap():
    e = entity(state="PROMISED", renegotiation_count=sm.RENEGOTIATION_CAP + 1, invoice_amount_inr=40000)
    result = sm.check_bounds(e, "mandate_offer", {"amount_inr": 40000}, NOW)
    assert not result.allowed
    assert "renegotiation_cap" in result.reason


def test_bound_mandate_amount_cap():
    e = entity(state="PROMISED", invoice_amount_inr=150_000)
    result = sm.check_bounds(e, "mandate_offer", {"amount_inr": 150_000}, NOW)
    assert not result.allowed
    assert "mandate_amount_cap" in result.reason


def test_bound_mandate_must_equal_ledger_amount():
    e = entity(state="PROMISED", invoice_amount_inr=40000)
    result = sm.check_bounds(e, "mandate_offer", {"amount_inr": 50000}, NOW)
    assert not result.allowed
    assert "equal ledger invoice amount" in result.reason


def test_bound_retry_on_execution_failure():
    e = entity(state="AT_RISK", retry_count=sm.RETRY_ON_EXECUTION_FAILURE + 1, invoice_amount_inr=40000)
    result = sm.check_bounds(e, "mandate_execute", {"amount_inr": 40000}, NOW)
    assert not result.allowed
    assert "retry_on_execution_failure" in result.reason


def test_bound_dispute_instant_stop_blocks_all_outbound():
    e = entity(state="DISPUTED")
    for kind in sm.OUTBOUND_KINDS:
        result = sm.check_bounds(e, kind, {"amount_inr": 1000}, NOW)
        assert not result.allowed, f"{kind} should be blocked once DISPUTED"
        assert "terminal state" in result.reason


def test_bound_legal_stage_never_auto_sent():
    e = entity(state="ESCALATE_3")
    result = sm.check_bounds(e, "message", {"stage": "legal"}, NOW)
    assert not result.allowed
    assert "merchant" in result.reason


def test_bound_no_mandate_reoffer_after_refusal():
    e = entity(state="LINKED", mandate_refused=True, invoice_amount_inr=40000)
    result = sm.check_bounds(e, "mandate_offer", {"amount_inr": 40000}, NOW)
    assert not result.allowed
    assert "NEVER" in result.reason


# ---------------------------------------------------------------------------
# 2. Dispute from ANY state -> DISPUTED, no further outbound actions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("start_state", NON_TERMINAL_STATES)
def test_dispute_from_any_state_reaches_disputed(start_state):
    e = entity(state=start_state)
    result = sm.transition(e, "dispute_raised", {}, NOW)
    assert result.state == "DISPUTED"


@pytest.mark.parametrize("start_state", NON_TERMINAL_STATES)
def test_dispute_then_no_further_outbound_actions_ever(start_state):
    e = sm.transition(entity(state=start_state), "dispute_raised", {}, NOW)
    assert e.state == "DISPUTED"
    # bombard it with more events afterward — it must never leave DISPUTED
    later = NOW
    for event_type in ALL_EVENT_TYPES:
        later += dt.timedelta(hours=1)
        e = sm.transition(e, event_type, {"amount_inr": 40000}, later)
        assert e.state == "DISPUTED"
    for kind in sm.OUTBOUND_KINDS:
        result = sm.check_bounds(e, kind, {"amount_inr": 40000}, later)
        assert not result.allowed


def test_dispute_is_one_way():
    e = entity(state="MANDATED")
    e = sm.transition(e, "dispute_raised", {}, NOW)
    assert e.state == "DISPUTED"
    e = sm.transition(e, "mandate_execute_success", {}, NOW + dt.timedelta(days=1))
    assert e.state == "DISPUTED"  # a "success" event must not resurrect it out of DISPUTED


# ---------------------------------------------------------------------------
# 3. 1000 random event sequences -> all terminate
# ---------------------------------------------------------------------------


def test_1000_random_sequences_all_terminate():
    rng = random.Random(42)
    trials = 1000
    steps_per_trial = sm.HARD_STEP_CAP + 10  # comfortably past the hard backstop

    non_terminal_counts = 0
    for trial in range(trials):
        e = sm.EntityState(entity_id=f"INV-FUZZ-{trial}")
        t = NOW
        for _ in range(steps_per_trial):
            event_type = rng.choice(ALL_EVENT_TYPES)
            payload = {"amount_inr": rng.choice([None, 40000, 999999])}
            t += dt.timedelta(hours=rng.randint(1, 48))
            e = sm.transition(e, event_type, payload, t)
        if e.state not in sm.TERMINAL_STATES:
            non_terminal_counts += 1

    assert non_terminal_counts == 0, f"{non_terminal_counts}/{trials} sequences failed to terminate"


def test_escalation_ladder_is_capped_and_forces_handoff():
    e = entity(state="ESCALATE_4")
    e = sm.transition(e, "promise_broken", {}, NOW)
    assert e.state == "HUMAN_HANDOFF"


def test_hard_step_cap_forces_termination_even_with_only_silent_events():
    """No failures, no dispute, no promise — just repeated no-op events. The
    step-count backstop must still force a terminal state (CLAUDE.md law #5:
    nothing loops forever, by construction, not by hoping inputs behave)."""
    e = sm.EntityState(entity_id="INV-SILENT")
    t = NOW
    for _ in range(sm.HARD_STEP_CAP + 5):
        t += dt.timedelta(days=1)
        e = sm.transition(e, "outreach_sent", {}, t)
    assert e.state in sm.TERMINAL_STATES
