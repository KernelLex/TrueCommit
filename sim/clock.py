"""Seeded virtual-day clock (BUILD.md Day 1-2). advance(n) fires whatever is
scheduled for each of the n days starting with the day the clock is currently
on, in a fixed deterministic order — callers register on the same clock in a
stable sequence, so two runs with the same seed produce byte-identical event
streams (CLAUDE.md law #6).
"""

from collections import defaultdict
from typing import Callable

DayCallback = Callable[[int], list[dict]]


class VirtualClock:
    def __init__(self, start_day: int = 0) -> None:
        self.day = start_day
        self._scheduled: dict[int, list[DayCallback]] = defaultdict(list)

    def schedule(self, day: int, fn: DayCallback) -> None:
        """fn(day) -> list of event dicts fired when the clock reaches `day`."""
        self._scheduled[day].append(fn)

    def advance(self, n: int) -> list[dict]:
        """Simulate `n` days, STARTING WITH THE DAY THE CLOCK IS ON, and leave
        the clock on the first day not yet simulated. From `start_day=0`:
        `advance(1)` fires day 0 and leaves `day == 1`; `advance(45)` fires
        days 0..44 and leaves `day == 45`.

        "Fire today, then move on" — not "move on, then fire". The original
        did the latter, which meant a clock starting at day 0 never fired
        anything scheduled for day 0 at all: `sim/run.py`'s `(0, "gentle")`
        beat silently never ran (BUILD_LOG 2026-08-26, fixed in packet P8).
        The bug survived a green determinism test because replaying identically
        proves reproducibility, never coverage. Note this is also exactly the
        convention `engine/integration/runner.WorldRunner.advance` uses, so the
        two clocks in the repo now mean the same thing by the same words.
        """
        fired: list[dict] = []
        for _ in range(n):
            for fn in self._scheduled.get(self.day, []):
                fired.extend(fn(self.day))
            self.day += 1
        return fired
