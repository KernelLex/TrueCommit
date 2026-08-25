"""Triage accuracy vs hand labels (BUILD.md Day 3 gate: >=90%).

Runs against any registered perception provider::

    python -m eval.triage_eval --provider heuristic   # free, offline
    python -m eval.triage_eval --provider anthropic   # needs ANTHROPIC_API_KEY

Writes `metrics/triage_accuracy_{provider}.json`. `--provider oracle` is
REFUSED — it replays the labels being scored (eval/provider_cli.py).

READ THE SPLIT, NOT JUST THE HEADLINE
-------------------------------------
36 of the 60 invoices have no conversation thread at all. For those, the only
inputs a provider gets are two booleans (`delivery_confirmed`,
`payment_failed_attempt`) which data/generate.py deliberately makes ~10%
noisy, while the label itself was drawn from a persona-weighted distribution
that those booleans cannot express. No provider — rules or LLM — can recover
that; the information is not in the input. So this eval reports accuracy split
by `with_thread` / `no_thread`, plus the best score any classifier could
achieve on the no-thread group given only those two flags (`ceiling`). The
headline number is the honest one to quote; the split is the one that says
where the remaining error actually lives.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from engine.perception.providers import get_provider
from engine.perception.triage import triage_invoice
from engine.schemas import Invoice, Message
from eval.provider_cli import CircularEvalRefused, add_provider_arg, apply_cache_flag, check_provider

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversations"
METRICS_DIR = ROOT / "metrics"
CAUSES = ["payment_failed", "delivery_dispute", "cashflow_delay", "dispute", "non_responsive"]
GATE = 0.90

IN_SAMPLE_PROVIDERS = {"heuristic"}
IN_SAMPLE_NOTE = (
    "Rules were authored with visibility of the full hand-labelled set (no held-out "
    "split), so this is an IN-SAMPLE upper bound on this dataset, not a generalisation "
    "estimate. Quote it with that caveat attached."
)


def _thread_messages_by_invoice() -> dict[str, list[Message]]:
    by_invoice: dict[str, list[Message]] = {}
    for f in sorted(CONV_DIR.glob("*.json")):
        thread = json.loads(f.read_text(encoding="utf-8"))
        inv_id = thread["invoice_id"]
        by_invoice.setdefault(inv_id, []).extend(Message.model_validate(m) for m in thread["messages"])
    return by_invoice


def _flag_ceiling(invoices: list[Invoice], truths: dict[str, str], ids: set[str]) -> float:
    """Best accuracy achievable on `ids` using only the two record flags.

    An oracle that always predicts each (delivery_confirmed, payment_failed_attempt)
    group's most common label. Anything above this on the no-thread group would
    mean the provider read information that is not in its inputs.
    """
    groups: dict[tuple[bool, bool], Counter] = defaultdict(Counter)
    for inv in invoices:
        if inv.id in ids:
            groups[(inv.delivery_confirmed, inv.payment_failed_attempt)][truths[inv.id]] += 1
    best = sum(c.most_common(1)[0][1] for c in groups.values())
    return round(best / len(ids), 4) if ids else 0.0


def run(provider: str | None = None) -> dict:
    name = check_provider(provider)
    get_provider(name)  # fail fast on an unknown/unconfigured provider

    invoices = [Invoice.model_validate(i) for i in json.loads((ROOT / "data" / "invoices.json").read_text(encoding="utf-8"))]
    ground_truth = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))["invoices"]
    thread_by_invoice = _thread_messages_by_invoice()

    truths: dict[str, str] = {}
    predictions: dict[str, str] = {}
    mismatches = []
    with_thread: set[str] = set()

    for invoice in invoices:
        truth = ground_truth.get(invoice.id, {}).get("cause")
        if truth is None:
            continue
        thread = thread_by_invoice.get(invoice.id, [])
        if thread:
            with_thread.add(invoice.id)
        truths[invoice.id] = truth
        predicted = triage_invoice(invoice, thread, provider=name).cause
        predictions[invoice.id] = predicted
        if predicted != truth:
            mismatches.append({
                "invoice_id": invoice.id, "predicted": predicted, "truth": truth,
                "has_thread": bool(thread),
            })

    scored = set(truths)
    no_thread = scored - with_thread
    correct = sum(1 for i in scored if predictions[i] == truths[i])
    accuracy = correct / len(scored) if scored else 0.0

    def subset(ids: set[str]) -> dict:
        hit = sum(1 for i in ids if predictions[i] == truths[i])
        return {"n": len(ids), "correct": hit, "accuracy": round(hit / len(ids), 4) if ids else None}

    per_cause = {}
    for cause in CAUSES:
        tp = sum(1 for i in scored if truths[i] == cause and predictions[i] == cause)
        fp = sum(1 for i in scored if truths[i] != cause and predictions[i] == cause)
        fn = sum(1 for i in scored if truths[i] == cause and predictions[i] != cause)
        per_cause[cause] = {
            "precision": tp / (tp + fp) if (tp + fp) else None,
            "recall": tp / (tp + fn) if (tp + fn) else None,
            "support": sum(1 for i in scored if truths[i] == cause),
        }

    result = {
        "provider": name,
        "accuracy": accuracy,
        "n": len(scored),
        "gate": GATE,
        "passes_gate": accuracy >= GATE,
        "per_cause": per_cause,
        "with_thread": subset(with_thread),
        "no_thread": subset(no_thread),
        "no_thread_flag_only_ceiling": _flag_ceiling(invoices, truths, no_thread),
        "mismatches": mismatches,
        "predictions": predictions,
        "truths": truths,
    }
    if name in IN_SAMPLE_PROVIDERS:
        result["in_sample"] = True
        result["in_sample_note"] = IN_SAMPLE_NOTE
    return result


def metrics_path(provider: str) -> Path:
    return METRICS_DIR / f"triage_accuracy_{provider}.json"


def main(argv: list[str] | None = None) -> int:
    parser = add_provider_arg(argparse.ArgumentParser(description=__doc__.splitlines()[0]))
    args = parser.parse_args(argv)
    apply_cache_flag(args.no_cache)

    try:
        result = run(args.provider)
    except CircularEvalRefused as exc:
        print(exc)
        return 2

    METRICS_DIR.mkdir(exist_ok=True)
    out = metrics_path(result["provider"])
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Triage accuracy [{result['provider']}]: {result['accuracy']:.1%} on n={result['n']} "
          f"(gate: {result['gate']:.0%}, {'PASS' if result['passes_gate'] else 'FAIL'})")
    print(f"  {'cause':<18} {'precision':>9} {'recall':>7} {'support':>8}")
    for cause, stats in result["per_cause"].items():
        p = f"{stats['precision']:.2f}" if stats["precision"] is not None else "n/a"
        r = f"{stats['recall']:.2f}" if stats["recall"] is not None else "n/a"
        print(f"  {cause:<18} {p:>9} {r:>7} {stats['support']:>8}")
    wt, nt = result["with_thread"], result["no_thread"]
    print(f"  with a conversation thread: {wt['correct']}/{wt['n']} = {wt['accuracy']:.1%}")
    print(f"  no thread (flags only):     {nt['correct']}/{nt['n']} = {nt['accuracy']:.1%} "
          f"(flag-only ceiling {result['no_thread_flag_only_ceiling']:.1%})")
    for m in result["mismatches"]:
        thread_note = "" if m["has_thread"] else "  [no thread]"
        print(f"  MISS {m['invoice_id']}: predicted={m['predicted']} truth={m['truth']}{thread_note}")
    if result.get("in_sample"):
        print(f"  NOTE: {IN_SAMPLE_NOTE}")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
