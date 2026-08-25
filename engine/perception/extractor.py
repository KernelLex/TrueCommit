"""Promise extractor — LLM call 2 of 4, the load-bearing wall (BUILD.md Day 4).

The mandate amount that ever reaches Razorpay is NEVER this function's
output directly — engine/judgment/state_machine.py's check_bounds() only
accepts an amount that matches the ledger's own invoice record. This
function's job is SEE + SPEAK only (master doc §2.2's design law); nothing
here is trusted to SPEND.
"""

import datetime as dt

from pydantic import BaseModel

from engine.perception.client import call_structured, load_prompt
from engine.schemas import Extraction, ExtractionLevel, Message


class _ExtractOut(BaseModel):
    level: ExtractionLevel
    amount_inr: int | None
    date: dt.date | None
    condition: str | None
    confidence: float


def extract_promise(message: Message, thread_messages: list[Message]) -> Extraction:
    """`thread_messages` is the thread up to and including `message`, in order."""
    thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages)
    user_content = (
        f"Thread so far:\n{thread_text}\n\n"
        f'Extract the commitment from the LAST message above: "{message.text}"'
    )
    out = call_structured(load_prompt("extract"), user_content, _ExtractOut)
    return Extraction(
        message_id=message.id, level=out.level, amount_inr=out.amount_inr,
        date=out.date, condition=out.condition, confidence=out.confidence,
    )
