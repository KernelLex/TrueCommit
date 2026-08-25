"""Promise ledger — orchestrates state_machine + trust into Actions, with the
audit trail written BEFORE any action is returned for execution (CLAUDE.md
law #3: "every action writes to the audit log before it executes. No
exceptions."). Zero LLM (BUILD.md Day 5).

Reserve pre-check (master doc §8.6, Tier-0): a `payment_failed` event for an
entity with an active reserve short-circuits the entire ladder — no touches,
straight to a recovered/KEPT outcome, logged as such.
"""

import datetime as dt

from engine.judgment import state_machine, trust
from engine.judgment.state_machine import EntityState
from engine.schemas import Action, AuditEntry, Invoice, Promise, TrustState

# Escalation stage -> the action the ladder attempts at that stage
# (master doc §3.2: "+2d firm notice -> +5d voice note -> +10d formal -> HARD STOP -> human").
_ESCALATE_ACTION: dict[str, tuple[str, dict]] = {
    "ESCALATE_1": ("message", {"stage": "firm"}),
    "ESCALATE_2": ("voice", {"stage": "firm"}),
    "ESCALATE_3": ("message", {"stage": "legal"}),  # deliberately hits the legal-stage bound
    "ESCALATE_4": ("human_handoff", {"reason": "escalation ladder exhausted"}),
}
TOUCH_COUNTED_KINDS = {"link", "mandate_offer", "message", "voice"}


class Ledger:
    def __init__(self) -> None:
        self.entities: dict[str, EntityState] = {}
        self.debtor_of: dict[str, str] = {}
        self.reserve_active: dict[str, bool] = {}
        self.promises: dict[str, Promise] = {}
        self.trust: dict[str, TrustState] = {}
        self.audit: list[AuditEntry] = []
        self._action_seq = 0
        self._audit_seq = 0
        self._promise_seq = 0

    # -- setup -----------------------------------------------------------

    def register_invoice(self, invoice: Invoice) -> None:
        entity = self._entity(invoice.id)
        entity.invoice_amount_inr = invoice.amount_inr
        self.entities[invoice.id] = entity
        self.debtor_of[invoice.id] = invoice.debtor_id

    def register_reserve(self, entity_id: str, active: bool) -> None:
        self.reserve_active[entity_id] = active

    # -- internals ---------------------------------------------------------

    def _entity(self, entity_id: str) -> EntityState:
        return self.entities.get(entity_id) or EntityState(entity_id=entity_id)

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
        debtor_id = self.debtor_of.get(entity_id, entity_id)

        if event_type == "payment_failed" and self.reserve_active.get(entity_id):
            return self._tier0_recover(entity, entity_id, now)

        prev_state = entity.state
        new_state = state_machine.transition(entity, event_type, payload, now)
        self.entities[entity_id] = new_state
        self._audit(entity_id, "judgment", f"{event_type}: {prev_state} -> {new_state.state}", {"event": event_type, "payload": payload}, now)

        self._update_trust(event_type, new_state, debtor_id, now)
        self._update_promise(event_type, entity_id, debtor_id, new_state, payload, now)

        if new_state.state == prev_state:
            return None  # nothing changed -> no new action to (re-)decide; avoids re-firing on every unrelated event
        return self._decide_action(new_state, now)

    def _tier0_recover(self, entity: EntityState, entity_id: str, now: dt.datetime) -> Action:
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

    def _decide_action(self, entity: EntityState, now: dt.datetime) -> Action | None:
        state = entity.state

        if state == "DISPUTED":
            return self._emit_action(entity, "evidence_packet", {"reason": "dispute raised"}, "dispute -> instant stop, evidence packet, human", now)

        if state == "MANDATED":
            result = self._try_action(entity, "mandate_offer", {"amount_inr": entity.invoice_amount_inr}, "L1+ promise, offering scheduled mandate", now)
            if result is not None:
                return result
            # blocked (cap exceeded / renegotiation cap / already refused) -> master doc §3.4: "larger -> partial + link"
            return self._try_action(entity, "link", {"amount_inr": entity.invoice_amount_inr}, "mandate blocked, falling back to payment link", now)

        if state == "LINKED":
            return self._try_action(entity, "link", {"amount_inr": entity.invoice_amount_inr}, "fallback to payment link", now)

        if state == "AT_RISK":
            return self._try_action(entity, "mandate_execute", {"amount_inr": entity.invoice_amount_inr, "retry": entity.retry_count}, "retrying mandate execution once", now)

        if state in _ESCALATE_ACTION:
            kind, params = _ESCALATE_ACTION[state]
            result = self._try_action(entity, kind, params, f"escalation stage {state}", now)
            if result is not None:
                return result
            # blocked (almost certainly the legal-stage bound at ESCALATE_3) -> route to a human instead of silently dropping
            return self._emit_action(entity, "human_handoff", {"reason": f"{state} action blocked, routing to merchant/human"}, f"{state} bound-blocked", now)

        return None

    def _try_action(self, entity: EntityState, kind: str, params: dict, reason: str, now: dt.datetime) -> Action | None:
        result = state_machine.check_bounds(entity, kind, params, now)
        if not result.allowed:
            self._audit(entity.entity_id, "sentinel", f"action blocked: {kind}", {"reason": result.reason, "params": params}, now)
            return None
        if kind in TOUCH_COUNTED_KINDS:
            entity.touches.append(now)
            self.entities[entity.entity_id] = entity
        return self._emit_action(entity, kind, params, reason, now, bounds_checked=True)

    def _emit_action(self, entity: EntityState, kind: str, params: dict, reason: str, now: dt.datetime, bounds_checked: bool = True) -> Action:
        action = Action(
            id=self._next_action_id(), entity_id=entity.entity_id, kind=kind,
            params=params, reason=reason, bounds_checked=bounds_checked, ts=now,
        )
        self._audit(entity.entity_id, "action", f"{kind}: {reason}", {"action_id": action.id, "params": params}, now)
        return action
