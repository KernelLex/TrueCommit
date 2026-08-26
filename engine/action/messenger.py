"""Simulated WA/email message queue with rail labeling (BUILD.md Day 6,
master doc §8.5). Dispatches an Action into a channel + names WHICH payment
rail it rides on — the dashboard's job later is to make that rail visible
per message, since "which rail" is the whole Scene-2 WhatsApp-bridge pitch.

Zero external calls here — this queues and tracks status only. The real
send (an actual WhatsApp/email API, or the Razorpay-hosted link itself)
is Phase C wiring; this queue is what Phase C's real senders will sit behind.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

from engine.schemas import Action, MessageChannel

Rail = Literal["wa_native_payment", "mandate_link", "plain_link", "voice_note", "sms_text", "text_only"]
DeliveryStatus = Literal["queued", "sent", "delivered", "failed"]

_DEFAULT_RAIL_FOR_KIND: dict[str, Rail] = {
    "mandate_offer": "mandate_link",
    "link": "plain_link",
    "voice": "voice_note",
    "sms": "sms_text",
    "message": "text_only",
}


class QueuedMessage(BaseModel):
    id: str
    action_id: str
    entity_id: str
    channel: MessageChannel
    """`wa` / `email` / `sms` (packet P14). The channel literal is the one in
    `engine/schemas.py` rather than a copy, so a channel can never exist here
    that the rest of the system does not know about."""
    rail: Rail
    text: str
    status: DeliveryStatus = "queued"
    ts: dt.datetime


class Messenger:
    def __init__(self) -> None:
        self.queue: list[QueuedMessage] = []
        self._n = 0

    def send(self, action: Action, channel: MessageChannel, text: str, rail: Rail | None = None) -> QueuedMessage:
        self._n += 1
        resolved_rail = rail or _DEFAULT_RAIL_FOR_KIND.get(action.kind, "text_only")
        msg = QueuedMessage(
            id=f"QM-{self._n:04d}", action_id=action.id, entity_id=action.entity_id,
            channel=channel, rail=resolved_rail, text=text, status="sent", ts=action.ts,
        )
        self.queue.append(msg)
        return msg

    def mark_delivered(self, message_id: str) -> None:
        for m in self.queue:
            if m.id == message_id:
                m.status = "delivered"
                return

    def mark_failed(self, message_id: str) -> None:
        for m in self.queue:
            if m.id == message_id:
                m.status = "failed"
                return

    def for_entity(self, entity_id: str) -> list[QueuedMessage]:
        return [m for m in self.queue if m.entity_id == entity_id]
