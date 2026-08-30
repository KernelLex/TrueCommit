"""Adversarial red-team suite (packet 3, 2026-08-30). CLI:
`python -m eval.red_team --seed 42`.

FOUR SYSTEMATIC EXPLOIT PERSONAS, each probing a specific mechanism this
project's own bounds and design decisions create, run against the REAL
`WorldRunner` (not a re-implementation):

  1. dispute-shield      — disputes the very first message, every time.
  2. promise-farmer      — always promises, never keeps a promise, always
                            gives the furthest-out date it can get away with.
  3. serial-refuser      — always refuses a mandate offer, never confirms.
  4. mandate-then-revoke — confirms every mandate offered, then has every
                            execution bounce with reason `mandate_revoked`.

ANTI-CIRCULARITY, stated precisely because it looks at first glance like it
breaks the SAME rule `eval/run_arms.py` states at the top of its own file:
`run_arms.py` never monkeypatches the DECISION FUNCTIONS
(`decide_reply_move`/`decide_mandate_move`/`keeps_promise`/
`mandate_executes`/`debit_failure_reason`) because its whole job is
comparing arms against the SAME frozen debtor population — changing what a
debtor DOES between arms would make the comparison meaningless. This file's
job is the opposite: it deliberately replaces the frozen population with a
SEPARATE, LABELLED, ADVERSARIAL one, exactly the way a security red-team
engagement swaps in a hostile actor instead of a normal user. It answers "if
some FRACTION of the portfolio behaved like a hostile actor, what would it
cost", not "how do our normal debtors behave" — a different question, run
here and only here, monkeypatched-and-restored exactly like
`run_arms.py`'s own sensitivity sweep already does for its one read-only
data override (never left behind, and every function is restored via
`try/finally`).

HONESTY (CLAUDE.md law 8): this is Tier-2, SIMULATED, adversarial-worst-case
analysis, not a real attack ever attempted against this system. Every number
printed is a hypothetical "if the whole portfolio behaved like this" bound,
useful for arguing about design trade-offs, not a claim about real debtors.

MITIGATION STATUS (packet 3's own requirement: mitigate two, honestly
document two you cannot fix without weakening a bound). See README.md's "How
this system can be gamed and what it costs" for the full reasoning:

  1. dispute-shield      — UNFIXABLE without weakening a bound. The freeze
                            IS the compliance fix (master doc: a dispute
                            stops the ladder cold); the zero-touch cost on
                            siblings is what that freeze COSTS, not a bug in it.
  2. promise-farmer      — MITIGATED (new this packet):
                            `state_machine.cap_promise_due_day` /
                            `cap_promise_due_date`, `MAX_PROMISE_HORIZON_DAYS`.
                            See `run_promise_farmer_mitigation_proof()` below
                            for why the ORIGINAL 45-day snapshot cannot show
                            this (60 > 45) and how the proof run does.
  3. serial-refuser      — UNFIXABLE without weakening a bound. Investigated
                            an allocation-layer (`engine/judgment/
                            allocation.py`) refusal-penalty and found it
                            STRUCTURALLY cannot help: `allocation.py` only
                            ever REORDERS which of one debtor's own invoices
                            spends an already-fixed weekly budget, never
                            shrinks the total spent — and `debtor_mandate_
                            refused` is one bool per DEBTOR, identical across
                            every invoice they hold, so there is nothing
                            within a debtor's own invoice set left to
                            discriminate by even for reordering. The cost is
                            the direct, unavoidable price of two compliance
                            commitments this project has already made and
                            will not walk back: `MAX_TOUCHES_PER_WEEK` (a
                            hard bound) and `trust.update_refusal()`'s
                            pending-neutral rule (master doc §3.2 — a refusal
                            must not be punished as if it were bad-faith
                            evidence). See tracking/BUILD_LOG.md 2026-08-30
                            for the investigation in full.
  4. mandate-then-revoke — ALREADY MITIGATED, by Packets 1+2, before this
                            file existed: a `mandate_execute_failed` with
                            reason `mandate_revoked` sets `entity.
                            mandate_refused = True`, which bars the WHOLE
                            debtor from any future `mandate_offer` (see
                            `test_debtor_judgment.py`). `run_
                            mandate_then_revoke()` below is this packet's
                            verification of that existing protection, not a
                            new code change — the "immunity window" it
                            measures (the gap between confirm and the revoke
                            landing) is the one bounded window the exploit
                            still gets, and it is small (single digits of
                            days) precisely because Packets 1+2 already
                            closed the rest.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine.integration.runner as runner_mod  # noqa: E402
from engine.integration.runner import WorldRunner  # noqa: E402
from engine.judgment.state_machine import TERMINAL_STATES  # noqa: E402

DAYS = 45
DEFAULT_SEED = 42


# ---------------------------------------------------------------------------
# The monkeypatch harness — isolated, restored-after, exactly like
# eval/run_arms.py's own sensitivity sweep (see this file's module docstring
# for why overriding FUNCTIONS rather than DATA is the right call here).
# ---------------------------------------------------------------------------


class _Patched:
    """Context manager: monkeypatches named attributes on `runner_mod` (the
    module `WorldRunner` actually calls its persona functions through — an
    `import X from Y` binds a NEW name in the importer, so patching `Y.X`
    would not affect what `WorldRunner` sees) and restores every one of them
    on exit, success or exception."""

    def __init__(self, **overrides):
        self._overrides = overrides
        self._originals: dict[str, object] = {}

    def __enter__(self):
        for name, value in self._overrides.items():
            self._originals[name] = getattr(runner_mod, name)
            setattr(runner_mod, name, value)
        return self

    def __exit__(self, *exc_info):
        for name, original in self._originals.items():
            setattr(runner_mod, name, original)
        return False


def _run(seed: int, **overrides) -> WorldRunner:
    with _Patched(**overrides):
        world = WorldRunner(seed=seed, real_razorpay=False, real_tts=False)
        world.advance(DAYS)
    return world


def _active_value(world: WorldRunner) -> int:
    return sum(world.ledger.entities[eid].invoice_amount_inr for eid in world.active_invoice_ids)


def _recovered(world: WorldRunner) -> int:
    return sum(
        world.ledger.entities[eid].invoice_amount_inr
        for eid in world.active_invoice_ids
        if world.ledger.entities[eid].state == "KEPT"
    )


# ---------------------------------------------------------------------------
# 1. Dispute-shield
# ---------------------------------------------------------------------------


def _always_dispute(rng, persona_id, stage):
    return "dispute"


def run_dispute_shield(seed: int = DEFAULT_SEED) -> dict:
    """Every debtor disputes the very first message they ever receive.

    Recovered rupees is NOT the honest metric here — a debtor who disputes
    100% of the time never converts regardless of whether the debtor-level
    freeze exists (it always ends at DISPUTED or a frozen HUMAN_HANDOFF,
    never KEPT), so "recovered_inr" is ~0 either way and hides what the
    freeze actually costs. The real, measured cost: only ONE invoice per
    debtor ever gets touched and reaches DISPUTED with a real evidence
    packet for a human; every OTHER invoice of that debtor gets ZERO
    touches at all and sits frozen until the idle sweep hands it to
    HUMAN_HANDOFF at day 37+ with no evidence packet and no dispute record
    of its own — a real observability cost (a merchant sees a generic
    handoff, not "this is frozen because a sibling is disputed") layered on
    top of the ~37 extra days of zero autonomous contact law 5's own
    termination guarantee does not by itself prevent."""
    world = _run(seed, decide_reply_move=_always_dispute)
    disputed = [eid for eid in world.active_invoice_ids if world.ledger.entities[eid].state == "DISPUTED"]
    zero_touch_handoffs = [
        eid for eid in world.active_invoice_ids
        if world.ledger.entities[eid].state == "HUMAN_HANDOFF" and len(world.ledger.entities[eid].touches) == 0
    ]
    frozen_value = sum(world.ledger.entities[eid].invoice_amount_inr for eid in zero_touch_handoffs)
    return {
        "exploit": "dispute-shield",
        "active_value_inr": _active_value(world),
        "recovered_inr": _recovered(world),  # ~0, stated for completeness — see docstring for why it's not the point
        "disputed_with_evidence_packet": len(disputed),
        "frozen_zero_touch_handoffs": len(zero_touch_handoffs),
        "frozen_zero_touch_value_inr": frozen_value,
        "debtors_shielded": len(world.ledger.disputed_entities_by_debtor),
    }


# ---------------------------------------------------------------------------
# 2. Promise-farmer
# ---------------------------------------------------------------------------


PROMISE_FARMER_HORIZON_DAYS = 300
"""How far out the exploit's claimed due date is. Not tuned to be
maximally damning — 300 days safely exceeds the 45-day observation window
and the idle sweep's own FINAL_SWEEP_DAY, which is the entire point: any
value bigger than the sweep's own horizon reproduces the exploit."""


