"""Sentinel v1 — the reliability agent, zero LLM (BUILD.md Day 6, master doc
§7.2). Watches every action for silent failure: retry x3 with backoff, a
visible dead-letter queue so nothing vanishes, a link-open timer that never
just assumes delivery, and a circuit breaker for sustained outages.

"No failure is ever silent, and every failure has a designed next step."

TUNABLE (packet P6, config/agents.yaml `sentinel:` section)
-------------------------------------------------------------------------
The four module constants below are still the source of truth for every
caller that constructs `Sentinel()` with no arguments (this file's own
tests, and `engine/integration/runner.py`'s `WorldRunner` — unchanged,
zero behaviour difference). `Sentinel.__init__` now also accepts each one
as an optional keyword, defaulting to its module constant, so a config-
driven caller can override them without this file needing to know
`config/agents.yaml` exists. `engine.config.build_sentinel()` is that
caller: it reads the yaml (falling back to these same constants wherever a
key is absent) and constructs a real, working `Sentinel` from the result.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel

MAX_RETRIES = 3
BACKOFF_MINUTES = [1, 5, 15]  # backoff before retry 1, 2, 3 respectively
LINK_OPEN_TIMEOUT_HOURS = 48
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures across the whole sentinel

AttemptOutcome = Literal["ok", "retry", "dead_letter"]


class DeadLetter(BaseModel):
    action_id: str
    entity_id: str
    kind: str
    attempts: int
    last_error: str
    ts: dt.datetime


class Sentinel:
    def __init__(
        self,
        *,
        max_retries: int = MAX_RETRIES,
        backoff_minutes: list[int] | None = None,
        link_open_timeout_hours: float = LINK_OPEN_TIMEOUT_HOURS,
        circuit_breaker_threshold: int = CIRCUIT_BREAKER_THRESHOLD,
    ) -> None:
        """Every parameter defaults to this module's own constant. Calling
        `Sentinel()` with no arguments — every existing call site — is
        unaffected; only a caller that passes these explicitly (in
        practice, `engine.config.build_sentinel()`) changes behaviour."""
        self.max_retries = max_retries
        self.backoff_schedule = list(backoff_minutes) if backoff_minutes is not None else list(BACKOFF_MINUTES)
        self.link_open_timeout_hours = link_open_timeout_hours
        self.circuit_breaker_threshold = circuit_breaker_threshold

        self.attempts: dict[str, int] = {}
        self.dead_letter: list[DeadLetter] = []
        self.link_sent_at: dict[str, dt.datetime] = {}
        self.link_opened: set[str] = set()
        self.consecutive_failures = 0
        self.circuit_open = False

    def record_send_attempt(self, action_id: str, entity_id: str, kind: str, success: bool, now: dt.datetime, error: str = "") -> AttemptOutcome:
        if success:
            self.attempts.pop(action_id, None)
            self.consecutive_failures = 0
            self.circuit_open = False
            return "ok"

        n = self.attempts.get(action_id, 0) + 1
        self.attempts[action_id] = n
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.circuit_breaker_threshold:
            self.circuit_open = True

        if n > self.max_retries:
            self.dead_letter.append(DeadLetter(action_id=action_id, entity_id=entity_id, kind=kind, attempts=n, last_error=error, ts=now))
            self.attempts.pop(action_id, None)
            return "dead_letter"
        return "retry"

    def backoff_minutes(self, action_id: str) -> int:
        n = max(self.attempts.get(action_id, 1), 1)
        idx = min(n, len(self.backoff_schedule)) - 1
        return self.backoff_schedule[idx]

    def should_pause_outbound(self) -> bool:
        return self.circuit_open

    # -- link-open tracking: sent != delivered != opened -----------------

    def track_link_sent(self, action_id: str, sent_at: dt.datetime) -> None:
        self.link_sent_at[action_id] = sent_at

    def mark_link_opened(self, action_id: str) -> None:
        self.link_opened.add(action_id)

    def link_timed_out(self, action_id: str, now: dt.datetime) -> bool:
        """True = link sent but never opened within the window -> treat as a
        soft refusal signal, never assume the message landed."""
        sent_at = self.link_sent_at.get(action_id)
        if sent_at is None or action_id in self.link_opened:
            return False
        return (now - sent_at).total_seconds() / 3600.0 >= self.link_open_timeout_hours
