"""Touch-budget allocation across a debtor's several open invoices — zero LLM
(packet: debtor-level judgment, 2026-08-30, master doc's own recovery-
hierarchy reasoning extended from "which instrument" to "which invoice gets
the scarce touch").

THE PROBLEM THIS FIXES: `MAX_TOUCHES_PER_WEEK` is enforced per DEBTOR
(`state_machine.check_bounds`'s `debtor_touches` argument), but a debtor
holding more open invoices than their remaining weekly budget used to get
whichever ones happened to be iterated first (`WorldRunner.active_invoice_ids`
is alphabetically sorted) — an accident of entity-id ordering, not a decision
about which touch would recover the most money. This module answers that
question with a fixed, deterministic, fully-tested formula instead.

HOW THE RANKING ACTUALLY TAKES EFFECT: `WorldRunner._run_outreach` still
calls `_outreach()` on EVERY eligible entity of a debtor every beat, exactly
as it always did — it does not pre-commit to a fixed "top N" and skip the
rest. What changes is the ORDER: entities are now attempted in PRIORITY order
(this module's ranking) rather than alphabetical entity-id order, so the
ledger's own existing, unmodified `check_bounds()` touch-cap naturally lets
the highest-priority ones' MESSAGES through first and refuses the excess —
the same bound, doing the same job, just handed a queue worth respecting.
Two things this preserves that a "pre-commit to N winners" design broke,
found by measuring, not by inspection (`tracking/BUILD_LOG.md` 2026-08-30):
  1. `outreach_sent` still fires for a lower-priority entity even when its
     resulting message gets blocked — its STATE still progresses
     (TRIAGED->ENGAGED) on schedule, exactly as before this feature existed.
     Pre-committing to a fixed top-N and skipping the rest ENTIRELY delayed
     that progression for everyone else, which measurably pushed every
     Scene-1 mandate offer in the pinned run out past day 21 (none at all
     survived inside a 12-day window some tests specifically relied on).
  2. A single entity's own reply can cascade past the message into a
     `mandate_offer` (extraction -> instrument, also touch-counted),
     genuinely consuming a whole week's budget in one entity's turn. A
     fixed top-N picked in advance cannot see that coming; letting the
     ledger's live gate decide, attempt by attempt, always agrees with what
     actually happened instead of a prediction of it.

THE FORMULA, argued explicitly: `score = trust_mean + AGE_WEIGHT *
min(age_days / AGE_NORMALIZATION_DAYS, 1.0) - ROTATION_PENALTY * touches_so_far`.
  - `trust_mean` (the debtor's Beta posterior mean) is a genuine probability
    estimate of "will a touch on this debtor convert" — the single most
    directly relevant signal to "maximise expected recovery" this codebase
    already computes.
  - `age_days` (days past due for THIS specific invoice) breaks ties and
    weights urgency: a debtor's oldest invoice is the one closest to running
    out of touch-schedule days before the idle sweep hands it to a human, so
    all else equal it gets the touch. Normalized and capped so one very old
    invoice cannot make age swamp trust entirely — both signals matter.
  - Deliberately NOT a function of invoice amount: the packet that asked for
    this named "trust and invoice age" specifically; see
    tracking/DECISIONS.md 2026-08-30 for why amount was left out rather than
    silently added.

A REAL BUG FOUND BY MEASURING THIS, not by inspection — see
tracking/BUILD_LOG.md 2026-08-30: `trust_mean` is a DEBTOR-level value,
identical across every one of that debtor's own invoices, and `age_days` is
fixed at load time (a due date never moves) — so for a FIXED set of a
debtor's invoices, `trust_mean + age_term` alone ranks them IDENTICALLY on
every single outreach beat for the entire run. The naive version of this
formula therefore doesn't "prioritize the oldest invoice" — it PERMANENTLY
EXCLUDES every invoice but the single oldest one for as long as that debtor
holds more open invoices than their weekly budget, since nothing in the
formula ever changes their relative order. Measured directly: 12 invoices
across 8 debtors received ZERO touches for the entire 45-day run under the
naive formula, and `recovered_inr` fell ~12% versus not allocating at all —
starvation, not smart prioritization. `ROTATION_PENALTY * touches_so_far`
fixes it: once an invoice wins a beat, its score drops for the NEXT one,
so a sibling with a smaller age gap overtakes within a beat or two, while a
genuinely much-older invoice can still win several beats in a row before a
much-younger one gets a look — a real scheduler's aging/rotation, not a new
modeling signal (`touches_so_far` is `len(entity.touches)`, already tracked
for every entity; this doesn't add a fact the ledger didn't already know).
"""

