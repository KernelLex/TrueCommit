"""Auditor v1 — the accuracy agent, the one justified 2nd-pass use of an LLM
(BUILD.md Day 7, master doc §7.3). Samples a fraction of extractions, checks
each one against the raw message, tracks a rolling agreement rate, and
quarantines the extractor — routing every money-adjacent action to human
review regardless of that read's own confidence — when the rolling rate
falls below threshold.

"Self-monitoring AI that benches itself when underperforming."

DEFAULT VERIFICATION IS ZERO-LLM, DELIBERATELY
-----------------------------------------------
The default check (`heuristic_cross_check`) re-runs the SAME extraction
through `engine/perception/providers/heuristic.py` — already-tested,
already-deterministic code, not new heuristic logic invented for this
module — and compares the resulting level against the one actually used.
This keeps the Auditor itself free and offline by default, exactly like
every other perception task in this codebase (heuristic first, LLM opt-in).

Honesty caveat, stated plainly: when the ACTIVE provider IS heuristic (this
project's own default), this cross-check trivially agrees with itself —
comparing a deterministic function's output against its own output can only
ever agree. The check is only a meaningful second opinion when the active
provider is a real LLM (anthropic/ollama), which is exactly when accuracy
drift is a real risk worth watching. `llm_verify()` below is the genuine
2nd-pass LLM path the master doc names — real, callable, opt-in, using the
already-written `engine/perception/prompts/verify.md` (Job 1) — for when a
caller wants the real thing rather than the cross-check.

QUARANTINE IS MEASURED, NEVER ENFORCED, BY THIS CLASS
-------------------------------------------------------
`Auditor` only samples, verifies, and reports `quarantined: bool`. It has no
reference to a Ledger and cannot hold or block anything itself — the one
caller that matters (`WorldRunner._inbound`, the single funnel every debtor
message passes through) reads `auditor.quarantined` and calls
`Ledger.set_auditor_quarantine()`, which is what actually widens the money
gate. Keeping the measurement and the enforcement in different objects
means a test can drive the Auditor's own logic without needing a Ledger at
all, and vice versa.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel

from engine.perception.providers import get_provider
from engine.schemas import Extraction, Message

DEFAULT_SAMPLE_RATE = 0.10
DEFAULT_QUARANTINE_THRESHOLD = 0.85
DEFAULT_ROLLING_WINDOW = 10
"""Small on purpose: a 45-day run samples ~10% of ~100 messages, so a
window in the tens is the largest that can realistically ever fill within
one run. A bigger, statistically comfier window would look good on paper
and never actually reach quarantine in a real demo — this project's own
"don't invent evidence" ethos applies to the sample size, not only to the
numbers it produces."""

VerifySource = Literal["heuristic_cross_check", "llm"]


class AuditSample(BaseModel):
    id: str
    message_id: str
    entity_id: str
    extraction_level: str
    extraction_confidence: float
    agrees: bool
    note: str
    source: VerifySource
    ts: dt.datetime


class DriftEvent(BaseModel):
    event: Literal["quarantined", "restored"]
    rolling_agreement: float
    sample_count: int
    ts: dt.datetime


def heuristic_cross_check(
    message: Message, thread_messages: list[Message], extraction: Extraction,
) -> tuple[bool, str, VerifySource]:
    """The default, zero-cost verification: re-extract independently via the
    heuristic provider and compare levels. See the module docstring's
    honesty caveat about what this means when heuristic IS the active
    provider. Uniform 3-argument signature (ignoring nothing) so `Auditor`
    can call any verify function identically — see `llm_verify` below."""
    reference = get_provider("heuristic").extract(message, thread_messages)
    if reference.level == extraction.level:
        return True, "", "heuristic_cross_check"
    return False, f"heuristic cross-check reads {reference.level}, extraction used {extraction.level}", "heuristic_cross_check"


def llm_verify(
    message: Message, thread_messages: list[Message], extraction: Extraction,
) -> tuple[bool, str, VerifySource]:
    """The real 2nd-pass LLM call the master doc names (§7.3, "verification
    prompt"), using Job 1 of `engine/perception/prompts/verify.md` — split
    out of that file at call time so Job 2's dispute-summary instructions
    never leak into this job's context. `thread_messages` is accepted for a
    uniform signature with `heuristic_cross_check` but unused: Job 1's own
    prompt only asks about the one message an extraction was read from.
    Raises exactly like every other perception call does without a key
    (`engine.perception.client`) — callers that want to stay offline should
    pass `heuristic_cross_check` instead (the `Auditor` default), not catch
    an exception here."""
    from engine.perception import client

    prompt = client.load_prompt("verify")
    job_1 = prompt.split("## Job 2", 1)[0]
    user_content = (
        f"Original message: {message.text!r}\n"
        f"Extraction produced: level={extraction.level}, amount_inr={extraction.amount_inr}, "
        f"date={extraction.date}, condition={extraction.condition!r}, "
        f"confidence={extraction.confidence}"
    )

    class _VerifyResult(BaseModel):
        agrees: bool
        note: str

    result = client.call_structured(job_1, user_content, _VerifyResult)
    return result.agrees, result.note, "llm"


class Auditor:
    def __init__(
        self,
        rng,
        *,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        quarantine_threshold: float = DEFAULT_QUARANTINE_THRESHOLD,
        rolling_window: int = DEFAULT_ROLLING_WINDOW,
        verify_fn=None,
    ) -> None:
        """`rng` is REQUIRED and must be a seeded `random.Random` — no
        silent fallback to an unseeded or freshly-seeded one, because that
        would make sampling look reproducible while quietly not being tied
        to the caller's own run (CLAUDE.md law 6).

        IMPORTANT: this must be a DEDICATED `random.Random` instance, never
        `WorldRunner.rng` itself. That stream drives every persona decision
        in sequence (reply moves, mandate moves, kept/broken promises,
        template choices) — interleaving Auditor sampling draws into it
        would shift every draw after the first sample and silently change
        the pinned 45-day numbers (recovered amount, KEPT/HANDOFF counts,
        `messages_sent`, all of it) the moment auditing was turned on.
        `WorldRunner` constructs a second, independently-seeded generator
        for exactly this reason — see its own `__init__`. `verify_fn`
        defaults to `heuristic_cross_check`; pass `llm_verify` for the real
        2nd-pass path — both share the same `(message, thread_messages,
        extraction) -> (agrees, note, source)` shape."""
        self.sample_rate = sample_rate
        self.quarantine_threshold = quarantine_threshold
        self.rolling_window = rolling_window
        self.rng = rng
        self._verify_fn = verify_fn or heuristic_cross_check
        self.samples: list[AuditSample] = []
        self.drift_log: list[DriftEvent] = []
        self.quarantined = False
        self._seq = 0

    def maybe_audit(
        self, message: Message, thread_messages: list[Message], extraction: Extraction,
        entity_id: str, now: dt.datetime,
    ) -> AuditSample | None:
        """Called once per extraction (the funnel every debtor message
        passes through already produces one). Returns `None` on the ~90% of
        calls the sample rate skips — that is the common case, not a
        failure, so callers should not treat `None` as an error."""
        if self.rng.random() > self.sample_rate:
            return None
        agrees, note, source = self._verify_fn(message, thread_messages, extraction)

        self._seq += 1
        sample = AuditSample(
            id=f"AUD-{self._seq:04d}", message_id=message.id, entity_id=entity_id,
            extraction_level=extraction.level, extraction_confidence=extraction.confidence,
            agrees=agrees, note=note, source=source, ts=now,
        )
        self.samples.append(sample)
        self._update_quarantine(now)
        return sample

    def rolling_agreement(self) -> float | None:
        """`None` until at least one sample exists — never a fabricated
        100%/0% before there is any evidence either way."""
        recent = self.samples[-self.rolling_window:]
        if not recent:
            return None
        return sum(1 for s in recent if s.agrees) / len(recent)

    def _update_quarantine(self, now: dt.datetime) -> None:
        if len(self.samples) < self.rolling_window:
            return  # not enough evidence yet to judge fairly either way
        agreement = self.rolling_agreement()
        was = self.quarantined
        self.quarantined = agreement < self.quarantine_threshold
        if self.quarantined != was:
            self.drift_log.append(DriftEvent(
                event="quarantined" if self.quarantined else "restored",
                rolling_agreement=agreement, sample_count=len(self.samples), ts=now,
            ))

    def status(self) -> dict:
        return {
            "sample_count": len(self.samples),
            "rolling_agreement": self.rolling_agreement(),
            "quarantined": self.quarantined,
            "sample_rate": self.sample_rate,
            "quarantine_threshold": self.quarantine_threshold,
            "rolling_window": self.rolling_window,
            "drift_log": [d.model_dump() for d in self.drift_log],
        }
