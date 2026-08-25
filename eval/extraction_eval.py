"""Extraction accuracy vs ground truth (BUILD.md Day 4 gate: >=85% level
accuracy). Blocked on ANTHROPIC_API_KEY until Phase B — everything except
the actual API call is ready now: run `python -m eval.extraction_eval` the
moment the key is set.
"""

import json
from collections import defaultdict
from pathlib import Path

from engine.perception.extractor import extract_promise
from engine.schemas import Message

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversations"
GROUND_TRUTH = ROOT / "data" / "ground_truth.json"
METRICS_DIR = ROOT / "metrics"
LEVELS = ["L1", "L2", "L3", "L4", "L5"]


def _load_threads() -> list[dict]:
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(CONV_DIR.glob("*.json"))]


def run() -> dict:
    ground_truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))["messages"]
    threads = _load_threads()

    predictions: dict[str, str] = {}
    truths: dict[str, str] = {}

    for thread in threads:
        messages = [Message.model_validate(m) for m in thread["messages"]]
        history: list[Message] = []
        for msg in messages:
            history.append(msg)
            if msg.direction != "in" or msg.id not in ground_truth:
                continue
            extraction = extract_promise(msg, history)
            predictions[msg.id] = extraction.level
            truths[msg.id] = ground_truth[msg.id]["level"]

    return _score(predictions, truths)


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
        "gate": 0.85,
        "passes_gate": overall_accuracy >= 0.85,
        "per_level": per_level,
        "predictions": predictions,
        "truths": truths,
    }


def main() -> None:
    result = run()
    METRICS_DIR.mkdir(exist_ok=True)
    (METRICS_DIR / "extraction_accuracy.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"Extraction accuracy: {result['overall_accuracy']:.1%} on n={result['n']} "
          f"(gate: {result['gate']:.0%}, {'PASS' if result['passes_gate'] else 'FAIL'})")
    for level, stats in result["per_level"].items():
        p = f"{stats['precision']:.2f}" if stats["precision"] is not None else "n/a"
        r = f"{stats['recall']:.2f}" if stats["recall"] is not None else "n/a"
        print(f"  {level}: precision={p} recall={r} support={stats['support']}")


if __name__ == "__main__":
    main()
