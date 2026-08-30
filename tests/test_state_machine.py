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
"""THE PIPELINE'S EVENT VOCABULARY — deliberately excludes `human_resolution`.

Packet P9 added one event that can move a terminal state (a human closing out a
handoff they were handed). It is excluded from this pool, and from the 1000-walk
termination fuzz below, because this pool models *event streams the pipeline can
actually produce*: `human_resolution` is not one of them. Nothing in `engine/`
emits it (`test_integration.py::test_the_runner_never_emits_a_human_resolution_event`)
and `POST /events` refuses it
(`test_review_queue.py::test_human_resolution_cannot_be_injected_through_the_general_event_route`),
so a walk containing it would be testing a stream that cannot exist.

Excluding it is the STRICTER choice, not the convenient one. Including it would
have weakened the two immutability tests below into "terminal states are immune
to everything except the event we just added"; excluding it keeps them at
"terminal states are immune to every event the pipeline can produce", and the
exception is pinned separately, from both ends, by
`test_human_resolution_is_the_one_event_that_moves_a_terminal_state` and by the
two tests named above that prove it has exactly one door.
"""


def entity(**overrides) -> sm.EntityState:
    base = sm.EntityState(entity_id="INV-TEST")
    return base.model_copy(update=overrides)


# ---------------------------------------------------------------------------
# 1. Each of the 8 hard bounds — try to violate it, assert it's blocked
# ---------------------------------------------------------------------------


def test_bound_max_touches_per_week():
    """No debtor window supplied -> the entity is its own debtor."""
    e = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=3)])
    result = sm.check_bounds(e, "message", {"stage": "firm"}, NOW)
    assert not result.allowed
    assert "max_touches_per_week" in result.reason


def test_bound_max_touches_per_week_resets_outside_window():
    e = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=10), NOW - dt.timedelta(days=9)])
    result = sm.check_bounds(e, "message", {"stage": "firm"}, NOW)
    assert result.allowed  # both touches are outside the 7-day window


def test_bound_max_touches_per_week_is_scoped_to_the_DEBTOR_not_the_invoice():
    """CLAUDE.md law 4 / master doc §3.4 word the cap "per debtor/customer".
    This invoice has been contacted ZERO times; two of its debtor's OTHER
    invoices were contacted this week. The law says the human is out of budget,
    so a third message must be blocked even though this entity is untouched."""
    untouched_invoice = entity(entity_id="INV-003", state="ESCALATE_1", touches=[])
    debtor_touches = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=3)]  # INV-001, INV-002

    per_invoice_only = sm.check_bounds(untouched_invoice, "message", {"stage": "firm"}, NOW)
    assert per_invoice_only.allowed, "sanity: nothing about THIS invoice blocks it"

    result = sm.check_bounds(untouched_invoice, "message", {"stage": "firm"}, NOW, debtor_touches)
    assert not result.allowed
    assert "max_touches_per_week" in result.reason
    assert "debtor" in result.reason


@pytest.mark.parametrize("kind", sorted(sm.TouchKind.__args__))
def test_every_touch_kind_is_capped_per_debtor(kind: str):
    """A mandate offer and a payment link are outbound contacts too — the cap
    can't be dodged by switching instrument."""
    e = entity(state="ESCALATE_1", invoice_amount_inr=40000)
    debtor_touches = [NOW - dt.timedelta(days=2), NOW - dt.timedelta(hours=6)]
    result = sm.check_bounds(e, kind, {"amount_inr": 40000, "stage": "firm"}, NOW, debtor_touches)
    assert not result.allowed
    assert "max_touches_per_week" in result.reason


def test_debtor_touch_window_also_rolls_off_after_seven_days():
    """The cap is a rolling window, not a lifetime budget — a debtor contacted
    twice last week is contactable again this week."""
    e = entity(state="ESCALATE_1", touches=[])
    stale = [NOW - dt.timedelta(days=8), NOW - dt.timedelta(days=7, minutes=1)]
    assert sm.check_bounds(e, "message", {"stage": "firm"}, NOW, stale).allowed

    one_fresh = [*stale, NOW - dt.timedelta(days=1)]
    assert sm.check_bounds(e, "message", {"stage": "firm"}, NOW, one_fresh).allowed
    two_fresh = [*one_fresh, NOW - dt.timedelta(days=2)]
    assert not sm.check_bounds(e, "message", {"stage": "firm"}, NOW, two_fresh).allowed


