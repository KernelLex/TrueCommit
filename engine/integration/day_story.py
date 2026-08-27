"""Day Story — the read models that make a simulated day legible to a human
(packet P10).

WHAT THIS IS NOT
----------------
It is not a decision layer, a second opinion, or a narrator. Every field it
produces is a re-presentation of something the pipeline already computed and
already wrote down:

  * conversation text            -> `WorldRunner.threads` (the real Messages)
  * what the system decided      -> `Ledger.audit` (the append-only trail)
  * why a bound allowed/refused  -> `Ledger.gate_log` (the checklist recorded
                                    AT gate time, not re-derived now)
  * customer names               -> `data/generate.DEBTOR_BY_ID`
  * trust as of a past day       -> `WorldRunner.day_snapshots` (photographed
                                    at the end of that day)

Nothing in this module invents a sentence, rounds a number, or fills a gap.
Where the data does not exist the field is `None` and carries a `*_note` that
says so in words (CLAUDE.md law 8). Two places that matters most:

  1. **Scene-2 cart customers have no name in the dataset.** `data/carts.json`
     stores `customer_id` and nothing else, so `debtor_name` is `None` and the
     UI shows the id. A plausible-looking business name would be a fabrication
     sitting next to real money.
  2. **Mandate execution is simulated, always.** tracking/TRACK_BAR.md §0: the
     registration link is genuinely real in TEST mode, but this sandbox account
     has UPI disabled and eMandate not enabled, so the hosted approval page
     cannot complete and no token can be charged. Every mandate step here is
     labelled with which of those two it is; see `MANDATE_STEP_NATURE`.

DAY NUMBERING
-------------
`day` is the runner's own day INDEX, the one `WorldRunner._ts(day)` maps to a
timestamp: a fresh runner is at `runner.day == 0` with nothing simulated,
`advance(1)` simulates day index 0 and leaves `runner.day == 1`. So the day a
judge just watched happen is always `runner.day - 1`.
"""

import datetime as dt
import json

from data.generate import DEBTOR_BY_ID
from engine.judgment import state_machine, trust as trust_math
from engine.judgment.ledger import (
    _ESCALATE_ACTION,
    _OUTREACH_ACTION,
    KILL_SWITCH_CHECK,
    GateRecord,
    Ledger,
)
from engine.schemas import AuditEntry, Message
from sim.run import SIM_EPOCH

# The ledger's own action tables are imported rather than restated: the
# guardrail PREVIEW below has to ask "what would this entity's next action be?"
# and the only correct answer is the one the ledger would give. A second copy of
# that mapping here would drift and start previewing checks against an action
# the ladder would never take.

CART_NAME_NOTE = (
    "no business name is stored for this Scene-2 cart customer — data/carts.json "
    "carries a customer_id only, so the id is shown rather than a made-up name"
)

ACCOUNT_GATE_NOTE = (
    "real registration link — approval blocked in this sandbox account "
    "(UPI/eMandate not enabled); continuing simulated below"
)

MANDATE_LIFECYCLE_NOTE = (
    "Registration links are REAL Razorpay TEST-mode objects when PK_REAL_RAZORPAY=1 "
    "and this run's one real-call budget for mandates is unspent. Execution and "
    "revocation are SIMULATED in every run without exception: charging a mandate "
    "needs a token a human authorized through the hosted page, and this test "
    "account has UPI disabled and eMandate not enabled (tracking/TRACK_BAR.md §0)."
)

MONEY_KINDS = {"link", "mandate_offer", "mandate_execute"}

