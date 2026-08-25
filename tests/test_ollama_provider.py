"""Tests for engine/perception/providers/ollama.py.

NO live Ollama call anywhere in this file except the one test explicitly
marked and skipped-unless-reachable at the bottom — every other test builds
an OllamaProvider with an injected httpx.MockTransport, the same pattern
tests/test_razorpay_client.py already established for httpx-based clients in
this repo. `pytest tests/` must be green with zero network access.
"""

import datetime as dt
import json
import os

import httpx
import pytest

from engine.perception import cache
from engine.perception.providers import get_provider, reset_instances
from engine.perception.providers.ollama import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    OllamaProvider,
    OllamaProviderError,
    build,
    get_fallback_events,
    reset_fallback_events,
)
from engine.schemas import Cart, CartItem, Invoice, Message

TS = dt.datetime(2026, 8, 27, 10, 0)  # a Thursday, matching the dataset's own clock


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Never touch the developer's real .cache/perception, and never let one
    test's fallback log leak into the next."""
    monkeypatch.setenv("PK_PERCEPTION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PK_PERCEPTION_PROVIDER", raising=False)
    cache.reset_stats()
    reset_instances()
    reset_fallback_events()
    yield
    reset_instances()
    reset_fallback_events()


def _msg(text: str, mid: str = "M-T-1", direction: str = "in", ts: dt.datetime = TS) -> Message:
    return Message(id=mid, thread_id="T-T", direction=direction, channel="wa", text=text, ts=ts)


def _invoice(**kw) -> Invoice:
    base = dict(id="INV-TEST", debtor_id="D-01", amount_inr=50000, issued=dt.date(2026, 7, 1),
                due=dt.date(2026, 8, 1), status="overdue", description="x")
    return Invoice.model_validate(base | kw)


def _cart(signals, stage="payment") -> Cart:
    return Cart(id="C-TEST", customer_id="CUST-1", amount_inr=2499,
                items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)],
                drop_stage=stage, drop_signals=signals, ts=TS)


def _chat_response(content_obj: dict | str, status: int = 200) -> httpx.Response:
    content = content_obj if isinstance(content_obj, str) else json.dumps(content_obj)
    return httpx.Response(status, json={
        "model": "test-model", "created_at": "2026-08-26T00:00:00Z",
        "message": {"role": "assistant", "content": content}, "done": True,
    })


