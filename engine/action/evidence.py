"""Dispute evidence packet — invoice + thread + delivery flag -> JSON + a
rendered card for the human-review queue (BUILD.md Day 6).

The 1-line summary is LLM-written in the real system (master doc §2.1,
temp 0, "trivial") but that's a Phase B concern (needs ANTHROPIC_API_KEY).
Until then, `summary` takes an explicit override or falls back to a
deterministic non-LLM placeholder — the packet's factual fields (amount,
delivery flag, thread excerpt) are never LLM-derived either way, so this
module works end-to-end today; only the one-line prose gets better later.
"""

import datetime as dt

from pydantic import BaseModel

from engine.schemas import Invoice, Message

EXCERPT_LAST_N = 6


class EvidencePacket(BaseModel):
    invoice_id: str
    debtor_id: str
    amount_inr: int
    delivery_confirmed: bool
    thread_excerpt: list[Message]
    summary: str
    ts: dt.datetime


def build_evidence_packet(
    invoice: Invoice, thread_messages: list[Message], now: dt.datetime, summary: str | None = None,
) -> EvidencePacket:
    return EvidencePacket(
        invoice_id=invoice.id,
        debtor_id=invoice.debtor_id,
        amount_inr=invoice.amount_inr,
        delivery_confirmed=invoice.delivery_confirmed,
        thread_excerpt=thread_messages[-EXCERPT_LAST_N:],
        summary=summary or "[no LLM summary yet - ANTHROPIC_API_KEY not configured, Phase B]",
        ts=now,
    )


def render_card(packet: EvidencePacket) -> str:
    """Plain-text rendering for the dashboard's human-review queue card."""
    lines = [
        f"DISPUTE -- {packet.invoice_id} (Rs.{packet.amount_inr:,})",
        f"Debtor: {packet.debtor_id}",
        f"Delivery confirmed: {'yes' if packet.delivery_confirmed else 'no'}",
        f"Summary: {packet.summary}",
        "-- last messages --",
    ]
    for m in packet.thread_excerpt:
        lines.append(f"[{m.direction}] {m.text}")
    return "\n".join(lines)