# step name -> (label, nature). `nature` is what the badge in the UI reads:
#   engine_decision    a real, audited decision this system made
#   razorpay_real      a real Razorpay TEST-mode API call happened
#   razorpay_simulated no network call; the URL is this build's own stand-in
#   simulated_persona  the frozen persona table decided this (sim/personas.py)
#   simulated_outcome  the outcome is simulated — see MANDATE_LIFECYCLE_NOTE
MANDATE_STEP_NATURE: dict[str, tuple[str, str]] = {
    "offered": ("Mandate offered", "engine_decision"),
    "offer_blocked": ("Mandate offer blocked by a bound", "engine_decision"),
    "offer_held": ("Mandate offer held for merchant approval", "engine_decision"),
    "offer_created": ("Mandate action created", "engine_decision"),
    "registered": ("Registration link issued", "razorpay_simulated"),
    "debtor_response": ("Debtor response", "simulated_persona"),
    "debtor_confirmed": ("Debtor approved the mandate", "simulated_persona"),
    "debtor_refused": ("Debtor refused the mandate", "simulated_persona"),
    "link_timeout": ("Link never opened — soft refusal", "engine_decision"),
    "execute_attempt": ("Execution attempted", "engine_decision"),
    "executed": ("Executed — invoice recovered", "simulated_outcome"),
    "execute_failed": ("Execution failed", "simulated_outcome"),
    "execute_blocked": ("Execution blocked by a bound", "engine_decision"),
    "revoked": ("Mandate revoked (delivery rejected)", "simulated_outcome"),
}


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def day_of(ts: dt.datetime) -> int:
    """Which simulated day index a timestamp falls in. The runner stamps every
    beat of day N at `SIM_EPOCH + N days`, and the dataset's seed conversations
    carry their own real timestamps, so this is the one rule that places both
    on the same calendar."""
    return (ts - SIM_EPOCH).days


def date_of_day(day: int) -> dt.date:
    return (SIM_EPOCH + dt.timedelta(days=day)).date()


def _jsonable(model) -> dict:
    """pydantic -> plain JSON types, via the model's own serializer. Used so a
    story built inside `POST /advance` and the same story fetched later from
    `GET /day/{n}/story` are byte-identical structures, not two shapes that
    happen to look alike (there is a test)."""
    return json.loads(model.model_dump_json())


def debtor_name(debtor_id: str | None) -> str | None:
    row = DEBTOR_BY_ID.get(debtor_id or "")
    return row["name"] if row else None


def _debtor_block(ledger: Ledger, entity_id: str) -> dict:
    debtor_id = ledger.debtor_of.get(entity_id)
    name = debtor_name(debtor_id)
    return {
        "debtor_id": debtor_id,
        "debtor_name": name,
        "debtor_label": name or debtor_id or entity_id,
        "debtor_name_note": None if name else CART_NAME_NOTE,
    }


def _trust_block(state, as_of_day: int | None) -> dict | None:
    if state is None:
        return None
    return {
        "debtor_id": state.debtor_id,
        "alpha": state.alpha,
        "beta": state.beta,
        "mean": trust_math.mean(state),
        "last_update": state.last_update.isoformat(),
        "as_of_day": as_of_day,
    }


# ---------------------------------------------------------------------------
# GET /debtors
# ---------------------------------------------------------------------------


def debtor_directory(runner) -> dict:
    """Every debtor/customer the ledger knows about, by id, with the name the
    dataset actually stores and the trust posterior actually recorded."""
    ledger = runner.ledger
    entities_by_debtor: dict[str, list[str]] = {}
    for entity_id, debtor_id in ledger.debtor_of.items():
        entities_by_debtor.setdefault(debtor_id, []).append(entity_id)

    out: dict[str, dict] = {}
    for debtor_id in sorted(set(entities_by_debtor) | set(ledger.trust)):
        name = debtor_name(debtor_id)
        state = ledger.trust.get(debtor_id)
        out[debtor_id] = {
            "debtor_id": debtor_id,
            "name": name,
            "label": name or debtor_id,
            "name_note": None if name else CART_NAME_NOTE,
            "entities": sorted(entities_by_debtor.get(debtor_id, [])),
            "trust_alpha": state.alpha if state else None,
            "trust_beta": state.beta if state else None,
            "trust_mean": trust_math.mean(state) if state else None,
            "trust_last_update": state.last_update.isoformat() if state else None,
            "trust_note": None if state else "no trust posterior yet — no event has touched this debtor",
        }
    return out