def test_check_bounds_stays_a_pure_predicate():
    """It reads no hidden state and mutates nothing — the reason it can be the
    single gate every action passes through."""
    before = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=1)], invoice_amount_inr=40000)
    snapshot = before.model_dump()
    debtor_touches = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=2)]
    debtor_snapshot = list(debtor_touches)

    sm.check_bounds(before, "message", {"stage": "firm"}, NOW, debtor_touches)
    sm.check_bounds(before, "mandate_offer", {"amount_inr": 40000}, NOW, debtor_touches)

    assert before.model_dump() == snapshot
    assert debtor_touches == debtor_snapshot


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


# ---------------------------------------------------------------------------
# 4. Terminal-state immutability, and its ONE exception (packet P9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_state", sorted(sm.TERMINAL_STATES))
@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_every_terminal_state_is_immune_to_every_pipeline_event(terminal_state, event_type):
    """The general law, stated the strong way: 4 terminal states x the whole
    pipeline event vocabulary, none of which moves anything. `human_resolution`
    is not in that vocabulary — see ALL_EVENT_TYPES' docstring."""
    e = entity(state=terminal_state, escalate_stage=4, retry_count=1, invoice_amount_inr=40000)
    result = sm.transition(e, event_type, {"amount_inr": 40000, "resolution": "recovered"}, NOW)
    assert result.state == terminal_state


def test_the_fuzz_pool_deliberately_excludes_the_one_exception():
    assert sm.HUMAN_RESOLUTION_EVENT not in ALL_EVENT_TYPES
    assert sm.HUMAN_RESOLVABLE_STATES < sm.TERMINAL_STATES


@pytest.mark.parametrize("start_state", sorted(sm.HUMAN_RESOLVABLE_STATES))
@pytest.mark.parametrize(
    "resolution,expected", [("recovered", "KEPT"), ("written_off", "CLEAN_LOSS")]
)
def test_human_resolution_is_the_one_event_that_moves_a_terminal_state(
    start_state, resolution, expected
):
    """A human closing out the case they were handed. This exists precisely
    because a HUMAN is acting: without it, HUMAN_HANDOFF is a state the system
    can never close, which fails CLAUDE.md law 5's "no silent deaths" as badly
    as a loop would."""
    e = entity(state=start_state)
    result = sm.transition(e, sm.HUMAN_RESOLUTION_EVENT, {"resolution": resolution}, NOW)
    assert result.state == expected


@pytest.mark.parametrize("already_closed", ["KEPT", "CLEAN_LOSS"])
def test_human_resolution_cannot_reopen_an_already_closed_outcome(already_closed):
    """The exception is scoped to the two states that are *waiting on a human*.
    KEPT and CLEAN_LOSS are already resolved, and stay immutable to it."""
    for resolution in ("recovered", "written_off"):
        e = entity(state=already_closed)
        assert sm.transition(e, sm.HUMAN_RESOLUTION_EVENT, {"resolution": resolution}, NOW).state == already_closed


@pytest.mark.parametrize("start_state", NON_TERMINAL_STATES)
def test_human_resolution_does_nothing_from_a_live_state(start_state):
    """It closes handoffs; it is not a "set this entity's state" primitive that
    could short-circuit a live ladder."""
    e = entity(state=start_state)
    result = sm.transition(e, sm.HUMAN_RESOLUTION_EVENT, {"resolution": "recovered"}, NOW)
    assert result.state == start_state


def test_a_malformed_human_resolution_moves_nothing():
    for payload in ({}, {"resolution": None}, {"resolution": "partially"}, {"resolution": "KEPT"}):
        e = entity(state="HUMAN_HANDOFF")
        assert sm.transition(e, sm.HUMAN_RESOLUTION_EVENT, payload, NOW).state == "HUMAN_HANDOFF"


def test_a_human_resolved_entity_is_terminal_again_immediately():
    """One click, one move. The reopened-then-closed entity is not left in a
    state that a second `human_resolution` could keep flipping."""
    e = sm.transition(entity(state="HUMAN_HANDOFF"), sm.HUMAN_RESOLUTION_EVENT, {"resolution": "recovered"}, NOW)
    assert e.state == "KEPT"
    e = sm.transition(e, sm.HUMAN_RESOLUTION_EVENT, {"resolution": "written_off"}, NOW)
    assert e.state == "KEPT"
    for kind in sm.OUTBOUND_KINDS:
        assert not sm.check_bounds(e, kind, {"amount_inr": 40000}, NOW).allowed


# ---------------------------------------------------------------------------
# 5. check_bounds_detailed() is a LENS on check_bounds(), never a second gate
#    (packet P10 — the guardrail introspection the Day Story screen renders)
# ---------------------------------------------------------------------------

ALL_ACTION_KINDS = sorted(sm.OUTBOUND_KINDS | {"evidence_packet", "human_handoff"})

