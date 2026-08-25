"""Oracle provider — replays `data/ground_truth.json`. DEMO BEATS ONLY.

WHY IT EXISTS
-------------
A scripted demo beat ("watch the L1 extraction fire on Acme's message") must
not depend on whichever provider happens to be configured, or on a network
call, while it is being screen-recorded. The oracle makes those beats
deterministic by replaying the hand labels.

WHY IT IS NOT A RESULT
----------------------
Scoring the oracle against ground truth measures nothing — it is the ground
truth. Both evals REFUSE `--provider oracle` by name for exactly that reason
(see `eval/extraction_eval.py`). If you ever see an oracle number quoted as
accuracy, it is a bug or a lie; there is no third option. Confidence is
hard-coded to 1.0 to make oracle output obvious in any audit trail.

It raises on an unknown entity rather than falling back to a guess: silently
degrading to a different provider mid-demo would be worse than a loud stop.
"""

import datetime as dt
import json
from functools import lru_cache
from pathlib import Path

from engine.perception.providers import PerceptionProvider
from engine.schemas import Cart, CartCause, Extraction, Invoice, InvoiceCause, Message

GROUND_TRUTH_PATH = Path(__file__).resolve().parents[3] / "data" / "ground_truth.json"


class OracleNotLabelled(KeyError):
    """The entity has no hand label — the oracle cannot invent one."""


@lru_cache(maxsize=1)
def _ground_truth(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"oracle provider needs hand labels at {p} — run `python -m data.generate` first"
        )
    return json.loads(p.read_text(encoding="utf-8"))


class OracleProvider(PerceptionProvider):
    name = "oracle"

    uses_cache = False
    """Replaying a static file is already instant; a cache would only add a
    way for it to go stale."""

    is_replay = True
    """Flag anything downstream (dashboard badge, audit entry) can read to
    label oracle output as replay rather than inference."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or GROUND_TRUTH_PATH

    def identity(self) -> str:
        return f"{self.name}:{self.path}"

    def _labels(self, section: str) -> dict:
        return _ground_truth(str(self.path)).get(section, {})

    def _require(self, section: str, entity_id: str, kind: str) -> dict:
        labels = self._labels(section)
        if entity_id not in labels:
            raise OracleNotLabelled(
                f"{entity_id} has no {kind} ground-truth label in {self.path}. The oracle "
                f"provider replays hand labels only — use provider 'heuristic' (or an LLM "
                f"provider) for entities outside the labelled set."
            )
        return labels[entity_id]

    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        label = self._require("messages", message.id, "extraction")
        raw_date = label.get("date")
        return Extraction(
            message_id=message.id,
            level=label["level"],
            amount_inr=label.get("amount_inr"),
            date=dt.date.fromisoformat(raw_date) if raw_date else None,
            condition=label.get("condition"),
            confidence=1.0,
        )

    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        label = self._require("invoices", invoice.id, "triage")
        return InvoiceCause(
            invoice_id=invoice.id, cause=label["cause"], confidence=1.0,
            evidence=["ground-truth replay (oracle provider) — not an inference"],
        )

    def _cart_cause(self, cart: Cart) -> CartCause:
        label = self._require("carts", cart.id, "cart cause")
        return CartCause(
            cart_id=cart.id, cause=label["cause"], confidence=1.0,
            evidence=["ground-truth replay (oracle provider) — not an inference"],
        )


def build() -> OracleProvider:
    return OracleProvider()