# ---------------------------------------------------------------------------
# GET /entities/{id}/conversation
# ---------------------------------------------------------------------------


def _message_row(message: Message) -> dict:
    row = _jsonable(message)
    row["day"] = day_of(message.ts)
    # M-SIM-* ids are minted by the runner during a run; everything else came
    # from data/conversations/*.json and is thread history the run inherited.
    row["origin"] = "run" if message.id.startswith("M-SIM-") else "dataset"
    return row


def conversation(runner, entity_id: str) -> dict:
    messages = runner.threads.get(entity_id, [])
    block = _debtor_block(runner.ledger, entity_id)
    entity = runner.ledger.entities.get(entity_id)
    return {
        "entity_id": entity_id,
        **block,
        "state": entity.state if entity else None,
        "channel": runner.channel_of.get(entity_id),
        "messages": [_message_row(m) for m in messages],
        "status": (
            f"{len(messages)} messages on record"
            if messages else
            "no messages on this thread — a Scene-2 cart or an invoice the ladder has not spoken on yet"
        ),
    }


# ---------------------------------------------------------------------------
# GET /entities/{id}/guardrail-checks  — a READ-ONLY LENS
# ---------------------------------------------------------------------------


def infer_pending_action(ledger: Ledger, entity: state_machine.EntityState) -> tuple[str, dict, str]:
    """"If this entity's next action were attempted right now, which action
    would it be?" — answered from the ledger's own tables, never a second copy
    of them.

    Returns (action_kind, params, where that came from).
    """
    held = next((h for h in ledger.pending_held_actions() if h.entity_id == entity.entity_id), None)
    if held is not None:
        return held.action.kind, dict(held.action.params), f"the action held in the review queue as {held.id}"

    state = entity.state
    if state == "DISPUTED":
        return "evidence_packet", {"reason": "dispute raised"}, "ledger._decide_action's DISPUTED branch"
    if state == "MANDATED":
        return (
            "mandate_offer", {"amount_inr": entity.invoice_amount_inr},
            "ledger._decide_money_action's first candidate at MANDATED",
        )
    if state == "LINKED":
        return "link", {"amount_inr": entity.invoice_amount_inr}, "ledger._decide_action's LINKED branch"
    if state == "AT_RISK":
        return (
            "mandate_execute", {"amount_inr": entity.invoice_amount_inr, "retry": entity.retry_count},
            "ledger._decide_action's AT_RISK retry branch",
        )
    if state in _ESCALATE_ACTION:
        kind, params = _ESCALATE_ACTION[state]
        return kind, dict(params), f"ledger._ESCALATE_ACTION[{state!r}]"
    if state in _OUTREACH_ACTION:
        kind, params = _OUTREACH_ACTION[state]
        return kind, dict(params), f"ledger._OUTREACH_ACTION[{state!r}]"
    return (
        "message", {"stage": "gentle"},
        f"no ladder action is scheduled from {state} — previewing the gentle nudge, the ladder's entry action",
    )


