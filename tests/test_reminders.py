"""Packet P14 — real SMS + real AI-generated voice reminders.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
---------------------------------------
The feature adds a new outbound CHANNEL. The single thing that could go wrong
in a way that matters is that a new channel becomes a way around bound #4 — a
merchant (or the ladder) reaching a debtor a third time this week because the
third message happened to be an SMS. So the load-bearing tests here are the
boring ones:

  * `sms` reaches EXACTLY the same verdict as `message` from `check_bounds()`
    on every input, so it cannot have been given a softer rule by accident;
  * a manual reminder is refused once the debtor's weekly budget is spent, and
    the refusal is audited;
  * both the AUTONOMOUS trigger (ESCALATE_2) and the OPERATOR trigger
    (`manual_reminder`) go through the same gate.

The TTS tests never touch the network: `gtts.gTTS` is stubbed in both
directions (writes a file / raises), which is enough to exercise the real
`generate_voice_note` including its deterministic filename, its empty-output
guard and its typed-failure fallback.
"""

import datetime as dt
import random

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.action import tts
from engine.judgment import state_machine as sm
from engine.judgment.ledger import (
    MANUAL_REMINDER_CHANNELS,
    TOUCH_COUNTED_KINDS,
    Ledger,
    ReviewQueueError,
)
from engine.integration.runner import WorldRunner
from engine.schemas import ActionKind, Invoice, MessageChannel

NOW = dt.datetime(2026, 8, 26, 9, 0)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _ledger(entity_id: str = "INV-001", debtor_id: str = "D-ACME", amount: int = 40000) -> Ledger:
    ledger = Ledger()
    ledger.register_invoice(Invoice(
        id=entity_id, debtor_id=debtor_id, amount_inr=amount,
        issued=NOW.date(), due=NOW.date(), status="overdue", description="test invoice",
    ))
    ledger.process_event("invoice_triaged", entity_id, {}, NOW)
    return ledger


def _spend_touches(ledger: Ledger, entity_id: str, n: int) -> None:
    """Burn `n` touches through the ORDINARY path — real `outreach_sent` events
    producing real, gated, touch-counted `message` actions. Deliberately not by
    poking `touches_by_debtor` directly: the point of the test below is that a
    manual reminder loses to a budget the normal flow spent, so the budget has
    to have been spent by the normal flow."""
    for _ in range(n):
        action = ledger.process_event("outreach_sent", entity_id, {"stage": "gentle"}, NOW)
        assert action is not None and action.kind == "message"


# ---------------------------------------------------------------------------
# 1. Schema: the two literals really were extended
# ---------------------------------------------------------------------------


def test_sms_is_a_real_message_channel():
    assert "sms" in MessageChannel.__args__
    assert set(MessageChannel.__args__) == {"wa", "email", "sms"}


def test_sms_is_a_real_action_kind_and_voice_was_not_renamed():
    assert "sms" in ActionKind.__args__
    # P14 upgraded what a `voice` action PRODUCES; it did not split or rename
    # the kind, and every pre-existing kind is still here.
    assert {"link", "mandate_offer", "mandate_execute", "message", "voice",
            "evidence_packet", "human_handoff"} <= set(ActionKind.__args__)


# ---------------------------------------------------------------------------
# 2. Bounds: sms is not a loophole
# ---------------------------------------------------------------------------


def test_sms_joined_every_set_the_other_outbound_kinds_are_in():
    assert "sms" in sm.OUTBOUND_KINDS
    assert "sms" in sm.TouchKind.__args__
    assert "sms" in TOUCH_COUNTED_KINDS
    assert set(MANUAL_REMINDER_CHANNELS) <= sm.OUTBOUND_KINDS


