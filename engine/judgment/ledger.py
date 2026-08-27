"""Promise ledger — orchestrates state_machine + trust into Actions, with the
audit trail written BEFORE any action is returned for execution (CLAUDE.md
law #3: "every action writes to the audit log before it executes. No
exceptions."). Zero LLM (BUILD.md Day 5).

Reserve pre-check (master doc §8.6, Tier-0): a `payment_failed` event for an
entity with an active reserve short-circuits the entire ladder — no touches,
straight to a recovered/KEPT outcome, logged as such.

Touches are counted PER DEBTOR (`touches_by_debtor`), not per entity, because
that is how CLAUDE.md law 4 and master doc §3.4 word bound #4. The ledger is
the only place that knows which entities belong to the same debtor, so it owns
the counter and hands the window to `check_bounds()` as an argument — the gate
itself stays a pure predicate. Consequence to expect in the numbers: a debtor
with five overdue invoices is contacted about at most two of them per week,
and the other three legitimately produce audited *blocks* rather than
messages. A block here is the bound working, not an error.

HUMAN IN THE LOOP (packet P9 — master doc §2.3 confidence gates, §3.6 queue)
---------------------------------------------------------------------------
Three gates sit on the decide path, all driven by the confidence the perception
layer attached to an extraction (`extraction_received`'s `confidence` payload
key, written by the runner from the real `Extraction` object):

  conf < 0.75  ->  ONE clarifying question instead of acting. A SECOND sub-0.75
                   extraction on the same entity does not get a second question:
                   it goes to the review queue ("still ambiguous after
                   clarification"). The agent asks once; asking again is a
                   human's call.
  conf < 0.90  ->  an action that would MOVE MONEY off that extraction is
                   decided but not emitted — it becomes a `HeldAction` waiting
                   on a merchant approve-click.
  legal stage  ->  the formal-notice draft enters the queue with NO approve
                   button at all (`sendable=False`). CLAUDE.md law 4 is
                   absolute: the agent never sends legal communication, human
                   click or not.

Two properties worth stating, because they are the point of the packet:

1. **`check_bounds()` runs at CLICK time, not at hold time.** A held action
   carries `bounds_checked=False` and is re-gated when the human approves it,
   against the debtor's touch budget *as of the click*. A hold created before
   the cap was spent cannot be approved through it afterwards.
2. **The merchant kill-switch is real, not cosmetic.** A paused entity
   (`ledger.paused`) has every outbound action refused inside `_gate()` — the
   same chokepoint the bounds live behind — so pausing a thread stops it
   everywhere, not only in the runner's outreach loop.

An extraction carrying NO confidence at all is not gated. That is deliberate:
the gate compares a number, and "we were told nothing" is not evidence of low
confidence. Every producer inside the system supplies one; the ungated case is
manual event injection in tests and demos.
"""

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from engine.judgment import state_machine, trust
from engine.judgment.state_machine import BoundsCheck, BoundsResult, EntityState
from engine.schemas import Action, AuditEntry, HeldAction, Invoice, Promise, TrustState

# Escalation stage -> the action the ladder attempts at that stage
# (master doc §3.2: "+2d firm notice -> +5d voice note -> +10d formal -> HARD STOP -> human").
_ESCALATE_ACTION: dict[str, tuple[str, dict]] = {
    "ESCALATE_1": ("message", {"stage": "firm"}),
    "ESCALATE_2": ("voice", {"stage": "firm"}),
    "ESCALATE_3": ("message", {"stage": "legal"}),  # deliberately hits the legal-stage bound
    "ESCALATE_4": ("human_handoff", {"reason": "escalation ladder exhausted"}),
}

# A scheduled outreach beat -> the message the ladder position justifies.
# BUILD.md's cadence is calendar-driven (day 0/7/14/21/30, gentle -> firm ->
# formal); this table is how that beat becomes a real, bounds-checked Action
# instead of copy the integration layer invents for itself. Every message the
# system sends is therefore touch-counted and audited before it goes out.
#
# ESCALATE_3/4 are deliberately absent: stage 3 is the formal/legal notice,
# which law 4 sends to the MERCHANT for review rather than to the debtor, and
# stage 4 is the ladder exhausting into a human handoff. Neither is a moment
# for the agent to send another nudge of its own.
_OUTREACH_ACTION: dict[str, tuple[str, dict]] = {
    "TRIAGED": ("message", {"stage": "gentle"}),
    "ENGAGED": ("message", {"stage": "gentle"}),
    "ESCALATE_1": ("message", {"stage": "firm"}),
    "ESCALATE_2": ("message", {"stage": "firm"}),
}

TOUCH_COUNTED_KINDS = {"link", "mandate_offer", "message", "voice", "sms"}

MANUAL_REMINDER_CHANNELS = ("voice", "sms", "message")
"""The kinds `manual_reminder()` will send. `voice`/`sms` are packet P14;
`message` joined in packet P15 (real contact identity) so the dashboard's
Contacts panel can offer WhatsApp/email — the entity's own thread channel —
as a third manual trigger alongside voice and SMS. All three are ordinary
outbound kinds in `state_machine.OUTBOUND_KINDS` and all three are
touch-counted above — a merchant clicking "Send WhatsApp reminder" spends the
SAME weekly budget an autonomous nudge would, and can be refused by the SAME
bound. `message` needed no new dispatch code to reach the wire: it is the
same `ActionKind` the autonomous ladder has emitted since Day 5, so
`WorldRunner._dispatch`'s existing `elif kind == "message":` branch handles it
unchanged (see `tests/test_reminders.py`'s message-channel additions). See
`manual_reminder()` for why the gating is not negotiable."""