def guardrail_preview(
    runner, entity_id: str, action_kind: str | None = None, stage: str | None = None
) -> dict | None:
    """READ-ONLY. Runs the bounds against this entity's state AS IT IS NOW and
    reports every check. It creates nothing, mutates nothing, and writes no
    audit entry — deliberately NOT routed through `Ledger._gate()`, which would
    do all three. A lens, not an action (there is a test that the audit trail
    and the gate log are the same length before and after).
    """
    ledger = runner.ledger
    entity = ledger.entities.get(entity_id)
    if entity is None:
        return None

    if action_kind is None:
        kind, params, source = infer_pending_action(ledger, entity)
    else:
        kind = action_kind
        source = "action_kind supplied by the caller"
        params = {}
        if kind in MONEY_KINDS:
            params["amount_inr"] = entity.invoice_amount_inr
        if kind == "mandate_execute":
            params["retry"] = entity.retry_count
    if stage is not None:
        params["stage"] = stage
        source = f"{source}; stage overridden by the caller"

    now = runner.now()
    debtor_id = ledger.debtor_of.get(entity_id, entity_id)
    debtor_touches = ledger.touches_by_debtor.get(debtor_id, [])
    paused = bool(ledger.paused.get(entity_id))
    outbound = kind in state_machine.OUTBOUND_KINDS

    checks: list[state_machine.BoundsCheck] = []
    if outbound:
        checks.append(state_machine.BoundsCheck(
            name=KILL_SWITCH_CHECK,
            passed=not paused,
            detail=(
                "thread paused by the merchant (kill-switch) — the hard bounds would not be consulted"
                if paused else "thread is not paused by the merchant"
            ),
        ))
    if not (paused and outbound):
        checks += state_machine.check_bounds_detailed(entity, kind, params, now, debtor_touches)

    return {
        "entity_id": entity_id,
        **_debtor_block(ledger, entity_id),
        "entity_state": entity.state,
        "action_kind": kind,
        "params": params,
        "params_source": source,
        "allowed": all(c.passed for c in checks),
        "checks": [c.model_dump() for c in checks],
        "evaluated_at": now.isoformat(),
        "evaluated_on_day": runner.day,
        "preview": True,
        "note": (
            "read-only preview: if this action were attempted right now, this is every check "
            "and its result. Nothing was created, sent, or written to the audit trail."
        ),
    }


# ---------------------------------------------------------------------------
# GET /entities/{id}/mandate-timeline
# ---------------------------------------------------------------------------


def _mandate_step(entry: AuditEntry) -> tuple[str, dict] | None:
    """Classify ONE audit entry as a mandate-lifecycle step, or not at all.

    Matching is on the structured `detail` keys wherever the writer put one
    there (`detail["event"]`, `detail["kind"]`, `detail["move"]`) and only falls
    back to the summary prefix for the two entries whose writer encodes the kind
    in the sentence itself. Nothing here re-words what happened: `extra` values
    below are lifted straight out of the entry.
    """
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    event = detail.get("event")
    summary = entry.summary

    if entry.layer == "judgment":
        if event == "mandate_offer_requested":
            return "offered", {"transition": summary}
        if event == "mandate_confirmed":
            return "debtor_confirmed", {"transition": summary}
        if event == "mandate_refused":
            payload = detail.get("payload") or {}
            return "debtor_refused", {"transition": summary, "reason": payload.get("reason")}
        if event == "mandate_execute_success":
            return "executed", {"transition": summary, "amount_inr": (detail.get("payload") or {}).get("amount_inr")}
        if event == "mandate_execute_failed":
            return "execute_failed", {"transition": summary, "amount_inr": (detail.get("payload") or {}).get("amount_inr")}
        if event == "delivery_rejected":
            return "revoked", {"transition": summary}
        return None

    if entry.layer == "sentinel":
        if summary == "action blocked: mandate_offer":
            return "offer_blocked", {"reason": detail.get("reason"), "params": detail.get("params")}
        if summary == "action blocked: mandate_execute":
            return "execute_blocked", {"reason": detail.get("reason"), "params": detail.get("params")}
        if summary == "action held for human approval" and detail.get("kind") == "mandate_offer":
            return "offer_held", {"reason": detail.get("reason"), "held_id": detail.get("held_id")}
        if summary.startswith("debtor mandate move:"):
            return "debtor_response", {"move": detail.get("move")}
        if "link never opened" in summary and detail.get("kind") == "mandate_offer":
            return "link_timeout", {"action_id": detail.get("action_id")}
        return None

    if entry.layer == "action":
        if summary.startswith("mandate_offer dispatched on rail"):
            return "registered", {
                "short_url": detail.get("short_url"),
                "simulated": detail.get("simulated"),
                "razorpay_id": detail.get("razorpay_id"),
                "razorpay_mode": detail.get("razorpay_mode"),
                "fallback_reason": detail.get("reason"),
                "rail": detail.get("rail"),
                "channel": detail.get("channel"),
                "text": detail.get("text"),
            }
        if summary.startswith("mandate_offer:"):
            return "offer_created", {
                "action_id": detail.get("action_id"),
                "amount_inr": (detail.get("params") or {}).get("amount_inr"),
            }
        if summary.startswith("mandate execution attempt"):
            return "execute_attempt", {
                "action_id": detail.get("action_id"),
                "retry": (detail.get("params") or {}).get("retry", 0),
                "source": detail.get("source"),
            }
    return None


