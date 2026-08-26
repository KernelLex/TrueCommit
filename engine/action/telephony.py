"""Real telephony: an actual outbound phone call and an actual WhatsApp
message, via Twilio — same lazy-credential, same-shape-of-honesty pattern as
`engine/action/razorpay_client.py`.

WHAT THIS IS AND WHAT IT IS NOT
--------------------------------
Every other channel in this codebase (voice/SMS/WhatsApp reminders,
`engine/action/messenger.py`, `engine/action/tts.py`) has been REAL CONTENT,
SIMULATED DELIVERY from Day 5 onward, because no telephony/SMS/WhatsApp
credential existed. This module is the one place that changes: when a real
Twilio account is configured (`TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` in
`.env`), it places a genuine phone call (Twilio's own TTS reads the ledger's
reminder text aloud) and sends a genuine WhatsApp message (via Twilio's
WhatsApp Sandbox) to a real number.

SAFETY GATE — READ THIS BEFORE CALLING ANYTHING HERE FROM runner.py
---------------------------------------------------------------------
This module has NO opinion about when it's safe to fire — that decision lives
in `WorldRunner._should_go_real_telephony()`, and it is deliberately strict:

  1. NEVER for an autonomous action, regardless of whether credentials exist.
     A `voice`/`message` Action from the escalation ladder must never place a
     real call — the automated 45-day simulator, and every `pytest` run that
     drives it, must stay network-free (CLAUDE.md's offline-test guarantee)
     even on a developer machine whose `.env` happens to hold real Twilio
     keys. Only `action.params["manual"] == True` (an operator's own click)
     can ever reach this module.
  2. Opt-IN via `PK_REAL_TELEPHONY=1` even when credentials exist — unlike
     `PK_REAL_TTS` (opt-out, because generating a local MP3 file has no
     real-world side effect on anyone), actually ringing a phone or messaging
     a real WhatsApp account is a side effect on a real human being, so it
     requires an explicit, separate opt-in on top of "the credential exists."
  3. NEVER against the synthetic demo fallback number. Only a contact with
     `source == "operator_submitted"` (packet P15) can ever be dialled for
     real — the demo constant `+919812345678` is never a real line to call,
     and dialling it for real regardless of who config happens to be set
     would risk reaching an actual stranger's phone.

Any non-2xx Twilio response, or a missing credential, raises `TelephonyError`
— callers must audit the failure (never swallow it silently) and fall back to
today's exact simulated fields, the same discipline `tts.py` uses for a gTTS
outage.
"""

import os
import re
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv

TWILIO_WHATSAPP_SANDBOX_DEFAULT = "whatsapp:+14155238886"
"""Twilio's shared, standard WhatsApp Sandbox number — the same for every
Twilio account in sandbox mode. Overridable via `TWILIO_WHATSAPP_FROM` in case
an account has since moved to a production WhatsApp Business number."""