def test_sms_is_capped_per_debtor_like_every_other_touch():
    e = sm.EntityState(entity_id="INV-001", state="ESCALATE_1", invoice_amount_inr=40000)
    debtor_touches = [NOW - dt.timedelta(days=2), NOW - dt.timedelta(hours=6)]
    result = sm.check_bounds(e, "sms", {"stage": "firm"}, NOW, debtor_touches)
    assert not result.allowed
    assert "max_touches_per_week" in result.reason


def test_sms_is_blocked_from_every_terminal_state():
    for terminal in sorted(sm.TERMINAL_STATES):
        e = sm.EntityState(entity_id="INV-001", state=terminal)
        result = sm.check_bounds(e, "sms", {"stage": "firm"}, NOW)
        assert not result.allowed, f"sms should be blocked from {terminal}"
        assert "terminal state" in result.reason


def test_a_legal_stage_sms_is_refused_like_a_legal_stage_message():
    e = sm.EntityState(entity_id="INV-001", state="ESCALATE_3")
    assert not sm.check_bounds(e, "sms", {"stage": "legal"}, NOW).allowed
    assert not sm.check_bounds(e, "voice", {"stage": "legal"}, NOW).allowed


def test_sms_and_message_reach_identical_verdicts_on_every_input():
    """THE PROOF that the new channel got no softer rule.

    If `sms` had been added to some of the bound enumerations and missed from
    another, this diverges immediately — a `message` would be refused where an
    `sms` sailed through, which is precisely the shape of hole a new channel
    introduces. Run over a large random sample rather than a handful of cases
    because the interesting inputs are combinations (terminal state AND a spent
    budget AND a legal stage), not any single field.
    """
    rng = random.Random(42)
    for _ in range(2000):
        e = sm.EntityState(
            entity_id="INV-FUZZ",
            state=rng.choice(list(sm.State.__args__)),
            renegotiation_count=rng.randint(0, sm.RENEGOTIATION_CAP + 2),
            retry_count=rng.randint(0, sm.RETRY_ON_EXECUTION_FAILURE + 2),
            mandate_refused=rng.random() < 0.3,
            touches=[NOW - dt.timedelta(days=rng.randint(0, 14)) for _ in range(rng.randint(0, 4))],
            invoice_amount_inr=rng.choice([None, 40_000, 150_000]),
        )
        params = {"stage": rng.choice(["gentle", "firm", "legal", "clarify", None])}
        debtor_touches = (
            None if rng.random() < 0.4
            else [NOW - dt.timedelta(days=rng.randint(0, 14)) for _ in range(rng.randint(0, 5))]
        )
        as_sms = sm.check_bounds(e, "sms", dict(params), NOW, debtor_touches)
        as_message = sm.check_bounds(e, "message", dict(params), NOW, debtor_touches)
        assert as_sms.allowed == as_message.allowed
        assert as_sms.reason == as_message.reason

        detailed = sm.check_bounds_detailed(e, "sms", dict(params), NOW, debtor_touches)
        assert as_sms.allowed == all(c.passed for c in detailed), (
            "the checklist lens must agree with the gate for sms exactly as it does for every "
            "other kind (test_state_machine's invariant, restated for the new channel)"
        )


# ---------------------------------------------------------------------------
# 3. manual_reminder: the operator path is genuinely gated
# ---------------------------------------------------------------------------


def test_manual_reminder_emits_a_bounds_checked_touch_counted_action():
    ledger = _ledger()
    out = ledger.manual_reminder("INV-001", "sms", NOW)
    assert out["blocked"] is False and out["block_reason"] is None
    action = out["action"]
    assert action.kind == "sms"
    assert action.bounds_checked is True
    assert action.params["manual"] is True
    assert ledger.touches_by_debtor["D-ACME"] == [NOW], "a manual reminder spends a real touch"


