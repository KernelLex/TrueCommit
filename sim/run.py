"""CLI: `python -m sim.run --days 45 --seed 42` (BUILD.md Day 1-2 acceptance:
two runs with the same seed must produce byte-identical output).

Scope note: this is the STANDALONE simulator — clock + frozen personas
generating a deterministic event stream. It uses a simplified escalation
cadence and a simplified renegotiation-cap check as stand-ins so the sim can
run end-to-end today; the AUTHORITATIVE bounds/transitions live in
engine/judgment/state_machine.py (Day 5) and are tested independently there.
Wiring this simulator's output through the real judgment layer + real
perception is later integration work (Phase B/C+), not this file's job.

Cart handling (Scene 2) is a single day-0 `cart_abandoned` event per cart —
Scene 2 depth is lower priority than Scene 1 per the CLAUDE.md cut order.
"""

import argparse
import json
import random
import sys
from pathlib import Path

import datetime as dt

from data.generate import DEBTOR_BY_ID, TODAY
from engine.schemas import Event
from sim.clock import VirtualClock
from sim.personas import decide_mandate_move, decide_reply_move, keeps_promise, mandate_executes

ROOT = Path(__file__).resolve().parent.parent
SIM_EPOCH = dt.datetime.combine(TODAY, dt.time(hour=9, minute=0))
MANDATE_CAP = 100_000
RENEGOTIATION_CAP = 2
TOUCH_SCHEDULE = [(0, "gentle"), (7, "gentle"), (14, "firm"), (21, "firm"), (30, "formal")]
FIRM_DUE_OFFSET = (3, 7)       # days out, inclusive range
CONDITIONAL_DUE_OFFSET = (7, 14)
MANDATE_EXECUTE_OFFSET = 2     # days after confirmation


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


class EventLog:
    def __init__(self) -> None:
        self._n = 0
        self.events: list[dict] = []

    def emit(self, day: int, type_: str, entity_id: str, payload: dict) -> dict:
        self._n += 1
        event = Event(
            event_id=f"E-{self._n:04d}", type=type_, entity_id=entity_id,
            payload=payload, ts=SIM_EPOCH + dt.timedelta(days=day),
        )
        ev = json.loads(event.model_dump_json())
        ev["day"] = day  # convenience for the dashboard timeline; not part of the Event contract
        self.events.append(ev)
        return ev


class InvoiceSim:
    """Tracks one invoice's simulated recovery state across the run."""

    def __init__(self, invoice: dict) -> None:
        self.invoice = invoice
        self.debtor = DEBTOR_BY_ID[invoice["debtor_id"]]
        self.persona = self.debtor["persona"]
        self.status = "active"   # active | promised | kept | disputed | human_handoff
        self.broken_count = 0

    def run_touch(self, clock: VirtualClock, log: EventLog, rng: random.Random, day: int, stage: str) -> list[dict]:
        if self.status in ("kept", "disputed", "human_handoff", "promised"):
            return []  # terminal, or a promise/mandate is already pending resolution

        if self.broken_count > RENEGOTIATION_CAP:
            log.emit(day, "human_handoff", self.invoice["id"], {"reason": "renegotiation_cap_exceeded", "broken_count": self.broken_count})
            self.status = "human_handoff"
            return []

        offer_mandate = (
            stage in ("firm", "formal")
            and self.debtor["enach_familiar"]
            and self.invoice["amount_inr"] <= MANDATE_CAP
        )

        if offer_mandate:
            log.emit(day, "mandate_offered", self.invoice["id"], {"stage": stage, "amount_inr": self.invoice["amount_inr"]})
            move = decide_mandate_move(rng, self.persona)
            log.emit(day, "debtor_reply", self.invoice["id"], {"stage": stage, "move": move, "context": "mandate_offer"})
            if move == "confirm_mandate":
                exec_day = day + MANDATE_EXECUTE_OFFSET
                self.status = "promised"
                clock.schedule(exec_day, lambda d: self._mandate_execute(log, rng, d))
                return []
            if move == "ignore":
                return []  # falls through to next scheduled touch, same as silence
            # refuse_but_promise: falls through to a firm promise below

        log.emit(day, "outreach_sent", self.invoice["id"], {"stage": stage})
        move = decide_reply_move(rng, self.persona, stage) if not offer_mandate else "promise_firm"
        log.emit(day, "debtor_reply", self.invoice["id"], {"stage": stage, "move": move})

        if move == "dispute":
            log.emit(day, "dispute_raised", self.invoice["id"], {"stage": stage})
            self.status = "disputed"
        elif move == "promise_firm":
            due = day + rng.randint(*FIRM_DUE_OFFSET)
            log.emit(day, "promise_made", self.invoice["id"], {"level": "firm", "due_day": due})
            self.status = "promised"
            clock.schedule(due, lambda d: self._promise_due(log, rng, d))
        elif move == "promise_conditional":
            due = day + rng.randint(*CONDITIONAL_DUE_OFFSET)
            log.emit(day, "promise_made", self.invoice["id"], {"level": "conditional", "due_day": due})
            self.status = "promised"
            clock.schedule(due, lambda d: self._promise_due(log, rng, d))
        # promise_vague / silence: no trackable commitment, stays active for next touch
        return []

    def _promise_due(self, log: EventLog, rng: random.Random, day: int) -> list[dict]:
        if self.status != "promised":
            return []
        if keeps_promise(rng, self.persona):
            log.emit(day, "promise_kept", self.invoice["id"], {})
            self.status = "kept"
        else:
            log.emit(day, "promise_broken", self.invoice["id"], {})
            self.broken_count += 1
            self.status = "active"
        return []

    def _mandate_execute(self, log: EventLog, rng: random.Random, day: int) -> list[dict]:
        if self.status != "promised":
            return []
        if mandate_executes(rng, self.persona):
            log.emit(day, "mandate_execute_success", self.invoice["id"], {"amount_inr": self.invoice["amount_inr"]})
            self.status = "kept"
        else:
            log.emit(day, "mandate_execute_failed", self.invoice["id"], {})
            self.broken_count += 1
            self.status = "active"
        return []


def run(days: int, seed: int) -> list[dict]:
    invoices = sorted(_load(ROOT / "data" / "invoices.json"), key=lambda r: r["id"])
    carts = sorted(_load(ROOT / "data" / "carts.json"), key=lambda r: r["id"])

    rng = random.Random(seed)
    log = EventLog()
    clock = VirtualClock(start_day=0)

    sims = {inv["id"]: InvoiceSim(inv) for inv in invoices if inv["status"] in ("overdue", "open")}
    for inv_id, sim in sims.items():
        for offset, stage in TOUCH_SCHEDULE:
            clock.schedule(offset, lambda d, s=sim, st=stage: s.run_touch(clock, log, rng, d, st))

    for cart in carts:
        log.emit(0, "cart_abandoned", cart["id"], {
            "drop_stage": cart["drop_stage"], "drop_signals": cart["drop_signals"],
            "reserve_active": cart["reserve_active"], "amount_inr": cart["amount_inr"],
        })

    clock.advance(days)
    return log.events


def main() -> None:
    parser = argparse.ArgumentParser(description="Promise Keeper simulator")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default=None, help="write JSON event log here instead of stdout")
    args = parser.parse_args()

    events = run(args.days, args.seed)
    output = json.dumps(events, indent=2)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output + "\n")

    counts: dict[str, int] = {}
    for ev in events:
        counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    print(f"# {len(events)} events over {args.days} days (seed={args.seed}): {counts}", file=sys.stderr)


if __name__ == "__main__":
    main()
