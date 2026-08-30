"""Mandate-acceptance learning — a SECOND, separate Beta(2,2) posterior,
learned live from the run itself rather than assumed (packet 4, 2026-08-31).

WHY THIS IS A DIFFERENT QUESTION FROM `trust.py`'s posterior: debtor trust
(`engine/judgment/trust.py`) answers "will THIS debtor keep a promise they
already made" — scored per debtor, decayed over calendar time. This module
answers a different, PORTFOLIO-level question: "of everyone who was OFFERED
a mandate, what fraction said yes" — a property of the whole population's
relationship with the INSTRUMENT itself, not of any one person's
promise-keeping history. `eval/run_arms.py`'s existing "mandate-acceptance
sensitivity band" already treats this rate as the single most consequential
unknown the whole thesis rests on, but only as a SWEPT HYPOTHETICAL
(10%-60%, picked because there is no real-world number yet) — this module is
what a LIVE run actually observes as it goes, so the hypothetical band and
the learned number can be compared side by side instead of the hypothetical
standing in for measurement forever.

SINGLE, GLOBAL POSTERIOR, NOT PER-DEBTOR, DELIBERATELY: a per-debtor version
would see at most one or two observations per debtor in a single run —
essentially never leaving the uninformative prior, and answering a question
("will THIS debtor accept a mandate") nobody asked. The portfolio-level rate
is the number the sensitivity band and the pitch both actually need. See
`tracking/DECISIONS.md` 2026-08-31 for this being a deliberate scoping
choice, not an oversight.

NO TIME DECAY, deliberately, unlike `trust.py`'s posterior: this is a
population-level learning statistic accumulated over one bounded run, not a
per-person behavioral-recency signal — there is no reason an acceptance
observed on day 3 should count for less than one observed on day 40 within
the same fixed-length run. A plain, cumulative Beta(2,2) update; `TrustState`
is reused for its shape only (`last_update` is stored for observability —
"as of when was this last touched" — and is never passed through
`trust.decay()`).

READ-ONLY / OBSERVATIONAL BY DESIGN: this posterior currently feeds the
dashboard meter and the break-even comparison ONLY. It does not change which
instrument gets offered, or any state transition — CLAUDE.md law 1 is about
the LLM specifically, but the same discipline applies here: wiring a live,
learning-updated parameter into `_decide_action`'s own instrument choice
would be a materially bigger, riskier change (reproducibility, new bounds
tests, a new class of decision the state machine has never taken a
live-updating signal for) that this packet's own one-line scope
("2nd Beta posterior over mandate registration, break-even number, dashboard
meter") did not ask for. Left here as a clearly labelled possible next step,
not built.
"""

import datetime as dt

from engine.schemas import TrustState

PORTFOLIO_ID = "__PORTFOLIO__"
"""Sentinel id distinct from any real debtor_id — this is a population-level
aggregate, never a debtor's own record, and the id is chosen to make that
obvious wherever it surfaces (audit trail, API, dashboard)."""

PRIOR_ALPHA = 2.0
PRIOR_BETA = 2.0


def new_acceptance(now: dt.datetime) -> TrustState:
    return TrustState(debtor_id=PORTFOLIO_ID, alpha=PRIOR_ALPHA, beta=PRIOR_BETA, last_update=now)


def update_accepted(state: TrustState, now: dt.datetime) -> TrustState:
    """A `mandate_confirmed` event: the debtor said yes to the instrument."""
    return TrustState(debtor_id=state.debtor_id, alpha=state.alpha + 1.0, beta=state.beta, last_update=now)


def update_declined(state: TrustState, now: dt.datetime) -> TrustState:
    """A `mandate_refused` event: an explicit decline, or a 48h silent
    timeout the Sentinel treats as a soft refusal — either way, the debtor
    did not accept the instrument they were offered.

    Deliberately NOT triggered by a `mandate_execute_failed` event (any
    reason, including `mandate_revoked`): that debtor DID accept the offer —
    they would never have reached execution otherwise — so a revoke afterward
    answers a different question ("will an accepted mandate actually pay
    out") than the one this posterior tracks ("did they say yes to being
    offered one"). Conflating the two would double-count a single debtor's
    behavior against a question they only answered once.
    """
    return TrustState(debtor_id=state.debtor_id, alpha=state.alpha, beta=state.beta + 1.0, last_update=now)


def mean(state: TrustState) -> float:
    return state.alpha / (state.alpha + state.beta)


def observations(state: TrustState) -> dict:
    """n_accepted/n_declined/n_total derived from the posterior's own
    departure from the Beta(2,2) prior — exact, since every update above
    moves alpha or beta by precisely 1.0 and nothing else ever touches
    either field."""
    n_accepted = round(state.alpha - PRIOR_ALPHA)
    n_declined = round(state.beta - PRIOR_BETA)
    return {"n_accepted": n_accepted, "n_declined": n_declined, "n_total": n_accepted + n_declined}