def test_manual_reminder_is_blocked_once_the_normal_flow_spent_the_budget():
    """The whole point of the packet, in one test.

    The debtor's two weekly touches are spent by the ORDINARY ladder. The
    merchant then clicks "send a reminder" — and is refused, exactly as a stale
    approve-click is refused in P9. A manual channel that could be spent for
    free would be a hole in bound #4, not a feature.
    """
    ledger = _ledger()
    _spend_touches(ledger, "INV-001", sm.MAX_TOUCHES_PER_WEEK)
    audit_before = len(ledger.audit)

    out = ledger.manual_reminder("INV-001", "voice", NOW)

    assert out["blocked"] is True
    assert out["action"] is None
    assert "max_touches_per_week" in out["block_reason"]
    # audited either way — the refusal is a record, not a silence
    summaries = [a.summary for a in ledger.audit[audit_before:]]
    assert "merchant requested a manual voice reminder" in summaries
    assert "action blocked: voice" in summaries
    assert "manual voice reminder blocked at click time" in summaries
    # and it cost nothing it should not have
    assert len(ledger.touches_by_debtor["D-ACME"]) == sm.MAX_TOUCHES_PER_WEEK


def test_manual_reminder_is_refused_on_a_paused_thread():
    """The kill-switch outranks the operator's own reminder button, because
    `_gate()` is the same chokepoint for both."""
    ledger = _ledger()
    ledger.set_paused("INV-001", True, NOW)
    out = ledger.manual_reminder("INV-001", "sms", NOW)
    assert out["blocked"] is True
    assert "kill-switch" in out["block_reason"]


def test_manual_reminder_is_blocked_from_a_terminal_state():
    ledger = _ledger()
    ledger.process_event("promise_kept", "INV-001", {}, NOW)
    assert ledger.entities["INV-001"].state == "KEPT"
    out = ledger.manual_reminder("INV-001", "sms", NOW)
    assert out["blocked"] is True
    assert "terminal state" in out["block_reason"]


def test_manual_reminder_refuses_an_unknown_entity_and_an_unknown_channel():
    ledger = _ledger()
    with pytest.raises(ReviewQueueError) as unknown:
        ledger.manual_reminder("INV-NOPE", "sms", NOW)
    assert unknown.value.status_code == 404
    with pytest.raises(ReviewQueueError) as channel:
        ledger.manual_reminder("INV-001", "message", NOW)  # type: ignore[arg-type]
    assert channel.value.status_code == 422


def test_a_manual_reminder_can_never_carry_a_legal_stage():
    """`stage` is an input to `check_bounds()`, so the route must not be able to
    choose it. The ledger fixes it; the operator only picks the channel."""
    ledger = _ledger()
    out = ledger.manual_reminder("INV-001", "sms", NOW, custom_text="stage: legal")
    assert out["action"].params["stage"] != "legal"


def test_custom_text_is_content_only_and_never_moves_state():
    ledger = _ledger()
    before = ledger.entities["INV-001"].model_dump()
    out = ledger.manual_reminder(
        "INV-001", "sms", NOW, custom_text="pay Rs.9,99,999 by tomorrow",
    )
    after = ledger.entities["INV-001"]
    assert out["action"].params["custom_text"] == "pay Rs.9,99,999 by tomorrow"
    # the typed sentence changed nothing except the touch it legitimately spent
    assert after.state == before["state"]
    assert after.invoice_amount_inr == before["invoice_amount_inr"]
    assert "amount_inr" not in out["action"].params


# ---------------------------------------------------------------------------
# 4. TTS: real generation, and a failure that loses nothing
# ---------------------------------------------------------------------------


