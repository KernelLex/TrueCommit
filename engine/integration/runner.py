"""WorldRunner — the integration runner (master doc §4.2, BUILD.md Day 7).

WHAT THIS IS
------------
`sim/run.py` is the STANDALONE simulator: frozen personas + a virtual clock
producing an event stream against a simplified internal state model. This
module keeps that file's persona tables and touch cadence and REPLACES its
simplified state logic with the real thing:

    real perception (engine/perception, provider-pluggable, cached)
        -> real judgment (engine/judgment/ledger.process_event)
            -> real action layer (engine/action/{messenger,sentinel,evidence,razorpay_client})

Pressing "Advance 1 Day" on the dashboard calls `advance(1)` here, and the
funnel/trust/₹-recovered numbers it moves are produced by the same code path a
production event would take. That is the whole point of the TIME-WARP demo
beat: nothing on screen is a mock.

THE FOUR RULES THIS FILE OBEYS (and how)
----------------------------------------
1. **State moves only through `ledger.process_event`.** This module never
   assigns an entity state and never constructs an `Action`. Everything it
   wants to happen it has to *ask for* by emitting an event; whether an action
   comes back — and which one — is the ledger's call, made behind
   `check_bounds()` with the audit entry already written (CLAUDE.md laws 3+4).

   Consequence worth stating plainly: this runner writes no outbound copy that
   isn't answering an Action the ledger handed back. That includes the plain
   gentle nudge — the ledger's `_OUTREACH_ACTION` table turns a scheduled
   outreach beat into a bounds-checked `message` Action (stage gentle/firm by
   ladder position), and the runner only drafts copy once it has one. When the
   ledger declines (the debtor already had their two touches this week, or the
   ladder is at a merchant-review/handoff stage), `_emit` returns None, no
   message is queued, and the *block* is what lands in the audit trail. A
   blocked touch is a normal, expected outcome here — with the cap scoped per
   debtor, five invoices sharing one debtor cannot all be chased on the same
   day, and the three that aren't are exactly the bound doing its job.
   See tracking/BUILD_LOG.md (2026-08-26, packets P2 + P8).

2. **Amounts come from ledger records only** (CLAUDE.md law 2). Extractions
   here really do carry amounts the "debtor" stated — they ride in the event
   payload and are ignored by every money decision. `check_bounds()` rejects
   any mandate whose amount is not identical to the ledger's invoice record,
   and the Razorpay calls below are handed `action.params["amount_inr"]`,
   which the ledger wrote. Scene-2 carts are registered as ledger records
   before their Tier-0 debit for the same reason: so the debited amount has a
   ledger record behind it rather than an event payload.

3. **Personas react to message PROPERTIES only** (CLAUDE.md law 7). The
   persona calls below are passed a stage (gentle/firm/formal) and, for the
   mandate table, nothing at all. Nothing about which provider, which rail, or
   which arm sent a message is visible to them. `sim/personas.py` is imported
   read-only and never touched.

4. **SEED=42, deterministic** (CLAUDE.md law 6). One seeded `random.Random`,
   drawn from in a fixed day -> entity -> beat order. Two fresh runners doing
   the same `advance()` sequence produce byte-identical audit trails; there is
   a test for it.

REAL RAZORPAY IS OPT-IN AND RATE-LIMITED BY DESIGN
--------------------------------------------------
Default (env unset): zero network calls, so the test suite stays offline and
free. With `PK_REAL_RAZORPAY=1`, at most the FIRST payment-link action and the
FIRST mandate-offer action of a run reach the real TEST-mode sandbox; every
later one flows through the simulated messenger. Real calls are wrapped in the
Sentinel (`record_send_attempt` -> retry/backoff/dead-letter), and the real
`short_url` is written into the audit trail — that is BUILD.md Day 6's "real
test-mode Payment Link URL appears in audit trail" criterion.
"""

import datetime as dt
import os
import random
from pathlib import Path
from typing import Any, Callable, Literal

from data.generate import DEBTOR_BY_ID
from engine.action import razorpay_client
from engine.action.evidence import build_evidence_packet
from engine.action.messenger import Messenger, Rail
from engine.action.razorpay_client import RazorpayError
from engine.action.sentinel import MAX_RETRIES, Sentinel
from engine.judgment.ledger import Ledger
from engine.judgment.state_machine import MAX_TOUCHES_PER_WEEK, TERMINAL_STATES, TOUCH_WINDOW_DAYS
from engine.perception.providers import get_provider
from engine.schemas import Action, AuditEntry, Cart, Event, Extraction, Invoice, Message
from sim.personas import decide_mandate_move, decide_reply_move, keeps_promise, mandate_executes
from sim.run import (
    CONDITIONAL_DUE_OFFSET,
    FIRM_DUE_OFFSET,
    MANDATE_EXECUTE_OFFSET,
    SIM_EPOCH,
    TOUCH_SCHEDULE,
)

ROOT = Path(__file__).resolve().parents[2]
SEED = 42

# --- cadence -----------------------------------------------------------------
TOUCH_STAGE_BY_DAY: dict[int, str] = {day: stage for day, stage in TOUCH_SCHEDULE}
LAST_TOUCH_DAY = max(TOUCH_STAGE_BY_DAY)
SWEEP_GRACE_DAYS = 7
FINAL_SWEEP_DAY = LAST_TOUCH_DAY + SWEEP_GRACE_DAYS
"""From this virtual day on, an entity with nothing left pending is handed to a
human rather than left hanging — CLAUDE.md law 5 ("every recovery path
terminates, no silent deaths") made true by the runner, not by luck."""

