"""Beta(alpha, beta) trust posterior per debtor — closed-form, zero LLM
(BUILD.md Day 5 / master doc §2.2 item 2).

Prior Beta(2,2). +1 alpha on a kept promise, +1 beta on a broken one.
Exponential decay toward the prior with a 60-virtual-day half-life, applied
lazily (on read) rather than on a timer — decay(trust, now) folds in however
much time has passed since last_update before any update is applied.
Mandate refusal is pending-neutral: it does NOT move alpha/beta directly
(master doc §3.2) — the manual promise it falls back to is what eventually
moves trust, via the ordinary kept/broken path. `update_refusal()` below is
now shared with the same reasoning for a bounced mandate DEBIT that isn't a
willingness signal (insufficient_funds / bank_downtime / account_closed_frozen
/ amount_exceeds_limit — see engine/schemas.py's `DebitFailureReason` and
`engine/judgment/state_machine.py`'s `mandate_execute_failed` handling): a
debtor who had the money but the bank's rail failed, or who simply doesn't
have the money THIS week, has not demonstrated the same thing as a debtor who
revoked a mandate they'd already approved. Same function, same "pending, not
punished" reasoning, one more evidence type it now covers.
"""

import datetime as dt

from engine.schemas import TrustState

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0
HALF_LIFE_DAYS = 60.0

REPRESENT_BASE_DELAY_DAYS = 2
REPRESENT_MAX_EXTRA_DELAY_DAYS = 3
"""`derive_retry_delay_days()`'s range: 2-5 days. A timing-reason debit
failure (insufficient_funds/amount_exceeds_limit) genuinely needs the debtor
to have money on the day the mandate fires again — re-trying tomorrow ignores
that the same funds gap will usually still be there. HIGHER trust -> SHORTER
delay (we believe a quick retry will already work); LOWER trust -> LONGER
delay (give more real time for funds to plausibly arrive before spending the
one allowed retry)."""

REPRESENT_MIN_SHRINK_FRACTION = 0.5
REPRESENT_MAX_SHRINK_FRACTION = 1.0
"""`derive_shrunk_tranche_inr()`'s range. Only used for the FALLBACK LINK
after a timing-reason retry is exhausted — never for a `mandate_execute`/
`mandate_offer` amount, which must always equal the ledger's invoice amount
exactly (CLAUDE.md law 2, `state_machine.check_bounds()`'s
`mandate_amount_matches_ledger` check is untouched by this feature). A link
carries no such bound, so a trust-derived PARTIAL ask is a deliberate,
code-computed (never LLM) recovery tactic: HIGHER trust -> ask for the full
remaining amount (they've earned the benefit of the doubt); LOWER trust ->
shrink toward half, on the theory that a smaller, achievable ask recovers
something from a debtor whose posterior says the full amount is unlikely,
rather than repeating an ask that already failed once."""


def new_trust(debtor_id: str, now: dt.datetime) -> TrustState:
    return TrustState(debtor_id=debtor_id, alpha=PRIOR_ALPHA, beta=PRIOR_BETA, last_update=now)


def decay(trust: TrustState, now: dt.datetime) -> TrustState:
    """Decays the posterior's excess over the prior toward zero, so old
    evidence fades but never crosses back past the uninformative prior."""
    elapsed_days = max((now - trust.last_update).total_seconds() / 86400.0, 0.0)
    factor = 0.5 ** (elapsed_days / HALF_LIFE_DAYS)
    alpha = PRIOR_ALPHA + (trust.alpha - PRIOR_ALPHA) * factor
    beta = PRIOR_BETA + (trust.beta - PRIOR_BETA) * factor
    return TrustState(debtor_id=trust.debtor_id, alpha=alpha, beta=beta, last_update=now)


def update_kept(trust: TrustState, now: dt.datetime) -> TrustState:
    decayed = decay(trust, now)
    return TrustState(debtor_id=trust.debtor_id, alpha=decayed.alpha + 1.0, beta=decayed.beta, last_update=now)


def update_broken(trust: TrustState, now: dt.datetime) -> TrustState:
    decayed = decay(trust, now)
    return TrustState(debtor_id=trust.debtor_id, alpha=decayed.alpha, beta=decayed.beta + 1.0, last_update=now)


def update_refusal(trust: TrustState, now: dt.datetime) -> TrustState:
    """Pending-neutral: only applies decay, no alpha/beta movement. Exists as
    its own function so callers never have to special-case "do nothing" and
    the audit trail can still log that a refusal was evaluated."""
    return decay(trust, now)


def mean(trust: TrustState) -> float:
    return trust.alpha / (trust.alpha + trust.beta)


def derive_retry_delay_days(trust: TrustState) -> int:
    """Days to wait before re-attempting a mandate execution that bounced for
    a TIMING reason (insufficient_funds/amount_exceeds_limit) — never used
    for a willingness failure, which escalates instead of retrying. Pure
    function of the CURRENT posterior; callers are responsible for decaying
    `trust` to `now` first (`Ledger.current_trust()`)."""
    m = mean(trust)
    return REPRESENT_BASE_DELAY_DAYS + round((1.0 - m) * REPRESENT_MAX_EXTRA_DELAY_DAYS)


def derive_shrunk_tranche_inr(trust: TrustState, original_amount_inr: int) -> int:
    """The fallback payment LINK's amount once a timing-reason retry is
    exhausted — see the module docstring and `REPRESENT_MIN_SHRINK_FRACTION`
    above for why this never touches a `mandate_offer`/`mandate_execute`
    amount. Always at least Rs.1, never above the original amount."""
    m = mean(trust)
    fraction = REPRESENT_MIN_SHRINK_FRACTION + m * (REPRESENT_MAX_SHRINK_FRACTION - REPRESENT_MIN_SHRINK_FRACTION)
    return max(1, round(original_amount_inr * fraction))
