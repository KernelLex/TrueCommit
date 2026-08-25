"""Seeded virtual-day clock (BUILD.md Day 1-2). advance(n) fires whatever is
scheduled for each day it passes through, in a fixed deterministic order —
callers register on the same clock in a stable sequence, so two runs with the
same seed produce byte-identical event streams (CLAUDE.md law #6).
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
        fired: list[dict] = []
        for _ in range(n):
            self.day += 1
            for fn in self._scheduled.get(self.day, []):
                fired.extend(fn(self.day))
        return fired