def mandate_timeline(runner, entity_id: str) -> dict:
    """The eMandate lifecycle for one entity, reconstructed from the audit trail
    in trail order. Empty (with a `status`, never an error) when no mandate was
    ever offered — a silent debtor or a Scene-2 reserve capture has no mandate
    lifecycle and should say so rather than render an empty stepper."""
    steps: list[dict] = []
    for entry in runner.ledger.audit:
        if entry.entity_id != entity_id:
            continue
        classified = _mandate_step(entry)
        if classified is None:
            continue
        step, extra = classified
        label, nature = MANDATE_STEP_NATURE[step]

        if step == "registered":
            simulated = extra.get("simulated")
            nature = "razorpay_simulated" if simulated is not False else "razorpay_real"
            label = "Registration link issued (REAL Razorpay TEST mode)" if simulated is False else label
        if step == "execute_attempt" and extra.get("source") == "reserve":
            label = "Reserve auto-debit attempted (Tier-0)"

        steps.append({
            "step": step,
            "label": label,
            "nature": nature,
            "real": nature == "razorpay_real",
            "ts": entry.ts.isoformat(),
            "day": day_of(entry.ts),
            "audit_id": entry.id,
            "summary": entry.summary,
            "detail": {k: v for k, v in extra.items() if v is not None},
            "gate_note": ACCOUNT_GATE_NOTE if nature == "razorpay_real" else None,
        })

    offered = any(s["step"] in ("offered", "offer_created", "offer_blocked", "offer_held") for s in steps)
    if not offered:
        return {
            "entity_id": entity_id,
            **_debtor_block(runner.ledger, entity_id),
            "steps": [],
            "status": (
                "no mandate was ever offered for this entity — nothing to show. "
                "A mandate is only offered against an L1-L3 promise from an eNACH-familiar debtor."
            ),
            "real_razorpay_enabled": runner.real_razorpay,
            "lifecycle_note": MANDATE_LIFECYCLE_NOTE,
            "account_gate_note": ACCOUNT_GATE_NOTE,
        }

    return {
        "entity_id": entity_id,
        **_debtor_block(runner.ledger, entity_id),
        "steps": steps,
        "status": f"{len(steps)} mandate lifecycle steps on the audit trail",
        "real_razorpay_enabled": runner.real_razorpay,
        "lifecycle_note": MANDATE_LIFECYCLE_NOTE,
        "account_gate_note": ACCOUNT_GATE_NOTE,
    }


# ---------------------------------------------------------------------------
# GET /day/{n}/story  (and POST /advance's `stories`)
# ---------------------------------------------------------------------------