MANUAL_REMINDER_STAGE = "firm"
"""The stage a manual reminder is sent at. Fixed here rather than accepted from
the caller, because `stage` is an input to `check_bounds()` — a route that could
choose its own stage could choose `"legal"`, and the agent never sends legal
communication (CLAUDE.md law 4). The operator picks the CHANNEL and may supply
their own words; they do not pick the escalation stage."""

# ---------------------------------------------------------------------------
# CONFIDENCE GATES (master doc §2.3) — deliberately NOT in state_machine.py.
#
# These are not bounds. The bounds in state_machine.py answer "is this action
# allowed?" and are hard constants no perception output may influence
# (CLAUDE.md law 4). These two answer a different question — "do we trust what
# we READ enough to act on it unsupervised?" — which is a property of the LLM
# boundary, so it lives on the decide path that owns that boundary. The bounds
# block; these two DEFER TO A HUMAN, which is a strictly weaker power: nothing
# here can ever let an action through that check_bounds() would refuse.
# ---------------------------------------------------------------------------

MONEY_ACTION_CONFIDENCE_GATE = 0.90
"""Below this, an extraction may not trigger a money action unsupervised."""

CLARIFY_CONFIDENCE_GATE = 0.75
"""Below this, the agent asks ONE clarifying question instead of acting."""

MAX_CLARIFY_QUESTIONS = 1
"""...and exactly one. The second ambiguous read goes to the queue."""

FORMAL_NOTICE_HOLD_REASON = (
    "formal notice draft — merchant must review and send it themselves; "
    "the agent never sends legal communication"
)

FORMAL_NOTICE_REFUSAL = (
    "the agent never sends legal communication, not even on a human click "
    "(CLAUDE.md law 4 / master doc §3.4). Send the notice yourself and use "
    "mark-handled."
)


KILL_SWITCH_CHECK = "merchant_kill_switch"
"""The one gate that is NOT a hard bound and so is not in `state_machine.py`:
the merchant's pause. It is enforced in `_gate()` (see below) ahead of the
bounds, so a recorded checklist that omitted it could show "all bounds passed"
next to a refusal. It is therefore prepended to every recorded checklist for an
outbound kind, which keeps `allowed == all(checks passed)` true for a
GateRecord exactly as `check_bounds_detailed()` keeps it true for the bounds."""


class GateRecord(BaseModel):
    """WHAT `_gate()` ACTUALLY SAW, kept so a human can be shown it later.

    Packet P10. The audit trail records that an action was allowed or blocked
    and *why* in one sentence; this records the full per-bound working behind
    that sentence, at the moment it was computed. It exists because the
    alternative — re-running the bounds against today's entity state to explain
    a decision made on day 3 — would put numbers on screen that were never the
    ones the system decided from (CLAUDE.md law 8).

    Pure bookkeeping: nothing reads a GateRecord to decide anything. It is
    append-only, written after the verdict, and `check_bounds()` neither sees
    it nor is affected by it. `audit_id` / `action_id` link it back into the
    append-only trail so the API can attach a checklist to the exact audit
    entry it belongs to instead of guessing by timestamp.
    """

    seq: int
    entity_id: str
    debtor_id: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    ts: dt.datetime
    allowed: bool
    reason: str
    checks: list[BoundsCheck] = Field(default_factory=list)
    audit_id: str | None = None
    """The audit entry this gate produced: the block entry when refused, the
    action entry when allowed. None while a passing gate's action is still
    being built, or for a hold (which is queued, not emitted)."""
    action_id: str | None = None


