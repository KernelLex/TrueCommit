"""Pure-Python rules provider — zero external dependencies, zero cost, offline.

WHAT IT IS
----------
A deterministic baseline for all three perception tasks (promise extraction,
invoice root-cause triage, cart-abandonment cause). No network, no API key, no
model weights: the same input always produces a byte-identical output, which
makes it the provider the simulator and the 3-arm run can lean on (CLAUDE.md
law 6: two identical runs must produce identical output).

WHAT IT IS NOT
--------------
It is not a language model and does not pretend to be. It reads keywords,
number formats and date phrases. It has no idea what a sentence means. Its
value is (a) the whole system runs for free today, and (b) it is the honest
BASELINE that any LLM provider has to beat on the same hand labels — a claim
like "our extractor is 92% accurate" is only interesting next to "and regex
gets 78%".

HONESTY NOTE (read before quoting its accuracy)
-----------------------------------------------
These rules were authored WITH VISIBILITY of the full hand-labelled set in
`data/ground_truth.json` — there is no held-out split. Its measured accuracy
is therefore IN-SAMPLE: an upper bound on this dataset, not an estimate of how
it would do on new messages. The eval writes `"in_sample": true` into
`metrics/*_heuristic.json` so this caveat travels with the number. Rules were
kept general on purpose (Indian amount formats, hedge words, conditional
grammar, payment-intent) rather than memorising specific message ids, but the
caveat stands and should be stated whenever the number is.

TUNING
------
Everything the rules key on lives in `HeuristicParams`. Construct your own and
pass it to `HeuristicProvider(params=...)` to tune without touching logic; the
params hash is part of the cache fingerprint, so retuning automatically
invalidates cached results instead of serving stale ones.

DESIGN LAW COMPLIANCE
---------------------
This module can output an `amount_inr` and a `date`. Neither is ever debited:
`engine/judgment/state_machine.py` only accepts an amount that matches the
ledger's own invoice record (law 1 + law 2). When an amount or date is not
explicit in the text, this module emits None and drops a level — it never
guesses, and it never derives a number the debtor did not state (e.g. "half
now" yields `condition`, not a silently halved invoice total).
"""

import calendar
import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

from engine.perception.providers import PerceptionProvider
from engine.schemas import (
    Cart,
    CartCause,
    CartCauseType,
    Extraction,
    ExtractionLevel,
    Invoice,
    InvoiceCause,
    InvoiceCauseType,
    Message,
)

# ---------------------------------------------------------------------------
# Tunable parameters
# ---------------------------------------------------------------------------

Str = tuple[str, ...]


