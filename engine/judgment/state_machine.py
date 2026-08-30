"""Escalation state machine + hard bounds — ZERO LLM (BUILD.md Day 5,
master doc §3.4). Every bound below is enforced here, only here, and cannot
be prompted around: nothing upstream (perception, drafting) can construct an
Action that skips check_bounds().

STATES vs the master doc's 3-terminal-state framing (Part 6 Q2: KEPT / CLEAN
LOSS / HUMAN_HANDOFF): DISPUTED is tracked as its own state (BUILD.md's Day-5
pytest explicitly checks for `state == "DISPUTED"`), but it is a HUMAN_HANDOFF
variant in every practical sense — dispute -> evidence packet -> human, no
further outbound actions, one-way. TERMINAL_STATES below includes it; pitch
material rolls it into the HUMAN_HANDOFF bucket. See tracking/DECISIONS.md.

Termination guarantee (CLAUDE.md law #5, "nothing loops forever"): escalation
is capped at ESCALATE_4 — the next failure event forces HUMAN_HANDOFF rather
than a 5th stage. A hard step-count safety valve (HARD_STEP_CAP) forces
HUMAN_HANDOFF regardless of event content if an entity somehow processes an
unreasonable number of events without resolving — a backstop, not the normal
path, but it makes "no infinite loop" true by construction, not by hoping the
event stream is well-behaved.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# HARD BOUNDS — master doc §3.4 / CLAUDE.md §3 law 4. Named constants only;
# nothing here is ever computed from an LLM output.
# ---------------------------------------------------------------------------

MAX_TOUCHES_PER_WEEK = 2  # PER DEBTOR, across every entity they hold — see check_bounds
TOUCH_WINDOW_DAYS = 7
RENEGOTIATION_CAP = 2
MANDATE_AMOUNT_CAP = 100_000
RETRY_ON_EXECUTION_FAILURE = 1
MAX_ESCALATE_STAGE = 4
HARD_STEP_CAP = 60  # termination backstop; see module docstring
MAX_PROMISE_HORIZON_DAYS = 60
"""Red-team packet, 2026-08-30 (`eval/red_team.py`'s "promise-farmer"
exploit): nothing downstream of perception ever sanity-checked a promise's
own claimed due date against a ceiling — a debtor who states an absurd
future date (something no natural template in `sim/personas.py` produces,
but nothing stops a real conversation, or a smarter/LLM extractor reading
one verbatim, from producing) stays PROMISED and `_pending_promise`-excluded
from BOTH the ordinary ladder and the idle sweep for as long as they like,
directly defeating CLAUDE.md law 5's "every recovery path terminates"
guarantee within any bounded observation window. `cap_promise_due_day()`
below is the fix: a claimed date beyond this many days out is truncated to
the ceiling, not rejected — the debtor's own words still reach the audit
trail and the evidence a human would eventually see verbatim; only the
SCHEDULING the system acts on refuses to treat "never" as a valid promise
date. Chosen to comfortably exceed the escalation ladder's own longest
legitimate cadence (`TOUCH_STAGE_BY_DAY`'s last beat is day 30) while still
being far short of the idle sweep's own horizon (`FINAL_SWEEP_DAY` = 37),
so a capped promise still resolves (kept or broken) before the sweep would
otherwise have to catch it as idle."""

State = Literal[
    "NEW", "TRIAGED", "ENGAGED", "PROMISED", "MANDATED", "LINKED", "AT_RISK",
    "ESCALATE_1", "ESCALATE_2", "ESCALATE_3", "ESCALATE_4",
    "KEPT", "CLEAN_LOSS", "HUMAN_HANDOFF", "DISPUTED",
]
TERMINAL_STATES: set[State] = {"KEPT", "CLEAN_LOSS", "HUMAN_HANDOFF", "DISPUTED"}
ESCALATE_STATES: list[State] = ["ESCALATE_1", "ESCALATE_2", "ESCALATE_3", "ESCALATE_4"]

HUMAN_RESOLVABLE_STATES: set[State] = {"HUMAN_HANDOFF", "DISPUTED"}
"""The two terminal states a HUMAN is allowed to close out — see
`HUMAN_RESOLUTION_EVENT` below. KEPT and CLEAN_LOSS are already resolved
outcomes and stay immutable to everything, including this."""

HUMAN_RESOLUTION_EVENT = "human_resolution"
"""THE ONE EXCEPTION to terminal-state immutability.

Everywhere else in this module a terminal state is a dead end: `transition()`
returns unchanged for every event once `state in TERMINAL_STATES`, and there
are parametrized tests that bombard each terminal state with the whole event
vocabulary to prove it. This single event type is exempt, and only from
HUMAN_HANDOFF / DISPUTED, because it does not represent the AGENT deciding
anything — it represents a human merchant telling the system how the case they
were handed actually ended ("we got paid" / "we wrote it off"). Without it a
handoff is a state the system can never close, which contradicts CLAUDE.md law
5's "no silent deaths" just as badly as a loop would.

Blast radius is contained by construction, not by convention:
  * no code path inside `engine/` ever emits it — the integration runner's
    event vocabulary does not contain it (there is a test),
  * `POST /events` explicitly REFUSES it (400), so it cannot be injected
    through the general-purpose manual event route (there is a test),
  * the only producer is `Ledger.resolve_handoff()`, reached only from
    `POST /entities/{id}/resolve-handoff`, which additionally refuses any
    entity that is not currently an open handoff/dispute.
"""

HUMAN_RESOLUTIONS: dict[str, State] = {"recovered": "KEPT", "written_off": "CLEAN_LOSS"}

TouchKind = Literal["link", "mandate_offer", "message", "voice", "sms"]
# Actions that go TO the debtor/customer and are what "no further outbound
# actions" (BUILD.md Day-5 dispute test) means to block. evidence_packet /
# human_handoff are the terminal-state's own resolution artifacts, not
# further outreach, so they're exempt from the terminal-state block below.
#
# `sms` joined both sets in packet P14. NO NEW BOUND CONSTANT WAS ADDED for it
# and none was weakened: an SMS is an outbound contact like any other, so it
# rides MAX_TOUCHES_PER_WEEK, the terminal-state stop and the legal-stage
# refusal on exactly the same terms as `message`/`voice`/`link`. That is the
# whole reason it is added to these sets rather than given a channel budget of
# its own — a new channel with its own allowance would be a way around bound #4.
OUTBOUND_KINDS = {"message", "link", "mandate_offer", "mandate_execute", "voice", "sms"}


class EntityState(BaseModel):
    entity_id: str
    state: State = "NEW"
    escalate_stage: int = 0
    renegotiation_count: int = 0
    retry_count: int = 0
    mandate_refused: bool = False
    touches: list[dt.datetime] = Field(default_factory=list)
    """This ENTITY's own outbound touches. Kept per-entity because per-invoice
    history is what the funnel, the Tier-0 "0 touches" claim and the dashboard
    timeline read. It is NOT what the touch cap is measured against — bound #4
    is per DEBTOR, see check_bounds()'s `debtor_touches` argument."""
    invoice_amount_inr: int | None = None
    step_count: int = 0


class BoundsResult(BaseModel):
    allowed: bool
    reason: str


class BoundsCheck(BaseModel):
    """One line of `check_bounds_detailed()`'s checklist — a single bound, the
    verdict it reached, and the real numbers it reached it from."""

    name: str
    passed: bool
    detail: str


def check_bounds(
    entity: EntityState,
    action_kind: str,
    params: dict[str, Any],
    now: dt.datetime,
    debtor_touches: list[dt.datetime] | None = None,
    debtor_mandate_refused: bool = False,
) -> BoundsResult:
    """The single gate every action passes through before it executes
    (CLAUDE.md law #4). Pure predicate — never mutates `entity`, never reads
    hidden state: everything it needs is an argument.

    `debtor_touches` is every outbound touch already made to the DEBTOR who
    owns this entity, across ALL of their entities (invoices, carts). The
    touch cap is worded per debtor in both CLAUDE.md law 4 and master doc §3.4
    ("max_touches_per_week = 2 (per debtor/customer)"), so it is counted per
    debtor here: a debtor holding five overdue invoices gets at most two
    messages a week in total, not two per invoice. We throttle the human, not
    the invoice.

    Passing it is the caller's job (the Ledger keeps `touches_by_debtor` and
    hands the right list in). When it is omitted the entity is treated as its
    own debtor and its own `touches` are used — the right answer for a
    single-entity debtor, and never more permissive than the old per-entity
    behaviour. The Ledger always guarantees the debtor list is a superset of
    the entity's own touches, so the two never disagree in production.

    `debtor_mandate_refused` (packet: debtor-level judgment, 2026-08-30) is
    "negotiation posture" lifted the same way the touch cap already is: a
    debtor who refused (or had revoked / a rail die on) a mandate on ONE of
    their invoices should not be offered a fresh mandate on ANOTHER — the
    refusal is a fact about the PERSON, not the invoice. Defaults to False so
    every caller that predates this feature (including every existing test)
    is completely unaffected.
    """
    if entity.state in TERMINAL_STATES and action_kind in OUTBOUND_KINDS:
        return BoundsResult(allowed=False, reason=f"entity in terminal state {entity.state}, no further outbound actions")

    if action_kind == "mandate_offer":
        if entity.mandate_refused:
            return BoundsResult(allowed=False, reason="post-refusal re-offer of mandate = NEVER")
        if debtor_mandate_refused:
            return BoundsResult(
                allowed=False,
                reason="post-refusal re-offer of mandate = NEVER (debtor-level: another invoice from this debtor already refused/revoked one)",
            )
        if entity.renegotiation_count > RENEGOTIATION_CAP:
            return BoundsResult(allowed=False, reason=f"renegotiation_cap ({RENEGOTIATION_CAP}) exceeded, no more mandate offers")
        amount = params.get("amount_inr")
        if amount is not None and amount > MANDATE_AMOUNT_CAP:
            return BoundsResult(allowed=False, reason=f"mandate_amount_cap (Rs.{MANDATE_AMOUNT_CAP:,}) exceeded, falls back to partial + link")

    if action_kind in ("mandate_offer", "mandate_execute"):
        amount = params.get("amount_inr")
        if amount is not None and entity.invoice_amount_inr is not None and amount != entity.invoice_amount_inr:
            return BoundsResult(allowed=False, reason="mandate amount must equal ledger invoice amount exactly, no invented numbers")

    if action_kind == "mandate_execute" and entity.retry_count > RETRY_ON_EXECUTION_FAILURE:
        return BoundsResult(allowed=False, reason=f"retry_on_execution_failure ({RETRY_ON_EXECUTION_FAILURE}) exceeded, falls to link/ladder/human")

    if action_kind in ("message", "link", "mandate_offer", "voice", "sms"):
        if params.get("stage") == "legal":
            return BoundsResult(allowed=False, reason="legal-stage notices go to the merchant for review; the agent never sends legal communication itself")
        window = entity.touches if debtor_touches is None else debtor_touches
        recent = [t for t in window if (now - t).days < TOUCH_WINDOW_DAYS]
        if len(recent) >= MAX_TOUCHES_PER_WEEK:
            scope = "this entity" if debtor_touches is None else "this debtor's entities"
            return BoundsResult(
                allowed=False,
                reason=f"max_touches_per_week ({MAX_TOUCHES_PER_WEEK}) exceeded across {scope}",
            )

    return BoundsResult(allowed=True, reason="ok")


def cap_promise_due_day(claimed_day: int, now_day: int) -> int:
    """The promise-farmer mitigation (`MAX_PROMISE_HORIZON_DAYS` above,
    2026-08-30): truncates a claimed due day to the ceiling, never further
    out. `claimed_day`/`now_day`/the return value are all virtual-day
    integers (the same unit `WorldRunner._due_day()` already computes in),
    so this is a pure, trivially-testable clamp with no knowledge of
    calendars or timezones. A `claimed_day` already inside the horizon
    passes through completely unchanged — this never pulls a legitimate
    near-term promise CLOSER, only refuses to push the ceiling further out."""
    return min(claimed_day, now_day + MAX_PROMISE_HORIZON_DAYS)


def cap_promise_due_date(claimed: dt.date, now: dt.date) -> dt.date:
    """Same clamp as `cap_promise_due_day()`, in real calendar dates rather
    than virtual-day integers — `Ledger._update_promise()` never sees
    `WorldRunner`'s day-integer clock, only `dt.date`/`dt.datetime`, so it
    needs its own unit. This is the defense-in-depth leg: any promise built
    from a directly-injected `extraction_received` event (API call, manual
    test event, a future real channel) gets the same ceiling as the
    simulator's own scheduling path, even though those two call sites never
    share a code path."""
    return min(claimed, now + dt.timedelta(days=MAX_PROMISE_HORIZON_DAYS))


def check_bounds_detailed(
    entity: EntityState,
    action_kind: str,
    params: dict[str, Any],
    now: dt.datetime,
    debtor_touches: list[dt.datetime] | None = None,
    debtor_mandate_refused: bool = False,
) -> list[BoundsCheck]:
    """A LENS ON `check_bounds()`, NEVER A SECOND GATE.

    `check_bounds()` above short-circuits: it returns the FIRST bound that
    refuses, because that is all a gate needs to decide. That makes it a poor
    thing to show a human — "blocked: max_touches_per_week" says nothing about
    the six other bounds that were never reached, and an ALLOWED action shows
    no working at all.

    This function answers the presentation question instead: run EVERY check
    that applies to `action_kind`, in `check_bounds()`'s own order, and report
    each one with the actual numbers it compared. Nothing here decides
    anything — the ledger's `_gate()` still calls `check_bounds()` for the
    verdict and only records this alongside it.

    THE INVARIANT THAT MAKES THAT SAFE:

        check_bounds(*args).allowed == all(c.passed for c in check_bounds_detailed(*args))

    for every input, proven over a large random sample by
    `tests/test_state_machine.py::test_check_bounds_detailed_can_never_disagree_with_check_bounds`
    (which also pins that the FIRST failing check is the one `check_bounds()`
    names in its reason). A checklist that could show something the real
    decision didn't would be worse than no checklist at all.

    A check appears in the list exactly when `check_bounds()` would actually
    evaluate its condition — same guards, same order. An amount-dependent
    check against `params` that carry no amount is not silently reported as
    "passed"; it is simply absent, because it did not run.

    One deliberate asymmetry, and the only one: the mandate-cap comparison is
    additionally guarded on the amount being numeric. `check_bounds()` would
    raise `TypeError` on a hand-typed `?amount_inr=lots`; this function is
    read by an HTTP preview route, so it declines to evaluate that check
    rather than crash. Every producer inside the system writes an int there
    (the ledger copies it from its own invoice record), so the two never
    diverge on any input the pipeline can actually make.
    """
    checks: list[BoundsCheck] = []
    amount = params.get("amount_inr")
    numeric_amount = isinstance(amount, (int, float)) and not isinstance(amount, bool)

    if action_kind in OUTBOUND_KINDS:
        terminal = entity.state in TERMINAL_STATES
        checks.append(BoundsCheck(
            name="terminal_state_stops_outbound",
            passed=not terminal,
            detail=(
                f"state {entity.state} is terminal — no further outbound actions"
                if terminal else
                f"state {entity.state} is not terminal "
                f"(terminal: {', '.join(sorted(TERMINAL_STATES))})"
            ),
        ))

    if action_kind == "mandate_offer":
        checks.append(BoundsCheck(
            name="no_mandate_reoffer_after_refusal",
            passed=not (entity.mandate_refused or debtor_mandate_refused),
            detail=(
                "this debtor already refused a mandate — re-offer is NEVER allowed"
                if entity.mandate_refused else
                "another invoice from this debtor already refused/revoked a mandate — "
                "re-offer is NEVER allowed at the debtor level either"
                if debtor_mandate_refused else
                "no mandate refusal on record for this entity or debtor"
            ),
        ))
        checks.append(BoundsCheck(
            name="renegotiation_cap",
            passed=entity.renegotiation_count <= RENEGOTIATION_CAP,
            detail=(
                f"renegotiations so far: {entity.renegotiation_count} "
                f"{'<=' if entity.renegotiation_count <= RENEGOTIATION_CAP else '>'} "
                f"cap {RENEGOTIATION_CAP}"
            ),
        ))
        if numeric_amount:
            checks.append(BoundsCheck(
                name="mandate_amount_cap",
                passed=not (amount > MANDATE_AMOUNT_CAP),
                detail=(
                    f"mandate amount: Rs.{amount:,} "
                    f"{'<=' if amount <= MANDATE_AMOUNT_CAP else '>'} "
                    f"cap Rs.{MANDATE_AMOUNT_CAP:,}"
                ),
            ))

    if action_kind in ("mandate_offer", "mandate_execute"):
        ledger_amount = entity.invoice_amount_inr
        if amount is not None and ledger_amount is not None:
            matches = amount == ledger_amount
            checks.append(BoundsCheck(
                name="mandate_amount_matches_ledger",
                passed=matches,
                detail=(
                    f"amount matches ledger: Rs.{amount:,} == Rs.{ledger_amount:,}"
                    if matches and numeric_amount else
                    f"amount {amount!r} != ledger invoice amount Rs.{ledger_amount:,} "
                    "(no invented numbers)"
                    if not matches else
                    f"amount matches ledger record ({amount!r})"
                ),
            ))

    if action_kind == "mandate_execute":
        checks.append(BoundsCheck(
            name="retry_on_execution_failure",
            passed=entity.retry_count <= RETRY_ON_EXECUTION_FAILURE,
            detail=(
                f"retries used: {entity.retry_count} "
                f"{'<=' if entity.retry_count <= RETRY_ON_EXECUTION_FAILURE else '>'} "
                f"limit {RETRY_ON_EXECUTION_FAILURE}"
            ),
        ))

    if action_kind in ("message", "link", "mandate_offer", "voice", "sms"):
        stage = params.get("stage")
        checks.append(BoundsCheck(
            name="legal_stage_goes_to_merchant",
            passed=stage != "legal",
            detail=(
                "stage 'legal' — legal-stage notices go to the merchant for review; "
                "the agent never sends legal communication itself"
                if stage == "legal" else
                "this action carries no stage — not a legal-stage notice"
                if stage is None else
                f"stage {stage!r} is not a legal-stage notice"
            ),
        ))
        window = entity.touches if debtor_touches is None else debtor_touches
        recent = [t for t in window if (now - t).days < TOUCH_WINDOW_DAYS]
        scope = "this entity" if debtor_touches is None else "this debtor's entities"
        checks.append(BoundsCheck(
            name="max_touches_per_week",
            passed=len(recent) < MAX_TOUCHES_PER_WEEK,
            detail=(
                f"touches in the last {TOUCH_WINDOW_DAYS} days across {scope}: "
                f"{len(recent)}/{MAX_TOUCHES_PER_WEEK} (limit {MAX_TOUCHES_PER_WEEK})"
            ),
        ))

    return checks


def transition(entity: EntityState, event_type: str, payload: dict[str, Any], now: dt.datetime) -> EntityState:
    """Pure function: (state, event) -> next state. Never raises on an
    unrecognized event_type — an unknown event is a no-op besides the step
    count, so a malformed/unexpected input can never wedge the machine."""
    entity = entity.model_copy(deep=True)
    entity.step_count += 1

    if entity.state in TERMINAL_STATES:
        # THE ONE EXCEPTION (see HUMAN_RESOLUTION_EVENT above): a human closing
        # out a handoff/dispute they were handed. Every other event, from every
        # other source, leaves a terminal state exactly where it is.
        if event_type == HUMAN_RESOLUTION_EVENT and entity.state in HUMAN_RESOLVABLE_STATES:
            resolved = HUMAN_RESOLUTIONS.get(str(payload.get("resolution")))
            if resolved is not None:
                entity.state = resolved
        return entity  # dead end reached; nothing else moves it further

    if event_type == "dispute_raised":
        entity.state = "DISPUTED"
        return entity

    if event_type == "invoice_triaged" and entity.state == "NEW":
        entity.state = "TRIAGED"
    elif event_type == "outreach_sent" and entity.state in ("TRIAGED", "ENGAGED", *ESCALATE_STATES, "AT_RISK"):
        if entity.state == "TRIAGED":
            entity.state = "ENGAGED"
    elif event_type == "extraction_received":
        entity.invoice_amount_inr = payload.get("invoice_amount_inr", entity.invoice_amount_inr)
        entity.state = "PROMISED"
    elif event_type == "cart_abandoned" and entity.state == "NEW" and not payload.get("reserve_active"):
        # Scene 2 cause -> instrument routing (master doc §3.3). A timed or
        # trust-hesitant cause carries a capturable commitment, so it lands on
        # PROMISED exactly like a Scene-1 timed extraction does — a SEPARATE
        # `mandate_offer_requested` event (mirroring `_offer_instrument`)
        # still has to arrive before it becomes MANDATED. Every other cause
        # (friction/price_shock/comparison/unknown) never enters that
        # negotiation at all: "friction path -> NO discount, NO mandate" is
        # worded as a direct routing rule, not a refusal to reach one.
        #
        # `reserve_active` carts are excluded here on purpose: master doc §8.6
        # Tier-0 is a SEPARATE, higher-priority pre-check on the very next
        # `payment_failed` event, and it needs these entities to still be NEW
        # when it runs — routing them to LINKED/PROMISED first would dispatch
        # a real link/mandate before Tier-0 ever gets a chance to short-
        # circuit silently.
        cause = payload.get("cause")
        entity.state = "PROMISED" if cause in ("timing", "trust") else "LINKED"
    elif event_type == "mandate_offer_requested" and entity.state == "PROMISED":
        entity.state = "MANDATED"
    elif event_type == "mandate_refused":
        entity.mandate_refused = True
        entity.state = "LINKED"
    elif event_type == "mandate_confirmed" and entity.state == "MANDATED":
        pass  # stays MANDATED, awaiting execution
    elif event_type == "mandate_execute_success":
        entity.state = "KEPT"
    elif event_type == "mandate_execute_failed":
        # Debit-failure taxonomy (2026-08-30, master doc's own recovery-
        # hierarchy reasoning extended to WHY a debit bounced): a failed
        # debit is NOT automatically a broken promise. See
        # engine/schemas.py's `DebitFailureReason` docstring and
        # tracking/AI_JUDGMENT.md for the full per-reason argument this
        # branch implements. `reason` absent (or unrecognized) falls back to
        # the ORIGINAL undifferentiated behavior below — every caller before
        # this feature existed, and any manual/test event that omits it,
        # keeps working exactly as before.
        reason = payload.get("reason")
        if reason == "bank_downtime":
            pass  # not the debtor's fault, not even a real attempt: no state
                  # change, no retry spent, no trust move — WorldRunner
                  # re-schedules the same execution on the same rail.
        elif reason == "account_closed_frozen":
            # The rail itself is dead. No retry regardless of remaining
            # budget — retrying a closed/frozen account cannot ever
            # succeed — straight to the link fallback, no escalation (this
            # is an infrastructure fact, not a willingness signal) and no
            # future mandate re-offer (mirrors the post-refusal bound).
            entity.mandate_refused = True
            entity.state = "LINKED"
        elif reason == "mandate_revoked":
            # A genuine willingness signal — the debtor killed the standing
            # instruction before this debit. Skips the AT_RISK grace
            # entirely (there is nothing to "retry" against a revoked
            # mandate) and escalates immediately; also blocks any future
            # mandate re-offer, same reasoning as an explicit refusal.
            entity.mandate_refused = True
            entity = _escalate(entity)
        elif entity.retry_count < RETRY_ON_EXECUTION_FAILURE:
            # insufficient_funds / amount_exceeds_limit / no reason given:
            # a timing or sizing problem, not willingness — give the one
            # allowed retry exactly as before.
            entity.retry_count += 1
            entity.state = "AT_RISK"
        else:
            # Retry exhausted. FIXED 2026-08-30 (found while testing this
            # packet, tracking/BUILD_LOG.md same date): this branch used to
            # read `entity.state = "LINKED"; entity = _escalate(entity)` —
            # but `_escalate()` unconditionally OVERWRITES `.state` again, so
            # the "LINKED" assignment was pure dead code and this path had
            # NEVER actually produced a link, contradicting master doc
            # §3.5's own jump-back matrix verbatim: "same-day polite retry
            # x1 -> payment link -> ladder resumes at current stage." No
            # existing test pinned the old (wrong) ESCALATE_1 behavior, so
            # nothing relied on the bug. `escalate_stage` is deliberately
            # left untouched here — the ladder position "resumes" from
            # wherever it already was if a LATER event escalates further,
            # rather than this fallback secretly advancing it on its own.
            entity.state = "LINKED"
    elif event_type == "promise_kept":
        entity.state = "KEPT"
    elif event_type == "promise_broken":
        entity.renegotiation_count += 1
        entity = _escalate(entity)
    elif event_type == "delivery_rejected":
        entity.state = "CLEAN_LOSS"  # Scene 2 delivery-secured mandate revoke branch, master doc §3.3
    elif event_type == "escalation_exhausted":
        entity.state = "HUMAN_HANDOFF"

    if entity.step_count > HARD_STEP_CAP and entity.state not in TERMINAL_STATES:
        entity.state = "HUMAN_HANDOFF"

    return entity


def _escalate(entity: EntityState) -> EntityState:
    # Read the current stage from `state` itself when already escalating,
    # rather than trusting `escalate_stage` alone — keeps this correct even
    # if a caller ever constructs/restores an EntityState where the two
    # fields aren't already in lockstep (they normally only move together,
    # via this function).
    current_stage = ESCALATE_STATES.index(entity.state) + 1 if entity.state in ESCALATE_STATES else entity.escalate_stage
    next_stage = current_stage + 1
    entity.escalate_stage = next_stage
    if next_stage > MAX_ESCALATE_STAGE:
        entity.state = "HUMAN_HANDOFF"
    else:
        entity.state = ESCALATE_STATES[next_stage - 1]
    return entity