def run_promise_farmer(seed: int = DEFAULT_SEED, horizon_days: int = PROMISE_FARMER_HORIZON_DAYS) -> dict:
    """Every debtor makes an otherwise ORDINARY firm/conditional promise
    (unmodified frozen reply-move behavior) but every L1-L3 extraction's
    DATE is overridden to `horizon_days` out — the one field a real
    malicious debtor fully controls just by what they type, and which nothing
    downstream ever sanity-checks against a ceiling (CLAUDE.md law 1: the
    LLM/extractor SEES and SPEAKS the date it was told, it does not invent
    one — but nothing says it has to be a REASONABLE one).

    NOT reachable through the frozen simulation's own templates as shipped
    (`FIRM_DUE_OFFSET`/`CONDITIONAL_DUE_OFFSET` cap out at 14 days, and the
    heuristic parser's own date vocabulary cannot even PRODUCE an
    absolute far-future date from natural text — see
    `tracking/BUILD_LOG.md` 2026-08-30 for why that was checked directly,
    not assumed) — this patches the PERCEPTION LAYER's own `extract()`
    output instead, which is exactly what a genuinely adversarial debtor
    typing an explicit absurd date through an LLM-based extractor (or any
    future, more capable one) WOULD produce. Measures how much of the
    portfolio is STILL non-terminal at day 45 — law 5's guarantee is
    "eventually", not "within any particular window", and nothing currently
    stops "eventually" from being arbitrarily large.

    RESTORE-ON-EXIT, and why it matters more here than it looks: `world.
    provider` is NOT a fresh object owned by this `WorldRunner` instance —
    `engine.perception.providers.get_provider()` memoises one instance PER
    PROVIDER NAME at module scope, so every `WorldRunner` created in the same
    process (real code, every other red-team run, every `eval/run_arms.py`
    call, every test) shares the identical provider object. An earlier
    version of this function assigned `world.provider.extract =
    _farmed_extract` and never put it back — invisible when this script only
    ever ran as its own fresh CLI process, but a real, found-by-measuring bug
    the moment a pytest suite calls this function next to anything else that
    also builds a `WorldRunner` in the same process (tracking/BUILD_LOG.md
    2026-08-30): every extraction system-wide, for the rest of that process,
    would silently keep farming a far-future date, corrupting unrelated
    runs' numbers. `try/finally` here restores the shared object exactly
    like `_Patched` already does for `runner_mod`'s attributes."""
    world = WorldRunner(seed=seed, real_razorpay=False, real_tts=False)
    original_extract = world.provider.extract
    far_future = world.now().date() + dt.timedelta(days=horizon_days)

    def _farmed_extract(message, thread_messages):
        extraction = original_extract(message, thread_messages)
        if extraction.level in ("L1", "L2", "L3"):
            extraction = extraction.model_copy(update={"date": far_future})
        return extraction

    world.provider.extract = _farmed_extract
    try:
        world.advance(DAYS)
    finally:
        world.provider.extract = original_extract

    active_value = _active_value(world)
    non_terminal = {
        eid: world.ledger.entities[eid].state
        for eid in world.active_invoice_ids
        if world.ledger.entities[eid].state not in TERMINAL_STATES
    }
    non_terminal_value = sum(world.ledger.entities[eid].invoice_amount_inr for eid in non_terminal)
    return {
        "exploit": "promise-farmer",
        "horizon_days": horizon_days,
        "active_value_inr": active_value,
        "non_terminal_at_day_45_inr": non_terminal_value,
        "non_terminal_count": len(non_terminal),
        "non_terminal_states": non_terminal,
    }