@dataclass(frozen=True)
class HeuristicParams:
    """Every keyword list, threshold and confidence the rules use.

    Grouped by the decision each one feeds. Defaults were chosen to be
    *general* (grammar and Indian money/date conventions) rather than tuned to
    individual messages — see the module docstring's honesty note.
    """

    # -- amounts ------------------------------------------------------------

    min_bare_amount: int = 1000
    """A number with no currency symbol and no k/L suffix must be at least
    this large to be read as rupees. Stops "3 damaged panels", "2 weeks" and
    "100% sure" from becoming money. A number written as "Rs.500" or "40k" is
    unambiguous and bypasses this floor."""

    amount_multipliers: tuple[tuple[str, int], ...] = (
        ("crores", 10_000_000), ("crore", 10_000_000), ("cr", 10_000_000),
        ("lakhs", 100_000), ("lakh", 100_000), ("lacs", 100_000), ("lac", 100_000),
        ("l", 100_000), ("k", 1_000),
    )
    """Indian shorthand. Longest-first — "lakhs" must win over "l"."""

    amount_unit_words: Str = (
        "day", "days", "week", "weeks", "month", "months", "year", "years",
        "hour", "hours", "hrs", "hr", "min", "mins", "am", "pm", "pieces",
        "pcs", "units", "nos", "items", "boxes", "panels", "percent",
    )
    """A number immediately followed by one of these counts things, not money."""

    # -- dates --------------------------------------------------------------

    next_prefix_adds_week: bool = False
    """How to read "next Wednesday". False (default) = the next Wednesday
    strictly after the message date. True = the Wednesday of the following
    week. Real threads use both meanings and the dataset's own labels are not
    self-consistent about it, so this is exposed rather than guessed at. Level
    classification is unaffected either way — only the resolved date moves."""

    week_of_month_days: tuple[int, ...] = (3, 10, 17, 24)
    """"first/second/third/fourth week of next month" resolves to these days.
    A convention, stated openly, because the phrase names a range not a day."""

    hedge_markers: Str = (
        "maybe", "may be", "perhaps", "probably", "possibly", "hopefully",
        "should be", "might", "can't promise", "cannot promise", "no promises",
        "not sure", "not 100", "roughly", "around", "approximately", "or so",
        "tentatively", "trying to", "try to", "aiming",
    )
    """Words that make a following date NOT explicit. "early next week" is a
    date; "maybe early next week" is a hope. Design law: if it is not explicit,
    do not invent it — drop to None and let the level fall."""

    hedge_window_chars: int = 25
    """A hedge only suppresses a date it is adjacent to (this many characters
    before the date phrase), not every date in a long message."""

    # -- levels: L5 (no commitment) ----------------------------------------

    dispute_markers: Str = (
        "not paying", "won't pay", "wont pay", "will not pay", "not going to pay",
        "refuse", "refusing", "disputing", "dispute this", "raising a dispute",
        "never received", "did not receive", "didn't receive", "not delivered",
        "damaged", "defective", "wrong item", "does not match", "doesn't match",
        "considering a return", "cancel the order", "not as per",
    )
    """Refusal / dispute / quality-complaint language. These are STICKY within
    a thread (see `dispute_is_sticky`)."""

    dispute_is_sticky: bool = True
    """Once a debtor has disputed in-thread, later non-committal messages stay
    L5 rather than softening to L4. Mirrors the state machine's own rule that
    a dispute is an instant stop from any state (CLAUDE.md law 4) — there is
    no commitment to extract from a thread that is in dispute. An explicit
    amount+date commitment still overrides it."""

    contradiction_markers: Str = (
        "already paid", "already settled", "already cleared", "already been paid",
        "paid this already", "we have paid", "payment already",
    )
    """A claim that contradicts our records is not a new commitment (L5), but
    unlike a dispute it is NOT sticky — threads recover from it ("actually let
    me check with accounts...")."""

    bare_ack_tokens: Str = (
        "ok", "okay", "k", "kk", "hmm", "hm", "noted", "yes", "yeah", "yep",
        "no", "haan", "han", "ji", "theek", "thik", "fine", "alright", "acha",
    )
    bare_ack_max_tokens: int = 2
    """A reply that is nothing but acknowledgment tokens ("ok") is
    silence-equivalent — L5. Two words of actual content ("sounds good") is a
    soft acknowledgment — L4."""

    # -- levels: L3 (conditional / partial) --------------------------------

    condition_markers: Str = (
        "once", "as soon as", "when ", "after ", "if ", "provided", "subject to",
        "depends on", "dependent on", "waiting for", "till ", "until",
        "contingent",
    )
    split_payment_markers: Str = (
        "half", "the rest", "rest by", "rest in", "remaining", "balance by",
        "part payment", "partial payment", "instalment", "installment",
        "tranche", "in two parts", "split",
    )
    condition_requires_payment_verb: bool = True
    """"we'll CLEAR this once the client pays" is a conditional promise (L3).
    "will HANDLE it once she's back" is a vague acknowledgment (L4) — the
    condition is not attached to a payment commitment. Requiring a payment
    verb in the same message is what separates them."""

    # -- levels: payment intent --------------------------------------------

    payment_verbs: Str = (
        "pay", "paying", "pays", "paid", "payment", "clear", "clearing",
        "settle", "settling", "settlement", "transfer", "transferring",
        "remit", "send", "sending", "sent", "process", "processing",
        "release", "releasing", "deposit", "credit", "debit", "auto-debit",
        "redo", "redoing", "wire", "neft", "rtgs", "imps", "upi",
    )
    """A date only becomes a promise date if the message is about paying.
    "will confirm by tomorrow" commits to a phone call, not to money. An
    explicit amount also counts as payment intent on its own (see
    `_has_payment_intent`) so "let's commit to 68000 by next Tuesday" is L1
    even though "commit" is not a payment verb."""

    # -- levels: confirmation inheritance ----------------------------------

    affirmation_markers: Str = (
        "yes", "yeah", "haan", "ji", "sure", "set it up", "go ahead", "do it",
        "please do", "that works", "agreed", "confirmed", "sounds good",
        "works for me", "okay set", "ok set",
    )
    inherit_from_preceding_outbound: bool = True
    """"haan set it up" is L1 when the message it answers put both an amount
    and a date on the table ("Rs.40,000 auto-debits Friday"). Inheritance is
    deliberately limited to the IMMEDIATELY preceding outbound message: that
    is the only context an affirmative can unambiguously be answering. It
    never inherits from an earlier inbound message, which would let a stale
    promise be re-counted as a fresh one."""

    # -- confidence ---------------------------------------------------------

    base_confidence: tuple[tuple[str, float], ...] = (
        ("L1", 0.90), ("L2", 0.82), ("L3", 0.78), ("L4", 0.70), ("L5", 0.72),
    )
    firmness_markers: Str = (
        "pakka", "pura", "poora", "guaranteed", "without fail", "for sure",
        "definitely", "in full", "confirm", "confirmed", "no delay", "tak",
    )
    """Hinglish and English firmness. Per the packet spec these move
    CONFIDENCE, never the level — "pakka" does not turn a vague message into a
    firm one, it only says the debtor sounded certain."""

    firmness_bonus: float = 0.03
    firmness_bonus_cap: float = 0.08
    hedge_penalty: float = 0.08
    inherited_confidence_penalty: float = 0.05
    confidence_floor: float = 0.30
    confidence_ceiling: float = 0.99

    # -- triage -------------------------------------------------------------

    payment_failure_markers: Str = (
        "bounced", "bounce", "didn't go through", "did not go through",
        "not go through", "gone through", "failed", "failure", "declined",
        "reversed", "returned unpaid", "insufficient funds", "gateway",
        "mismatch", "nach return", "transaction failed", "payment failed",
    )
    broad_dispute_markers: Str = (
        "not paying", "won't pay", "wont pay", "will not pay", "refuse",
        "refusing", "disputing", "dispute this", "raising a dispute",
        "does not match", "doesn't match", "considering a return",
        "cancel the order", "contract terms", "wrong amount", "billing error",
        "not as per",
    )
    """The debtor is contesting the INVOICE — its price, its terms, or their
    obligation to pay it at all. Scope is what separates `dispute` from
    `delivery_dispute` (the triage prompt's own words: "lean delivery_dispute
    or dispute depending on scope")."""

    delivery_complaint_markers: Str = (
        "damaged", "defective", "broken", "wrong item", "wrong items",
        "never received", "did not receive", "didn't receive", "not delivered",
        "short shipped", "missing items", "arrived with", "quality",
        "poor condition",
    )
    triage_confidence: tuple[tuple[str, float], ...] = (
        ("payment_failed_thread", 0.88), ("payment_failed_flag", 0.65),
        ("dispute", 0.85), ("delivery_dispute", 0.82),
        ("delivery_dispute_flag", 0.55), ("non_responsive", 0.85),
        ("cashflow_delay", 0.72), ("cashflow_delay_default", 0.45),
    )

    # -- cart cause ---------------------------------------------------------

    cart_signal_keywords: tuple[tuple[str, Str], ...] = (
        ("friction", ("otp", "timeout", "declined", "insufficient", "bank_server",
                      "gateway", "server_error", "payment_error", "3ds_fail",
                      "upi_intent", "card_error")),
        ("price_shock", ("shipping", "shipping_fee", "tax", "surcharge",
                         "handling_fee", "convenience_fee", "cost_shown")),
        ("trust", ("first_time", "no_saved", "cod_unavailable", "reviews",
                   "no_reviews", "unknown_merchant", "security")),
        ("timing", ("salary", "payday", "next_month", "after_payday",
                    "later_this_month", "budget_next")),
        ("comparison", ("compared", "competitor", "other_tab", "price_check_site")),
    )
    cart_unknown_markers: Str = ("no_signal", "low_activity", "unclear")
    cart_confidence_by_hits: tuple[float, ...] = (0.55, 0.75, 0.85)
    """Confidence for 1, 2, 3+ matching signals."""
    cart_unknown_confidence: float = 0.35

    extra: dict[str, str] = field(default_factory=dict)
    """Free-form slot for experiments, so tuning never needs a schema change
    (it still participates in the cache fingerprint)."""

    # -- identity -----------------------------------------------------------

    def fingerprint(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


DEFAULT_PARAMS = HeuristicParams()

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

WEEKDAYS: dict[str, int] = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4,
    "saturday": 5, "sunday": 6, "mon": 0, "tues": 1, "tue": 1, "wed": 2,
    "thurs": 3, "thur": 3, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_WEEKDAY_RE = re.compile(
    r"\b(?:(this|next|coming|by|on|before)\s+)?"
    r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tues|tue|wed|thurs|thur|thu|fri|sat|sun)\b",
    re.I,
)
_MONTH_END_RE = re.compile(r"\bmonth[- ]?end\b|\bend of (?:the |this )?month\b", re.I)
_WEEK_OF_MONTH_RE = re.compile(
    r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+week\s+of\s+(next|this)\s+month\b", re.I
)
_ORDINAL_DAY_RE = re.compile(
    r"\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\b(?!\s+week)(?:\s+of\s+(next|this)\s+month)?", re.I
)
_NEXT_WEEK_EDGE_RE = re.compile(
    r"\b(early|start of|beginning of|end of|late|later)\s+next\s+week\b", re.I
)
_RELATIVE_DAY_RE = re.compile(r"\b(today|tomorrow|tonight)\b", re.I)