def _guardrail_summary(record: GateRecord | None, entry: AuditEntry) -> dict | None:
    """The checklist behind one audited decision.

    For a BLOCK the audited reason is surfaced verbatim from the trail
    (`audited_reason`) alongside the recorded checklist, and the two are the
    same sentence by construction — `_gate()` wrote both from one
    `BoundsResult`. For an ALLOWED action the checklist is the one recorded at
    gate time with the params the action was really created from; it is NOT
    re-evaluated against today's entity state, which for a three-week-old day
    would show numbers the decision never saw.
    """
    if record is None:
        return None
    if not record.checks:
        # A gate record with no checks means no bound applied to this kind at
        # all (the RBI pre-/post-debit notices: mandatory, not discretionary,
        # so `check_bounds_detailed()` has nothing to evaluate for them) —
        # not a checklist that happened to pass everything. An empty
        # "Guardrails checked" panel would misleadingly imply checks ran.
        return None
    detail = entry.detail if isinstance(entry.detail, dict) else {}
    return {
        "status": "allowed" if record.allowed else "blocked",
        "kind": record.kind,
        "params": record.params,
        "reason": record.reason,
        "audited_reason": detail.get("reason") if not record.allowed else None,
        "checks": [c.model_dump() for c in record.checks],
        "passed": sum(1 for c in record.checks if c.passed),
        "total": len(record.checks),
        "action_id": record.action_id,
        "recorded_at": record.ts.isoformat(),
        "source": "recorded by engine/judgment/ledger.py `_gate()` at the moment the decision was made",
    }


def _beats(runner, entity_id: str, entries: list[tuple[int, AuditEntry]], gate_by_audit: dict, day: int) -> list[dict]:
    """One ordered list merging what was SAID and what was DECIDED.

    Ordering note worth stating, because it is not the obvious one: every beat
    inside a simulated day carries the SAME timestamp (the runner stamps a whole
    day at one instant on purpose — see `WorldRunner._ts`), so sorting by `ts`
    alone would scramble the day into an arbitrary order. The real order is the
    append order of the audit trail, so that is the tiebreaker.

    Every message the run produces is also audited (outbound in `_send`,
    inbound in `_inbound`, both carrying `message_id` + `text`), so walking the
    trail recovers the conversation IN PLACE rather than interleaving two lists
    by a timestamp they share. Dataset seed messages are not audited — they are
    thread history the run inherited — so they are appended by their own real
    timestamps and labelled `origin: "dataset"`.
    """
    thread = runner.threads.get(entity_id, [])
    by_message_id = {m.id: m for m in thread}
    seen: set[str] = set()
    keyed: list[tuple[dt.datetime, int, dict]] = []

    for index, entry in entries:
        detail = entry.detail if isinstance(entry.detail, dict) else {}
        # Inbound entries key the thread message as `message_id`; outbound
        # dispatch entries carry BOTH the messenger queue id (`message_id`) and
        # the thread's own id (`thread_message_id`) — the thread one first, or
        # an outbound beat would fail to resolve and fall out of order.
        message = by_message_id.get(
            detail.get("thread_message_id") or detail.get("message_id") or ""
        )
        base = {
            "ts": entry.ts.isoformat(),
            "day": day,
            "audit_id": entry.id,
            "layer": entry.layer,
            "summary": entry.summary,
            "detail": json.loads(json.dumps(detail, default=str)),
            "guardrail_summary": _guardrail_summary(gate_by_audit.get(entry.id), entry),
        }
        if message is not None and message.id not in seen and day_of(message.ts) == day:
            seen.add(message.id)
            keyed.append((entry.ts, index, {
                "type": "message",
                "message_id": message.id,
                "direction": message.direction,
                "channel": message.channel,
                "text": message.text,
                "origin": "run" if message.id.startswith("M-SIM-") else "dataset",
                **base,
            }))
        else:
            keyed.append((entry.ts, index, {"type": "audit", **base}))

    for offset, message in enumerate(thread):
        if message.id in seen or day_of(message.ts) != day:
            continue
        keyed.append((message.ts, 10**9 + offset, {
            "type": "message",
            "message_id": message.id,
            "direction": message.direction,
            "channel": message.channel,
            "text": message.text,
            "origin": "run" if message.id.startswith("M-SIM-") else "dataset",
            "ts": message.ts.isoformat(),
            "day": day,
            "audit_id": None,
            "layer": None,
            "summary": None,
            "detail": {},
            "guardrail_summary": None,
        }))

    keyed.sort(key=lambda item: (item[0], item[1]))
    return [beat for _, _, beat in keyed]