def _fixed_handler(content_obj, status: int = 200, *, captured: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        return _chat_response(content_obj, status)
    return handler


def _sequential_handler(contents: list, *, captured: list):
    """Returns `contents[i]` on the i-th call (clamped), keyed off `captured`
    so the test can also inspect every request body sent."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        idx = min(len(captured) - 1, len(contents) - 1)
        return _chat_response(contents[idx])
    return handler


def _provider(handler, model: str = "qwen2.5:7b") -> OllamaProvider:
    return OllamaProvider(model=model, transport=httpx.MockTransport(handler))


def _connect_error_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


# ---------------------------------------------------------------------------
# Registration / convention discovery / config
# ---------------------------------------------------------------------------


def test_ollama_is_discovered_by_convention():
    """No edit to providers/__init__.py needed: providers/ollama.py exposing
    build() is enough for get_provider('ollama') to find it."""
    provider = get_provider("ollama")
    assert provider.name == "ollama"
    assert provider.uses_cache is True


def test_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("PK_OLLAMA_MODEL", raising=False)
    provider = build()
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == DEFAULT_BASE_URL
    assert provider.model == DEFAULT_MODEL == "qwen2.5:7b"


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example-host:9999")
    monkeypatch.setenv("PK_OLLAMA_MODEL", "qwen2.5:3b")
    provider = OllamaProvider()
    assert provider.base_url == "http://example-host:9999"
    assert provider.model == "qwen2.5:3b"


def test_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example-host:9999")
    monkeypatch.setenv("PK_OLLAMA_MODEL", "qwen2.5:3b")
    provider = OllamaProvider(base_url="http://explicit:1234", model="qwen2.5:7b")
    assert provider.base_url == "http://explicit:1234"
    assert provider.model == "qwen2.5:7b"


# ---------------------------------------------------------------------------
# Happy path — one per task, using the SAME prompt files every LLM provider shares
# ---------------------------------------------------------------------------


def test_extract_happy_path():
    captured = []
    handler = _fixed_handler(
        {"level": "L1", "amount_inr": 40000, "date": "2026-08-28", "condition": None, "confidence": 0.92},
        captured=captured,
    )
    provider = _provider(handler)
    msg = _msg("will clear 40000 by Friday pakka")
    extraction = provider.extract(msg, [msg])

    assert extraction.level == "L1"
    assert extraction.amount_inr == 40000
    assert extraction.date == dt.date(2026, 8, 28)
    assert extraction.confidence == 0.92
    body = captured[0]
    assert body["model"] == "qwen2.5:7b"
    assert body["stream"] is False
    assert body["messages"][0]["role"] == "system"
    assert "promise-extraction" in body["messages"][0]["content"]  # extract.md, shared with anthropic_provider
    assert body["format"]["properties"]["level"]["enum"] == ["L1", "L2", "L3", "L4", "L5"]


def test_triage_happy_path():
    captured = []
    handler = _fixed_handler(
        {"cause": "cashflow_delay", "confidence": 0.8, "evidence": ["debtor is engaging"]},
        captured=captured,
    )
    provider = _provider(handler)
    thread = [_msg("boss month end tight, will clear 40k by Friday pakka")]
    result = provider.triage(_invoice(), thread)

    assert result.cause == "cashflow_delay"
    assert result.confidence == 0.8
    assert result.evidence == ["debtor is engaging"]
    assert "root-cause triage" in captured[0]["messages"][0]["content"]  # triage.md


def test_cart_cause_happy_path():
    captured = []
    handler = _fixed_handler(
        {"cause": "friction", "confidence": 0.9, "evidence": ["two OTP failures"]},
        captured=captured,
    )
    provider = _provider(handler)
    result = provider.cart_cause(_cart(["otp_fail", "otp_fail"]))

    assert result.cause == "friction"
    assert result.confidence == 0.9
    assert "cart-abandonment" in captured[0]["messages"][0]["content"]  # cart_cause.md


# ---------------------------------------------------------------------------
# Reference date injection (packet P7). The bug this guards against: the user
# turn used to carry the message text and thread with NO date at all, so a
# model had nothing to resolve "by Friday" against — one live smoke call
# answered 2023-10-06. See tracking/BUILD_LOG.md, 2026-08-26.
# ---------------------------------------------------------------------------


def _user_content(captured: list) -> str:
    return captured[0]["messages"][1]["content"]


def test_extract_user_content_states_todays_date():
    captured = []
    handler = _fixed_handler(
        {"level": "L1", "amount_inr": 40000, "date": "2026-08-28", "condition": None, "confidence": 0.9},
        captured=captured,
    )
    msg = _msg("will clear 40000 by Friday pakka")
    _provider(handler).extract(msg, [msg])

    content = _user_content(captured)
    assert "Today is 2026-08-27 (Thursday)." in content  # derived from message.ts, never the wall clock
    assert "YYYY-MM-DD" in content  # and the answer must come back as an ISO date
    assert "will clear 40000 by Friday pakka" in content


def test_extract_user_content_dates_every_thread_line():
    """A "Friday" quoted in a five-day-old message must not read as this
    week's Friday — so each thread line carries its own date."""
    captured = []
    handler = _fixed_handler(
        {"level": "L4", "amount_inr": None, "date": None, "condition": None, "confidence": 0.6},
        captured=captured,
    )
    older = _msg("chasing this again", mid="M-T-0", direction="out", ts=dt.datetime(2026, 8, 24, 9, 0))
    msg = _msg("we're on it")
    _provider(handler).extract(msg, [older, msg])

    content = _user_content(captured)
    assert "[out] 2026-08-24 (Monday): chasing this again" in content
    assert "[in] 2026-08-27 (Thursday): we're on it" in content


def test_triage_user_content_carries_issued_due_and_today():
    captured = []
    handler = _fixed_handler(
        {"cause": "cashflow_delay", "confidence": 0.8, "evidence": ["engaging"]}, captured=captured
    )
    thread = [_msg("month end tight, will clear it")]
    _provider(handler).triage(_invoice(), thread)

    content = _user_content(captured)
    assert "Today is 2026-08-27 (Thursday)." in content  # latest date on record for the invoice
    assert "issued 2026-07-01 (Wednesday)" in content
    assert "due 2026-08-01 (Saturday)" in content
    assert "26 days past due as of today" in content


def test_triage_with_no_thread_says_so_instead_of_inventing_a_date():
    """An invoice nobody has contacted yet has no dated activity — and is not
    a silent debtor (tracking/DECISIONS.md: `non_responsive` requires outreach
    that went unanswered)."""
    captured = []
    handler = _fixed_handler(
        {"cause": "cashflow_delay", "confidence": 0.45, "evidence": ["no thread"]}, captured=captured
    )
    _provider(handler).triage(_invoice(), [])

    content = _user_content(captured)
    assert "Today is" not in content  # nothing to anchor on — do not invent one
    assert "no outreach has been sent" in content


def test_both_llm_providers_assemble_the_same_user_content():
    """The bug was duplicated inline assembly in two providers. Assert there
    is only one copy left by checking both produce the identical user turn."""
    from engine.perception import assembly

    captured = []
    handler = _fixed_handler(
        {"level": "L1", "amount_inr": 40000, "date": "2026-08-28", "condition": None, "confidence": 0.9},
        captured=captured,
    )
    msg = _msg("will clear 40000 by Friday pakka")
    _provider(handler).extract(msg, [msg])

    assert _user_content(captured) == assembly.extract_user_content(msg, [msg])


# ---------------------------------------------------------------------------
# Robustness (a): confidence normalisation — observed live: 85 instead of 0.85
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_conf,expected", [(85, 0.85), (100, 1.0), (150, 1.0), (0.85, 0.85), (0, 0.0)])
def test_confidence_normalisation(raw_conf, expected):
    handler = _fixed_handler(
        {"level": "L2", "amount_inr": None, "date": "2026-08-28", "condition": None, "confidence": raw_conf}
    )
    provider = _provider(handler)
    msg = _msg("will clear it by Friday")
    extraction = provider.extract(msg, [msg])
    assert extraction.confidence == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Robustness (c): unparseable dates never become a guess
# ---------------------------------------------------------------------------


def test_unparseable_date_becomes_none():
    handler = _fixed_handler(
        {"level": "L2", "amount_inr": 5000, "date": "next Friday", "condition": None, "confidence": 0.7}
    )
    provider = _provider(handler)
    msg = _msg("paying 5000 next Friday")
    extraction = provider.extract(msg, [msg])
    assert extraction.date is None  # never invented from an unparseable string


def test_iso_date_is_accepted():
    handler = _fixed_handler(
        {"level": "L1", "amount_inr": 5000, "date": "2026-09-01", "condition": None, "confidence": 0.9}
    )
    provider = _provider(handler)
    msg = _msg("paying 5000 by Sept 1")
    extraction = provider.extract(msg, [msg])
    assert extraction.date == dt.date(2026, 9, 1)


# ---------------------------------------------------------------------------
# Robustness (b): invalid-JSON / schema-violation retry then typed error
# ---------------------------------------------------------------------------


def test_invalid_json_retries_once_then_recovers():
    captured: list = []
    handler = _sequential_handler(
        ["not valid json at all",
         {"level": "L4", "amount_inr": None, "date": None, "condition": None, "confidence": 0.6}],
        captured=captured,
    )
    provider = _provider(handler)
    msg = _msg("we're on it")
    extraction = provider.extract(msg, [msg])

    assert extraction.level == "L4"
    assert len(captured) == 2
    assert "Return ONLY valid JSON" in captured[1]["messages"][1]["content"]


def test_invalid_json_twice_raises_typed_error():
    captured: list = []
    handler = _sequential_handler(["still not json", "still not json"], captured=captured)
    provider = _provider(handler)
    msg = _msg("we're on it")
    with pytest.raises(OllamaProviderError):
        provider.extract(msg, [msg])
    assert len(captured) == 2  # exactly one retry, no infinite loop


def test_schema_violation_retries_then_raises():
    captured: list = []
    handler = _sequential_handler([{"level": "L1"}, {"level": "L1"}], captured=captured)  # missing required fields
    provider = _provider(handler)
    msg = _msg("will clear 40000 by Friday")
    with pytest.raises(OllamaProviderError):
        provider.extract(msg, [msg])
    assert len(captured) == 2


# ---------------------------------------------------------------------------
# Fallback: Ollama unreachable -> heuristic, the call SUCCEEDS, degradation recorded
# ---------------------------------------------------------------------------


def test_unreachable_falls_back_to_heuristic_and_succeeds():
    provider = _provider(_connect_error_handler)
    msg = _msg("will clear 40000 by Friday pakka")
    extraction = provider.extract(msg, [msg])  # must NOT raise

    assert extraction.level == "L1"  # the heuristic's own answer for this text
    assert extraction.amount_inr == 40000
    assert len(provider.fallback_events) == 1
    assert provider.fallback_events[0]["kind"] == "extract"
    assert provider.fallback_events[0]["entity_id"] == msg.id
    assert provider.fallback_events[0]["model"] == "qwen2.5:7b"

    module_events = get_fallback_events()
    assert len(module_events) == 1
    assert module_events[0]["kind"] == "extract"


def test_unreachable_falls_back_for_triage_and_cart_cause_too():
    provider = _provider(_connect_error_handler)
    thread = [_msg("we initiated a transfer but it bounced, bank flagged a mismatch")]
    triage_result = provider.triage(_invoice(), thread)
    assert triage_result.cause == "payment_failed"

    cart_result = provider.cart_cause(_cart(["otp_fail", "otp_fail"]))
    assert cart_result.cause == "friction"

    assert {e["kind"] for e in provider.fallback_events} == {"triage", "cart_cause"}


def test_timeout_also_triggers_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    provider = _provider(handler)
    msg = _msg("ok")
    extraction = provider.extract(msg, [msg])  # must NOT raise
    assert extraction.level == "L5"  # heuristic: a bare "ok" is silence-equivalent
    assert len(provider.fallback_events) == 1


def test_http_error_status_does_not_fall_back():
    """An HTTP error is a real, loud failure, not a connectivity problem — it
    must raise, never silently degrade (that would hide a real
    misconfiguration, e.g. a typo'd model name, behind a quietly-passing call)."""
    handler = _fixed_handler({"error": "model not found"}, status=404)
    provider = _provider(handler)
    msg = _msg("will clear 40000 by Friday")
    with pytest.raises(OllamaProviderError):
        provider.extract(msg, [msg])
    assert provider.fallback_events == []  # not a fallback case


# ---------------------------------------------------------------------------
# Model name participates in the cache fingerprint
# ---------------------------------------------------------------------------


def test_model_name_is_part_of_identity():
    p7 = OllamaProvider(model="qwen2.5:7b", transport=httpx.MockTransport(_connect_error_handler))
    p3 = OllamaProvider(model="qwen2.5:3b", transport=httpx.MockTransport(_connect_error_handler))
    assert p7.identity().startswith("ollama:qwen2.5:7b:prompts@")
    assert p3.identity().startswith("ollama:qwen2.5:3b:prompts@")
    assert p7.identity() != p3.identity()


def test_model_name_changes_the_cache_fingerprint():
    p7 = OllamaProvider(model="qwen2.5:7b", transport=httpx.MockTransport(_connect_error_handler))
    p3 = OllamaProvider(model="qwen2.5:3b", transport=httpx.MockTransport(_connect_error_handler))
    payload = {"some": "payload"}
    assert cache.fingerprint(p7.identity(), payload) != cache.fingerprint(p3.identity(), payload)


def test_prompt_text_is_part_of_the_cache_fingerprint(tmp_path, monkeypatch):
    """Before P7 only the model id was fingerprinted, so editing a prompt
    silently re-served answers computed under the previous wording — which
    would have poisoned the re-measurement the prompt edit was made for."""
    from engine.perception import assembly

    provider = _provider(_connect_error_handler)
    before = provider.identity()

    edited = tmp_path / "prompts"
    edited.mkdir()
    for name in ("extract", "triage", "cart_cause"):
        (edited / f"{name}.md").write_text("a different prompt", encoding="utf-8")
    monkeypatch.setattr(assembly, "PROMPTS_DIR", edited)

    after = provider.identity()
    assert before != after
    payload = {"some": "payload"}
    assert cache.fingerprint(before, payload) != cache.fingerprint(after, payload)


def test_different_models_do_not_share_a_cache_entry():
    """End to end through the real cache: extracting the same message id with
    two different models must recompute for the second model, not silently
    serve the first model's cached answer under its name."""
    handler_7b = _fixed_handler(
        {"level": "L1", "amount_inr": 40000, "date": "2026-08-28", "condition": None, "confidence": 0.9}
    )
    handler_3b = _fixed_handler(
        {"level": "L2", "amount_inr": None, "date": "2026-08-28", "condition": None, "confidence": 0.7}
    )
    msg = _msg("will clear 40000 by Friday", mid="M-CACHE-MODEL")

    r7 = _provider(handler_7b, model="qwen2.5:7b").extract(msg, [msg])
    r3 = _provider(handler_3b, model="qwen2.5:3b").extract(msg, [msg])

    assert r7.level == "L1"
    assert r3.level == "L2"  # not a stale/shared cache hit from the 7b run


# ---------------------------------------------------------------------------
# Live smoke — skipped unless a real Ollama is reachable at OLLAMA_BASE_URL
# ---------------------------------------------------------------------------


def _ollama_reachable() -> bool:
    base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
    try:
        resp = httpx.get(f"{base}/api/version", timeout=0.5)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="no reachable Ollama at OLLAMA_BASE_URL")
def test_live_ollama_extract_smoke(monkeypatch):
    """Not mocked — a real call to a real local Ollama, kept to exactly one
    cheap message so the rest of the suite stays fast and this is the only
    test in the file that can be slow. Skips cleanly (never fails) on a
    machine with no Ollama running, per the hard rule that `pytest tests/`
    must not require one."""
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "0")
    provider = OllamaProvider()
    msg = _msg("will pay the full 5000 by tomorrow")
    extraction = provider.extract(msg, [msg])
    assert extraction.level in {"L1", "L2", "L3", "L4", "L5"}
    assert 0.0 <= extraction.confidence <= 1.0