def _identity_cap(claimed_day: int, now_day: int) -> int:
    return claimed_day  # counterfactual pass-through: the pre-mitigation behavior


def run_promise_farmer_mitigation_proof(
    seed: int = DEFAULT_SEED,
    horizon_days: int = PROMISE_FARMER_HORIZON_DAYS,
    proof_window_days: int = 100,
) -> dict:
    """`run_promise_farmer()` above measures non-terminal value at day 45 —
    the SAME snapshot day the other three exploits use, for comparability.
    But `MAX_PROMISE_HORIZON_DAYS = 60` is chosen to be LARGER than 45 (see
    that constant's own docstring — 60 is picked against the ladder's and
    sweep's cadence, not against this script's snapshot day), so a CAPPED
    promise is legitimately still pending at day 45, same as an uncapped one
    would be. Day-45 non-terminal count cannot, by construction, show the
    mitigation's effect — a 45-day window is too short to see a 60-day-out
    resolution land.

    What the cap actually buys is "resolves within a FIXED, finite time",
    not "resolves inside this particular window" — proven here by running
    long enough (`proof_window_days`, comfortably past the 60-day ceiling)
    to see the difference directly: WITH today's real code (the cap active,
    unpatched), every farmed invoice must resolve by ~day
    (booked_day + MAX_PROMISE_HORIZON_DAYS + a short escalation tail) at the
    latest. WITHOUT it (`cap_promise_due_day` patched back to a pass-through,
    reproducing exactly what shipped before this packet), a debtor who
    claimed `horizon_days` out is still that far out — non-terminal count is
    UNCHANGED even after `proof_window_days`, because nothing ever moved its
    schedule closer. Same monkeypatch-and-restore harness as the rest of this
    file, patched onto `runner_mod` specifically because that is the name
    `WorldRunner._due_day()` actually calls through (see this file's own
    module docstring on `from X import Y` binding a new name in the
    importer)."""
    def _farm_and_advance(world: WorldRunner, days: int) -> None:
        """Same shared-singleton-provider hazard as `run_promise_farmer()`
        above (see its docstring) — restored via try/finally so this
        function never leaks a farmed `extract` into whatever `WorldRunner`
        runs next in the same process."""
        original_extract = world.provider.extract
        far_future = world.now().date() + dt.timedelta(days=horizon_days)

        def _farmed_extract(message, thread_messages):
            extraction = original_extract(message, thread_messages)
            if extraction.level in ("L1", "L2", "L3"):
                extraction = extraction.model_copy(update={"date": far_future})
            return extraction

        world.provider.extract = _farmed_extract
        try:
            world.advance(days)
        finally:
            world.provider.extract = original_extract

    def _non_terminal_count(world: WorldRunner) -> int:
        return sum(
            1 for eid in world.active_invoice_ids
            if world.ledger.entities[eid].state not in TERMINAL_STATES
        )

    mitigated = WorldRunner(seed=seed, real_razorpay=False, real_tts=False)
    _farm_and_advance(mitigated, proof_window_days)
    mitigated_non_terminal = _non_terminal_count(mitigated)

    with _Patched(cap_promise_due_day=_identity_cap):
        unmitigated = WorldRunner(seed=seed, real_razorpay=False, real_tts=False)
        _farm_and_advance(unmitigated, proof_window_days)
    unmitigated_non_terminal = _non_terminal_count(unmitigated)

    return {
        "exploit": "promise-farmer (mitigation proof)",
        "proof_window_days": proof_window_days,
        "horizon_days_claimed": horizon_days,
        "with_cap_non_terminal_count": mitigated_non_terminal,
        "without_cap_non_terminal_count": unmitigated_non_terminal,
    }


