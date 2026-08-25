"""Beta(alpha, beta) trust posterior per debtor — closed-form, zero LLM
(BUILD.md Day 5 / master doc §2.2 item 2).

Prior Beta(2,2). +1 alpha on a kept promise, +1 beta on a broken one.
Exponential decay toward the prior with a 60-virtual-day half-life, applied
lazily (on read) rather than on a timer — decay(trust, now) folds in however
much time has passed since last_update before any update is applied.
Mandate refusal is pending-neutral: it does NOT move alpha/beta directly
(master doc §3.2) — the manual promise it falls back to is what eventually
moves trust, via the ordinary kept/broken path.
"""

import datetime as dt

from engine.schemas import TrustState

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0
HALF_LIFE_DAYS = 60.0


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