FIRST_FAILURE_REASON_KEYWORD = {
    "terminal_state_stops_outbound": "terminal state",
    "no_mandate_reoffer_after_refusal": "NEVER",
    "renegotiation_cap": "renegotiation_cap",
    "mandate_amount_cap": "mandate_amount_cap",
    "mandate_amount_matches_ledger": "equal ledger invoice amount",
    "retry_on_execution_failure": "retry_on_execution_failure",
    "legal_stage_goes_to_merchant": "merchant",
    "max_touches_per_week": "max_touches_per_week",
}


def _random_bounds_input(rng: random.Random) -> tuple[sm.EntityState, str, dict, list | None]:
    e = sm.EntityState(
        entity_id=f"INV-FUZZ-{rng.randint(0, 999)}",
        state=rng.choice(list(sm.State.__args__)),
        escalate_stage=rng.randint(0, 5),
        renegotiation_count=rng.randint(0, sm.RENEGOTIATION_CAP + 2),
        retry_count=rng.randint(0, sm.RETRY_ON_EXECUTION_FAILURE + 2),
        mandate_refused=rng.random() < 0.3,
        touches=[NOW - dt.timedelta(days=rng.randint(0, 14)) for _ in range(rng.randint(0, 4))],
        invoice_amount_inr=rng.choice([None, 40_000, 100_000, 100_001, 150_000]),
    )
    kind = rng.choice(ALL_ACTION_KINDS)
    params: dict = {}
    if rng.random() < 0.85:
        params["amount_inr"] = rng.choice([None, 40_000, 99_999, 100_000, 100_001, 150_000])
    if rng.random() < 0.7:
        params["stage"] = rng.choice(["gentle", "firm", "formal", "legal", "clarify", None])
    debtor_touches = (
        None if rng.random() < 0.4
        else [NOW - dt.timedelta(days=rng.randint(0, 14)) for _ in range(rng.randint(0, 5))]
    )
    return e, kind, params, debtor_touches


def test_check_bounds_detailed_can_never_disagree_with_check_bounds():
    """THE PROOF that the dashboard's "Guardrails checked" panel can never show
    something the real decision didn't reach.

    `check_bounds()` short-circuits on the first refusal and is the only gate;
    `check_bounds_detailed()` runs every applicable check so a human can see
    the working. If those two could ever disagree, the UI would be showing a
    decision the system never made — CLAUDE.md law 8, applied to a screen.

    Two properties over 5000 random (entity, action_kind, params, window)
    combinations:
      1. allowed == all checks passed,
      2. when refused, the FIRST failing check is the bound check_bounds()
         actually names in its reason (so the checklist's order is the gate's
         order, not a re-ranking).
    """
    rng = random.Random(20260826)
    seen_kinds: set[str] = set()
    seen_first_failures: set[str] = set()
    refused = 0

    for _ in range(5000):
        e, kind, params, debtor_touches = _random_bounds_input(rng)
        verdict = sm.check_bounds(e, kind, params, NOW, debtor_touches)
        checks = sm.check_bounds_detailed(e, kind, params, NOW, debtor_touches)
        seen_kinds.add(kind)

        assert verdict.allowed == all(c.passed for c in checks), (
            f"{kind} {params} against {e.state}: check_bounds said allowed="
            f"{verdict.allowed} ({verdict.reason}) but the checklist says "
            f"{[(c.name, c.passed) for c in checks]}"
        )

        if not verdict.allowed:
            refused += 1
            first_failure = next(c for c in checks if not c.passed)
            seen_first_failures.add(first_failure.name)
            keyword = FIRST_FAILURE_REASON_KEYWORD[first_failure.name]
            assert keyword in verdict.reason, (
                f"first failing check {first_failure.name} does not match the "
                f"reason check_bounds gave: {verdict.reason}"
            )

        assert all(c.detail for c in checks), "every check must carry its numbers"

    # the sample really did exercise the whole surface, not one lucky corner
    assert seen_kinds == set(ALL_ACTION_KINDS)
    assert seen_first_failures == set(FIRST_FAILURE_REASON_KEYWORD)
    assert refused > 1000


def test_check_bounds_detailed_is_a_pure_predicate_too():
    before = entity(state="ESCALATE_1", touches=[NOW - dt.timedelta(days=1)], invoice_amount_inr=40000)
    snapshot = before.model_dump()
    debtor_touches = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=2)]
    debtor_snapshot = list(debtor_touches)
    params = {"amount_inr": 40000}
    params_snapshot = dict(params)

    sm.check_bounds_detailed(before, "mandate_offer", params, NOW, debtor_touches)

    assert before.model_dump() == snapshot
    assert debtor_touches == debtor_snapshot
    assert params == params_snapshot


