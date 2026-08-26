"""Real contact identity for outreach (packet P15) — plain Python, no
framework coupling, same style as `engine/action/sentinel.py` / `Ledger`.

WHAT THIS IS AND WHAT IT IS NOT
--------------------------------
Every debtor/customer in this codebase has, until now, shared exactly one
synthetic fake contact (`DEMO_CUSTOMER_CONTACT` / `DEMO_CUSTOMER_EMAIL` in
`engine/integration/runner.py`). This module lets an operator submit a REAL
name + phone number for a debtor/customer, and `WorldRunner.resolve_contact()`
(the only reader of this book) uses it in place of the demo fallback wherever
a contact is needed downstream.

It does NOT place any real call, SMS or WhatsApp message — there is no
telephony/SMS/WhatsApp-Business credential in this project (same discipline as
`engine/action/tts.py`'s dial/send status fields). A submitted contact only
ever affects two things: (1) what the audit trail / dashboard displays, and
(2) the `customer.contact` field actually sent to the REAL Razorpay TEST API
when `PK_REAL_RAZORPAY=1` creates a real payment link or mandate — Razorpay's
sandbox genuinely reads that field (see `tracking/BUILD_LOG.md`'s note that it
rejects some malformed numbers, e.g. repeated digits).

WHY A SEPARATE MODULE INSTEAD OF FIELDS ON Invoice/Debtor
-----------------------------------------------------------
Contact submission is an operator action with its own audit shape (CLAUDE.md
law 3: audited before it takes effect) and its own validation surface (a
malformed phone/empty name must 422 before anything is stored) — the same
reason `Sentinel` and `Ledger` are their own classes rather than fields
bolted onto `EntityState`. Keeping it here also keeps `engine/schemas.py`
(the shared pydantic contracts every layer communicates through) free of a
concept ("who do we call") that only the action/integration layers need.
"""

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, Field

_PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")
"""Optional leading '+', first digit 1-9 (no leading zero), 8-15 digits total.
Deliberately permissive about the country-code prefix (some operators will
type `+91...`, others just the 10-digit number) rather than hard-coding a
single country's format — this is a demo-console contact book, not a KYC
form. Spaces/hyphens are stripped before this pattern is checked, not matched
by it, so '+91 98123-45678' and '+919812345678' validate identically."""


class ContactError(ValueError):
    """A submitted name or phone number failed validation. Typed so the API
    layer can catch exactly this and turn it into a 422 with the message
    below, rather than a generic 500 or a silently-accepted bad record."""


class Contact(BaseModel):
    name: str
    phone: str
    submitted_at: dt.datetime
    source: Literal["operator_submitted"] = "operator_submitted"
    telegram_chat_id: str | None = None
    """Optional (packet P17). Telegram addresses a `chat_id`, not a phone
    number — a debtor must message the bot once before it can reach them
    (`engine.action.telegram_bot.get_recent_chats()` discovers this after
    that opt-in). `None` means no Telegram opt-in has happened yet for this
    contact; the name/phone half of a Contact is independent of it and can
    exist without it, same as it always could."""


class ContactBook:
    """Keyed by `WorldRunner._contact_key(entity_id)` — a debtor_id for a real
    invoice, or the entity_id itself for a Scene-2 cart (which has no
    debtor). One submission therefore applies to every sibling invoice of the
    same debtor, matching the existing per-debtor touch-cap precedent
    (packet P8) — a debtor should never have two contradictory phone numbers
    on file depending on which of their invoices the operator happened to
    click on."""

    def __init__(self) -> None:
        self._contacts: dict[str, Contact] = {}

    def submit(self, key: str, name: str, phone: str, now: dt.datetime) -> Contact:
        clean_name = name.strip()
        if not clean_name:
            raise ContactError("name must not be empty")

        clean_phone = re.sub(r"[\s-]+", "", phone)
        if not _PHONE_RE.match(clean_phone):
            raise ContactError(
                f"phone {phone!r} is not a valid number "
                "(expected an optional '+' then 8-15 digits, no leading zero)"
            )

        existing = self._contacts.get(key)
        contact = Contact(
            name=clean_name, phone=clean_phone, submitted_at=now,
            telegram_chat_id=existing.telegram_chat_id if existing else None,
        )
        self._contacts[key] = contact
        return contact

    def link_telegram(self, key: str, chat_id: str, now: dt.datetime) -> Contact:
        """Attach a discovered Telegram `chat_id` to whatever contact already
        exists for `key`, WITHOUT touching name/phone (packet P17). Raises
        `ContactError` if no name/phone was ever submitted for this key —
        Telegram linking is additive to an existing real contact, not a way
        to create one with no name attached."""
        existing = self._contacts.get(key)
        if existing is None:
            raise ContactError(f"no contact on file for {key!r} yet - submit name/phone first")
        updated = existing.model_copy(update={"telegram_chat_id": chat_id, "submitted_at": now})
        self._contacts[key] = updated
        return updated

    def get(self, key: str) -> Contact | None:
        return self._contacts.get(key)

    def all(self) -> dict[str, Contact]:
        return dict(self._contacts)
