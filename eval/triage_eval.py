"""Triage accuracy vs ground truth (BUILD.md Day 3 gate: >=90%). Blocked on
ANTHROPIC_API_KEY until Phase B — run `python -m eval.triage_eval` once the
key is set.
"""

import json
from pathlib import Path

from engine.perception.triage import triage_invoice
from engine.schemas import Invoice, Message

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversations"
METRICS_DIR = ROOT / "metrics"


def _thread_messages_by_invoice() -> dict[str, list[Message]]:
    by_invoice: dict[str, list[Message]] = {}
    for f in sorted(CONV_DIR.glob("*.json")):
        thread = json.loads(f.read_text(encoding="utf-8"))
        inv_id = thread["invoice_id"]
        by_invoice.setdefault(inv_id, []).extend(Message.model_validate(m) for m in thread["messages"])
    return by_invoice


def run() -> dict:
    invoices = [Invoice.model_validate(i) for i in json.loads((ROOT / "data" / "invoices.json").read_text(encoding="utf-8"))]
    ground_truth = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))["invoices"]
    thread_by_invoice = _thread_messages_by_invoice()

    correct = 0
    total = 0
    mismatches = []
    for invoice in invoices:
        truth = ground_truth.get(invoice.id, {}).get("cause")
        if truth is None:
            continue
        total += 1
        predicted = triage_invoice(invoice, thread_by_invoice.get(invoice.id, [])).cause
        if predicted == truth:
            correct += 1
        else:
            mismatches.append({"invoice_id": invoice.id, "predicted": predicted, "truth": truth})

    accuracy = correct / total if total else 0.0
    return {"accuracy": accuracy, "n": total, "gate": 0.90, "passes_gate": accuracy >= 0.90, "mismatches": mismatches}


def main() -> None:
    result = run()
    METRICS_DIR.mkdir(exist_ok=True)
    (METRICS_DIR / "triage_accuracy.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Triage accuracy: {result['accuracy']:.1%} on n={result['n']} "
          f"(gate: {result['gate']:.0%}, {'PASS' if result['passes_gate'] else 'FAIL'})")
    for m in result["mismatches"]:
        print(f"  MISS {m['invoice_id']}: predicted={m['predicted']} truth={m['truth']}")


if __name__ == "__main__":
    main()