from engine.schemas import TrustState

AGE_WEIGHT = 0.5
AGE_NORMALIZATION_DAYS = 60.0
"""At 60+ days past due, age contributes its full weight (0.5) to the score —
roughly the trust posterior's own decay half-life (`trust.HALF_LIFE_DAYS`),
so the two signals are calibrated on a comparable timescale rather than one
saturating far faster than the other."""

ROTATION_PENALTY = 0.3
"""Per touch an invoice has already received. Chosen so a SINGLE touch is
usually enough to flip a close race (typical age-gap contributions are well
under 0.3) but a genuinely much older sibling (near the full 0.5 age_term
gap) can still win two beats running before a much younger invoice
overtakes it — aging still dominates, it just cannot become permanent
exclusion. See `tests/test_allocation.py` for the exact rotation this
produces on both a close-age and a wide-age pair."""


def score_invoice_for_touch(trust_mean: float, age_days: int, touches_so_far: int = 0) -> float:
    """Higher score = higher priority for the debtor's next scarce touch.
    Pure function, no side effects, no randomness — the same
    (trust_mean, age_days, touches_so_far) triple always produces the same
    score (CLAUDE.md law 6)."""
    age_term = AGE_WEIGHT * min(max(age_days, 0) / AGE_NORMALIZATION_DAYS, 1.0)
    rotation_term = ROTATION_PENALTY * max(touches_so_far, 0)
    return trust_mean + age_term - rotation_term


def rank_by_priority(
    entity_ids: list[str],
    age_days_by_entity: dict[str, int],
    debtor_trust: TrustState,
    touches_so_far_by_entity: dict[str, int] | None = None,
) -> list[str]:
    """Every one of ONE debtor's eligible entities, reordered highest-
    priority first by `score_invoice_for_touch` — never truncated. The
    caller (`WorldRunner._run_outreach`) attempts ALL of them in this order;
    the ledger's own touch-cap bound decides how many actually get a
    dispatched message, exactly as it always has (see the module docstring
    for why a truncating version was tried and measurably made things worse).

    `touches_so_far_by_entity` (an entity's own `len(EntityState.touches)`)
    feeds the rotation term that keeps this from permanently favoring a
    debtor's single oldest invoice — see the module docstring. Missing
    entries default to 0 (never touched — the correct default: an untouched
    invoice should never be penalized for something that hasn't happened).

    `entity_ids` MUST already be in a fixed, deterministic order (the caller
    sorts it) — this function breaks score ties by that input order via a
    stable sort, so two invoices with identical scores are never ordered
    inconsistently between runs.
    """
    touches_so_far_by_entity = touches_so_far_by_entity or {}
    trust_mean = debtor_trust.alpha / (debtor_trust.alpha + debtor_trust.beta)
    # Rotation is measured RELATIVE to the least-touched entity in THIS
    # eligible set, not against an absolute count. An entity tied for fewest
    # touches always scores on its raw trust+age alone — it is never
    # penalized for something that hasn't happened to it — so a persistently
    # low-age invoice's disadvantage shrinks every round its siblings pull
    # further ahead on touch count, rather than needing enough ABSOLUTE
    # touches to outweigh a fixed per-touch cost that never arrives if it is
    # always the one being skipped.
    min_touches = min((touches_so_far_by_entity.get(eid, 0) for eid in entity_ids), default=0)
    return sorted(
        entity_ids,
        key=lambda eid: score_invoice_for_touch(
            trust_mean, age_days_by_entity.get(eid, 0),
            touches_so_far_by_entity.get(eid, 0) - min_touches,
        ),
        reverse=True,
    )


def allocate_touch_budget(
    entity_ids: list[str],
    age_days_by_entity: dict[str, int],
    debtor_trust: TrustState,
    budget: int,
    touches_so_far_by_entity: dict[str, int] | None = None,
) -> list[str]:
    """A convenience slice of `rank_by_priority()` for a caller that genuinely
    wants a hard top-`budget` cut rather than a full attempt order (kept for
    direct testability of "who would win" independent of how the ledger's
    live gate would actually resolve cascades) — NOT what `_run_outreach`
    itself calls; see that module's `rank_by_priority` for why."""
    if budget >= len(entity_ids):
        return list(entity_ids)
    if budget <= 0:
        return []
    return rank_by_priority(entity_ids, age_days_by_entity, debtor_trust, touches_so_far_by_entity)[:budget]