_AMOUNT_RE = re.compile(
    r"(?P<cur>(?:rs\.?|inr|₹)\s*)?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)"
    r"(?P<suf>\s*(?:crores|crore|cr|lakhs|lakh|lacs|lac|l|k)\b)?",
    re.I,
)
_ORDINAL_SUFFIX_RE = re.compile(r"(st|nd|rd|th)\b", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9']+")

_WEEK_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3, "4th": 3}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip().lower()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalise(text))


def _contains_any(text: str, markers: tuple[str, ...]) -> str | None:
    """First marker present in `text` (already normalised), else None."""
    for marker in markers:
        if marker in text:
            return marker
    return None


def _contains_word(text: str, words: tuple[str, ...]) -> str | None:
    present = set(_tokens(text))
    for w in words:
        if w in present:
            return w
    return None


# ---------------------------------------------------------------------------
# Amount parsing
# ---------------------------------------------------------------------------


def parse_amounts(text: str, params: HeuristicParams = DEFAULT_PARAMS) -> list[int]:
    """Every rupee amount EXPLICIT in `text`, left to right.

    Handles: plain digits (45000), Indian comma grouping (Rs.1,45,000),
    shorthand (40k, 1.5L, 1.5 lakh, 2cr), currency prefixes (Rs. / Rs / ₹ /
    INR). Rejects: ordinals ("the 15th"), percentages ("100% sure"), counts
    ("2 weeks", "3 damaged panels"), ids ("INV-001"), and bare numbers below
    `min_bare_amount`.
    """
    lowered = text.lower()
    out: list[int] = []
    for m in _AMOUNT_RE.finditer(lowered):
        start, end = m.span("num")
        if start > 0 and (lowered[start - 1].isalnum() or lowered[start - 1] in "-/#"):
            continue  # part of an identifier like INV-001 / #B-220
        tail = lowered[m.end():]
        suffix = (m.group("suf") or "").strip()
        if not suffix and _ORDINAL_SUFFIX_RE.match(lowered[end:]):
            continue  # "the 15th"
        if tail[:1] == "%" or re.match(r"\s*(?:%|per ?cent)", tail):
            continue
        if re.match(rf"\s*(?:{'|'.join(params.amount_unit_words)})\b", tail):
            continue
        raw = m.group("num").replace(",", "")
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - regex guarantees numeric
            continue
        multiplier = 1
        for token, mult in params.amount_multipliers:
            if suffix == token:
                multiplier = mult
                break
        has_currency = bool(m.group("cur"))
        if multiplier == 1 and not has_currency and value < params.min_bare_amount:
            continue
        amount = int(round(value * multiplier))
        if amount > 0:
            out.append(amount)
    return out


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def _next_weekday(ref: dt.date, target: int, *, allow_today: bool = False) -> dt.date:
    delta = (target - ref.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return ref + dt.timedelta(days=delta)


def _month_end(ref: dt.date) -> dt.date:
    end = dt.date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    if end <= ref:
        y, m = (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)
        end = dt.date(y, m, calendar.monthrange(y, m)[1])
    return end


def _next_month(ref: dt.date) -> tuple[int, int]:
    return (ref.year + 1, 1) if ref.month == 12 else (ref.year, ref.month + 1)


def _day_of_month(year: int, month: int, day: int) -> dt.date:
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _date_candidates(text: str, ref: dt.date, params: HeuristicParams) -> list[tuple[int, dt.date, str]]:
    """(start_index, resolved_date, phrase) for every EXPLICIT date phrase.

    Deliberately does NOT match vague ranges — bare "next week", "this week",
    "soon", "a few days", "next month" resolve to nothing, which is what drops
    a message from L1 to L2 or L2 to L4. That is the design law working, not a
    parser gap.
    """
    found: list[tuple[int, dt.date, str]] = []

    for m in _WEEKDAY_RE.finditer(text):
        target = WEEKDAYS[m.group(2).lower()]
        modifier = (m.group(1) or "").lower()
        d = _next_weekday(ref, target, allow_today=modifier in {"this", ""})
        if modifier == "next" and params.next_prefix_adds_week:
            d += dt.timedelta(days=7)
        found.append((m.start(), d, m.group(0)))

    for m in _MONTH_END_RE.finditer(text):
        found.append((m.start(), _month_end(ref), m.group(0)))

    for m in _WEEK_OF_MONTH_RE.finditer(text):
        idx = _WEEK_ORDINALS[m.group(1).lower()]
        day = params.week_of_month_days[min(idx, len(params.week_of_month_days) - 1)]
        if m.group(2).lower() == "next":
            y, mo = _next_month(ref)
        else:
            y, mo = ref.year, ref.month
        found.append((m.start(), _day_of_month(y, mo, day), m.group(0)))

    for m in _ORDINAL_DAY_RE.finditer(text):
        day = int(m.group(1))
        if not 1 <= day <= 31:
            continue
        scope = (m.group(2) or "").lower()
        if scope == "next":
            y, mo = _next_month(ref)
        elif scope == "this":
            y, mo = ref.year, ref.month
        else:  # bare "the 10th" — the next time that day comes round
            y, mo = (ref.year, ref.month) if day > ref.day else _next_month(ref)
        found.append((m.start(), _day_of_month(y, mo, day), m.group(0)))

    for m in _NEXT_WEEK_EDGE_RE.finditer(text):
        monday = ref + dt.timedelta(days=7 - ref.weekday())
        edge = m.group(1).lower()
        d = monday if edge in {"early", "start of", "beginning of"} else monday + dt.timedelta(days=4)
        found.append((m.start(), d, m.group(0)))

    for m in _RELATIVE_DAY_RE.finditer(text):
        word = m.group(1).lower()
        found.append((m.start(), ref if word in {"today", "tonight"} else ref + dt.timedelta(days=1), word))

    found.sort(key=lambda t: t[0])
    return found


def parse_date(text: str, ref: dt.date, params: HeuristicParams = DEFAULT_PARAMS) -> dt.date | None:
    """The date the debtor is committing to, or None if none is explicit.

    The LAST explicit date phrase wins: "we sent it on the 10th but it bounced,
    redoing it by Friday" is a promise about Friday, not about the 10th.
    A hedge word immediately before the phrase ("maybe early next week")
    suppresses it — hedged timing is not explicit timing.
    """
    lowered = _normalise(text)
    candidates = _date_candidates(lowered, ref, params)
    if not candidates:
        return None
    start, resolved, _phrase = candidates[-1]
    window = lowered[max(0, start - params.hedge_window_chars):start]
    if _contains_any(window, params.hedge_markers):
        return None
    return resolved


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class HeuristicProvider(PerceptionProvider):
    name = "heuristic"

    def __init__(self, params: HeuristicParams | None = None) -> None:
        self.params = params or DEFAULT_PARAMS
        self._base_conf = dict(self.params.base_confidence)
        self._triage_conf = dict(self.params.triage_confidence)

    def identity(self) -> str:
        return f"{self.name}:{self.params.fingerprint()}"

    # -- extraction ---------------------------------------------------------

    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        p = self.params
        text = _normalise(message.text)
        ref = message.ts.date()

        amounts = parse_amounts(text, p)
        amount = amounts[0] if amounts else None
        has_intent = self._has_payment_intent(text, amounts)
        date = parse_date(text, ref, p) if has_intent else None
        hedged = _contains_any(text, p.hedge_markers) is not None

        level, condition, inherited = self._classify(
            message, thread_messages, text, amount, date, amounts, has_intent
        )

        # L3 promises cannot be summarised by one (amount, date) pair — that is
        # the whole reason L3 exists. Keep the amount of the tranche actually
        # stated, drop the date, and let `condition` carry the shape.
        if level == "L3":
            date = None
        if level in {"L4", "L5"}:
            amount, date = None, None
        if inherited is not None:
            amount, date = inherited

        return Extraction(
            message_id=message.id,
            level=level,
            amount_inr=amount,
            date=date,
            condition=condition,
            confidence=self._confidence(level, text, hedged, inherited is not None),
        )

    def _has_payment_intent(self, text: str, amounts: list[int]) -> bool:
        """An explicit rupee amount is itself payment intent; otherwise the
        message has to actually be about paying."""
        return bool(amounts) or _contains_word(text, self.params.payment_verbs) is not None

    def _classify(
        self, message: Message, thread: list[Message], text: str,
        amount: int | None, date: dt.date | None, amounts: list[int], has_intent: bool,
    ) -> tuple[ExtractionLevel, str | None, tuple[int | None, dt.date | None] | None]:
        p = self.params

        # L5 — refusal / dispute / quality complaint (sticky within the thread)
        marker = _contains_any(text, p.dispute_markers)
        if marker:
            return "L5", None, None
        if p.dispute_is_sticky and self._thread_disputed(message, thread):
            if not (has_intent and (amount is not None or date is not None)):
                return "L5", None, None

        # L5 — a claim that contradicts our records is not a commitment
        if _contains_any(text, p.contradiction_markers):
            return "L5", None, None

        # L5 — bare acknowledgment is silence-equivalent
        toks = _tokens(text)
        if toks and len(toks) <= p.bare_ack_max_tokens and all(t in p.bare_ack_tokens for t in toks):
            return "L5", None, None

        # L3 — split / partial payment
        split = _contains_any(text, p.split_payment_markers)
        if split:
            detail = f"split/partial payment offer (\"{split}\") — tranches, not one amount+date pair"
            if len(amounts) > 1:
                detail += f"; amounts stated: {', '.join(str(a) for a in amounts)}"
            return "L3", detail, None

        # L3 — conditional on a stated external event
        cond = _contains_any(text, p.condition_markers)
        if cond and (not p.condition_requires_payment_verb
                     or _contains_word(text, p.payment_verbs) is not None):
            return "L3", f"contingent on a stated external event (\"{cond.strip()}\") — timing not committed", None

        # L1 / L2 — how much of {amount, date} is explicit
        if amount is not None and date is not None:
            return "L1", None, None
        if amount is not None or date is not None:
            return "L2", None, None

        # L1 by inheritance — an affirmative answering an amount+date offer
        inherited = self._inherit(message, thread, text)
        if inherited is not None:
            return "L1", None, inherited

        return "L4", None, None

    def _thread_disputed(self, message: Message, thread: list[Message]) -> bool:
        for m in thread:
            if m.id == message.id or m.direction != "in":
                continue
            if _contains_any(_normalise(m.text), self.params.dispute_markers):
                return True
        return False

    def _inherit(
        self, message: Message, thread: list[Message], text: str
    ) -> tuple[int, dt.date] | None:
        p = self.params
        if not p.inherit_from_preceding_outbound:
            return None
        if _contains_any(text, p.affirmation_markers) is None:
            return None
        prev = None
        for m in thread:
            if m.id == message.id:
                break
            prev = m
        if prev is None or prev.direction != "out":
            return None
        prev_text = _normalise(prev.text)
        prev_amounts = parse_amounts(prev_text, p)
        prev_date = parse_date(prev_text, prev.ts.date(), p)
        if prev_amounts and prev_date is not None:
            return prev_amounts[0], prev_date
        return None

    def _confidence(self, level: str, text: str, hedged: bool, inherited: bool) -> float:
        p = self.params
        conf = self._base_conf.get(level, 0.6)
        bonus = min(
            p.firmness_bonus_cap,
            p.firmness_bonus * sum(1 for m in p.firmness_markers if m in text),
        )
        conf += bonus
        if hedged:
            conf -= p.hedge_penalty
        if inherited:
            conf -= p.inherited_confidence_penalty
        return round(max(p.confidence_floor, min(p.confidence_ceiling, conf)), 3)

    # -- triage -------------------------------------------------------------

    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        inbound = [m for m in thread_messages if m.direction == "in"]
        outbound = [m for m in thread_messages if m.direction == "out"]
        substantive = [m for m in inbound if not self._is_bare_ack(m.text)]
        blob = _normalise(" ".join(m.text for m in inbound))

        cause, key, evidence = self._triage_rules(invoice, blob, substantive, outbound)
        return InvoiceCause(
            invoice_id=invoice.id,
            cause=cause,
            confidence=self._triage_conf.get(key, 0.5),
            evidence=evidence,
        )

    def _triage_rules(
        self, invoice: Invoice, blob: str, substantive: list[Message], outbound: list[Message]
    ) -> tuple[InvoiceCauseType, str, list[str]]:
        p = self.params

        # 1. The debtor's own words about a failed attempt outrank every flag.
        failure = _contains_any(blob, p.payment_failure_markers)
        if failure:
            return "payment_failed", "payment_failed_thread", [
                f"debtor describes a failed payment attempt (\"{failure}\")"
            ]

        # 2. Dispute family. Scope decides which one: contesting the invoice /
        #    the obligation itself is `dispute`; a complaint confined to the
        #    condition of what was delivered is `delivery_dispute`.
        broad = _contains_any(blob, p.broad_dispute_markers)
        delivery = _contains_any(blob, p.delivery_complaint_markers)
        if broad:
            ev = [f"debtor contests the invoice/obligation itself (\"{broad}\")"]
            if delivery:
                ev.append(f"also cites a delivery/quality issue (\"{delivery}\") — broader scope wins")
            return "dispute", "dispute", ev
        if delivery:
            if invoice.delivery_confirmed:
                return "dispute", "dispute", [
                    f"delivery complaint (\"{delivery}\") contradicted by delivery_confirmed=True",
                    "flag for human review given the contradiction",
                ]
            return "delivery_dispute", "delivery_dispute", [
                f"delivery/quality complaint (\"{delivery}\")", "delivery not confirmed clean"
            ]

        # 3. We asked and got nothing back (a bare "ok" is not a response).
        if not substantive and outbound:
            return "non_responsive", "non_responsive", [
                f"{len(outbound)} outreach attempt(s), no substantive reply"
            ]

        # 4. Debtor has never spoken — only the record flags are left.
        if not substantive:
            if invoice.payment_failed_attempt:
                return "payment_failed", "payment_failed_flag", [
                    "payment_failed_attempt=True on record", "no thread to corroborate"
                ]
            if not invoice.delivery_confirmed:
                return "delivery_dispute", "delivery_dispute_flag", [
                    "delivery_confirmed=False on record", "no thread to corroborate"
                ]
            return "cashflow_delay", "cashflow_delay_default", [
                "no thread and no contradicting flag — behavioural delay is the default"
            ]

        # 5. Debtor is engaging and not disputing: a behavioural delay.
        return "cashflow_delay", "cashflow_delay", [
            "debtor is engaging with no dispute or failed-payment signal",
            f"{len(substantive)} substantive inbound message(s)",
        ]

    def _is_bare_ack(self, text: str) -> bool:
        toks = _tokens(text)
        return bool(toks) and len(toks) <= self.params.bare_ack_max_tokens and all(
            t in self.params.bare_ack_tokens for t in toks
        )

    # -- cart cause ---------------------------------------------------------

    def _cart_cause(self, cart: Cart) -> CartCause:
        p = self.params
        signals = [s.lower() for s in cart.drop_signals]
        joined = " ".join(signals)

        if not signals or _contains_any(joined, p.cart_unknown_markers):
            return CartCause(
                cart_id=cart.id, cause="unknown", confidence=p.cart_unknown_confidence,
                evidence=["no signal strong enough to call — not forcing a guess"],
            )

        scores: dict[str, list[str]] = {}
        for cause, keywords in p.cart_signal_keywords:
            hits = [s for s in signals if any(k in s for k in keywords)]
            if hits:
                scores[cause] = hits

        if not scores:
            return CartCause(
                cart_id=cart.id, cause="unknown", confidence=p.cart_unknown_confidence,
                evidence=[f"signals present but unrecognised: {', '.join(signals)}"],
            )

        order = [c for c, _ in p.cart_signal_keywords]
        best: CartCauseType = max(scores, key=lambda c: (len(scores[c]), -order.index(c)))  # type: ignore[assignment]
        hits = scores[best]
        conf = p.cart_confidence_by_hits[min(len(hits), len(p.cart_confidence_by_hits)) - 1]
        evidence = [f"drop_stage={cart.drop_stage}", f"matched signal(s): {', '.join(hits)}"]
        if len(scores) > 1:
            runner = ", ".join(c for c in scores if c != best)
            evidence.append(f"weaker competing signal(s): {runner}")
            conf = round(conf - 0.1, 3)
        return CartCause(cart_id=cart.id, cause=best, confidence=conf, evidence=evidence)


def build() -> HeuristicProvider:
    return HeuristicProvider()
