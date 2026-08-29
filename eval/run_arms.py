"""Three-arm comparison: Arm A (silence) / Arm B (generic reminder every 3
days) / Arm C (full Promise Keeper). BUILD.md Day 8 — this is the literal
first sentence of Track 3's bar: "Show measured money recovered across a
batch." CLI: `python -m eval.run_arms --seed 42`.

SCOPE: the 60 invoices / 12 debtors in data/invoices.json (Scene 1, B2B
receivables). Cart/Scene 2 and its Tier-0 reserve-failover mechanic are
OUT of scope here — reserve failover is a cart-only concept (already
separately measured: both reserve carts recover at 0 touches, see
tracking/BUILD_QUALITY.md) and does not apply to an invoice population, so
no "Tier-0 = 0" row appears in this report. Stated once here rather than
silently omitted.

ANTI-CIRCULARITY (CLAUDE.md law 7): all three arms drive the SAME frozen
`sim/personas.py` tables — `decide_reply_move`/`keeps_promise`/
`decide_mandate_move` are never edited or monkeypatched to change WHAT a
persona does, only WHICH messages an arm sends in the first place. Arms A
and B are deliberately simple, self-contained simulations (not the full
WorldRunner) because they have none of Promise Keeper's judgment layer by
definition; Arm C is the real, already-tested `WorldRunner`, unmodified.

HONESTY, per CLAUDE.md law 8: Tier 1 (this whole file) is SIMULATED
recovery — the personas are scripted, not real debtors. Every number this
script prints is labelled Tier 2 in the output; never claim it as
real-world proof.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.generate import DEBTOR_BY_ID, TODAY  # noqa: E402
from engine.integration.runner import WorldRunner  # noqa: E402
from sim import personas as personas_mod  # noqa: E402
from sim.personas import decide_reply_move, keeps_promise  # noqa: E402

DAYS = 45
DEFAULT_SEED = 42
ARM_B_REMINDER_INTERVAL_DAYS = 3
ARM_B_PROMISE_LAG_DAYS = 2  # days between "I'll pay" and the payment actually landing
MANDATE_ACCEPTANCE_SWEEP = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]


def _load_invoices() -> list[dict]:
    return json.loads((ROOT / "data" / "invoices.json").read_text(encoding="utf-8"))


def _date(value) -> dt.date:
    return dt.date.fromisoformat(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------
# Arm A — silence
# ---------------------------------------------------------------------------


def run_arm_a(invoices: list[dict]) -> dict:
    """No outreach, ever. The frozen persona tables (sim/personas.py) define
    how a debtor REACTS to a message — there is no spontaneous, no-message
    self-cure probability defined anywhere in them. Recovering 0% here is a
    consequence of that, stated honestly, not a number invented to make the
    other arms look better (CLAUDE.md law 7): a real Arm A almost certainly
    has some non-zero self-cure rate; this simulation does not model one
    because doing so would mean editing or supplementing the frozen tables,
    which the freeze forbids."""
    total_amount = sum(i["amount_inr"] for i in invoices)
    return {
        "arm": "A_silence",
        "description": "No intervention of any kind for 45 days.",
        "total_invoices": len(invoices),
        "total_amount_inr": total_amount,
        "recovered_invoices": 0,
        "recovered_amount_inr": 0,
        "recovered_pct_by_amount": 0.0,
        "recovered_pct_by_count": 0.0,
        "touches_total": 0,
        "touches_per_recovery": None,
        "dso_days_mean": None,
        "false_escalation_rate": None,
        "cost_per_rupee_recovered": None,
        "note": (
            "0% recovery is a direct, honest consequence of the frozen persona "
            "tables having no self-cure-without-a-message probability defined - "
            "not a claim that real-world silence recovers nothing."
        ),
    }


# ---------------------------------------------------------------------------
# Arm B — generic reminder every 3 days, no judgment layer
# ---------------------------------------------------------------------------


def run_arm_b(invoices: list[dict], seed: int) -> dict:
    """A blind, fixed-cadence reminder: the same flat 'please pay' message
    every 3 days, always at the same tone ("gentle" - no escalation ladder),
    never offering a mandate or a payment link, with no memory of a broken
    promise (it just keeps nagging on the same cadence). This is what "send
    a template reminder" collections tooling looks like without a judgment
    layer - the explicit status-quo baseline the bar asks for.

    Each persona reacts via the SAME `decide_reply_move`/`keeps_promise`
    tables Arm C uses, at the SAME "gentle" stage every time (never firm/
    formal - there is no escalation concept in this arm at all).

    ONE piece of realism this arm keeps, deliberately: it does not re-prompt
    while a promise it already extracted is still pending (not yet due) -
    even the dumbest real reminder tooling doesn't nag someone the day after
    they said "I'll pay Friday." Once that promise resolves (kept, or its
    due date passes unpaid), the blind `ARM_B_REMINDER_INTERVAL_DAYS` cadence
    resumes with no memory of the broken promise and no escalation. This is
    the one guard needed for "generic reminder" to mean the status quo
    rather than an unrealistic every-3-days spam that no real system, however
    unsophisticated, actually runs.
    """
    rng = random.Random(seed)
    total_amount = sum(i["amount_inr"] for i in invoices)
    touches_total = 0
    recovered_amount = 0
    recovered_count = 0
    dso_samples: list[int] = []

    for inv in invoices:
        persona_id = DEBTOR_BY_ID[inv["debtor_id"]]["persona"]
        due = _date(inv["due"])
        paid_day: int | None = None
        touches = 0
        pending_promise_due: int | None = None

        day = 0
        while day < DAYS and paid_day is None:
            if pending_promise_due is not None and day < pending_promise_due:
                day += 1  # waiting on an active promise - no reminder sent, no touch
                continue
            if pending_promise_due is not None and day >= pending_promise_due:
                if keeps_promise(rng, persona_id):
                    paid_day = pending_promise_due
                    break
                pending_promise_due = None  # broken - resume the blind cadence, no escalation

            touches += 1
            move = decide_reply_move(rng, persona_id, "gentle")
            if move in ("promise_firm", "promise_vague", "promise_conditional"):
                pending_promise_due = day + ARM_B_PROMISE_LAG_DAYS
            day += ARM_B_REMINDER_INTERVAL_DAYS

        touches_total += touches
        if paid_day is not None and paid_day <= DAYS:
            recovered_count += 1
            recovered_amount += inv["amount_inr"]
            paid_date = TODAY + dt.timedelta(days=paid_day)
            dso_samples.append((paid_date - due).days)

    return {
        "arm": "B_generic_reminder",
        "description": f"Flat reminder every {ARM_B_REMINDER_INTERVAL_DAYS} days, no escalation, no instrument, no judgment layer.",
        "total_invoices": len(invoices),
        "total_amount_inr": total_amount,
        "recovered_invoices": recovered_count,
        "recovered_amount_inr": recovered_amount,
        "recovered_pct_by_amount": round(100 * recovered_amount / total_amount, 1),
        "recovered_pct_by_count": round(100 * recovered_count / len(invoices), 1),
        "touches_total": touches_total,
        "touches_per_recovery": round(touches_total / recovered_count, 2) if recovered_count else None,
        "dso_days_mean": round(sum(dso_samples) / len(dso_samples), 1) if dso_samples else None,
        "false_escalation_rate": None,  # this arm has no escalation concept - N/A, not zero
        "cost_per_rupee_recovered": None,  # no real messaging cost modeled for a simulated baseline arm
        "note": "No escalation ladder exists in this arm, so 'false-escalation rate' is not applicable (not zero).",
    }


# ---------------------------------------------------------------------------
# Arm C — the real WorldRunner, unmodified
# ---------------------------------------------------------------------------


def run_arm_c(seed: int, mandate_table_override: dict | None = None) -> dict:
    """The real system. `WorldRunner` is used exactly as built and tested
    elsewhere in this repo - nothing here re-implements or approximates it.
    `mandate_table_override`, when given, is applied ONLY for the duration of
    this call via monkeypatching `sim.personas.MANDATE_TABLE` and restored
    immediately after - the frozen file on disk is never touched, and the
    override is read-only data computed by `_scaled_mandate_table` below,
    never a change to WHAT a persona does relative to its own frozen shape,
    only a deliberate what-if sweep of one scalar (packet: three-arm runner).
    """
    original_table = personas_mod.MANDATE_TABLE
    if mandate_table_override is not None:
        personas_mod.MANDATE_TABLE = mandate_table_override
    try:
        world = WorldRunner(seed=seed, real_razorpay=False, real_tts=False)
        world.advance(DAYS)
    finally:
        personas_mod.MANDATE_TABLE = original_table

    ledger = world.ledger
    invoices = world.invoices
    total_amount = sum(inv.amount_inr for inv in invoices.values())

    recovered_amount = 0
    recovered_count = 0
    dso_samples: list[int] = []
    for entity_id, entity in ledger.entities.items():
        if entity_id not in invoices:
            continue  # cart entity, out of this report's scope
        if entity.state == "KEPT":
            recovered_count += 1
            recovered_amount += invoices[entity_id].amount_inr
            kept_promises = [
                p for p in ledger.promises.values()
                if p.invoice_id == entity_id and p.status == "kept"
            ]
            if kept_promises:
                promise = kept_promises[-1]
                dso_samples.append((promise.due - invoices[entity_id].due).days)

    # touches_by_debtor is keyed by debtor_id for invoices but by a distinct
    # CUST-xx customer_id for carts (both share one dict on the ledger) - a
    # blind sum would leak cart-driven touches into this invoice-only scope.
    # Restrict to debtor_ids that actually own at least one invoice.
    invoice_debtor_ids = {inv.debtor_id for inv in invoices.values()}
    touches_total = sum(
        len(t) for debtor_id, t in ledger.touches_by_debtor.items()
        if debtor_id in invoice_debtor_ids
    )

    # False-escalation rate: of every ESCALATE_1+ action the ladder actually
    # sent, what fraction went to an entity that ended up KEPT anyway - i.e.
    # an escalation the entity's own eventual behaviour did not need. This is
    # a measured proxy, not a claim about individual counterfactual intent.
    escalate_actions = [
        a for a in world.actions
        if a.entity_id in invoices and a.params.get("stage") in ("firm", "formal")
    ]
    escalations_to_eventually_kept = [
        a for a in escalate_actions if ledger.entities.get(a.entity_id, None) and ledger.entities[a.entity_id].state == "KEPT"
    ]
    false_escalation_rate = (
        round(100 * len(escalations_to_eventually_kept) / len(escalate_actions), 1)
        if escalate_actions else 0.0
    )

    return {
        "arm": "C_promise_keeper",
        "description": "Full system: triage, promise extraction, trust-weighted escalation, bounds-gated instrument choice.",
        "total_invoices": len(invoices),
        "total_amount_inr": total_amount,
        "recovered_invoices": recovered_count,
        "recovered_amount_inr": recovered_amount,
        "recovered_pct_by_amount": round(100 * recovered_amount / total_amount, 1),
        "recovered_pct_by_count": round(100 * recovered_count / len(invoices), 1),
        "touches_total": touches_total,
        "touches_per_recovery": round(touches_total / recovered_count, 2) if recovered_count else None,
        "dso_days_mean": round(sum(dso_samples) / len(dso_samples), 1) if dso_samples else None,
        "false_escalation_rate": false_escalation_rate,
        "false_escalation_definition": "% of firm/formal-stage escalation sends that went to an entity which ended KEPT anyway (measured proxy, not individual counterfactual certainty)",
        "cost_per_rupee_recovered": None,  # needs a real per-channel cost model (README-stage decision, not this script's job)
        "bound_violations": len(world.bound_violations()),
        "scope_note": (
            "This figure is invoices only (per this report's scope), and will not match the "
            "previously-published Rs.23,36,494 whole-world figure in TRACK_BAR.md/BUILD_QUALITY.md - "
            "that figure additionally includes Scene 2/cart mechanics out of scope for this report: "
            "the 2 Tier-0 reserve-cart recoveries (Rs.1,899 + Rs.5,250 = Rs.7,149) and, since "
            "2026-08-29, the 2 non-reserve carts that recover through a matched instrument (master "
            "doc §3.3: C-05's scheduled mandate + C-07's delivery-secured mandate, Rs.2,499 + "
            "Rs.2,499 = Rs.4,998). Reconciled exactly: "
            "Rs.23,36,494 - Rs.7,149 - Rs.4,998 = Rs.23,24,347."
        ),
    }


# ---------------------------------------------------------------------------
# Sensitivity band: sweep mandate-acceptance rate 10%-60%, Arm C only
# ---------------------------------------------------------------------------


def _scaled_mandate_table(target_confirm_rate: float) -> dict:
    """A read-only, eval-time COPY of `sim.personas.MANDATE_TABLE`, scaled so
    the weighted-by-debtor-count average `confirm_mandate` probability hits
    `target_confirm_rate`. Never mutates the frozen file. Each persona's
    `confirm_mandate` is scaled by a single factor (preserving relative
    persona shape - reliable_promiser still highest, disputer still near
    zero); the removed/added probability mass is taken from/given to
    `ignore` first, spilling into `refuse_but_promise` only if `ignore`
    alone cannot absorb it, since `ignore` represents disengagement while
    `refuse_but_promise` represents an active negotiation signal that
    shouldn't be the first thing this what-if knob touches.
    """
    base = personas_mod.MANDATE_TABLE
    persona_counts: dict[str, int] = {}
    for debtor in DEBTOR_BY_ID.values():
        persona_counts[debtor["persona"]] = persona_counts.get(debtor["persona"], 0) + 1
    total_debtors = sum(persona_counts.values())
    baseline_avg = sum(
        base[p]["confirm_mandate"] * n for p, n in persona_counts.items()
    ) / total_debtors
    scale = target_confirm_rate / baseline_avg if baseline_avg > 0 else 0.0

    scaled: dict[str, dict[str, float]] = {}
    for persona_id, moves in base.items():
        new_confirm = min(1.0, moves["confirm_mandate"] * scale)
        delta = new_confirm - moves["confirm_mandate"]
        ignore = moves.get("ignore", 0.0)
        refuse = moves.get("refuse_but_promise", 0.0)
        # delta > 0: pull from ignore first, then refuse_but_promise
        # delta < 0: give back to ignore first (uncapped upward, it's a probability mass sink)
        ignore_take = min(ignore, delta) if delta > 0 else delta  # can go negative (adds to ignore)
        remainder = delta - ignore_take
        new_ignore = max(0.0, ignore - ignore_take)
        new_refuse = max(0.0, refuse - remainder)
        scaled[persona_id] = {
            "confirm_mandate": round(new_confirm, 4),
            "refuse_but_promise": round(new_refuse, 4),
            "ignore": round(new_ignore, 4),
        }
    return scaled


def run_sensitivity_band(seed: int, rates: list[float] | None = None) -> list[dict]:
    """Re-runs Arm C once per target mandate-acceptance rate. This exists
    because the headline recovery number depends on an acceptance rate this
    project has not measured in the real world (no real debtors have been
    asked to approve a real mandate at scale) - a single point estimate would
    silently bake in whichever value the frozen personas happen to average
    to. A band survives a judge who disagrees with that assumption.
    """
    rates = rates if rates is not None else MANDATE_ACCEPTANCE_SWEEP
    band = []
    for rate in rates:
        scaled_table = _scaled_mandate_table(rate)
        result = run_arm_c(seed, mandate_table_override=scaled_table)
        result["target_mandate_acceptance_rate"] = rate
        band.append(result)
    return band


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=str, default=str(ROOT / "metrics.json"))
    args = parser.parse_args(argv)

    invoices = _load_invoices()

    arm_a = run_arm_a(invoices)
    arm_b = run_arm_b(invoices, seed=args.seed)
    arm_c = run_arm_c(args.seed)
    sensitivity = run_sensitivity_band(args.seed)

    output = {
        "tier": "Tier 2 — SIMULATED recovery against scripted, frozen personas. "
                "Never present these numbers as real-world proof (CLAUDE.md law 8).",
        "scope": f"{len(invoices)} invoices / {len(DEBTOR_BY_ID)} debtors (data/invoices.json), Scene 1 only. "
                 "No Tier-0/reserve-failover row: that mechanic is cart-only (Scene 2), out of scope here.",
        "seed": args.seed,
        "days": DAYS,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "arms": {"A_silence": arm_a, "B_generic_reminder": arm_b, "C_promise_keeper": arm_c},
        "mandate_acceptance_sensitivity_band": sensitivity,
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")

    print(f"Tier 2 (SIMULATED) - {len(invoices)} invoices, seed={args.seed}, {DAYS} days")
    print(f"{'Arm':<20} {'Recovered Rs.':>15} {'% by Rs.':>8} {'% by count':>11} {'Touches/recovery':>17} {'DSO (days)':>11}")
    for arm in (arm_a, arm_b, arm_c):
        print(
            f"{arm['arm']:<20} {arm['recovered_amount_inr']:>15,} {arm['recovered_pct_by_amount']!s:>8} "
            f"{arm['recovered_pct_by_count']!s:>11} {arm['touches_per_recovery']!s:>17} {arm['dso_days_mean']!s:>11}"
        )
    print(f"\nMandate-acceptance sensitivity band ({MANDATE_ACCEPTANCE_SWEEP[0]*100:.0f}%-{MANDATE_ACCEPTANCE_SWEEP[-1]*100:.0f}%):")
    for row in sensitivity:
        print(
            f"  target={row['target_mandate_acceptance_rate']*100:>4.0f}%  "
            f"recovered=Rs.{row['recovered_amount_inr']:>12,} ({row['recovered_pct_by_amount']}%)"
        )
    print(f"\nWritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
