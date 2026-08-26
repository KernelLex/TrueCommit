"""Real text-to-speech for the voice-reminder channel (packet P14) — gTTS.

WHAT IS REAL HERE AND WHAT IS NOT (state this out loud, every time)
-------------------------------------------------------------------
* **REAL:** the audio. `generate_voice_note()` calls Google Translate's TTS
  endpoint through `gTTS` and writes a genuine, playable MP3 to disk. A human
  reviewing a case can press play on the dashboard and hear the reminder in
  Hindi/Hinglish. Nothing about the file is a placeholder — verified live on
  2026-08-26 (61,632 bytes, `ff f3` MPEG frame sync; see
  tracking/BUILD_LOG.md).
* **SIMULATED:** the delivery. No phone rings. There is no Twilio/Exotel/any
  telephony credential in this repo, so nothing dials a handset, and every
  dispatched call record carries `dial_status: "simulated_no_telephony_provider"`
  — the same discipline `execute_mandate`'s `simulated: true` + `reason` uses.
  If real telephony credentials ever land, this flips real exactly the way the
  Razorpay mandate rail did: the generated MP3 becomes the media payload of a
  real outbound call, `dial_status` starts reporting the provider's answer, and
  nothing above this module changes. That integration is NOT built here — there
  are no credentials for it, and a stubbed "provider" that fabricated delivery
  receipts would be worse than no provider at all.

THE LLM/AI BOUNDARY (CLAUDE.md law 1)
-------------------------------------
gTTS is an AI system and it is listed in tracking/AI_JUDGMENT.md as one. It is
a SPEAK-only use, and the narrowest kind: by the time it runs, the sentence has
already been fully determined by the ledger's templates and the ledger's own
amount record. TTS chooses no words, no amount, no date, no recipient and no
timing — it converts a finished string into audio. It cannot influence a state
transition because it runs strictly AFTER `check_bounds()` has passed and the
Action has been audited.

NETWORK FAILURE IS NOT A REASON TO LOSE THE REMINDER
----------------------------------------------------
gTTS hits the public internet. A DNS hiccup must not take down a simulated day,
so callers are expected to catch `VoiceGenerationError` and record the reminder
with `audio_generation: "failed"` plus the transcript — the message still
exists, it just has no audio. Sentinel's own ethos: no failure is silent, every
failure has a designed next step.

DETERMINISM (CLAUDE.md law 6)
-----------------------------
Filenames are derived, never random — no uuid4 anywhere. Given the same
action/entity ids (or, with no name supplied, the same text) a re-run writes to
the same path and overwrites it, so replaying a scenario leaves one predictable
file per reminder rather than a growing pile of orphans.
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VOICE_NOTE_DIR = ROOT / "demo_assets" / "voice_notes"
"""Where generated MP3s land. `api/main.py` mounts this directory read-only at
`/voice-notes`, so the dashboard plays them from `/api/voice-notes/<file>.mp3`
through the Vite proxy."""

VOICE_LANG = "hi"
"""Hindi. The reminder copy is Hinglish (Roman-script Hindi with English
loanwords), which the Hindi voice reads naturally. Deliberately NOT
language-detected per sentence: a detector would be a second, unnecessary
model making a choice about content, and it would make the same input produce
different audio on different days."""

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class VoiceGenerationError(RuntimeError):
    """gTTS could not produce audio — almost always the network, occasionally a
    rejected/empty input. Typed so callers can catch exactly this and fall back
    to a transcript-only reminder instead of swallowing every exception."""


def voice_note_stem(entity_id: str, action_id: str) -> str:
    """The deterministic filename stem for one reminder. Same ids -> same file."""
    return _slug(f"{entity_id}-{action_id}")


def _slug(raw: str) -> str:
    cleaned = _SAFE.sub("-", raw).strip("-._")
    return cleaned or "voice-note"


def _stem_from_text(text: str) -> str:
    """Fallback stem when no name is supplied: a content hash, so the same
    sentence always resolves to the same file (and never a random uuid)."""
    return "vn-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def generate_voice_note(text: str, out_dir: Path, *, name: str | None = None) -> Path:
    """Synthesize `text` to a real MP3 under `out_dir` and return its path.

    `name` is the deterministic stem (callers pass `voice_note_stem(entity_id,
    action_id)`); omitted, the stem is a hash of the text itself. Either way the
    path is a pure function of the inputs — re-running a scenario overwrites the
    same file rather than accumulating new ones.

    Raises `VoiceGenerationError` on any failure, including the "gTTS returned
    without raising but wrote nothing usable" case. A zero-byte or missing file
    is deleted before raising, so a failed generation never leaves a corpse on
    disk that a later reader could mistake for real audio.
    """
    if not text or not text.strip():
        raise VoiceGenerationError("refusing to synthesize an empty reminder")

    stem = _slug(name) if name else _stem_from_text(text)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{stem}.mp3"

    try:
        from gtts import gTTS  # imported here so the module loads without network/deps

        gTTS(text=text, lang=VOICE_LANG).save(str(path))
    except Exception as exc:  # gTTS raises gtts.tts.gTTSError, requests errors, OSError...
        _discard(path)
        raise VoiceGenerationError(f"gTTS failed to generate audio: {exc}") from exc

    if not path.exists() or path.stat().st_size == 0:
        _discard(path)
        raise VoiceGenerationError("gTTS returned successfully but produced no audio bytes")
    return path


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