class ReviewQueueError(Exception):
    """A review-queue click that cannot be honoured: unknown id, already
    resolved, wrong entity state, or an item the agent must never send.
    Carries the HTTP status the API layer should answer with, so the refusal
    reason is decided here (in judgment) rather than re-derived in the route."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class Ledger:
    def __init__(self) -> None:
        self.entities: dict[str, EntityState] = {}
        self.debtor_of: dict[str, str] = {}
        self.touches_by_debtor: dict[str, list[dt.datetime]] = {}
        """debtor_id -> every outbound touch made to that human, across all of
        their entities. This is what bound #4 is measured against."""
        self.reserve_active: dict[str, bool] = {}
        self.promises: dict[str, Promise] = {}
        self.trust: dict[str, TrustState] = {}
        self.audit: list[AuditEntry] = []

        self.held_actions: list[HeldAction] = []
        """The approval queue (master doc §3.6). Append-only in practice: items
        are resolved by flipping `status`, never removed, so the queue is as
        auditable as the audit trail itself."""
        self.extraction_confidence: dict[str, float] = {}
        """entity_id -> the confidence of the LATEST extraction against it.
        Cleared when an extraction arrives without one, so the gates never read
        a stale number from a previous message."""
        self.clarify_count: dict[str, int] = {}
        """entity_id -> clarifying questions the AGENT has asked. Capped at
        MAX_CLARIFY_QUESTIONS; a human can approve more from the queue."""
        self.paused: dict[str, bool] = {}
        """entity_id -> merchant kill-switch. True = no outbound action of any
        kind, from any path."""

        self.gate_log: list[GateRecord] = []
        """Every `_gate()` call, with the full per-bound checklist it computed
        (packet P10). Append-only, read-only to everything: the dashboard's
        "Guardrails checked" panel renders these, so what a judge sees is the
        working the decision was actually made from, not a re-derivation."""
        self._pending_gate: GateRecord | None = None

        self._action_seq = 0
        self._gate_seq = 0
        self._audit_seq = 0
        self._promise_seq = 0
        self._held_seq = 0

    # -- setup -----------------------------------------------------------

    def register_invoice(self, invoice: Invoice) -> None:
        """Also the registration path for a Scene-2 cart expressed as a ledger
        record: `invoice.debtor_id` carries the cart's `customer_id`, so a
        customer's carts and invoices share one touch budget the same way a
        debtor's invoices do."""
        entity = self._entity(invoice.id)
        entity.invoice_amount_inr = invoice.amount_inr
        self.entities[invoice.id] = entity
        self.debtor_of[invoice.id] = invoice.debtor_id

    def register_reserve(self, entity_id: str, active: bool) -> None:
        self.reserve_active[entity_id] = active

    # -- internals ---------------------------------------------------------

    def _entity(self, entity_id: str) -> EntityState:
        return self.entities.get(entity_id) or EntityState(entity_id=entity_id)

    def _debtor_id(self, entity_id: str) -> str:
        """An unregistered entity is its own debtor — a lone entity's touch
        budget then equals its debtor's, which is the correct degenerate case."""
        return self.debtor_of.get(entity_id, entity_id)

    def _debtor_touches(self, entity_id: str) -> list[dt.datetime]:
        return self.touches_by_debtor.get(self._debtor_id(entity_id), [])

    def _record_touch(self, entity: EntityState, now: dt.datetime) -> None:
        """One touch, written to both scopes: the entity's own history (funnel,
        timeline, the Tier-0 '0 touches' claim) and the debtor's window (the
        only one bound #4 is measured against)."""
        entity.touches.append(now)
        self.entities[entity.entity_id] = entity
        self.touches_by_debtor.setdefault(self._debtor_id(entity.entity_id), []).append(now)

    def _trust_for(self, debtor_id: str, now: dt.datetime) -> TrustState:
        existing = self.trust.get(debtor_id)
        return trust.decay(existing, now) if existing else trust.new_trust(debtor_id, now)

    def _audit(self, entity_id: str, layer: str, summary: str, detail: dict, ts: dt.datetime) -> AuditEntry:
        self._audit_seq += 1
        entry = AuditEntry(id=f"AE-{self._audit_seq:05d}", entity_id=entity_id, layer=layer, summary=summary, detail=detail, ts=ts)
        self.audit.append(entry)
        return entry

    def _next_action_id(self) -> str:
        self._action_seq += 1
        return f"A-{self._action_seq:04d}"

    # -- main entry point ----------------------------------------------------

    def process_event(self, event_type: str, entity_id: str, payload: dict, now: dt.datetime) -> Action | None:
        entity = self._entity(entity_id)
        debtor_id = self._debtor_id(entity_id)

        if event_type == "payment_failed" and self.reserve_active.get(entity_id):
            return self._tier0_recover(entity, entity_id, now)

        prev_state = entity.state
        new_state = state_machine.transition(entity, event_type, payload, now)
        self.entities[entity_id] = new_state
        self._audit(entity_id, "judgment", f"{event_type}: {prev_state} -> {new_state.state}", {"event": event_type, "payload": payload}, now)

        self._update_trust(event_type, new_state, debtor_id, now)
        self._update_promise(event_type, entity_id, debtor_id, new_state, payload, now)

        if event_type == "extraction_received":
            confidence = self._record_confidence(entity_id, payload)
            if confidence is not None and confidence < CLARIFY_CONFIDENCE_GATE:
                # Master doc §2.3: "agent asks ONE clarifying question INSTEAD
                # of acting". Checked here rather than inside `_decide_action`
                # so it holds even when the extraction moved no state (a second
                # ambiguous reply against an already-PROMISED entity would
                # otherwise short-circuit out below and act on nothing).
                return self._decide_clarify(new_state, confidence, now)

        if event_type == "outreach_sent" and new_state.state == prev_state:
            # A scheduled outreach beat against an entity that is already
            # ENGAGED / ESCALATE_1 / ESCALATE_2 moves no state, but it is still
            # a real touch someone asked for — so it gets a real, bounds-checked
            # Action rather than silently producing nothing. (Every OTHER event
            # that changes nothing is a genuine no-op; see below.)
            return self._decide_outreach(new_state, now)

        if new_state.state == prev_state:
            return None  # nothing changed -> no new action to (re-)decide; avoids re-firing on every unrelated event
        return self._decide_action(new_state, now)

    def _tier0_recover(self, entity: EntityState, entity_id: str, now: dt.datetime) -> Action | None:
        if self.paused.get(entity_id):
            # The kill-switch outranks even the 0-touch happy path: a merchant
            # who paused a thread has said "move no money on this", and a
            # reserve capture is money moving.
            self._audit(entity_id, "sentinel", "action blocked: mandate_execute",
                        {"reason": "thread paused by merchant (kill-switch)", "tier": 0}, now)
            return None
        self._audit(entity_id, "judgment", "Tier-0 reserve pre-check: active reserve found, recovering silently", {"tier": 0}, now)
        action = Action(
            id=self._next_action_id(), entity_id=entity_id, kind="mandate_execute",
            params={"source": "reserve", "amount_inr": entity.invoice_amount_inr}, reason="reserve_active pre-check, Tier-0 silent recovery",
            bounds_checked=True, ts=now,
        )
        self._audit(entity_id, "action", "Tier-0 reserve auto-debit executed, 0 touches", {"action_id": action.id}, now)
        entity = entity.model_copy(deep=True)
        entity.state = "KEPT"
        self.entities[entity_id] = entity
        return action

    def _update_trust(self, event_type: str, entity: EntityState, debtor_id: str, now: dt.datetime) -> None:
        current = self._trust_for(debtor_id, now)
        if event_type in ("promise_kept", "mandate_execute_success"):
            self.trust[debtor_id] = trust.update_kept(current, now)
        elif event_type in ("promise_broken",) or (event_type == "mandate_execute_failed" and entity.state != "AT_RISK"):
            self.trust[debtor_id] = trust.update_broken(current, now)
        elif event_type == "mandate_refused":
            self.trust[debtor_id] = trust.update_refusal(current, now)
        else:
            # `human_resolution` lands here ON PURPOSE (decay only, no update).
            # A merchant closing a handoff as recovered/written-off is
            # bookkeeping about OUR process, not new evidence about how this
            # debtor behaves — inventing a Beta update from an admin click
            # would put a number in the trust curve that no debtor action
            # earned.
            self.trust[debtor_id] = current  # still apply decay so reads are always fresh

    def _update_promise(self, event_type: str, entity_id: str, debtor_id: str, entity: EntityState, payload: dict, now: dt.datetime) -> None:
        if event_type == "extraction_received":
            self._promise_seq += 1
            promise = Promise(
                id=f"P-{self._promise_seq:04d}", debtor_id=debtor_id, invoice_id=entity_id,
                amount_inr=payload.get("amount_inr") or entity.invoice_amount_inr or 1,
                due=payload.get("due") or now.date(), status="pending",
                source_msg=payload.get("message_id", ""),
            )
            self.promises[promise.id] = promise
        elif event_type == "promise_kept":
            for p in self.promises.values():
                if p.invoice_id == entity_id and p.status in ("pending", "at_risk", "renegotiated"):
                    p.status = "kept"
        elif event_type == "promise_broken":
            for p in self.promises.values():
                if p.invoice_id == entity_id and p.status in ("pending", "at_risk"):
                    p.status = "broken"
        elif event_type == "dispute_raised":
            for p in self.promises.values():
                if p.invoice_id == entity_id:
                    p.status = "disputed"
        elif event_type == state_machine.HUMAN_RESOLUTION_EVENT:
            # No silent deaths on the promise ledger either: a handoff the
            # merchant closed leaves no promise sitting "pending" forever.
            # `disputed` promises keep that status — the dispute is the more
            # informative fact about them.
            closed = "kept" if payload.get("resolution") == "recovered" else "broken"
            for p in self.promises.values():
                if p.invoice_id == entity_id and p.status in ("pending", "at_risk", "renegotiated"):
                    p.status = closed

    # -- confidence gates (master doc §2.3) ----------------------------------

    def _record_confidence(self, entity_id: str, payload: dict) -> float | None:
        raw = payload.get("confidence")
        if raw is None:
            self.extraction_confidence.pop(entity_id, None)
            return None
        confidence = float(raw)
        self.extraction_confidence[entity_id] = confidence
        return confidence

    def _decide_clarify(self, entity: EntityState, confidence: float, now: dt.datetime) -> Action | None:
        """conf < 0.75. One clarifying question, then the queue.

        The clarifying question is an ordinary outbound `message` Action: it is
        audited before it is returned, it passes `check_bounds()`, and it spends
        a touch from the debtor's weekly budget exactly like a nudge does. An
        agent that could ask "just to confirm?" for free would have found a way
        around bound #4.
        """
        entity_id = entity.entity_id
        asked = self.clarify_count.get(entity_id, 0)
        gate = f"confidence {confidence:.2f} < {CLARIFY_CONFIDENCE_GATE:.2f} clarify gate"

        if asked >= MAX_CLARIFY_QUESTIONS:
            self._hold_action(
                entity, "message", {"stage": "clarify"},
                f"still ambiguous after clarification, {gate}",
                f"still ambiguous after clarification ({gate})", now,
            )
            return None

        self.clarify_count[entity_id] = asked + 1
        return self._try_action(entity, "message", {"stage": "clarify"}, gate, now)

    def _hold_action(
        self, entity: EntityState, kind: str, params: dict, reason: str, hold_reason: str,
        now: dt.datetime, sendable: bool = True, label: str | None = None,
    ) -> HeldAction:
        """Build the Action the ladder wanted and put it in the queue instead of
        emitting it. Audited BEFORE the queue is mutated (law 3 read the way it
        has to be read once the queue exists: the queue is a place actions live,
        so every change to it is an event the trail must already contain).

        The Action carries `bounds_checked=False` and no `action_id` reaches the
        audit detail under that key — a held action has not passed the gate and
        must never be mistakable in the trail for one that has.
        """
        self._held_seq += 1
        held_id = f"H-{self._held_seq:04d}"
        self._audit(
            entity.entity_id, "sentinel", "action held for human approval",
            {"held_id": held_id, "kind": kind, "params": dict(params), "reason": hold_reason,
             "sendable": sendable, "label": label,
             "debtor_id": self._debtor_id(entity.entity_id)}, now,
        )
        action = Action(
            id=self._next_action_id(), entity_id=entity.entity_id, kind=kind,
            params=dict(params), reason=reason, bounds_checked=False, ts=now,
        )
        held = HeldAction(
            id=held_id, entity_id=entity.entity_id, action=action, reason=hold_reason,
            ts=now, sendable=sendable, label=label,
        )
        self.held_actions.append(held)
        return held

    def _decide_action(self, entity: EntityState, now: dt.datetime) -> Action | None:
        state = entity.state

        if state == "DISPUTED":
            return self._emit_action(entity, "evidence_packet", {"reason": "dispute raised"}, "dispute -> instant stop, evidence packet, human", now)

        if state == "ENGAGED":
            # The gentle nudge. It has no instrument to offer yet, but it IS an
            # outbound contact, so it is an Action like any other: audited
            # first, bounds-checked, touch-counted. Blocked here (the debtor
            # already had their two touches this week) simply means no nudge.
            return self._decide_outreach(entity, now)

        if state == "MANDATED":
            return self._decide_money_action(entity, now)

        if state == "LINKED":
            return self._try_action(entity, "link", {"amount_inr": entity.invoice_amount_inr}, "fallback to payment link", now)

        if state == "AT_RISK":
            return self._try_action(entity, "mandate_execute", {"amount_inr": entity.invoice_amount_inr, "retry": entity.retry_count}, "retrying mandate execution once", now)

        if state in _ESCALATE_ACTION:
            kind, params = _ESCALATE_ACTION[state]
            result = self._try_action(entity, kind, dict(params), f"escalation stage {state}", now)
            if result is not None:
                return result
            if params.get("stage") == "legal":
                # The formal-notice stage (master doc §3.6's second reason the
                # approval queue exists). The bound above already refused to
                # send it; the DRAFT still has to reach the merchant, or the
                # "compliant escalation" story ends in a silence rather than in
                # a human holding a notice. `sendable=False` means there is no
                # approve-send button anywhere in the stack for this item — the
                # merchant sends it themselves, outside the system, and marks it
                # handled.
                self._hold_action(
                    entity, kind, dict(params), f"escalation stage {state}",
                    FORMAL_NOTICE_HOLD_REASON, now,
                    sendable=False, label="formal_notice_draft",
                )
            # blocked -> route to a human instead of silently dropping
            return self._emit_action(entity, "human_handoff", {"reason": f"{state} action blocked, routing to merchant/human"}, f"{state} bound-blocked", now)

        return None

    def _decide_money_action(self, entity: EntityState, now: dt.datetime) -> Action | None:
        """MANDATED — the money gate (master doc §2.3, third bullet).

        This is the ONLY branch an extraction drives straight into money
        movement, so it is the only one the gate applies to. The other money
        actions are reached from elsewhere and are not "an extraction that would
        trigger a MONEY action": `mandate_execute` at AT_RISK is the one allowed
        retry of a mandate the debtor ALREADY approved (driven by an execution
        webhook, not by a reading of a sentence), the LINKED `link` is the
        post-refusal fallback, and the Tier-0 reserve capture never involves
        perception at all.

        Both candidates here are money-adjacent — the mandate offer and the
        link the ladder falls back to when the mandate is bound-blocked (master
        doc §3.4, "larger -> partial + link") — so a low-confidence extraction
        holds whichever of the two the ladder would actually have sent. The
        bounds check that picks between them is a preview, not the gate: the
        real gate re-runs on the approve click.
        """
        amount = entity.invoice_amount_inr
        candidates: tuple[tuple[str, dict, str], ...] = (
            ("mandate_offer", {"amount_inr": amount}, "L1+ promise, offering scheduled mandate"),
            ("link", {"amount_inr": amount}, "mandate blocked, falling back to payment link"),
        )
        confidence = self.extraction_confidence.get(entity.entity_id)
        gated = confidence is not None and confidence < MONEY_ACTION_CONFIDENCE_GATE

        for kind, params, reason in candidates:
            if not self._gate(entity, kind, params, now).allowed:
                continue
            if gated:
                self._hold_action(
                    entity, kind, params, reason,
                    f"confidence {confidence:.2f} < {MONEY_ACTION_CONFIDENCE_GATE:.2f} money gate",
                    now,
                )
                return None
            self._record_touch(entity, now)
            return self._emit_action(entity, kind, params, reason, now)
        return None

    def _decide_outreach(self, entity: EntityState, now: dt.datetime) -> Action | None:
        """The plain-nudge half of the action table (`_OUTREACH_ACTION`).
        Returns None when the ladder position has no nudge to make (ESCALATE_3
        is merchant-review territory, ESCALATE_4 is a handoff) or when the
        bound blocked it — in the second case `_try_action` has already written
        the block to the audit trail."""
        entry = _OUTREACH_ACTION.get(entity.state)
        if entry is None:
            return None
        kind, params = entry
        return self._try_action(entity, kind, dict(params), f"scheduled outreach at {entity.state}", now)

    def _gate(self, entity: EntityState, kind: str, params: dict, now: dt.datetime) -> BoundsResult:
        """The single place an action is allowed or refused, and the single
        place a refusal is written to the trail.

        Order matters: the merchant kill-switch is checked FIRST, because it is
        the one refusal a human asked for by name and it should be the reason
        recorded, not whichever bound happened to also apply.

        Packet P10 added the `GateRecord` written alongside the verdict. It
        changes nothing about the verdict: `check_bounds()` is still the only
        thing consulted, still short-circuits, still gets exactly the same
        arguments. The record is the same call re-run through
        `check_bounds_detailed()` — a lens, not a second opinion (see that
        function's docstring for the invariant that makes the two inseparable).
        """
        entity_id = entity.entity_id
        outbound = kind in state_machine.OUTBOUND_KINDS
        debtor_touches = self._debtor_touches(entity_id)

        if self.paused.get(entity_id) and outbound:
            result = BoundsResult(allowed=False, reason="thread paused by merchant (kill-switch)")
            checks = [BoundsCheck(
                name=KILL_SWITCH_CHECK, passed=False,
                detail="thread paused by the merchant (kill-switch) — the hard bounds were not consulted",
            )]
        else:
            result = state_machine.check_bounds(entity, kind, params, now, debtor_touches)
            checks = [BoundsCheck(
                name=KILL_SWITCH_CHECK, passed=True,
                detail="thread is not paused by the merchant",
            )] if outbound else []
            checks += state_machine.check_bounds_detailed(entity, kind, params, now, debtor_touches)

        self._gate_seq += 1
        record = GateRecord(
            seq=self._gate_seq, entity_id=entity_id, debtor_id=self._debtor_id(entity_id),
            kind=kind, params=dict(params), ts=now,
            allowed=result.allowed, reason=result.reason, checks=checks,
        )
        self.gate_log.append(record)
        self._pending_gate = record

        if not result.allowed:
            entry = self._audit(
                entity_id, "sentinel", f"action blocked: {kind}",
                {"reason": result.reason, "params": params, "debtor_id": self._debtor_id(entity_id)}, now,
            )
            record.audit_id = entry.id
            self._pending_gate = None
        return result

    def _claim_gate(self, action: Action, audit_id: str) -> None:
        """Link the gate that just passed to the action it let through.

        The gate immediately precedes the emit on every path that has one, so
        the pending record is the right one or there is none — the identity
        check below is what makes "or there is none" safe. `_tier0_recover`'s
        reserve capture and the evidence-packet/handoff emits have no gate of
        their own; they simply match nothing and stay unlinked, which is the
        honest answer for them (`_gate` never ran).
        """
        record = self._pending_gate
        if (
            record is not None and record.allowed and record.action_id is None
            and record.entity_id == action.entity_id and record.kind == action.kind
            and record.params == action.params
        ):
            record.action_id = action.id
            record.audit_id = audit_id
            self._pending_gate = None

    def _try_action(self, entity: EntityState, kind: str, params: dict, reason: str, now: dt.datetime) -> Action | None:
        if not self._gate(entity, kind, params, now).allowed:
            return None
        if kind in TOUCH_COUNTED_KINDS:
            self._record_touch(entity, now)
        return self._emit_action(entity, kind, params, reason, now, bounds_checked=True)

    def _emit_action(self, entity: EntityState, kind: str, params: dict, reason: str, now: dt.datetime, bounds_checked: bool = True) -> Action:
        action = Action(
            id=self._next_action_id(), entity_id=entity.entity_id, kind=kind,
            params=params, reason=reason, bounds_checked=bounds_checked, ts=now,
        )
        entry = self._audit(entity.entity_id, "action", f"{kind}: {reason}", {"action_id": action.id, "params": params}, now)
        self._claim_gate(action, entry.id)
        return action

    # -- the human side of the loop -----------------------------------------
    #
    # Everything below is reached ONLY from the review-queue API routes. Each
    # one writes its audit entry BEFORE it changes anything, and none of them
    # can produce an action the ordinary path could not: approvals re-enter
    # `_gate()`, rejections fall back down the same ladder a bounds-block does,
    # and the formal-notice draft is refused outright.

    def pending_held_actions(self) -> list[HeldAction]:
        return [h for h in self.held_actions if h.status == "pending"]

    def held_action(self, held_id: str) -> HeldAction | None:
        return next((h for h in self.held_actions if h.id == held_id), None)

    def _pending_held(self, held_id: str) -> HeldAction:
        held = self.held_action(held_id)
        if held is None:
            raise ReviewQueueError(f"unknown held action {held_id}", 404)
        if held.status != "pending":
            raise ReviewQueueError(
                f"held action {held_id} is already {held.status}; a queue item is decided once", 409
            )
        return held

    def approve_held(self, held_id: str, now: dt.datetime) -> dict:
        """The approve-click. Returns
        `{"held", "action", "blocked", "block_reason"}`.

        The gate runs HERE, at click time, against the entity and the debtor
        touch budget as they are NOW — which is the whole reason approval is a
        separate step rather than a rubber stamp on a decision already made. A
        hold created on a Tuesday and approved the following Monday is measured
        against Monday's budget; if the debtor has since had their two touches,
        the human's click is refused and audited like any other bound block.
        """
        held = self._pending_held(held_id)
        if not held.sendable:
            raise ReviewQueueError(FORMAL_NOTICE_REFUSAL, 403)

        entity = self._entity(held.entity_id)
        kind, params = held.action.kind, dict(held.action.params)
        self._audit(
            held.entity_id, "judgment", "human approved held action",
            {"held_id": held.id, "kind": kind, "params": params, "hold_reason": held.reason}, now,
        )

        gate = self._gate(entity, kind, params, now)
        if not gate.allowed:
            held.status = "blocked"
            held.resolved_ts = now
            held.resolution_note = f"approved by a human, then refused at click time: {gate.reason}"
            self._audit(
                held.entity_id, "sentinel", "human-approved action blocked at click time",
                {"held_id": held.id, "kind": kind, "reason": gate.reason}, now,
            )
            return {"held": held, "action": None, "blocked": True, "block_reason": gate.reason}

        if kind in TOUCH_COUNTED_KINDS:
            self._record_touch(entity, now)
        action = self._emit_action(entity, kind, params, f"human approved: {held.action.reason}", now)
        held.status = "approved"
        held.resolved_ts = now
        held.emitted_action_id = action.id
        held.resolution_note = f"emitted as {action.id} after re-passing check_bounds at click time"
        return {"held": held, "action": action, "blocked": False, "block_reason": None}

    def reject_held(self, held_id: str, now: dt.datetime) -> dict:
        """The reject-click. The entity falls back down the ladder exactly the
        way a bounds-block does — a rejected mandate offer tries the payment
        link, which is what `_decide_money_action` would have done had the
        mandate been refused by a bound.

        The fallback is NOT re-held. Re-holding it would be an infinite loop
        with a human at the bottom of it, and the click that just happened IS
        the human review the money gate exists to obtain.
        """
        held = self._pending_held(held_id)
        if not held.sendable:
            raise ReviewQueueError(FORMAL_NOTICE_REFUSAL, 403)

        self._audit(
            held.entity_id, "judgment", "human rejected held action",
            {"held_id": held.id, "kind": held.action.kind, "params": dict(held.action.params),
             "hold_reason": held.reason}, now,
        )
        held.status = "rejected"
        held.resolved_ts = now

        entity = self._entity(held.entity_id)
        fallback: Action | None = None
        if held.action.kind == "mandate_offer":
            fallback = self._try_action(
                entity, "link", {"amount_inr": entity.invoice_amount_inr},
                "human rejected the mandate offer, falling back to payment link", now,
            )
        held.resolution_note = (
            f"fell back to {fallback.kind} {fallback.id}" if fallback is not None
            else "no fallback action was available; the ladder continues at its next scheduled beat"
        )
        return {"held": held, "action": fallback}

    def mark_held_handled(self, held_id: str, now: dt.datetime, note: str | None = None) -> HeldAction:
        """The only button a `sendable=False` item has. It sends nothing — it
        records that a human dealt with it outside the system."""
        held = self._pending_held(held_id)
        self._audit(
            held.entity_id, "judgment", "human marked held item handled outside the system",
            {"held_id": held.id, "kind": held.action.kind, "hold_reason": held.reason, "note": note}, now,
        )
        held.status = "handled"
        held.resolved_ts = now
        held.resolution_note = note or "merchant handled this outside the system; the agent sent nothing"
        return held

    def manual_reminder(
        self,
        entity_id: str,
        channel: Literal["voice", "sms", "message"],
        now: dt.datetime,
        custom_text: str | None = None,
    ) -> dict:
        """THE OPERATOR-TRIGGERED REMINDER (packet P14). Returns
        `{"action", "blocked", "block_reason"}` — the same shape `approve_held`
        returns, so the dashboard renders a refusal here with the same UI it
        already uses for a stale approval.

        THIS IS NOT A DEMO CONSOLE, AND THE DIFFERENCE IS THE POINT.
        `check_bounds()` runs here, at click time, against the debtor's touch
        budget as of the click — exactly as it does for `approve_held`. A manual
        reminder competes for the SAME `MAX_TOUCHES_PER_WEEK` budget an
        autonomous nudge does, because the bound protects the human being
        contacted and that human cannot tell whether the third message this week
        was decided by a ladder or by a merchant with a mouse. A channel that
        could be spent by hand for free would be a hole in bound #4, not a
        feature. So: a click that would exceed the cap comes back
        `blocked: True`, audited, with nothing sent.

        Everything else on the path is unchanged and shared, deliberately:
          * `_gate()` is the same chokepoint (so the merchant kill-switch, the
            terminal-state stop and the legal-stage refusal all apply),
          * the touch is recorded through `_record_touch` like any other,
          * the Action is built by `_emit_action`, audited before it is
            returned, carrying `bounds_checked=True`.

        The AUTONOMOUS path is untouched by this method. `_ESCALATE_ACTION`
        already maps `ESCALATE_2 -> ("voice", {"stage": "firm"})` and still
        reaches the wire through `_decide_action` -> `_try_action` -> `_gate`.
        P14 changed what a `voice` action PRODUCES downstream (real generated
        audio), not when the agent decides to make one.

        `custom_text` is the operator's own words, and it is content only: it is
        carried in `params` for the dispatch layer to speak/send, and it is
        never read by `check_bounds()`, never parsed for an amount or a date,
        and cannot influence a state transition. The stage is fixed
        (`MANUAL_REMINDER_STAGE`) rather than accepted from the caller.
        """
        if channel not in MANUAL_REMINDER_CHANNELS:
            raise ReviewQueueError(
                f"channel must be one of {list(MANUAL_REMINDER_CHANNELS)}", 422
            )
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ReviewQueueError(f"unknown entity {entity_id}", 404)

        params: dict[str, Any] = {"stage": MANUAL_REMINDER_STAGE, "manual": True}
        if custom_text:
            params["custom_text"] = custom_text

        self._audit(
            entity_id, "judgment", f"merchant requested a manual {channel} reminder",
            {"channel": channel, "params": dict(params),
             "note": "operator-triggered; check_bounds runs at click time exactly as it does "
                     "for an autonomous nudge — this does not bypass the touch cap",
             "debtor_id": self._debtor_id(entity_id)}, now,
        )

        gate = self._gate(entity, channel, params, now)
        if not gate.allowed:
            self._audit(
                entity_id, "sentinel", f"manual {channel} reminder blocked at click time",
                {"channel": channel, "reason": gate.reason}, now,
            )
            return {"action": None, "blocked": True, "block_reason": gate.reason}

        self._record_touch(entity, now)
        action = self._emit_action(
            entity, channel, params, f"manual {channel} reminder requested by the merchant", now,
        )
        return {"action": action, "blocked": False, "block_reason": None}

    # -- RBI E-Mandate Framework: pre-/post-debit notices (packet, 2026-08-27)
    #
    # These are mandatory transaction disclosures, not discretionary outreach:
    # a T-1 warning before every mandate execution, and a confirmation after
    # every completed one, both required regardless of how many times the
    # debtor has already been contacted that week. `mandate_pre_debit_notice`
    # / `mandate_post_debit_notice` are deliberately absent from
    # `state_machine.OUTBOUND_KINDS` and `TOUCH_COUNTED_KINDS` (see
    # engine/schemas.py's ActionKind docstring), so `_gate()`'s touch-cap,
    # terminal-state and kill-switch checks do not apply to them and
    # `check_bounds()` allows unconditionally by construction — still called
    # via `_try_action`, so law 4 ("every action passes check_bounds()") holds
    # to the letter even though nothing here can ever fail it. The amount is
    # always the ledger's own invoice record (law 2), never a caller-supplied
    # number.

    def pre_debit_notice(self, entity_id: str, execute_on: dt.date, now: dt.datetime) -> Action | None:
        """T-1 pre-debit notification, due the day before a confirmed mandate
        executes. Returns None only if the entity doesn't exist (the caller
        already checked the mandate is still pending and the entity isn't
        terminal before scheduling this)."""
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        amount = entity.invoice_amount_inr
        params: dict[str, Any] = {"amount_inr": amount, "execute_on": execute_on.isoformat()}
        return self._try_action(
            entity, "mandate_pre_debit_notice", params,
            f"RBI E-Mandate Framework: pre-debit (T-1) notice, Rs.{amount:,} debits on {execute_on.isoformat()}",
            now,
        )

    def post_debit_notice(self, entity_id: str, execute_action_id: str, now: dt.datetime) -> Action | None:
        """Post-transaction confirmation, due immediately after a mandate
        executes successfully. `execute_action_id` (the `mandate_execute`
        Action's own id) doubles as the transaction reference the notice
        quotes — it is already a real, ledger-issued, unique id, so no new
        reference scheme is invented for it."""
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        amount = entity.invoice_amount_inr
        params: dict[str, Any] = {"amount_inr": amount, "txn_ref": execute_action_id}
        return self._try_action(
            entity, "mandate_post_debit_notice", params,
            f"RBI E-Mandate Framework: post-debit confirmation, Rs.{amount:,} debited, ref {execute_action_id}",
            now,
        )

    def resolve_handoff(self, entity_id: str, resolution: str, now: dt.datetime) -> EntityState:
        """Close an open handoff/dispute. The ONLY producer of
        `human_resolution`, the one event allowed to move a terminal state —
        see `state_machine.HUMAN_RESOLUTION_EVENT` for why that exception
        exists and how it is contained."""
        if resolution not in state_machine.HUMAN_RESOLUTIONS:
            raise ReviewQueueError(
                f"resolution must be one of {sorted(state_machine.HUMAN_RESOLUTIONS)}", 422
            )
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ReviewQueueError(f"unknown entity {entity_id}", 404)
        if entity.state not in state_machine.HUMAN_RESOLVABLE_STATES:
            raise ReviewQueueError(
                f"{entity_id} is {entity.state}, not an open handoff or dispute", 409
            )
        self._audit(
            entity_id, "judgment", f"human resolution: {resolution}",
            {"resolution": resolution, "from_state": entity.state,
             "note": "the one event allowed to move a terminal state; API review queue only"}, now,
        )
        self.process_event(state_machine.HUMAN_RESOLUTION_EVENT, entity_id, {"resolution": resolution}, now)
        return self.entities[entity_id]

    def set_paused(self, entity_id: str, paused: bool, now: dt.datetime) -> EntityState:
        """The merchant kill-switch (master doc §3.6: "pause any thread with one
        click"). Audited before it takes effect, in both directions — an
        unpause is as much a decision as a pause."""
        entity = self.entities.get(entity_id)
        if entity is None:
            raise ReviewQueueError(f"unknown entity {entity_id}", 404)
        self._audit(
            entity_id, "judgment",
            "thread paused by merchant (kill-switch)" if paused else "thread unpaused by merchant",
            {"paused": paused, "state": entity.state}, now,
        )
        self.paused[entity_id] = paused
        return entity

    def paused_entities(self) -> list[str]:
        return sorted(eid for eid, is_paused in self.paused.items() if is_paused)
