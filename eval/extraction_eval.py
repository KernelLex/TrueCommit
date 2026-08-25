"""Extraction accuracy vs hand labels (BUILD.md Day 4 gate: >=85% level accuracy).

Runs against any registered perception provider::

    python -m eval.extraction_eval                      # $PK_PERCEPTION_PROVIDER or heuristic
    python -m eval.extraction_eval --provider heuristic # free, offline, no key needed
    python -m eval.extraction_eval --provider anthropic # needs ANTHROPIC_API_KEY

Writes `metrics/extraction_accuracy_{provider}.json` so provider numbers sit
side by side instead of overwriting each other — the rules baseline is what
makes an LLM's number mean something.

`--provider oracle` is REFUSED (see eval/provider_cli.py): the oracle replays
the very labels this scores.

Only the LEVEL is gated, per BUILD.md. Amount/date agreement is reported
alongside as diagnostics — a provider that gets the level right by guessing an
amount is worth knowing about, and CLAUDE.md law 1 means a wrong amount here
still cannot become a wrong debit.
"""

import argparse
import datetime as dt
import json
from pathlib import Path

from engine.perception.extractor import extract_promise
from engine.perception.providers import get_provider
from engine.schemas import Message
from eval.provider_cli import CircularEvalRefused, add_provider_arg, apply_cache_flag, check_provider

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversations"
GROUND_TRUTH = ROOT / "data" / "ground_truth.json"
METRICS_DIR = ROOT / "metrics"
LEVELS = ["L1", "L2", "L3", "L4", "L5"]
GATE = 0.85

IN_SAMPLE_PROVIDERS = {"heuristic"}
IN_SAMPLE_NOTE = (
    "Rules were authored with visibility of the full hand-labelled set (no held-out "
    "split), so this is an IN-SAMPLE upper bound on this dataset, not a generalisation "
    "estimate. Quote it with that caveat attached."
)


def _load_threads() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CONV_DIR.glob("*.json"))]


def _iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


def run(provider: str | None = None) -> dict:
    name = check_provider(provider)
    get_provider(name)  # fail fast on an unknown/unconfigured provider

    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["messages"]
    threads = _load_threads()

    predictions: dict[str, str] = {}
    truths: dict[str, str] = {}
    amount_hits = date_hits = 0
    mismatches: list[dict] = []

    for thread in threads:
        messages = [Message.model_validate(m) for m in thread["messages"]]
        history: list[Message] = []
        for msg in messages:
            history.append(msg)
            if msg.direction != "in" or msg.id not in ground_truth:
                continue
            extraction = extract_promise(msg, history, provider=name)
            truth = ground_truth[msg.id]
            predictions[msg.id] = extraction.level
            truths[msg.id] = truth["level"]
            amount_hits += extraction.amount_inr == truth["amount_inr"]
            date_hits += _iso(extraction.date) == truth["date"]
            if extraction.level != truth["level"]:
                mismatches.append({
                    "message_id": msg.id, "predicted": extraction.level,
                    "truth": truth["level"], "text": msg.text,
                })

    result = _score(predictions, truths)
    n = result["n"] or 1
    result.update({
        "provider": name,
        "amount_agreement": round(amount_hits / n, 4),
        "date_agreement": round(date_hits / n, 4),
        "mismatches": mismatches,
    })
    if name in IN_SAMPLE_PROVIDERS:
        result["in_sample"] = True
        result["in_sample_note"] = IN_SAMPLE_NOTE
    return result


def _score(predictions: dict[str, str], truths: dict[str, str]) -> dict:
    correct = sum(1 for mid in truths if predictions.get(mid) == truths[mid])
    overall_accuracy = correct / len(truths) if truths else 0.0

    per_level = {}
    for level in LEVELS:
        tp = sum(1 for mid in truths if truths[mid] == level and predictions.get(mid) == level)
        fp = sum(1 for mid in truths if truths[mid] != level and predictions.get(mid) == level)
        fn = sum(1 for mid in truths if truths[mid] == level and predictions.get(mid) != level)
        precision = tp / (tp + fp) if (tp + fp) else None
        recall = tp / (tp + fn) if (tp + fn) else None
        support = sum(1 for mid in truths if truths[mid] == level)
        per_level[level] = {"precision": precision, "recall": recall, "support": support}

    return {
        "overall_accuracy": overall_accuracy,
        "n": len(truths),
        "gate": GATE,
        "passes_gate": overall_accuracy >= GATE,
        "per_level": per_level,
        "predictions": predictions,
        "truths": truths,
    }


def metrics_path(provider: str) -> Path:
    return METRICS_DIR / f"extraction_accuracy_{provider}.json"


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

    print(f"Extraction accuracy [{result['provider']}]: {result['overall_accuracy']:.1%} "
          f"on n={result['n']} (gate: {result['gate']:.0%}, "
          f"{'PASS' if result['passes_gate'] else 'FAIL'})")
    print(f"  {'level':<6} {'precision':>9} {'recall':>7} {'support':>8}")
    for level, stats in result["per_level"].items():
        p = f"{stats['precision']:.2f}" if stats["precision"] is not None else "n/a"
        r = f"{stats['recall']:.2f}" if stats["recall"] is not None else "n/a"
        print(f"  {level:<6} {p:>9} {r:>7} {stats['support']:>8}")
    print(f"  amount agreement: {result['amount_agreement']:.1%}   "
          f"date agreement: {result['date_agreement']:.1%}   (diagnostic, not gated)")
    for m in result["mismatches"]:
        print(f"  MISS {m['message_id']}: predicted={m['predicted']} truth={m['truth']} | {m['text'][:70]}")
    if result.get("in_sample"):
        print(f"  NOTE: {IN_SAMPLE_NOTE}")
    print(f"  wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
