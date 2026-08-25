"""Shared user-content assembly for every prompt-driven perception provider.

WHY THIS MODULE EXISTS (it is a bug fix, not a refactor)
--------------------------------------------------------
`ollama.py` and `anthropic_provider.py` each built their own user turn from the
same ingredients, by copy-paste. Both copies had the SAME defect: they sent the
message text and the thread but never told the model what day it is. A model
cannot resolve "by Friday", "month end" or "the 5th of next month" without a
reference date — one live smoke call resolved "Friday" to 2023-10-06, and the
7b date agreement sat at 63.6% against the heuristic's 93.2% (the heuristic
never had the bug because it reads `message.ts` directly). See
tracking/BUILD_LOG.md, 2026-08-26, "the shared LLM prompts never state today's
date".

Two copies of an input bug is one bug too many, so assembly now lives here and
both providers import it. Adding a third LLM provider gets the fix for free.

WHAT "TODAY" MEANS HERE (determinism, CLAUDE.md law 6)
------------------------------------------------------
Never the wall clock. `Today` is derived from the data itself:

* extraction — `message.ts.date()`, the moment the debtor actually sent the
  message being extracted. That is the only date the phrase "next Wednesday"
  in that message can possibly be relative to.
* triage — the latest message date on record for that invoice. An invoice with
  no thread has no dated activity at all, so no Today line is invented for it;
  it is told so explicitly instead (which is also the signal that nobody has
  chased this debtor yet — an invoice nobody contacted is not a silent one).

Two identical runs therefore produce byte-identical prompts, and a cached
answer stays valid until the underlying data changes.

CACHE FINGERPRINTING
--------------------
`prompt_fingerprint()` hashes the prompt files plus this module's own
`ASSEMBLY_VERSION`. LLM providers fold it into `identity()`, which
`PerceptionProvider._cached()` feeds to `cache.fingerprint()`. Editing a
prompt or this assembly therefore invalidates cached answers automatically,
instead of silently re-serving results computed under the old wording — the
same guarantee `HeuristicParams.fingerprint()` already gives the rules
provider.
"""

import datetime as dt
import hashlib
from pathlib import Path

from engine.schemas import Cart, Invoice, Message

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

ASSEMBLY_VERSION = "2"
"""Bump when the shape of an assembled user turn changes. Part of
`prompt_fingerprint()`, so a bump invalidates every cached LLM answer.
v1 = the original (no reference date). v2 = reference dates injected."""

_FINGERPRINTED_PROMPTS = ("extract", "triage", "cart_cause")


def _stamp(d: dt.date) -> str:
    """`2026-08-27 (Thursday)` — the format every Today line uses."""
    return f"{d.isoformat()} ({d.strftime('%A')})"


def today_line(d: dt.date) -> str:
    return f"Today is {_stamp(d)}."


def _thread_lines(messages: list[Message]) -> str:
    return "\n".join(f"[{m.direction}] {_stamp(m.ts.date())}: {m.text}" for m in messages)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract_user_content(message: Message, thread_messages: list[Message]) -> str:
    """The user turn for one promise-extraction call.

    `thread_messages` is the thread up to and including `message`, in order.
    Every line carries its own date so "Friday" in a five-day-old message is
    not read as this week's Friday.
    """
    sent = message.ts.date()
    return (
        f"{today_line(sent)} That is the date the debtor sent the message you are extracting. "
        f'Resolve every relative date ("Friday", "next Wednesday", "month end", "the 5th") '
        f"against it and return `date` as an ISO calendar date (YYYY-MM-DD), never as words.\n\n"
        f"Thread so far (oldest first; [out] = us, [in] = the debtor):\n"
        f"{_thread_lines(thread_messages)}\n\n"
        f"Extract the commitment from the LAST message above, sent {_stamp(sent)}:\n"
        f'"{message.text}"'
    )


# ---------------------------------------------------------------------------
# Triage
# ---------------------------------------------------------------------------


def _as_of(thread_messages: list[Message]) -> dt.date | None:
    """The latest date on record for this invoice, or None when nothing has
    happened on it yet. Deliberately NOT the wall clock."""
    return max((m.ts.date() for m in thread_messages), default=None)


def triage_user_content(invoice: Invoice, thread_messages: list[Message]) -> str:
    as_of = _as_of(thread_messages)
    inbound = sum(1 for m in thread_messages if m.direction == "in")
    outbound = len(thread_messages) - inbound

    if as_of is None:
        header = (
            "No dated activity on record for this invoice: no outreach has been sent to this "
            "debtor and no reply has been missed."
        )
        overdue = ""
    else:
        header = f"{today_line(as_of)} It is the latest date on record for this invoice."
        days = (as_of - invoice.due).days
        overdue = f", {days} days past due as of today" if days > 0 else ""

    thread_text = _thread_lines(thread_messages) or "(no messages at all)"
    return (
        f"{header}\n\n"
        f"Invoice {invoice.id}: Rs.{invoice.amount_inr:,}, status={invoice.status}.\n"
        f"issued {_stamp(invoice.issued)}, due {_stamp(invoice.due)}{overdue}.\n"
        f"delivery_confirmed={invoice.delivery_confirmed}, "
        f"payment_failed_attempt={invoice.payment_failed_attempt}.\n\n"
        f"Thread so far ({outbound} from us, {inbound} from the debtor):\n{thread_text}"
    )


# ---------------------------------------------------------------------------
# Cart cause (no dates involved — kept here so all three live together)
# ---------------------------------------------------------------------------


def cart_cause_user_content(cart: Cart) -> str:
    return (
        f"Cart {cart.id}: Rs.{cart.amount_inr:,}, drop_stage={cart.drop_stage}, "
        f"drop_signals={cart.drop_signals}, reserve_active={cart.reserve_active}."
    )


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def prompt_fingerprint() -> str:
    """Hash of the shared prompt files + this module's assembly version.

    Deliberately ONE hash over all three prompts rather than one per task: a
    provider has a single `identity()`, and over-invalidating (a triage edit
    also drops cached extractions) only costs recompute time, while
    under-invalidating would silently report a stale accuracy number. Safety
    over convenience — this is the cache that feeds the gate measurements.

    Read from disk on every call rather than memoised: an eval run that edits a
    prompt between invocations must not be served the previous wording's cache.
    """
    h = hashlib.sha256(ASSEMBLY_VERSION.encode("utf-8"))
    for name in _FINGERPRINTED_PROMPTS:
        path = PROMPTS_DIR / f"{name}.md"
        h.update(name.encode("utf-8"))
        h.update(path.read_bytes() if path.exists() else b"")
    return h.hexdigest()[:12]