CART_BEAT_DAY = 1
LINK_TIMEOUT_DAYS = 2  # engine/action/sentinel.LINK_OPEN_TIMEOUT_HOURS == 48
MANDATE_LEVELS = ("L1", "L2", "L3")
"""Promise levels firm enough to be worth converting into an instrument. L4/L5
carry no commitment to capture, so no mandate is offered against them."""

ENV_REAL_RAZORPAY = "PK_REAL_RAZORPAY"

# --- rails (master doc §8.5) -------------------------------------------------
def _rail_for(kind: str, channel: str) -> Rail:
    if kind == "mandate_offer":
        return "mandate_link"
    if kind == "link":
        return "wa_native_payment" if channel == "wa" else "plain_link"
    if kind == "voice":
        return "voice_note"
    return "text_only"


# ---------------------------------------------------------------------------
# Persona move -> message text
#
# A small deterministic template table. The seeded rng picks the variant; the
# amount is the ledger's invoice amount (a debtor restating the figure they
# were chased for) and the weekday is derived from the offset the rng drew, so
# the REAL extractor has a real, explicit amount+date to find. Every template
# below was checked against the heuristic provider's rules to make sure it
# lands on the level the persona move means — the extractor is not told the
# answer, it has to read it out of the text like any other message.
# ---------------------------------------------------------------------------

PROMISE_FIRM_TEMPLATES = (
    "will clear Rs.{amount:,} by {weekday} pakka",
    "we will transfer Rs.{amount:,} on {weekday}, confirmed",
    "paying Rs.{amount:,} by {weekday} without fail",
)
PROMISE_VAGUE_TEMPLATES = (
    "trying my best on this one, things are tight right now",
    "it's on our radar, going through a slow patch",
    "will sort it out soon, bear with us",
)
PROMISE_CONDITIONAL_TEMPLATES = (
    "we'll clear this once our client's payment comes in",
    "can pay as soon as our supplier refunds the overcharge",
    "will settle Rs.{amount:,} once the GST refund lands",
    "can I do half now and the rest in 2 weeks?",
)
DISPUTE_TEMPLATES = (
    "we're not paying this, the consignment arrived damaged",
    "we are disputing this invoice, the lot does not match the approved sample",
    "not paying, we never received this order",
)
MANDATE_CONFIRM_TEMPLATES = (
    "haan set it up, easier for me",
    "yes please go ahead with the auto-debit",
    "ok set it up",
)
MANDATE_REFUSE_TEMPLATES = (
    "I don't do auto-debit setups, will transfer Rs.{amount:,} by {weekday} manually",
    "no auto-debit please, but Rs.{amount:,} will be cleared by {weekday}",
)

ESCALATION_TEXT = {
    "gentle": "Hi — a quick reminder that Rs.{amount:,} against {entity_id} is past its due date. When can we expect it?",
    "firm": "Firm reminder: Rs.{amount:,} against {entity_id} is still outstanding. Please confirm a payment date.",
    "legal": "[merchant review required] Formal notice draft for {entity_id}, Rs.{amount:,}.",
    # Master doc §2.3's clarify gate. ONE question, and it asks for exactly the
    # two fields the extractor could not read with confidence — an amount and a
    # date — while quoting the LEDGER's outstanding figure, never the one
    # perception thought it saw.
    "clarify": (
        "Just so we get this right before setting anything up: how much are you clearing "
        "against {entity_id}, and on which date? (Rs.{amount:,} is outstanding.)"
    ),
}
VOICE_TEXT = "[voice note, Hinglish] {entity_id} ka Rs.{amount:,} abhi tak pending hai, please clear kara dijiye."


def _real_razorpay_enabled() -> bool:
    return os.environ.get(ENV_REAL_RAZORPAY, "").strip().lower() in {"1", "true", "yes", "on"}