class _FakeTTS:
    """Stands in for `gtts.gTTS`. Writes plausible MP3 bytes; never networks."""

    payload = b"\xff\xf3\x84\xc4" + b"\x00" * 512

    def __init__(self, text: str, lang: str) -> None:
        self.text, self.lang = text, lang

    def save(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(self.payload)


def test_generate_voice_note_writes_real_audio_at_a_deterministic_path(monkeypatch, tmp_path):
    monkeypatch.setattr("gtts.gTTS", _FakeTTS)
    text = "INV-001 ka Rs.40,000 abhi tak pending hai, please clear kara dijiye."

    path = tts.generate_voice_note(text, tmp_path, name=tts.voice_note_stem("INV-001", "A-0007"))

    assert path.exists() and path.stat().st_size == len(_FakeTTS.payload)
    assert path.name == "INV-001-A-0007.mp3"
    # SEED=42 determinism: the same ids re-resolve to the same file, no uuid4
    again = tts.generate_voice_note(text, tmp_path, name=tts.voice_note_stem("INV-001", "A-0007"))
    assert again == path
    assert len(list(tmp_path.glob("*.mp3"))) == 1


def test_generate_voice_note_speaks_hindi_for_hinglish_copy(monkeypatch, tmp_path):
    captured = {}

    class _Capture(_FakeTTS):
        def __init__(self, text: str, lang: str) -> None:
            captured["text"], captured["lang"] = text, lang
            super().__init__(text, lang)

    monkeypatch.setattr("gtts.gTTS", _Capture)
    tts.generate_voice_note("abhi tak pending hai", tmp_path, name="x")
    assert captured["lang"] == tts.VOICE_LANG == "hi"
    assert captured["text"] == "abhi tak pending hai"


def test_a_network_failure_raises_the_typed_error_and_leaves_no_corpse(monkeypatch, tmp_path):
    class _Boom(_FakeTTS):
        def save(self, path: str) -> None:
            with open(path, "wb") as fh:
                fh.write(b"partial")  # a half-written file, as a real failure leaves
            raise ConnectionError("getaddrinfo failed")

    monkeypatch.setattr("gtts.gTTS", _Boom)
    with pytest.raises(tts.VoiceGenerationError) as exc:
        tts.generate_voice_note("hello", tmp_path, name="INV-001-A-0001")
    assert "getaddrinfo failed" in str(exc.value)
    assert list(tmp_path.glob("*.mp3")) == [], "a failed generation must not leave a fake file behind"


def test_a_zero_byte_result_counts_as_a_failure(monkeypatch, tmp_path):
    class _Empty(_FakeTTS):
        def save(self, path: str) -> None:
            open(path, "wb").close()

    monkeypatch.setattr("gtts.gTTS", _Empty)
    with pytest.raises(tts.VoiceGenerationError):
        tts.generate_voice_note("hello", tmp_path, name="e")


def test_empty_text_is_refused_before_any_call_is_made(tmp_path):
    with pytest.raises(tts.VoiceGenerationError):
        tts.generate_voice_note("   ", tmp_path, name="e")


# ---------------------------------------------------------------------------
# 5. The runner: both triggers produce real content, honestly labelled
# ---------------------------------------------------------------------------


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A real WorldRunner with gTTS stubbed and the audio directory redirected
    into tmp_path — the real `generate_voice_note` still runs, so the filename,
    the size guard and the failure handling are all genuinely exercised."""
    monkeypatch.setattr("gtts.gTTS", _FakeTTS)
    monkeypatch.setattr(tts, "VOICE_NOTE_DIR", tmp_path)
    return WorldRunner(real_razorpay=False, real_tts=True)


def _drive_to_escalate_2(world: WorldRunner, entity_id: str) -> None:
    world._emit("invoice_triaged", entity_id, {}, 0)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_1 (message, touch 1)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_2 (voice,   touch 2)


def test_the_autonomous_escalate_2_path_still_produces_a_voice_action_now_with_real_audio(world):
    """The existing ladder hook, upgraded rather than replaced.

    `_ESCALATE_ACTION["ESCALATE_2"] == ("voice", {"stage": "firm"})` is
    untouched, and it still reaches the wire through `_decide_action` ->
    `_try_action` -> `_gate`. What is new is what comes out the other end: a
    real MP3 and an explicit simulated-dial marker.
    """
    _drive_to_escalate_2(world, "INV-001")

    assert world.ledger.entities["INV-001"].state == "ESCALATE_2"
    voice = [a for a in world.actions if a.kind == "voice"]
    assert len(voice) == 1
    assert voice[0].bounds_checked is True
    assert not voice[0].params.get("manual"), "this one was the AGENT's decision, not a click"

    record = world.reminders[-1]
    assert record["channel"] == "voice"
    assert record["audio_generation"] == "ok"
    assert record["dial_status"] == "simulated_no_telephony_provider"
    assert record["audio_bytes"] > 0
    assert (tts.VOICE_NOTE_DIR / f"INV-001-{voice[0].id}.mp3").exists()


def test_the_autonomous_voice_path_is_blockable_by_the_same_bound(world):
    """Same ladder position, one touch less budget: refused, audited, silent on
    the wire. Nothing about the escalation ladder exempts the new channel."""
    _drive_to_escalate_2(world, "INV-001")
    world.ledger.touches_by_debtor.setdefault("INV-001", [])

    # A second entity belonging to the SAME debtor now has no budget left.
    debtor = world.ledger.debtor_of["INV-001"]
    sibling = next(
        eid for eid, d in world.ledger.debtor_of.items() if d == debtor and eid != "INV-001"
    )
    _drive_to_escalate_2(world, sibling)

    blocked = [
        r for r in world.ledger.gate_log
        if r.entity_id == sibling and r.kind == "voice" and not r.allowed
    ]
    assert blocked, "the sibling's voice escalation should have hit the shared debtor cap"
    assert "max_touches_per_week" in blocked[0].reason
    assert not any(r["entity_id"] == sibling and r["channel"] == "voice" for r in world.reminders)


def test_a_voice_reminder_survives_a_tts_outage_as_a_transcript(monkeypatch, tmp_path):
    class _Boom(_FakeTTS):
        def save(self, path: str) -> None:
            raise ConnectionError("no route to host")

    monkeypatch.setattr("gtts.gTTS", _Boom)
    monkeypatch.setattr(tts, "VOICE_NOTE_DIR", tmp_path)
    world = WorldRunner(real_razorpay=False, real_tts=True)

    _drive_to_escalate_2(world, "INV-001")

    record = world.reminders[-1]
    assert record["audio_generation"] == "failed"
    assert record["audio_url"] is None
    assert record["text"], "the transcript is still the real reminder"
    assert "no route to host" in record["audio_error"]
    # and the failure is visible, not swallowed
    assert any(
        "voice note audio generation failed" in a.summary for a in world.ledger.audit
    )


def test_tts_can_be_switched_off_without_pretending_it_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "VOICE_NOTE_DIR", tmp_path)
    world = WorldRunner(real_razorpay=False, real_tts=False)
    _drive_to_escalate_2(world, "INV-001")
    record = world.reminders[-1]
    assert record["audio_generation"] == "disabled"
    assert list(tmp_path.glob("*.mp3")) == []


def test_an_sms_rides_the_sms_rail_and_says_it_was_never_delivered(world):
    out = world.ledger.manual_reminder("INV-001", "sms", world.now())
    world.dispatch_action(out["action"])

    record = world.reminders[-1]
    assert record["channel"] == "sms"
    assert record["send_status"] == "simulated_no_sms_provider"
    assert record["text"], "the SMS text IS the content — there is no generation step"
    queued = [m for m in world.messenger.queue if m.action_id == out["action"].id]
    assert queued and queued[0].rail == "sms_text" and queued[0].channel == "sms"


def test_a_custom_text_reminder_is_spoken_verbatim(world):
    typed = "Sharma-ji, Rs.40,000 ka payment {please} clear kar dijiye"
    out = world.ledger.manual_reminder("INV-001", "voice", world.now(), custom_text=typed)
    world.dispatch_action(out["action"])
    record = world.reminders[-1]
    # verbatim, braces and all — custom text is never run through .format()
    assert record["text"] == typed
    assert record["manual"] is True


def test_reminders_are_not_registered_as_openable_instruments(world):
    """P11's 48h link-open window is about a LINK. A voice note and an SMS carry
    no URL and produce no open event, so claiming a "never opened" verdict for
    them would be manufacturing a delivery signal that does not exist. This pins
    that decision, and that P11's own wiring is untouched."""
    out = world.ledger.manual_reminder("INV-001", "sms", world.now())
    world.dispatch_action(out["action"])
    assert world._open_instruments.get("INV-001") in (None, [])
    assert out["action"].id not in world.sentinel.link_sent_at
    # ...but the Sentinel still tracked the send attempt like every other kind
    assert out["action"].id not in world.sentinel.attempts  # success clears it
    assert world.sentinel.dead_letter == []


# ---------------------------------------------------------------------------
# 6. Through the API, the way an operator actually drives it
# ---------------------------------------------------------------------------


def _entity_with_budget(client) -> str:
    from api.main import ledger

    return next(
        eid for eid, e in ledger.entities.items()
        if e.state not in sm.TERMINAL_STATES
        and len(ledger.touches_by_debtor.get(ledger.debtor_of.get(eid, eid), [])) < sm.MAX_TOUCHES_PER_WEEK
    )


def test_remind_now_sends_a_real_sms_and_lists_it_back(client):
    client.post("/advance", json={"days": 1})
    entity_id = _entity_with_budget(client)

    body = client.post(f"/entities/{entity_id}/remind-now", json={"channel": "sms"}).json()
    assert body["blocked"] is False
    assert body["action"]["kind"] == "sms"
    assert body["reminder"]["send_status"] == "simulated_no_sms_provider"

    listed = client.get(f"/entities/{entity_id}/reminders").json()
    assert listed["counts"]["sent"] == 1
    assert listed["reminders"][0]["text"] == body["reminder"]["text"]
    assert "no telephony or SMS-gateway credential" in listed["honesty"]["simulated"]


def test_remind_now_reports_a_bound_refusal_as_a_200_not_an_error(client):
    """P9 decision #7, restated for this route: the request was valid and the
    system did exactly what it should. Making a working stopping rule an HTTP
    error would teach the dashboard to treat it as a failure."""
    client.post("/advance", json={"days": 1})
    entity_id = _entity_with_budget(client)

    for _ in range(sm.MAX_TOUCHES_PER_WEEK + 1):
        response = client.post(f"/entities/{entity_id}/remind-now", json={"channel": "sms"})
        assert response.status_code == 200

    body = response.json()
    assert body["blocked"] is True
    assert "max_touches_per_week" in body["block_reason"]
    assert body["action"] is None

    listed = client.get(f"/entities/{entity_id}/reminders").json()
    assert listed["counts"]["blocked"] >= 1
    refusal = next(r for r in listed["reminders"] if r["status"] == "blocked")
    assert "max_touches_per_week" in refusal["block_reason"]
    assert any(c["name"] == "max_touches_per_week" and not c["passed"] for c in refusal["checks"])


def test_remind_now_refuses_a_channel_it_does_not_own(client):
    client.post("/advance", json={"days": 1})
    entity_id = _entity_with_budget(client)
    assert client.post(
        f"/entities/{entity_id}/remind-now", json={"channel": "message"}
    ).status_code == 422


def test_reminders_404s_on_an_unknown_entity(client):
    assert client.get("/entities/INV-NOPE/reminders").status_code == 404


def test_remind_now_is_refused_on_a_paused_thread_through_the_api(client):
    client.post("/advance", json={"days": 1})
    entity_id = _entity_with_budget(client)
    client.post(f"/entities/{entity_id}/pause")
    body = client.post(f"/entities/{entity_id}/remind-now", json={"channel": "sms"}).json()
    assert body["blocked"] is True
    assert "kill-switch" in body["block_reason"]
