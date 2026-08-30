"""Real WhatsApp messages via Meta's own direct Cloud API — bypassing Twilio
(or any reseller) entirely. Same lazy-credential, same-shape-of-honesty
pattern as `engine/action/telephony.py` / `engine/action/telegram_bot.py`.

WHY META DIRECT, NOT TWILIO'S WHATSAPP SANDBOX (packet P16/P17 chronology,
tracking/BUILD_LOG.md 2026-08-27)
-------------------------------------------------------------------------------
Twilio's WhatsApp Sandbox hit a real wall: `HTTP 400: ContentSid Required`
sending a plain-text message outside a live 24h session. That is Meta's own
WhatsApp Business Platform policy (a pre-approved message TEMPLATE is
required for any business-initiated message outside a session opened by the
recipient's own most recent message) — it applies identically here, via any
reseller, or none. Going direct to Meta removes Twilio as a paying middleman
and the ContentSid-hunting friction that came with the reseller layer, but it
does NOT remove the underlying WhatsApp rule, which is repeated below rather
than treated as solved.

WHAT IS REAL AND WHAT IS NOT
------------------------------
The message content is exactly as real as every other channel this project
has built (the ledger's own template, or an operator's own words). Delivery
is genuinely real too, like Telegram (packet P17) and unlike SMS (still
simulated — no SMS-gateway credential exists): a message sent through this
module is a real WhatsApp Business Platform send, subject to Meta's own
delivery/rate-limit behaviour, not a local simulation.

TEMPLATE vs FREE-FORM, AND WHY BOTH ARE NEEDED HERE
------------------------------------------------------
`send_template()` sends a pre-approved template (e.g. `hello_world`, which
every new Meta test app gets auto-approved with no waiting) — the only way to
reach someone who has not yet messaged the business, or whose 24h session has
expired. `send_text()` sends genuine free-form content, but ONLY works inside
a live session the RECIPIENT opened by messaging first — attempting it
outside that window returns a real Meta rejection, surfaced here as
`WhatsAppError`, never silently downgraded or retried as a template on the
caller's behalf (that would be this module making a content decision, which
CLAUDE.md law 1 reserves for the judgment layer / the human, never a
delivery client).

CONTACT SHAPE
--------------
Unlike Telegram (which needs a discovered `chat_id`), WhatsApp addresses a
phone number — the SAME `Contact.phone` `engine/action/contacts.py` already
carries for every other channel. No new contact field was needed.
"""

import os
from typing import Any

import httpx
from dotenv import load_dotenv

GRAPH_API_VERSION = "v21.0"
"""Meta's current stable Graph API version as of this build (2026-08-27).
Confirmed via Meta's own current documentation, not assumed from an older
training-time default — Graph API versions are supported for a multi-year
window, so this does not need per-session re-verification."""

TIMEOUT_SECONDS = 30.0


class WhatsAppError(Exception):
    """Raised on a missing/invalid credential or any non-2xx Meta Graph API
    response. One exception type so callers don't need to know Meta's own
    error JSON shape (`error.message`, `error.code`, etc.)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _credentials() -> tuple[str, str] | None:
    """`(access_token, phone_number_id)`, or `None` if either is missing."""
    load_dotenv()
    token = os.environ.get("META_WHATSAPP_ACCESS_TOKEN", "")
    phone_number_id = os.environ.get("META_WHATSAPP_PHONE_NUMBER_ID", "")
    if token and phone_number_id:
        return token, phone_number_id
    return None


def is_configured() -> bool:
    """True only when both the access token and phone number ID are present.
    Does not call Meta — safe to call from anywhere, including at import
    time, to decide whether to even attempt a real dispatch."""
    return _credentials() is not None


def _e164_digits(number: str) -> str:
    """Meta's `to` field wants digits only, no leading `+` (unlike Twilio's
    E.164-with-plus convention) — confirmed against Meta's own API Setup
    quickstart sample. Bare 10-digit numbers are assumed +91 (India), same
    convention `engine.action.contacts.ContactBook` already normalizes to."""
    digits = "".join(ch for ch in number if ch.isdigit())
    if number.strip().startswith("+"):
        return digits
    if len(digits) == 10:
        return f"91{digits}"
    return digits


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    creds = _credentials()
    if creds is None:
        raise WhatsAppError(
            "META_WHATSAPP_ACCESS_TOKEN / META_WHATSAPP_PHONE_NUMBER_ID not set - add them to .env"
        )
    token, phone_number_id = creds
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise WhatsAppError(f"network error calling Meta's Graph API: {exc}") from exc
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not response.is_success:
        detail = body.get("error", {}).get("message", f"HTTP {response.status_code}")
        raise WhatsAppError(detail, status_code=response.status_code)
    return body


def send_text(to_number: str, text: str) -> dict[str, Any]:
    """Genuine free-form WhatsApp text. Only succeeds inside a live 24h
    session the recipient opened by messaging first — a real Meta rejection
    outside that window surfaces as `WhatsAppError`, exactly as it should:
    this module does not decide to fall back to a template on its own.
    `text` is decided entirely upstream (CLAUDE.md law 1); this function
    sends it verbatim."""
    to = _e164_digits(to_number)
    result = _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    })
    return {"message_id": result.get("messages", [{}])[0].get("id"), "to": to}


def send_template(to_number: str, template_name: str = "hello_world", language_code: str = "en_US") -> dict[str, Any]:
    """Send a pre-approved WhatsApp message TEMPLATE — the only way to reach
    someone outside a live session, or as the very first message ever sent to
    them. `hello_world` is auto-approved on every new Meta test app with no
    waiting; a custom template matching this project's own reminder copy
    would need to be created and approved once in the Meta Business Manager
    before it could be used here (not built — see tracking/DECISIONS.md for
    why the template-authoring step was left for a future session)."""
    to = _e164_digits(to_number)
    result = _post({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": template_name, "language": {"code": language_code}},
    })
    return {"message_id": result.get("messages", [{}])[0].get("id"), "to": to, "template": template_name}