def test_check_bounds_detailed_reports_every_applicable_bound_with_real_numbers():
    """The judge-facing shape: a mandate offer is measured against six bounds,
    and each line carries the figures it compared — not a pass/fail pill."""
    e = entity(state="MANDATED", invoice_amount_inr=40000, touches=[NOW - dt.timedelta(days=1)])
    checks = sm.check_bounds_detailed(e, "mandate_offer", {"amount_inr": 40000}, NOW)

    assert [c.name for c in checks] == [
        "terminal_state_stops_outbound",
        "no_mandate_reoffer_after_refusal",
        "renegotiation_cap",
        "mandate_amount_cap",
        "mandate_amount_matches_ledger",
        "legal_stage_goes_to_merchant",
        "max_touches_per_week",
    ]
    assert all(c.passed for c in checks)
    detail = {c.name: c.detail for c in checks}
    assert "Rs.40,000" in detail["mandate_amount_cap"]
    # same `{:,}` grouping check_bounds()'s own reason strings use, so the
    # checklist and the refusal reason read as one voice
    assert f"Rs.{sm.MANDATE_AMOUNT_CAP:,}" in detail["mandate_amount_cap"]
    assert "Rs.40,000 == Rs.40,000" in detail["mandate_amount_matches_ledger"]
    assert "1/2" in detail["max_touches_per_week"]


def test_a_check_that_did_not_run_is_absent_rather_than_reported_as_passed():
    """No amount in params -> the two amount bounds were not evaluated, so they
    do not appear. Reporting them as "passed" would be a claim the gate never
    made."""
    e = entity(state="ESCALATE_1")
    names = {c.name for c in sm.check_bounds_detailed(e, "message", {"stage": "firm"}, NOW)}
    assert "mandate_amount_cap" not in names
    assert "mandate_amount_matches_ledger" not in names
    assert names == {
        "terminal_state_stops_outbound",
        "legal_stage_goes_to_merchant",
        "max_touches_per_week",
    }


def test_check_bounds_itself_still_short_circuits_on_the_first_refusal():
    """The gate did not become the checklist: it still returns ONE reason, the
    first bound that refused, exactly as before."""
    e = entity(state="DISPUTED", mandate_refused=True, renegotiation_count=9, invoice_amount_inr=40000)
    result = sm.check_bounds(e, "mandate_offer", {"amount_inr": 999_999}, NOW)
    assert isinstance(result, sm.BoundsResult)
    assert not result.allowed
    assert "terminal state" in result.reason  # the FIRST one, not a list of all four


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


# ---------------------------------------------------------------------------
# 9. Promise-horizon cap (red-team packet, 2026-08-30) — the promise-farmer
#    mitigation. A claimed due day/date arbitrarily far in the future must be
#    truncated to `MAX_PROMISE_HORIZON_DAYS`, never rejected outright: the
#    debtor's own words still reach the audit trail verbatim, only the
#    SCHEDULING refuses to treat "never" as a valid promise date.
# ---------------------------------------------------------------------------


def test_cap_promise_due_day_passes_through_a_near_term_claim():
    assert sm.cap_promise_due_day(10, now_day=3) == 10


def test_cap_promise_due_day_truncates_a_far_future_claim():
    assert sm.cap_promise_due_day(10_000, now_day=3) == 3 + sm.MAX_PROMISE_HORIZON_DAYS


def test_cap_promise_due_day_is_inclusive_at_the_exact_ceiling():
    ceiling = 3 + sm.MAX_PROMISE_HORIZON_DAYS
    assert sm.cap_promise_due_day(ceiling, now_day=3) == ceiling


def test_cap_promise_due_day_never_pulls_a_near_term_claim_closer():
    """The cap is a ceiling, not a target — a promise due tomorrow must stay
    due tomorrow, not get pushed out to the ceiling."""
    assert sm.cap_promise_due_day(4, now_day=3) == 4


def test_cap_promise_due_date_passes_through_a_near_term_claim():
    now = dt.date(2026, 8, 30)
    claimed = now + dt.timedelta(days=10)
    assert sm.cap_promise_due_date(claimed, now) == claimed


def test_cap_promise_due_date_truncates_a_far_future_claim():
    now = dt.date(2026, 8, 30)
    claimed = now + dt.timedelta(days=10_000)
    assert sm.cap_promise_due_date(claimed, now) == now + dt.timedelta(days=sm.MAX_PROMISE_HORIZON_DAYS)


def test_cap_promise_due_date_never_pulls_a_near_term_claim_closer():
    now = dt.date(2026, 8, 30)
    claimed = now + dt.timedelta(days=1)
    assert sm.cap_promise_due_date(claimed, now) == claimed