# ---------------------------------------------------------------------------
# 3. Serial-refuser
# ---------------------------------------------------------------------------


def _always_promise_firm(rng, persona_id, stage):
    return "promise_firm"


def _always_refuse_mandate(rng, persona_id):
    return "refuse_but_promise"


def run_serial_refuser(seed: int = DEFAULT_SEED) -> dict:
    """Every debtor promises firmly (so every one of them reaches a mandate
    offer) and refuses every single one. `trust.update_refusal()` is
    pending-neutral by explicit master-doc design (§3.2) — this measures
    what that costs in touches spent chasing a population that has proven,
    definitively, it will never accept an instrument, while its measured
    trust never reflects that."""
    world = _run(
        seed, decide_reply_move=_always_promise_firm, decide_mandate_move=_always_refuse_mandate,
    )
    total_touches = sum(len(e.touches) for e in world.ledger.entities.values())
    mandate_offers = sum(1 for a in world.actions if a.kind == "mandate_offer")
    refusals = sum(1 for e in world.events if e.type == "mandate_refused")
    trust_means = {
        did: t.alpha / (t.alpha + t.beta) for did, t in world.ledger.trust.items()
    }
    return {
        "exploit": "serial-refuser",
        "total_touches_spent": total_touches,
        "mandate_offers_made": mandate_offers,
        "mandate_refusals": refusals,
        "trust_mean_range": (min(trust_means.values()), max(trust_means.values())) if trust_means else (None, None),
        "recovered_inr": _recovered(world),
    }


