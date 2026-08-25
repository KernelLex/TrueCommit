"""Coverage assertions for the standalone simulator (packet P8).

Why this file exists: `sim/run.py`'s only acceptance criterion was DETERMINISM
(BUILD.md Day 1-2, "two seeded runs replay identically"). A run that silently
skipped its very first scheduled beat replayed byte-identically every time, so
the green determinism test could never notice — and for a while one did:
`VirtualClock.advance()` incremented the day BEFORE firing callbacks, so
nothing scheduled on the clock's own start day ever ran (BUILD_LOG 2026-08-26).

Determinism proves reproducibility, never coverage. These tests assert WHAT the
clock and the simulator fire, not merely that they fire it twice the same way.
"""

import sim.run as sim_run
from sim.clock import VirtualClock
from sim.run import TOUCH_SCHEDULE


def _recorder(seen: list[int]):
    def fn(day: int) -> list[dict]:
        seen.append(day)
        return [{"day": day}]
    return fn


# ---------------------------------------------------------------------------
# the clock itself
# ---------------------------------------------------------------------------


def test_advance_fires_the_day_the_clock_is_already_on():
    """The day-0 regression, pinned. A callback registered for the clock's
    start day must run on the first advance, not be skipped over."""
    seen: list[int] = []
    clock = VirtualClock(start_day=0)
    clock.schedule(0, _recorder(seen))

    fired = clock.advance(1)

    assert seen == [0], "day-0 work was dropped — advance() moved before it fired"
    assert fired == [{"day": 0}]
    assert clock.day == 1  # the first day NOT yet simulated


def test_advance_fires_every_day_in_the_span_exactly_once():
    seen: list[int] = []
    clock = VirtualClock(start_day=0)
    for day in range(6):
        clock.schedule(day, _recorder(seen))

    clock.advance(5)

    assert seen == [0, 1, 2, 3, 4]  # day 5 is not reached by a 5-day advance
    assert clock.day == 5


def test_advance_is_resumable_without_skipping_or_repeating_a_day():
    """Five single-day advances land exactly where one five-day advance does —
    the same guarantee the dashboard's "Advance 1 Day" button relies on."""
    stepwise: list[int] = []
    one_shot: list[int] = []
    for span, seen in ((1, stepwise), (5, one_shot)):
        clock = VirtualClock(start_day=0)
        for day in range(5):
            clock.schedule(day, _recorder(seen))
        for _ in range(5 // span):
            clock.advance(span)
        assert clock.day == 5
    assert stepwise == one_shot == [0, 1, 2, 3, 4]


def test_a_non_zero_start_day_fires_its_own_first_day_too():
    seen: list[int] = []
    clock = VirtualClock(start_day=10)
    clock.schedule(10, _recorder(seen))
    clock.schedule(9, _recorder(seen))  # already in the past, never fires

    clock.advance(2)

    assert seen == [10]
    assert clock.day == 12


def test_callbacks_on_the_same_day_fire_in_registration_order():
    """Determinism (CLAUDE.md law 6) rests on this: same seed, same order."""
    seen: list[str] = []
    clock = VirtualClock(start_day=0)
    for tag in ("a", "b", "c"):
        clock.schedule(0, lambda day, t=tag: seen.append(t) or [])
    clock.advance(1)
    assert seen == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# ...and the simulator that rides on it
# ---------------------------------------------------------------------------


def test_the_simulator_actually_runs_its_day_zero_touch_beat():
    """`TOUCH_SCHEDULE` opens with `(0, "gentle")`. Before the clock fix that
    beat had never run once — the simulator's first outreach was day 7."""
    assert TOUCH_SCHEDULE[0] == (0, "gentle")
    events = sim_run.run(45, 42)

    day_zero = [e for e in events if e["day"] == 0]
    assert [e for e in day_zero if e["type"] == "outreach_sent"], "day-0 gentle beat never fired"
    # day 0 is no longer just the 12 directly-emitted cart events
    assert {e["type"] for e in day_zero} > {"cart_abandoned"}


def test_the_simulator_fires_every_scheduled_touch_day():
    """Coverage, not just reproducibility: every day in TOUCH_SCHEDULE has to
    show up in the output, or a beat is being silently skipped again."""
    events = sim_run.run(45, 42)
    touched_days = {e["day"] for e in events if e["type"] == "outreach_sent"}
    for day, _stage in TOUCH_SCHEDULE:
        assert day in touched_days, f"no outreach on scheduled touch day {day}"


def test_the_simulator_still_replays_identically():
    """BUILD.md Day 1-2's original acceptance criterion, unchanged by the fix."""
    assert sim_run.run(45, 42) == sim_run.run(45, 42)
