"""Promise extractor — the load-bearing wall (BUILD.md Day 4).

The mandate amount that ever reaches Razorpay is NEVER this function's output
directly — engine/judgment/state_machine.py's check_bounds() only accepts an
amount that matches the ledger's own invoice record. This function's job is
SEE + SPEAK only (master doc §2.2's design law); nothing here is trusted to
SPEND. That is what makes it safe to swap the backend behind it.

Which backend runs is resolved by `engine.perception.providers.get_provider`:
explicit argument → `PK_PERCEPTION_PROVIDER` → `heuristic` (free, offline,
deterministic). The Claude path this module used to inline now lives in
`providers/anthropic_provider.py`, unchanged.
"""

import datetime as dt

from pydantic import BaseModel

from engine.perception.providers import get_provider
from engine.schemas import Extraction, ExtractionLevel, Message


class _ExtractOut(BaseModel):
    """The shape an LLM provider is constrained to return.

    Deliberately carries no message_id: the model names nothing. The caller
    attaches the id (SEE/SPEAK, never SPEND — and never NAME either).
    """

    level: ExtractionLevel
    amount_inr: int | None
    date: dt.date | None
    condition: str | None
    confidence: float


def extract_promise(
    message: Message, thread_messages: list[Message], *, provider: str | None = None
) -> Extraction:
    """`thread_messages` is the thread up to and including `message`, in order."""
    return get_provider(provider).extract(message, thread_messages)
