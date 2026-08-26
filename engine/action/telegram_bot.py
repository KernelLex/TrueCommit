"""Real Telegram messages: text and a real generated audio file, via the
official Telegram Bot API — same lazy-credential, same honesty pattern as
`engine/action/razorpay_client.py` / `engine/action/telephony.py`.

WHY TELEGRAM (packet P17, replacing the Twilio-WhatsApp real-message attempt)
-------------------------------------------------------------------------------
WhatsApp Business Platform messaging (whether direct from Meta or through a
reseller like Twilio) is free only up to a point and requires either a paid
number, a pre-approved message template for any business-initiated message
outside a live 24h session, or a business-KYC'd provider — every path was
checked live during this build (see tracking/BUILD_LOG.md and
tracking/DECISIONS.md, 2026-08-27) and each had a real wall. Telegram's Bot
API has none of that: creating a bot via @BotFather is free forever, no card,
no template approval, no business verification, and once a user has messaged
the bot once (the one-time opt-in every messaging platform requires in some
form), the bot can message them freely, real text or real audio, no template
wall at all. WhatsApp stays documented as the intended real-world channel
(CLAUDE.md's own framing — merchants' actual debtors use WhatsApp, not
Telegram) but is not what this deployed demo's real dispatch runs on.

WHAT IS REAL AND WHAT IS NOT
------------------------------
The text and the generated MP3 audio are exactly as real as every other
channel this project has built (gTTS, the ledger's own templates). What
changes here versus `engine/action/telephony.py`'s Twilio path is that
Telegram's own delivery is REAL too — a Telegram bot sending a message
genuinely delivers it, unlike a phone call/SMS/WhatsApp message where no
telephony/SMS-gateway/WhatsApp-Business credential exists in this project.
There is nothing to simulate on the delivery side once a real bot token and a
real chat_id are used — Telegram IS the free, real channel.

CHAT ID, NOT A PHONE NUMBER
------------------------------
Telegram bots address a `chat_id`, not a phone number — a debtor must send
the bot one message first (the opt-in) before the bot can reach them, and
`chat_id` is discovered from that message via `get_recent_chats()` below. This
is a genuinely different contact shape from `engine/action/contacts.py`'s
name+phone, which is why `Contact` grew an optional `telegram_chat_id` field
(packet P17) alongside name/phone rather than replacing it.

AUDIO FORMAT — A DELIBERATE, DOCUMENTED SIMPLIFICATION
----------------------------------------------------------
`send_voice` uses Telegram's `sendAudio` method, not `sendVoice`. `sendVoice`
renders as Telegram's native round voice-message bubble, but only for an OGG
file encoded with Opus; the gTTS pipeline (`engine/action/tts.py`) produces
MP3. Converting would need `ffmpeg` as a system dependency, which is a new
deployment requirement this close to the Sep 1 freeze for a cosmetic
difference — `sendAudio` accepts the MP3 directly and still delivers real,
playable audio, just as a standard audio-file bubble (title/performer shown)
rather than the round voice-note style. Documented here rather than silently
shipped; upgrading to `sendVoice` + an OGG/Opus conversion step is a clean,
isolated future change if ever wanted.
"""

import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

API_BASE = "https://api.telegram.org"
TIMEOUT_SECONDS = 30.0


class TelegramError(Exception):
    """Raised on a missing/invalid bot token, a malformed chat_id, or any
    non-2xx Telegram response. One exception type so callers don't need to
    know Telegram's own error JSON shape."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _token() -> str:
    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN not set - add it to .env")
    return token


def is_configured() -> bool:
    """True only when a bot token is present. Does not call Telegram — safe
    to call from anywhere, including at import time."""
    load_dotenv()
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _url(method: str) -> str:
    return f"{API_BASE}/bot{_token()}/{method}"


def _post(method: str, **kwargs) -> dict[str, Any]:
    try:
        response = httpx.post(_url(method), timeout=TIMEOUT_SECONDS, **kwargs)
    except httpx.HTTPError as exc:
        raise TelegramError(f"network error calling Telegram: {exc}") from exc
    body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    if not response.is_success or not body.get("ok", False):
        raise TelegramError(
            body.get("description", f"Telegram returned HTTP {response.status_code}"),
            status_code=response.status_code,
        )
    return body["result"]


def send_message(chat_id: str, text: str) -> dict[str, Any]:
    """A REAL Telegram text message, genuinely delivered. `text` is decided
    entirely upstream (the ledger's template, or an operator's own typed
    words) — this function chooses no content (CLAUDE.md law 1)."""
    result = _post("sendMessage", json={"chat_id": chat_id, "text": text})
    return {"message_id": result.get("message_id"), "chat_id": chat_id}


def send_voice(chat_id: str, audio_path: Path, caption: str | None = None) -> dict[str, Any]:
    """A REAL, genuinely delivered audio file — see the module docstring for
    why this uses `sendAudio` (a standard audio-file bubble) rather than
    `sendVoice` (Telegram's native round voice-note style, which needs an
    OGG/Opus-encoded file this project does not produce)."""
    with open(audio_path, "rb") as fh:
        files = {"audio": (audio_path.name, fh, "audio/mpeg")}
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        result = _post("sendAudio", data=data, files=files)
    return {"message_id": result.get("message_id"), "chat_id": chat_id}


def get_recent_chats(limit: int = 20) -> list[dict[str, Any]]:
    """Reads the bot's own inbox (`getUpdates`) to discover the `chat_id` of
    whoever has messaged it — the one-time opt-in every Telegram bot needs
    before it can message someone. Returns the most recent distinct chats,
    newest first, each `{"chat_id", "username", "first_name", "text", "date"}`
    — read-only, decides nothing, exists purely so an operator (or the
    dashboard) can find a real chat_id to attach to a Contact after asking a
    debtor to message the bot once.
    """
    result = _post("getUpdates", params={"limit": 100})
    seen: dict[str, dict[str, Any]] = {}
    for update in result:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        chat = message.get("chat", {})
        chat_id = str(chat.get("id"))
        seen[chat_id] = {
            "chat_id": chat_id,
            "username": chat.get("username"),
            "first_name": chat.get("first_name"),
            "text": message.get("text"),
            "date": message.get("date"),
        }
    return list(reversed(list(seen.values())))[:limit]