class TelephonyError(Exception):
    """Raised on a missing/invalid credential, a malformed number, or any
    non-2xx Twilio response. One exception type so the runner's audit code
    doesn't need to know Twilio's own exception hierarchy."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _e164(number: str) -> str:
    """Normalize to E.164. Bare 10-digit numbers are assumed +91 (India) —
    every contact this codebase generates or accepts (packet P15's
    `ContactBook`) is in that format; a number already carrying a `+` is
    trusted as-is."""
    n = re.sub(r"[\s-]+", "", number)
    if n.startswith("+"):
        return n
    if len(n) == 10:
        return f"+91{n}"
    return f"+{n}"


def _credentials() -> tuple[str, str, str] | None:
    """Twilio supports two distinct credential shapes, and this project
    accepts either: the main Account SID + Auth Token (Console dashboard
    home), or a scoped API Key SID + Secret (Console -> API keys & tokens) —
    which, unlike the Auth Token, ALSO needs the Account SID alongside it to
    identify which account the key belongs to. Returns
    `(username, password, account_sid)` for `Client(...)`, or `None` if
    neither complete pair is present."""
    load_dotenv()
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    api_key_sid = os.environ.get("TWILIO_API_KEY_SID", "")
    api_key_secret = os.environ.get("TWILIO_API_KEY_SECRET", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if account_sid and api_key_sid and api_key_secret:
        return api_key_sid, api_key_secret, account_sid
    if account_sid and auth_token:
        return account_sid, auth_token, account_sid
    return None


def is_configured() -> bool:
    """True only when a complete credential pair (either shape — see
    `_credentials()`) is present. Does not import or construct a Twilio
    client — safe to call from anywhere, including at import time, to decide
    whether to even attempt a real dispatch."""
    return _credentials() is not None


def _client():
    """Twilio's REST client, constructed lazily from `.env` — mirrors
    `RazorpayClient.__init__`'s lazy-load pattern exactly. Importing this
    module never requires the credentials to exist; only calling this does."""
    creds = _credentials()
    if creds is None:
        raise TelephonyError(
            "Twilio credentials not set - add TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN, "
            "or TWILIO_ACCOUNT_SID + TWILIO_API_KEY_SID + TWILIO_API_KEY_SECRET, to .env"
        )
    username, password, account_sid = creds
    from twilio.rest import Client  # imported lazily so the package is only required when actually used

    return Client(username, password, account_sid)


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def place_call(to_number: str, text: str) -> dict[str, Any]:
    """Place a REAL outbound phone call. Twilio's own text-to-speech reads
    `text` aloud via TwiML `<Say>` — this module generates no audio of its
    own and chooses no words; `text` is the ledger's already-finished
    reminder sentence (or an operator's own typed words), exactly as
    `engine/action/tts.py` receives it. `to_number` must be a number Twilio
    is permitted to call — on a trial account, one that was verified during
    signup.

    Returns `{"sid", "status", "to", "from"}` on success. Raises
    `TelephonyError` on any failure — including a trial-account restriction
    (an unverified `to_number`), which is a genuine Twilio rejection, not a
    bug here.
    """
    from twilio.base.exceptions import TwilioRestException

    load_dotenv()
    from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if not from_number:
        raise TelephonyError("TWILIO_PHONE_NUMBER not set - add it to .env")

    # Twilio TRIAL accounts reject the inline `twiml=` parameter outright —
    # confirmed live 2026-08-27 (see tracking/BUILD_LOG.md): "Invalid or
    # disallowed parameters provided - trial accounts have limited parameter
    # access." Only `url=` (Twilio fetches TwiML from a URL it can reach) is
    # allowed on trial. That means this project's own `/telephony/twiml`
    # route (api/main.py) must be PUBLICLY reachable — set `PUBLIC_BASE_URL`
    # in `.env` once this app is deployed or tunnelled; until then, calling
    # this function raises rather than silently trying the inline path that
    # is now known to fail on trial accounts.
    base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not base_url:
        raise TelephonyError(
            "PUBLIC_BASE_URL not set - Twilio trial accounts require a URL it can "
            "fetch TwiML from (inline twiml= is rejected on trial), so this app must "
            "be reachable from the internet (deployed, or tunnelled) before a real call "
            "can speak custom text. Add PUBLIC_BASE_URL to .env once you have one."
        )

    to = _e164(to_number)
    twiml_url = f"{base_url}/telephony/twiml?text={quote(text)}"
    try:
        call = _client().calls.create(url=twiml_url, to=to, from_=from_number)
    except TwilioRestException as exc:
        raise TelephonyError(str(exc), status_code=getattr(exc, "status", None)) from exc
    return {"sid": call.sid, "status": call.status, "to": to, "from": from_number, "twiml_url": twiml_url}


def send_whatsapp(to_number: str, text: str) -> dict[str, Any]:
    """Send a REAL WhatsApp message via Twilio's Sandbox (or a production
    WhatsApp Business sender, if `TWILIO_WHATSAPP_FROM` is ever repointed to
    one). `text` is real content chosen entirely upstream — this function
    sends it verbatim, never rewriting or summarizing it.

    The recipient must have opted into the sandbox (sent the sandbox's
    "join <phrase>" message from their own WhatsApp) or the send fails with a
    genuine Twilio error — surfaced as `TelephonyError`, never silently
    swallowed.
    """
    from twilio.base.exceptions import TwilioRestException

    load_dotenv()
    from_whatsapp = os.environ.get("TWILIO_WHATSAPP_FROM", TWILIO_WHATSAPP_SANDBOX_DEFAULT)
    to = f"whatsapp:{_e164(to_number)}"
    try:
        msg = _client().messages.create(from_=from_whatsapp, to=to, body=text)
    except TwilioRestException as exc:
        raise TelephonyError(str(exc), status_code=getattr(exc, "status", None)) from exc
    return {"sid": msg.sid, "status": msg.status, "to": to, "from": from_whatsapp}