# ---------------------------------------------------------------------------
# 4. Mandate-then-revoke
# ---------------------------------------------------------------------------


def _always_confirm_mandate(rng, persona_id):
    return "confirm_mandate"


def _always_fails(rng, persona_id):
    return False


def _always_revoked(rng, persona_id):
    return "mandate_revoked"


def run_mandate_then_revoke(seed: int = DEFAULT_SEED) -> dict:
    """Every debtor confirms every mandate offered (the debtor-level
    negotiation-posture signal Packet 2 checks reads this as a genuine
    willingness signal) and then has the execution itself bounce with
    `mandate_revoked` every time — the debtor registered, bought the
    confirm-to-execute window's worth of being left alone, then pulled out."""
    world = _run(
        seed, decide_reply_move=_always_promise_firm, decide_mandate_move=_always_confirm_mandate,
        mandate_executes=_always_fails, debit_failure_reason=_always_revoked,
    )
    confirmed = sum(1 for a in world.ledger.audit if a.detail.get("event") == "mandate_confirmed")
    revoked = sum(1 for e in world.events if e.type == "mandate_execute_failed" and e.payload.get("reason") == "mandate_revoked")
    barred_debtors = sorted(world.ledger.debtor_mandate_refused.keys())
    # the immunity window: days between confirmation and the revoke landing, per entity
    windows = []
    for eid in world.active_invoice_ids:
        confirm_ts = next(
            (a.ts for a in world.ledger.audit if a.entity_id == eid and a.detail.get("event") == "mandate_confirmed"),
            None,
        )
        revoke_ts = next(
            (e.ts for e in world.events if e.entity_id == eid and e.type == "mandate_execute_failed"
             and e.payload.get("reason") == "mandate_revoked"),
            None,
        )
        if confirm_ts and revoke_ts:
            windows.append((revoke_ts - confirm_ts).days)
    return {
        "exploit": "mandate-then-revoke",
        "mandates_confirmed": confirmed,
        "mandates_revoked": revoked,
        "debtors_now_barred_from_any_future_mandate": len(barred_debtors),
        "immunity_window_days": windows,
        "max_immunity_window_days": max(windows) if windows else 0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_all(seed: int = DEFAULT_SEED) -> dict:
    return {
        "tier": "Tier 2 — SIMULATED adversarial worst case. Never real debtor behavior. CLAUDE.md law 8.",
        "seed": seed,
        "days": DAYS,
        "dispute_shield": run_dispute_shield(seed),
        "promise_farmer": run_promise_farmer(seed),
        "promise_farmer_mitigation_proof": run_promise_farmer_mitigation_proof(seed),
        "serial_refuser": run_serial_refuser(seed),
        "mandate_then_revoke": run_mandate_then_revoke(seed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", action="store_true", help="print raw JSON instead of the summary table")
    args = parser.parse_args()

    report = run_all(args.seed)

    if args.json:
        import json
        print(json.dumps(report, indent=2, default=str))
        return

    print(f"Tier 2 (SIMULATED, ADVERSARIAL) - seed={args.seed}, {DAYS} days\n")

    ds = report["dispute_shield"]
    print("1. dispute-shield")
    print(f"   active value in play:              Rs.{ds['active_value_inr']:,}")
    print(f"   recovered (~0 either way):          Rs.{ds['recovered_inr']:,}")
    print(f"   disputed w/ real evidence packet:   {ds['disputed_with_evidence_packet']}")
    print(f"   frozen, ZERO touches, no evidence:  {ds['frozen_zero_touch_handoffs']} invoices, Rs.{ds['frozen_zero_touch_value_inr']:,}")
    print(f"   debtors whose portfolio shielded:   {ds['debtors_shielded']}")

    pf = report["promise_farmer"]
    print("\n2. promise-farmer")
    print(f"   active value in play:              Rs.{pf['active_value_inr']:,}")
    print(f"   still non-terminal at day {DAYS}:      Rs.{pf['non_terminal_at_day_45_inr']:,} ({pf['non_terminal_count']} invoices)")
    pfp = report["promise_farmer_mitigation_proof"]
    print(f"   MITIGATED (MAX_PROMISE_HORIZON_DAYS cap) - proof over {pfp['proof_window_days']} days:")
    print(f"     with cap (real code):    {pfp['with_cap_non_terminal_count']} still non-terminal")
    print(f"     without cap (patched):   {pfp['without_cap_non_terminal_count']} still non-terminal")

    sr = report["serial_refuser"]
    print("\n3. serial-refuser")
    print(f"   total touches spent:        {sr['total_touches_spent']}")
    print(f"   mandate offers made:        {sr['mandate_offers_made']}")
    print(f"   mandate refusals:           {sr['mandate_refusals']}")
    lo, hi = sr["trust_mean_range"]
    print(f"   trust mean range:           {lo:.3f} - {hi:.3f}" if lo is not None else "   trust mean range:           n/a")
    print(f"   recovered anyway:           Rs.{sr['recovered_inr']:,}")

    mr = report["mandate_then_revoke"]
    print("\n4. mandate-then-revoke")
    print(f"   mandates confirmed:                       {mr['mandates_confirmed']}")
    print(f"   mandates revoked at execution:             {mr['mandates_revoked']}")
    print(f"   debtors now barred from ANY future mandate: {mr['debtors_now_barred_from_any_future_mandate']}")
    print(f"   max immunity window observed:              {mr['max_immunity_window_days']} day(s)")


if __name__ == "__main__":
    main()