class WorldRunner:
    """Owns the virtual day counter, the Ledger, the Messenger, the Sentinel,
    the seeded rng and the perception provider — i.e. one whole world.

    `day` is the number of virtual days that have ELAPSED: a fresh runner is at
    day 0 with nothing simulated, `advance(1)` simulates day-index 0 and leaves
    `day == 1`, `advance(45)` simulates day-indexes 0..44 and leaves `day == 45`
    ("Run to Day 45 ⏩").
    """

    def __init__(
        self,
        seed: int = SEED,
        provider: str | None = None,
        root: Path = ROOT,
        real_razorpay: bool | None = None,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.ledger = Ledger()
        self.messenger = Messenger()
        self.sentinel = Sentinel()
        self.provider = get_provider(provider)
        self.provider_name = self.provider.name
        self.real_razorpay = _real_razorpay_enabled() if real_razorpay is None else real_razorpay

        self.day = 0
        self.events: list[Event] = []
        self.actions: list[Action] = []
        self.extractions: list[Extraction] = []
        self.triage: dict[str, Any] = {}
        self.cart_causes: dict[str, Any] = {}
        self.evidence_packets: list[Any] = []

        self.invoices: dict[str, Invoice] = {}
        self.carts: dict[str, Cart] = {}
        self.threads: dict[str, list[Message]] = {}
        self.channel_of: dict[str, Literal["wa", "email"]] = {}

        self.active_invoice_ids: list[str] = []
        self.disputed_invoice_ids: list[str] = []

        # scheduling: day -> ordered list of (kind, entity_id, data)
        self._schedule: dict[int, list[tuple[str, str, dict]]] = {}
        self._pending_promise: dict[str, int] = {}   # entity_id -> promise token
        self._mandate_pending: set[str] = set()
        self._promise_token = 0
        self._event_seq = 0
        self._audit_seq = 0
        self._msg_seq: dict[str, int] = {}
        self._real_link_used = False
        self._real_mandate_used = False

        self._load(root)

    # -- setup ---------------------------------------------------------------

    def _load(self, root: Path) -> None:
        import json

        for row in json.loads((root / "data" / "invoices.json").read_text(encoding="utf-8")):
            invoice = Invoice.model_validate(row)
            self.invoices[invoice.id] = invoice
            self.ledger.register_invoice(invoice)
        for row in json.loads((root / "data" / "carts.json").read_text(encoding="utf-8")):
            cart = Cart.model_validate(row)
            self.carts[cart.id] = cart
            if cart.reserve_active:
                self.ledger.register_reserve(cart.id, True)

        for path in sorted((root / "data" / "conversations").glob("*.json")):
            thread = json.loads(path.read_text(encoding="utf-8"))
            entity_id = thread["invoice_id"]
            self.threads[entity_id] = [Message.model_validate(m) for m in thread["messages"]]
            self.channel_of[entity_id] = thread["channel"]

        self.active_invoice_ids = sorted(
            i.id for i in self.invoices.values() if i.status in ("open", "overdue")
        )
        self.disputed_invoice_ids = sorted(
            i.id for i in self.invoices.values() if i.status == "disputed"
        )

    # -- clock ---------------------------------------------------------------

    def advance(self, n_days: int = 1) -> dict:
        """Run `n_days` virtual days through the real pipeline. Returns what
        moved during THIS call (the dashboard's Advance-Day payload)."""
        first_event = len(self.events)
        first_action = len(self.actions)
        first_audit = len(self.ledger.audit)

        for _ in range(max(0, n_days)):
            self._run_day(self.day)
            self.day += 1

        return {
            "day": self.day,
            "new_events": len(self.events) - first_event,
            "new_actions": len(self.actions) - first_action,
            "new_audit": len(self.ledger.audit) - first_audit,
            "funnel_summary": self.funnel_summary(),
        }

    def _run_day(self, day: int) -> None:
        if day == 0:
            self._day_zero(day)
        self._run_scheduled(day)
        self._run_outreach(day)
        if day == CART_BEAT_DAY:
            self._cart_beats(day)
        if day >= FINAL_SWEEP_DAY:
            self._sweep_idle(day)

    def _ts(self, day: int) -> dt.datetime:
        """Day-granular timestamps on purpose: the Sentinel's 48h link window
        and the ledger's 7-day touch window are both day-scale, and giving
        every beat within a day the same instant keeps those comparisons exact
        instead of hostage to intra-day ordering."""
        return SIM_EPOCH + dt.timedelta(days=day)

    def _schedule_at(self, day: int, kind: str, entity_id: str, data: dict) -> None:
        self._schedule.setdefault(day, []).append((kind, entity_id, data))

    def _run_scheduled(self, day: int) -> None:
        for kind, entity_id, data in self._schedule.pop(day, []):
            if kind == "promise_due":
                self._resolve_promise(day, entity_id, data)
            elif kind == "mandate_execute":
                self._resolve_mandate_execution(day, entity_id)
            elif kind == "link_timeout":
                self._resolve_link_timeout(day, entity_id, data)

    # -- day 0: triage + the invoices that arrive already disputed ------------

    def _day_zero(self, day: int) -> None:
        for entity_id in self.active_invoice_ids:
            self._triage(entity_id, day)
        for entity_id in self.disputed_invoice_ids:
            self._triage(entity_id, day)
            # The dataset records these as disputed on arrival. Dispute is an
            # instant stop from any state (bound #4) -> evidence packet -> human.
            self._emit("dispute_raised", entity_id, {"source": "invoice status on record"}, day)

    def _triage(self, entity_id: str, day: int) -> None:
        invoice = self.invoices[entity_id]
        cause = self.provider.triage(invoice, self._thread(entity_id))
        self.triage[entity_id] = cause
        self._audit(
            entity_id, "perception", f"triage: {cause.cause} (conf {cause.confidence})",
            {"cause": cause.cause, "confidence": cause.confidence, "evidence": cause.evidence,
             "provider": self.provider_name}, day,
        )
        self._emit(
            "invoice_triaged", entity_id,
            {"cause": cause.cause, "confidence": cause.confidence}, day,
        )

    # -- outreach cadence ----------------------------------------------------

    def _run_outreach(self, day: int) -> None:
        stage = TOUCH_STAGE_BY_DAY.get(day)
        if stage is None:
            return
        for entity_id in self.active_invoice_ids:
            entity = self.ledger.entities.get(entity_id)
            if entity is None or entity.state in TERMINAL_STATES:
                continue
            if self.ledger.paused.get(entity_id):
                # The merchant kill-switch (master doc §3.6). The ledger would
                # refuse the action anyway — `_gate()` blocks every outbound
                # kind on a paused entity — but stopping here means the persona
                # is never asked to react either, so a paused thread produces no
                # simulated reply to a message that was never sent. The skip is
                # audited: a paused thread is visibly quiet, not silently dead.
                self._audit(entity_id, "sentinel", "outreach skipped: thread paused by merchant",
                            {"stage": stage, "state": entity.state}, day)
                continue
            if entity_id in self._mandate_pending or entity_id in self._pending_promise:
                continue  # a commitment is already live; chasing it now would be a wasted touch
            self._outreach(entity_id, stage, day)

    def _outreach(self, entity_id: str, stage: str, day: int) -> None:
        action = self._emit("outreach_sent", entity_id, {"stage": stage}, day)
        if action is None:
            # The ledger declined this touch — almost always the per-debtor
            # touch cap, occasionally a ladder position that has nothing to say
            # (ESCALATE_3/4). Either way nothing was sent, so there is nothing
            # for the debtor to react to: asking the persona for a reply here
            # would be simulating an answer to a message that never went out.
            # The block itself is already in the audit trail.
            return

        persona = self._persona(entity_id)
        move = decide_reply_move(self.rng, persona, stage)  # PROPERTIES only (law 7)
        self._audit(entity_id, "sentinel", f"debtor reply move: {move} (stage {stage})",
                    {"move": move, "stage": stage}, day)

        if move == "silence":
            return
        if move == "dispute":
            text = self.rng.choice(DISPUTE_TEMPLATES)
            self._inbound(entity_id, text, day)
            self._emit("dispute_raised", entity_id, {"stage": stage}, day)
            return

        extraction = self._reply_promise(entity_id, move, day)
        if extraction is None:
            return
        self._offer_instrument(entity_id, extraction, day)

    def _reply_promise(self, entity_id: str, move: str, day: int) -> Extraction | None:
        """Persona move -> real message text -> REAL extraction -> ledger event."""
        amount = self.invoices[entity_id].amount_inr
        if move == "promise_firm":
            offset = self.rng.randint(*FIRM_DUE_OFFSET)
            template = self.rng.choice(PROMISE_FIRM_TEMPLATES)
        elif move == "promise_conditional":
            offset = self.rng.randint(*CONDITIONAL_DUE_OFFSET)
            template = self.rng.choice(PROMISE_CONDITIONAL_TEMPLATES)
        else:  # promise_vague -> nothing concrete to capture
            self._inbound(entity_id, self.rng.choice(PROMISE_VAGUE_TEMPLATES), day)
            return None

        text = template.format(amount=amount, weekday=self._weekday(day + offset))
        extraction = self._inbound(entity_id, text, day)
        if extraction.level not in MANDATE_LEVELS:
            return None  # the extractor read no capturable commitment out of it

        self._book_promise(entity_id, extraction, day, offset)
        return extraction

    def _book_promise(self, entity_id: str, extraction: Extraction, day: int, offset: int) -> None:
        due_day = self._due_day(extraction, day, offset)
        # The payload carries what the debtor SAID (amount included). Note the
        # deliberate absence of `invoice_amount_inr`: that key is the only way
        # an event can move the ledger's own amount record, and perception is
        # never allowed to (CLAUDE.md law 2).
        self._emit(
            "extraction_received", entity_id,
            {
                "message_id": extraction.message_id, "level": extraction.level,
                "amount_inr": extraction.amount_inr, "confidence": extraction.confidence,
                "condition": extraction.condition,
                "due": (SIM_EPOCH + dt.timedelta(days=due_day)).date().isoformat(),
            },
            day,
        )
        self._promise_token += 1
        self._pending_promise[entity_id] = self._promise_token
        self._schedule_at(due_day, "promise_due", entity_id, {"token": self._promise_token})

    def _due_day(self, extraction: Extraction, day: int, offset: int) -> int:
        """Prefer the date the REAL extractor read out of the message; fall
        back to the drawn offset when it read none (an L2/L3 with no explicit
        date is exactly the case the extractor is designed to leave empty)."""
        if extraction.date is not None:
            candidate = (extraction.date - SIM_EPOCH.date()).days
            if candidate > day:
                return candidate
        return day + offset

    # -- promise -> instrument ----------------------------------------------

    def _offer_instrument(self, entity_id: str, extraction: Extraction, day: int) -> None:
        """A capturable promise from an eNACH-familiar debtor is the moment the
        whole thesis turns on: convert stated intent into a self-executing
        instrument instead of asking again later."""
        if not self.invoices[entity_id].enach_familiar:
            return
        entity = self.ledger.entities.get(entity_id)
        if entity is None or entity.state != "PROMISED":
            return

        action = self._emit("mandate_offer_requested", entity_id, {"level": extraction.level}, day)
        if action is None or action.kind != "mandate_offer":
            # The ledger declined to offer a mandate (amount over the cap, cap
            # on renegotiations, post-refusal) and fell back to a link. Nothing
            # for the mandate table to answer — the promise runs to its date.
            return

        move = decide_mandate_move(self.rng, self._persona(entity_id))  # instrument only (law 7)
        self._audit(entity_id, "sentinel", f"debtor mandate move: {move}", {"move": move}, day)

        if move == "ignore":
            return  # link never opened -> the scheduled 48h timeout handles it
        if move == "confirm_mandate":
            self._inbound(entity_id, self.rng.choice(MANDATE_CONFIRM_TEMPLATES), day)
            self._emit("mandate_confirmed", entity_id, {"amount_inr": action.params.get("amount_inr")}, day)
            self._pending_promise.pop(entity_id, None)  # the instrument supersedes the promise
            self._mandate_pending.add(entity_id)
            self._schedule_at(day + MANDATE_EXECUTE_OFFSET, "mandate_execute", entity_id, {})
            return

        # refuse_but_promise: no auto-debit, but a fresh manual commitment.
        offset = self.rng.randint(*FIRM_DUE_OFFSET)
        text = self.rng.choice(MANDATE_REFUSE_TEMPLATES).format(
            amount=self.invoices[entity_id].amount_inr, weekday=self._weekday(day + offset)
        )
        refusal = self._inbound(entity_id, text, day)
        if refusal.level in MANDATE_LEVELS:
            self._book_promise(entity_id, refusal, day, offset)
        self._emit("mandate_refused", entity_id, {"reason": "debtor declined auto-debit"}, day)

    # -- scheduled resolutions ----------------------------------------------

    def _resolve_promise(self, day: int, entity_id: str, data: dict) -> None:
        if self._pending_promise.get(entity_id) != data.get("token"):
            return  # superseded by a later promise or by a mandate
        entity = self.ledger.entities.get(entity_id)
        if entity is None or entity.state in TERMINAL_STATES:
            self._pending_promise.pop(entity_id, None)
            return
        self._pending_promise.pop(entity_id, None)
        kept = keeps_promise(self.rng, self._persona(entity_id))
        self._emit("promise_kept" if kept else "promise_broken", entity_id, {}, day)

    def _resolve_mandate_execution(self, day: int, entity_id: str) -> None:
        entity = self.ledger.entities.get(entity_id)
        if entity is None or entity.state in TERMINAL_STATES:
            self._mandate_pending.discard(entity_id)
            return
        ok = mandate_executes(self.rng, self._persona(entity_id))
        amount = entity.invoice_amount_inr  # ledger record, never the extraction
        action = self._emit(
            "mandate_execute_success" if ok else "mandate_execute_failed",
            entity_id, {"amount_inr": amount}, day,
        )
        if ok:
            self._mandate_pending.discard(entity_id)
            return
        if action is not None and action.kind == "mandate_execute":
            self._schedule_at(day + 1, "mandate_execute", entity_id, {})  # the one allowed retry
        else:
            self._mandate_pending.discard(entity_id)

    def _resolve_link_timeout(self, day: int, entity_id: str, data: dict) -> None:
        action_id = data["action_id"]
        if not self.sentinel.link_timed_out(action_id, self._ts(day)):
            return
        self._audit(
            entity_id, "sentinel",
            f"link never opened within {LINK_TIMEOUT_DAYS * 24}h — treating as soft refusal",
            {"action_id": action_id, "kind": data["kind"]}, day,
        )
        if data["kind"] != "mandate_offer":
            return  # a plain link timing out is a signal, not a state change
        entity = self.ledger.entities.get(entity_id)
        if entity is None or entity.state in TERMINAL_STATES:
            return
        self._emit("mandate_refused", entity_id, {"reason": "mandate link never opened (soft refusal)"}, day)

    # -- Scene 2: carts ------------------------------------------------------

    def _cart_beats(self, day: int) -> None:
        for cart_id in sorted(self.carts):
            cart = self.carts[cart_id]
            cause = self.provider.cart_cause(cart)
            self.cart_causes[cart_id] = cause
            self._audit(
                cart_id, "perception", f"cart cause: {cause.cause} (conf {cause.confidence})",
                {"cause": cause.cause, "confidence": cause.confidence, "evidence": cause.evidence,
                 "drop_stage": cart.drop_stage, "provider": self.provider_name}, day,
            )
            # Register the cart's ledger record BEFORE any money event, so the
            # Tier-0 debit amount comes from a ledger record (law 2) and not
            # from the event payload.
            self.ledger.register_invoice(self._cart_record(cart))
            self._emit(
                "cart_abandoned", cart_id,
                {"drop_stage": cart.drop_stage, "cause": cause.cause,
                 "reserve_active": cart.reserve_active}, day,
            )
            if cart.reserve_active:
                # master doc §8.6 — the 0-touches beat: the reserve pre-check
                # inside the ledger short-circuits the entire ladder.
                self._emit("payment_failed", cart_id, {"drop_stage": cart.drop_stage}, day)

    @staticmethod
    def _cart_record(cart: Cart) -> Invoice:
        """A Cart expressed as the ledger record shape. Not a real invoice —
        the ledger is entity-generic and only needs the authoritative amount."""
        return Invoice(
            id=cart.id, debtor_id=cart.customer_id, amount_inr=cart.amount_inr,
            issued=cart.ts.date(), due=cart.ts.date(), status="open",
            description=f"abandoned cart ({cart.drop_stage} stage)",
        )

    # -- termination sweep ---------------------------------------------------

    def _sweep_idle(self, day: int) -> None:
        """CLAUDE.md law 5: no silent deaths. Past the last scheduled touch,
        anything still open with nothing pending goes to a human."""
        for entity_id in self.active_invoice_ids:
            entity = self.ledger.entities.get(entity_id)
            if entity is None or entity.state in TERMINAL_STATES:
                continue
            if entity_id in self._pending_promise or entity_id in self._mandate_pending:
                continue
            self._emit(
                "escalation_exhausted", entity_id,
                {"reason": "touch schedule exhausted, no live commitment"}, day,
            )

    # -- the human side of the loop (packet P9) ------------------------------

    def now(self) -> dt.datetime:
        """The current virtual instant — what "now" means to a merchant
        clicking a review-queue button while the world sits at day N. The API's
        review-queue routes hand this to the ledger so a human click is
        timestamped inside the same clock the run uses, and so the touch window
        re-checked at approval time is measured against it."""
        return self._ts(self.day)

    def dispatch_action(self, action: Action) -> None:
        """Send an Action the LEDGER produced OUTSIDE the event loop — i.e. one
        a human approved (or the link a rejection fell back to). This module
        still constructs nothing: it is handed a finished, bounds-checked Action
        and does exactly what `_emit` does with one."""
        self.actions.append(action)
        self._dispatch(action, self.day)

    # -- the one way anything happens ---------------------------------------

    def _emit(self, event_type: str, entity_id: str, payload: dict, day: int) -> Action | None:
        now = self._ts(day)
        self._event_seq += 1
        event = Event(
            event_id=f"E-{self._event_seq:05d}", type=event_type,
            entity_id=entity_id, payload=payload, ts=now,
        )
        self.events.append(event)

        action = self.ledger.process_event(event_type, entity_id, payload, now)
        if action is None:
            return None
        self.actions.append(action)
        self._dispatch(action, day)
        return action

    # -- action layer --------------------------------------------------------

    def _dispatch(self, action: Action, day: int) -> None:
        now = self._ts(day)
        entity_id = action.entity_id
        kind = action.kind

        if kind in ("link", "mandate_offer"):
            detail = self._payment_instrument(action, day)
            text = self._instrument_text(action, detail)
            self._send(action, text, day, extra=detail)
            self.sentinel.track_link_sent(action.id, now)
            self._schedule_at(day + LINK_TIMEOUT_DAYS, "link_timeout", entity_id,
                              {"action_id": action.id, "kind": kind})
        elif kind == "message":
            stage = action.params.get("stage", "firm")
            self._send(action, ESCALATION_TEXT.get(stage, ESCALATION_TEXT["firm"]).format(
                amount=self._amount(entity_id), entity_id=entity_id), day)
        elif kind == "voice":
            self._send(action, VOICE_TEXT.format(amount=self._amount(entity_id), entity_id=entity_id), day)
        elif kind == "mandate_execute":
            self._audit(entity_id, "action", f"mandate execution attempt (retry {action.params.get('retry', 0)})",
                        {"action_id": action.id, "params": action.params, "source": action.params.get("source", "mandate")}, day)
        elif kind == "evidence_packet":
            self._build_evidence(action, day)
        elif kind == "human_handoff":
            self._audit(entity_id, "action", "routed to the human review queue",
                        {"action_id": action.id, "params": action.params}, day)

    def _instrument_text(self, action: Action, detail: dict) -> str:
        """Copy for a payment instrument. The amount is `action.params`' —
        i.e. the ledger's — and the date is the promise the ledger booked, so
        the message can never quote a number perception invented."""
        amount = action.params.get("amount_inr") or self._amount(action.entity_id)
        url = detail.get("short_url", "")
        if action.kind == "mandate_offer":
            due = self._promise_due_date(action.entity_id)
            when = due.strftime("%A") if due else "the agreed date"
            return (
                f"Rs.{amount:,} will auto-debit on {when} for {action.entity_id} — "
                f"approve the mandate once here: {url}"
            )
        return f"Payment link for {action.entity_id}, Rs.{amount:,}: {url}"

    def _promise_due_date(self, entity_id: str) -> dt.date | None:
        due = [p.due for p in self.ledger.promises.values()
               if p.invoice_id == entity_id and p.status == "pending"]
        return due[-1] if due else None

    def _send(self, action: Action, text: str, day: int, extra: dict | None = None) -> None:
        entity_id = action.entity_id
        channel = self.channel_of.get(entity_id, "wa")
        rail = _rail_for(action.kind, channel)
        message = self.messenger.send(action, channel, text, rail)
        self.messenger.mark_delivered(message.id)
        self.sentinel.record_send_attempt(action.id, entity_id, action.kind, True, self._ts(day))
        self._append_thread(entity_id, "out", channel, text, day)
        self._audit(
            entity_id, "action", f"{action.kind} dispatched on rail {rail} via {channel}",
            {"action_id": action.id, "message_id": message.id, "rail": rail,
             "channel": channel, "text": text, **(extra or {})}, day,
        )

    def _build_evidence(self, action: Action, day: int) -> None:
        entity_id = action.entity_id
        invoice = self.invoices.get(entity_id)
        if invoice is None:
            self._audit(entity_id, "action", "dispute stop (no invoice record for evidence packet)",
                        {"action_id": action.id}, day)
            return
        summary = (
            f"{DEBTOR_BY_ID.get(invoice.debtor_id, {}).get('name', invoice.debtor_id)} disputes "
            f"{invoice.id} (Rs.{invoice.amount_inr:,}); delivery_confirmed="
            f"{'yes' if invoice.delivery_confirmed else 'no'}. Ladder stopped, routed to human review."
        )
        packet = build_evidence_packet(invoice, self._thread(entity_id), self._ts(day), summary=summary)
        self.evidence_packets.append(packet)
        self._audit(entity_id, "action", "evidence packet built, ladder stopped",
                    {"action_id": action.id, "summary": summary,
                     "delivery_confirmed": invoice.delivery_confirmed,
                     "excerpt_messages": len(packet.thread_excerpt)}, day)

    # -- Razorpay: opt-in, rate-limited, Sentinel-wrapped --------------------

    def _payment_instrument(self, action: Action, day: int) -> dict:
        """Returns the instrument detail recorded in the audit entry. Real
        sandbox call only when opted in AND this is the first of its kind."""
        kind = action.kind
        amount = action.params.get("amount_inr")  # ledger's number, never perception's
        if not self.real_razorpay or not isinstance(amount, int):
            return {"simulated": True, "short_url": self._sim_url(action)}
        if kind == "link" and self._real_link_used:
            return {"simulated": True, "short_url": self._sim_url(action), "reason": "real-call budget for links already used this run"}
        if kind == "mandate_offer" and self._real_mandate_used:
            return {"simulated": True, "short_url": self._sim_url(action), "reason": "real-call budget for mandates already used this run"}
        if kind == "link":
            self._real_link_used = True
        else:
            self._real_mandate_used = True
        return self._real_razorpay_call(action, amount, day)

    def _real_razorpay_call(self, action: Action, amount: int, day: int) -> dict:
        entity_id = action.entity_id
        invoice = self.invoices.get(entity_id)
        debtor = DEBTOR_BY_ID.get(invoice.debtor_id, {}) if invoice else {}
        customer = {
            "name": debtor.get("name", entity_id),
            # Synthetic, non-routable demo contact — never real PII. It is NOT
            # a repeated-digit filler on purpose: `POST /payment_links` rejects
            # those outright ("Recurring digits in customer contact are
            # disallowed", found live 2026-08-26 — see tracking/BUILD_LOG.md),
            # even though `subscription_registration/auth_links` accepts them.
            "contact": "+919812345678",
            "email": "promise-keeper-demo@example.com",
        }
        description = f"Promise Keeper {action.kind} for {entity_id}"
        now = self._ts(day)

        for _ in range(MAX_RETRIES + 1):
            try:
                if action.kind == "link":
                    response = razorpay_client.create_payment_link(amount, description, customer)
                else:
                    response = razorpay_client.create_mandate_registration_link(amount, description, customer)
                self.sentinel.record_send_attempt(action.id, entity_id, action.kind, True, now)
                detail = {
                    "simulated": False,
                    "short_url": response.get("short_url"),
                    "razorpay_id": response.get("id"),
                    "razorpay_mode": "test",
                }
                self._audit(
                    entity_id, "action",
                    f"REAL Razorpay test-mode {action.kind}: {response.get('short_url')}",
                    {"action_id": action.id, **detail}, day,
                )
                return detail
            except RazorpayError as exc:
                outcome = self.sentinel.record_send_attempt(
                    action.id, entity_id, action.kind, False, now, error=str(exc)
                )
                self._audit(
                    entity_id, "sentinel", f"Razorpay {action.kind} failed -> {outcome}",
                    {"action_id": action.id, "error": str(exc),
                     "backoff_minutes": self.sentinel.backoff_minutes(action.id)}, day,
                )
                if outcome == "dead_letter":
                    break
        return {"simulated": True, "short_url": self._sim_url(action),
                "reason": "real Razorpay call failed, fell back to the simulated rail"}

    @staticmethod
    def _sim_url(action: Action) -> str:
        return f"https://rzp.io/sim/{action.kind}/{action.id}"

    # -- perception + threads -----------------------------------------------

    def _inbound(self, entity_id: str, text: str, day: int) -> Extraction:
        """Append a debtor message and run it through the REAL extractor."""
        channel = self.channel_of.get(entity_id, "wa")
        message = self._append_thread(entity_id, "in", channel, text, day)
        thread = self._thread(entity_id)
        extraction = self.provider.extract(message, thread)
        self.extractions.append(extraction)
        self._audit(
            entity_id, "perception",
            f"extraction {extraction.level} (conf {extraction.confidence})",
            {"message_id": message.id, "level": extraction.level,
             "amount_inr": extraction.amount_inr,
             "date": extraction.date.isoformat() if extraction.date else None,
             "condition": extraction.condition, "text": text,
             "provider": self.provider_name}, day,
        )
        return extraction

    def _append_thread(self, entity_id: str, direction: str, channel: str, text: str, day: int) -> Message:
        n = self._msg_seq.get(entity_id, 0) + 1
        self._msg_seq[entity_id] = n
        message = Message(
            id=f"M-SIM-{entity_id}-{n:03d}", thread_id=f"T-SIM-{entity_id}",
            direction=direction, channel=channel, text=text, ts=self._ts(day),
        )
        self.threads.setdefault(entity_id, []).append(message)
        return message

    def _thread(self, entity_id: str) -> list[Message]:
        return self.threads.get(entity_id, [])

    # -- small helpers -------------------------------------------------------

    def _persona(self, entity_id: str) -> str:
        invoice = self.invoices[entity_id]
        return DEBTOR_BY_ID[invoice.debtor_id]["persona"]

    def _amount(self, entity_id: str) -> int:
        entity = self.ledger.entities.get(entity_id)
        if entity is not None and entity.invoice_amount_inr:
            return entity.invoice_amount_inr
        invoice = self.invoices.get(entity_id)
        return invoice.amount_inr if invoice else 0

    @staticmethod
    def _weekday(day: int) -> str:
        return (SIM_EPOCH + dt.timedelta(days=day)).strftime("%A")

    def _audit(self, entity_id: str, layer: str, summary: str, detail: dict, day: int) -> AuditEntry:
        """Action/sentinel/perception-layer audit records.

        The ledger writes its own judgment-layer entries (before every action,
        law 3); this is the layers the ledger doesn't own writing theirs into
        the SAME append-only trail. A separate id namespace (`AD-`) keeps the
        ledger's own sequence untouched.
        """
        self._audit_seq += 1
        entry = AuditEntry(
            id=f"AD-{self._audit_seq:05d}", entity_id=entity_id, layer=layer,
            summary=summary, detail=detail, ts=self._ts(day),
        )
        self.ledger.audit.append(entry)
        return entry

    # -- read models ---------------------------------------------------------

    def funnel_summary(self) -> dict:
        states: dict[str, int] = {}
        recovered_inr = 0
        outstanding_inr = 0
        for entity in self.ledger.entities.values():
            states[entity.state] = states.get(entity.state, 0) + 1
            amount = entity.invoice_amount_inr or 0
            if entity.state == "KEPT":
                recovered_inr += amount
            elif entity.state != "CLEAN_LOSS":
                outstanding_inr += amount

        promises: dict[str, int] = {}
        for promise in self.ledger.promises.values():
            promises[promise.status] = promises.get(promise.status, 0) + 1

        action_kinds: dict[str, int] = {}
        for action in self.actions:
            action_kinds[action.kind] = action_kinds.get(action.kind, 0) + 1

        zero_touch = sorted(
            eid for eid in self.carts
            if (e := self.ledger.entities.get(eid)) is not None and e.state == "KEPT" and not e.touches
        )

        return {
            "day": self.day,
            "provider": self.provider_name,
            "states": dict(sorted(states.items())),
            "recovered_inr": recovered_inr,
            "outstanding_inr": outstanding_inr,
            "promises": dict(sorted(promises.items())),
            "actions": dict(sorted(action_kinds.items())),
            "events_total": len(self.events),
            "actions_total": len(self.actions),
            "messages_sent": len(self.messenger.queue),
            "audit_entries": len(self.ledger.audit),
            "dead_letter": len(self.sentinel.dead_letter),
            "tier0_zero_touch_recoveries": zero_touch,
            "held_actions_pending": len(self.ledger.pending_held_actions()),
            "held_actions_total": len(self.ledger.held_actions),
            "paused_threads": len(self.ledger.paused_entities()),
        }

    def world_summary(self) -> dict:
        return {
            "day": self.day,
            "provider_name": self.provider_name,
            "seed": self.seed,
            "real_razorpay": self.real_razorpay,
            "counts": {
                "invoices": len(self.invoices),
                "carts": len(self.carts),
                "entities": len(self.ledger.entities),
                "events": len(self.events),
                "actions": len(self.actions),
                "extractions": len(self.extractions),
                "messages": len(self.messenger.queue),
                "audit_entries": len(self.ledger.audit),
                "promises": len(self.ledger.promises),
                "evidence_packets": len(self.evidence_packets),
                "dead_letter": len(self.sentinel.dead_letter),
            },
        }

    def audit_summaries(self) -> list[tuple[str, str, str]]:
        """(entity_id, layer, summary) for every audit entry, in order — the
        determinism fixture: two identical runs must produce this identically."""
        return [(a.entity_id, a.layer, a.summary) for a in self.ledger.audit]

    def touch_windows(self) -> dict[str, int]:
        """Max messages queued to each ENTITY inside any rolling
        TOUCH_WINDOW_DAYS window. Informational: the law is per debtor, so
        `debtor_touch_windows()` below is the one bound #4 is judged on."""
        return self._worst_windows(self._stamps_by(lambda m: m.entity_id))

    def debtor_touch_windows(self) -> dict[str, int]:
        """Max messages queued to each DEBTOR inside any rolling
        TOUCH_WINDOW_DAYS window, counting every entity they hold — the
        message-queue-side proof of bound #4 as CLAUDE.md law 4 words it.
        Computed from the queue, independently of the ledger's own counter, so
        it can actually disagree with it if the gate ever leaks."""
        return self._worst_windows(self._stamps_by(self._debtor_of_message))

    def _debtor_of_message(self, message: Message) -> str:
        invoice = self.invoices.get(message.entity_id)
        if invoice is not None:
            return invoice.debtor_id
        cart = self.carts.get(message.entity_id)
        return cart.customer_id if cart is not None else message.entity_id

    def _stamps_by(self, key: Callable[[Message], str]) -> dict[str, list[dt.datetime]]:
        grouped: dict[str, list[dt.datetime]] = {}
        for message in self.messenger.queue:
            grouped.setdefault(key(message), []).append(message.ts)
        return grouped

    @staticmethod
    def _worst_windows(grouped: dict[str, list[dt.datetime]]) -> dict[str, int]:
        worst: dict[str, int] = {}
        for group_id, stamps in grouped.items():
            stamps.sort()
            peak = 0
            for i, start in enumerate(stamps):
                count = sum(1 for t in stamps[i:] if (t - start).days < TOUCH_WINDOW_DAYS)
                peak = max(peak, count)
            worst[group_id] = peak
        return worst

    def bound_violations(self) -> list[str]:
        """Empty list = every bound held. Used by the tests and the System
        Health screen; cheap enough to call after any advance."""
        problems = [f"{a.id} ({a.kind}) bypassed check_bounds" for a in self.actions if not a.bounds_checked]
        problems += [
            f"debtor {debtor_id} received {peak} messages in a {TOUCH_WINDOW_DAYS}-day window"
            for debtor_id, peak in sorted(self.debtor_touch_windows().items())
            if peak > MAX_TOUCHES_PER_WEEK
        ]
        return problems