def _entity_block(runner, entity_id: str, entries, gate_by_audit, snapshot, day: int) -> dict:
    ledger = runner.ledger
    entities_snap = snapshot.get("entities", {})
    trust_snap = snapshot.get("trust", {})
    end_of_day = entities_snap.get(entity_id)
    live = ledger.entities.get(entity_id)
    debtor_id = ledger.debtor_of.get(entity_id)

    beats = _beats(runner, entity_id, entries, gate_by_audit, day)
    guardrails = [b["guardrail_summary"] for b in beats if b["guardrail_summary"]]
    amount = (end_of_day or live).invoice_amount_inr if (end_of_day or live) else None

    return {
        "entity_id": entity_id,
        **_debtor_block(ledger, entity_id),
        "kind": "cart" if entity_id in runner.carts else ("invoice" if entity_id in runner.invoices else "unknown"),
        "channel": runner.channel_of.get(entity_id),
        "invoice_amount_inr": amount,
        "state_end_of_day": end_of_day.state if end_of_day else None,
        "state_now": live.state if live else None,
        "escalate_stage_end_of_day": end_of_day.escalate_stage if end_of_day else None,
        "paused": bool(ledger.paused.get(entity_id)),
        "trust": _trust_block(trust_snap.get(debtor_id or ""), day),
        "trust_note": (
            None if trust_snap.get(debtor_id or "")
            else f"no trust posterior was recorded for this debtor at the end of day {day}"
        ),
        "beats": beats,
        "counts": {
            "beats": len(beats),
            "messages": sum(1 for b in beats if b["type"] == "message"),
            "guardrail_checks": len(guardrails),
            "blocks": sum(1 for g in guardrails if g["status"] == "blocked"),
        },
        "has_mandate_activity": any(
            _mandate_step(entry) is not None for _, entry in entries
        ),
    }


def build_day_story(runner, day: int) -> dict:
    """Everything that happened on one simulated day, per entity, in order."""
    ledger = runner.ledger
    simulated = day in runner.day_snapshots
    snapshot = runner.day_snapshots.get(day, {})

    by_entity: dict[str, list[tuple[int, AuditEntry]]] = {}
    order: list[str] = []
    for index, entry in enumerate(ledger.audit):
        if day_of(entry.ts) != day:
            continue
        if entry.entity_id not in by_entity:
            by_entity[entry.entity_id] = []
            order.append(entry.entity_id)
        by_entity[entry.entity_id].append((index, entry))

    gate_by_audit = {r.audit_id: r for r in ledger.gate_log if r.audit_id is not None}
    entities = [
        _entity_block(runner, entity_id, by_entity[entity_id], gate_by_audit, snapshot, day)
        for entity_id in order
    ]

    return {
        "day": day,
        "date": date_of_day(day).isoformat(),
        "world_day": runner.day,
        "simulated": simulated,
        "entities": entities,
        "counts": {
            "entities": len(entities),
            "beats": sum(e["counts"]["beats"] for e in entities),
            "messages": sum(e["counts"]["messages"] for e in entities),
            "blocks": sum(e["counts"]["blocks"] for e in entities),
        },
        "status": (
            f"{len(entities)} entities had activity on day {day}" if entities
            else (
                f"day {day} ran with no audited activity"
                if simulated else
                f"day {day} has not been simulated yet — the world is at day {runner.day}"
            )
        ),
        "notes": {
            "day_numbering": (
                "day indexes are 0-based: advance(1) simulates day 0 and leaves the world at day 1, "
                "so the day that just happened is always world_day - 1"
            ),
            "mandate_lifecycle": MANDATE_LIFECYCLE_NOTE,
        },
    }


def build_stories(runner, days) -> dict[str, list[dict]]:
    """`{day: [entity block, ...]}` for `POST /advance` — the same builder, so
    the advance payload and `GET /day/{n}/story` can never drift apart (there is
    a test that compares their beats)."""
    return {str(day): build_day_story(runner, day)["entities"] for day in days}
