"""Perception provider abstraction: registry resolution, the heuristic rules,
the cache, and the eval-integrity rule that keeps the oracle out of metrics.

The heuristic cases below are hand-picked from data/conversations — real
messages with real hand labels, not synthetic strings — so a regression in the
rules shows up here before it shows up in the accuracy number.
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from engine.perception import cache
from engine.perception.cart_cause import infer_cart_cause
from engine.perception.extractor import extract_promise
from engine.perception.providers import (
    PerceptionProvider,
    available_providers,
    get_provider,
    register,
    reset_instances,
    resolve_provider_name,
)
from engine.perception.providers.heuristic import (
    HeuristicParams,
    HeuristicProvider,
    parse_amounts,
    parse_date,
)
from engine.perception.providers.oracle import OracleNotLabelled, OracleProvider
from engine.perception.triage import triage_invoice
from engine.schemas import Cart, CartItem, Invoice, Message

ROOT = Path(__file__).resolve().parent.parent
CONV_DIR = ROOT / "data" / "conversations"

TS = dt.datetime(2026, 8, 27, 10, 0)  # a Thursday, matching the dataset's own clock


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never touch the developer's real .cache/perception during tests."""
    monkeypatch.setenv("PK_PERCEPTION_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("PK_PERCEPTION_PROVIDER", raising=False)
    cache.reset_stats()
    reset_instances()
    yield
    reset_instances()


def _msg(text: str, mid: str = "M-T-1", direction: str = "in", ts: dt.datetime = TS) -> Message:
    return Message(id=mid, thread_id="T-T", direction=direction, channel="wa", text=text, ts=ts)


def _thread(thread_id: str) -> list[Message]:
    raw = json.loads((CONV_DIR / f"{thread_id}.json").read_text(encoding="utf-8"))
    return [Message.model_validate(m) for m in raw["messages"]]


def _extract_from_dataset(thread_id: str, message_id: str):
    """Extract a real dataset message with its real thread history."""
    messages = _thread(thread_id)
    history: list[Message] = []
    for m in messages:
        history.append(m)
        if m.id == message_id:
            return extract_promise(m, history, provider="heuristic")
    raise AssertionError(f"{message_id} not found in {thread_id}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_builtin_providers_are_registered():
    assert {"heuristic", "anthropic", "oracle"} <= set(available_providers())


def test_default_provider_is_heuristic(monkeypatch):
    monkeypatch.delenv("PK_PERCEPTION_PROVIDER", raising=False)
    assert resolve_provider_name() == "heuristic"
    assert get_provider().name == "heuristic"


def test_env_var_selects_provider(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "oracle")
    assert resolve_provider_name() == "oracle"
    assert get_provider().name == "oracle"


def test_explicit_argument_beats_env_var(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "oracle")
    assert resolve_provider_name("heuristic") == "heuristic"
    assert get_provider("heuristic").name == "heuristic"


def test_provider_name_is_case_insensitive_and_trimmed(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "  HEURISTIC ")
    assert resolve_provider_name() == "heuristic"


def test_unknown_provider_raises_a_useful_error():
    with pytest.raises(ValueError, match="unknown perception provider"):
        get_provider("does-not-exist")


def test_third_party_provider_can_register():
    class Stub(HeuristicProvider):
        name = "stub-test-provider"

    register("stub-test-provider", Stub, replace=True)
    assert get_provider("stub-test-provider").name == "stub-test-provider"


def test_all_providers_implement_the_contract():
    for name in ("heuristic", "anthropic", "oracle"):
        provider = get_provider(name)
        assert isinstance(provider, PerceptionProvider)
        for method in ("extract", "triage", "cart_cause"):
            assert callable(getattr(provider, method))


# ---------------------------------------------------------------------------
# Heuristic: determinism
# ---------------------------------------------------------------------------


def test_heuristic_is_deterministic(monkeypatch):
    """CLAUDE.md law 6: two identical runs must produce identical output.
    Cache off, so this proves the RULES are deterministic, not the cache."""
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "0")
    msg = _msg("boss month end tight, will clear 40k by Friday pakka")
    first = extract_promise(msg, [msg], provider="heuristic")
    second = extract_promise(msg, [msg], provider="heuristic")
    assert first.model_dump_json() == second.model_dump_json()


def test_heuristic_triage_and_cart_are_deterministic(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "0")
    inv = Invoice(id="INV-001", debtor_id="D-01", amount_inr=40000, issued=dt.date(2026, 7, 1),
                  due=dt.date(2026, 8, 13), status="overdue", description="x")
    thread = _thread("T-01")
    first = triage_invoice(inv, thread, provider="heuristic")
    second = triage_invoice(inv, thread, provider="heuristic")
    assert first.model_dump_json() == second.model_dump_json()

    cart = Cart(id="C-01", customer_id="CUST-01", amount_inr=2499,
                items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)],
                drop_stage="payment", drop_signals=["otp_fail", "otp_fail"], ts=TS)
    a = infer_cart_cause(cart, provider="heuristic")
    b = infer_cart_cause(cart, provider="heuristic")
    assert a.model_dump_json() == b.model_dump_json()


# ---------------------------------------------------------------------------
# Heuristic: the L1-L5 ladder on real dataset messages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "thread_id,message_id,level,amount",
    [
        # T-01: the master doc's own worked example - Hinglish, explicit amount + date
        ("T-01", "M-01-2", "L1", 40000),
        # T-01: "haan set it up" confirms the amount+date the previous message offered
        ("T-01", "M-01-4", "L1", 40000),
        # T-03: date explicit ("month end"), amount never restated
        ("T-03", "M-03-2", "L2", None),
        # T-16: split payment - cannot be one amount+date pair
        ("T-16", "M-16-2", "L3", 45000),
        # T-15: contingent on the debtor's own client paying
        ("T-15", "M-15-2", "L3", None),
        # T-19: adversarial string-along, nothing concrete
        ("T-19", "M-19-2", "L4", None),
        # T-19: "will confirm by tomorrow" - a date, but no payment commitment
        ("T-19", "M-19-6", "L4", None),
        # T-12: dispute - nothing to extract
        ("T-12", "M-12-2", "L5", None),
        # T-05: "already paid" contradicts our records - not a promise
        ("T-05", "M-05-2", "L5", None),
        # T-10: a bare "ok" is silence-equivalent
        ("T-10", "M-10-2", "L5", None),
    ],
)
def test_heuristic_levels_on_real_messages(thread_id, message_id, level, amount):
    extraction = _extract_from_dataset(thread_id, message_id)
    assert extraction.level == level, f"{message_id}: {extraction!r}"
    assert extraction.amount_inr == amount


def test_dispute_is_sticky_within_a_thread():
    """T-12's "fine, waiting to hear back" is L5, not L4: a thread in dispute
    has no commitment to extract (mirrors the state machine's dispute stop)."""
    assert _extract_from_dataset("T-12", "M-12-4").level == "L5"


def test_hinglish_firmness_moves_confidence_not_level():
    plain = extract_promise(_msg("will clear 40000 by Friday"), [_msg("will clear 40000 by Friday")],
                            provider="heuristic")
    firm_text = "will clear 40000 by Friday pakka, pura amount"
    firm = extract_promise(_msg(firm_text), [_msg(firm_text)], provider="heuristic")
    assert plain.level == firm.level == "L1"
    assert firm.confidence > plain.confidence


def test_never_invents_an_amount_or_date():
    """Design law: if it is not explicit, output None and drop a level."""
    text = "we'll settle the invoice soon, sometime next week"
    e = extract_promise(_msg(text), [_msg(text)], provider="heuristic")
    assert e.level == "L4"
    assert e.amount_inr is None and e.date is None


# ---------------------------------------------------------------------------
# Heuristic: amount + date parsers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("will clear 40k by Friday", [40000]),
        ("can do 1.5L this month", [150000]),
        ("1.5 lakh is the balance", [150000]),
        ("paying 45000 tomorrow", [45000]),
        ("Rs.1,45,000 in full", [145000]),
        ("Rs. 4,50,000 confirmed", [450000]),
        ("₹22,000 today", [22000]),
        ("INR 8500 sent", [8500]),
        ("2 crore project", [20000000]),
        ("I can send 45000 now and the remaining 100000 by month end", [45000, 100000]),
        # things that look like numbers but are not money
        ("not 100% sure", []),
        ("rest by the 15th", []),
        ("half now and half in 2 weeks", []),
        ("arrived with 3 damaged panels", []),
        ("invoice INV-001 is overdue", []),
        ("Rs.500 short", [500]),  # currency prefix beats the bare-number floor
    ],
)
def test_amount_parser(text, expected):
    assert parse_amounts(text) == expected


def test_bare_number_floor_is_tunable():
    params = HeuristicParams(min_bare_amount=1)
    assert parse_amounts("sending 300 shortly", params) == [300]
    assert parse_amounts("sending 300 shortly") == []


@pytest.mark.parametrize(
    "text,expected",
    [
        ("will clear 40k by Friday", dt.date(2026, 8, 28)),
        ("paying 63000 this Thursday", dt.date(2026, 8, 27)),
        ("we will clear the outstanding by month end", dt.date(2026, 8, 31)),
        ("76000 by the 5th of next month", dt.date(2026, 9, 5)),
        ("let's fix it at the 10th", dt.date(2026, 9, 10)),
        ("clear this by the second week of next month", dt.date(2026, 9, 10)),
        ("will process it early next week", dt.date(2026, 8, 31)),
        # vague -> None, never a guess
        ("maybe next week", None),
        ("let's say this week", None),
        ("will pay soon", None),
        ("need a few more days", None),
        # hedged timing is not explicit timing
        ("maybe early next week", None),
    ],
)
def test_date_parser(text, expected):
    assert parse_date(text, TS.date()) == expected


def test_last_date_wins():
    """"sent on the 10th but it bounced, redoing it by Friday" promises Friday."""
    text = "we initiated a transfer on the 10th but it bounced, redoing it by this Friday"
    assert parse_date(text, TS.date()) == dt.date(2026, 8, 28)


# ---------------------------------------------------------------------------
# Heuristic: triage + cart cause
# ---------------------------------------------------------------------------


def _invoice(**kw) -> Invoice:
    base = dict(id="INV-TEST", debtor_id="D-01", amount_inr=50000, issued=dt.date(2026, 7, 1),
                due=dt.date(2026, 8, 1), status="overdue", description="x")
    return Invoice.model_validate(base | kw)


def test_triage_payment_failed_from_thread():
    thread = [_msg("we initiated a transfer but it bounced, bank flagged a mismatch")]
    assert triage_invoice(_invoice(), thread, provider="heuristic").cause == "payment_failed"


def test_triage_dispute_vs_delivery_dispute_is_decided_by_scope():
    delivery_only = [_msg("the set arrived with 3 damaged panels, raised this weeks ago")]
    assert triage_invoice(_invoice(delivery_confirmed=False), delivery_only,
                          provider="heuristic").cause == "delivery_dispute"
    broad = [_msg("we're not paying this, the set arrived with 3 damaged panels")]
    assert triage_invoice(_invoice(delivery_confirmed=False), broad,
                          provider="heuristic").cause == "dispute"


def test_triage_non_responsive_needs_outreach_with_no_substantive_reply():
    outreach_only = [_msg("Invoice is 35 days overdue.", mid="M-o", direction="out")]
    assert triage_invoice(_invoice(), outreach_only, provider="heuristic").cause == "non_responsive"
    bare_ack = outreach_only + [_msg("ok", mid="M-i")]
    assert triage_invoice(_invoice(), bare_ack, provider="heuristic").cause == "non_responsive"


def test_triage_defaults_to_cashflow_delay_when_the_debtor_is_engaging():
    thread = [_msg("boss month end tight, will clear 40k by Friday pakka")]
    assert triage_invoice(_invoice(), thread, provider="heuristic").cause == "cashflow_delay"


def _cart(signals, stage="payment") -> Cart:
    return Cart(id="C-TEST", customer_id="CUST-1", amount_inr=2499,
                items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)],
                drop_stage=stage, drop_signals=signals, ts=TS)


@pytest.mark.parametrize(
    "signals,cause",
    [
        (["otp_fail", "otp_fail"], "friction"),
        (["viewed_shipping_fee", "left_after_shipping_shown"], "price_shock"),
        (["first_time_buyer", "no_saved_card", "cod_unavailable_pincode"], "trust"),
        (["salary_mentioned_in_support_chat"], "timing"),
        (["compared_prices_other_tab", "returned_after_2h"], "comparison"),
        (["no_signal_low_activity"], "unknown"),
        ([], "unknown"),
    ],
)
def test_cart_cause_signal_mapping(signals, cause):
    assert infer_cart_cause(_cart(signals), provider="heuristic").cause == cause


def test_weak_cart_signals_stay_unknown_with_low_confidence():
    result = infer_cart_cause(_cart(["no_signal_low_activity"]), provider="heuristic")
    assert result.cause == "unknown"
    assert result.confidence <= 0.5


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def test_params_change_the_cache_fingerprint():
    """Retuning must invalidate cached answers, never serve stale ones."""
    a = HeuristicProvider()
    b = HeuristicProvider(HeuristicParams(min_bare_amount=5))
    assert a.identity() != b.identity()


def test_params_are_tunable_without_touching_logic():
    strict = HeuristicProvider(HeuristicParams(dispute_is_sticky=False))
    thread = _thread("T-12")
    target = next(m for m in thread if m.id == "M-12-4")
    history = thread[: thread.index(target) + 1]
    assert strict.extract(target, history).level == "L4"      # stickiness off
    assert get_provider("heuristic").extract(target, history).level == "L5"  # on by default


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_cache_hit_on_second_call(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "1")
    cache.reset_stats()
    msg = _msg("will clear 40000 by Friday", mid="M-CACHE-1")
    first = extract_promise(msg, [msg], provider="heuristic")
    assert cache.stats() == {"hits": 0, "misses": 1, "writes": 1}
    second = extract_promise(msg, [msg], provider="heuristic")
    assert cache.stats()["hits"] == 1
    assert first.model_dump_json() == second.model_dump_json()


def test_cache_miss_when_the_input_changes(monkeypatch):
    """Same message id, edited text -> a miss, not a stale answer."""
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "1")
    a = _msg("will clear 40000 by Friday", mid="M-CACHE-2")
    b = _msg("will clear 90000 by Friday", mid="M-CACHE-2")
    assert extract_promise(a, [a], provider="heuristic").amount_inr == 40000
    assert extract_promise(b, [b], provider="heuristic").amount_inr == 90000


def test_cache_can_be_disabled(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "0")
    cache.reset_stats()
    msg = _msg("will clear 40000 by Friday", mid="M-CACHE-3")
    extract_promise(msg, [msg], provider="heuristic")
    extract_promise(msg, [msg], provider="heuristic")
    assert cache.stats() == {"hits": 0, "misses": 0, "writes": 0}


def test_oracle_skips_the_cache(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "1")
    cache.reset_stats()
    thread = _thread("T-01")
    target = thread[1]
    get_provider("oracle").extract(target, thread[:2])
    assert cache.stats() == {"hits": 0, "misses": 0, "writes": 0}


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------


def test_oracle_replays_ground_truth_at_confidence_one():
    thread = _thread("T-01")
    target = thread[1]  # M-01-2, the Hinglish L1
    extraction = get_provider("oracle").extract(target, thread[:2])
    truth = json.loads((ROOT / "data" / "ground_truth.json").read_text(encoding="utf-8"))["messages"]
    assert extraction.level == truth[target.id]["level"]
    assert extraction.amount_inr == truth[target.id]["amount_inr"]
    assert extraction.confidence == 1.0


def test_oracle_raises_clearly_for_an_unlabelled_entity():
    unknown = _msg("will pay 5000 tomorrow", mid="M-NOT-IN-GROUND-TRUTH")
    with pytest.raises(OracleNotLabelled, match="no extraction ground-truth label"):
        OracleProvider().extract(unknown, [unknown])
    with pytest.raises(OracleNotLabelled, match="no triage ground-truth label"):
        OracleProvider().triage(_invoice(id="INV-NOPE"), [])


# ---------------------------------------------------------------------------
# Eval integrity
# ---------------------------------------------------------------------------


def test_evals_refuse_the_oracle_provider():
    """Scoring a ground-truth replay against ground truth is circular. Both
    evals must refuse it by name, not merely discourage it."""
    from eval import extraction_eval, triage_eval
    from eval.provider_cli import CircularEvalRefused

    for module in (extraction_eval, triage_eval):
        with pytest.raises(CircularEvalRefused, match="circular"):
            module.run("oracle")
        assert module.main(["--provider", "oracle"]) == 2


def test_evals_refuse_the_oracle_via_env_var_too(monkeypatch):
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "oracle")
    from eval import extraction_eval
    from eval.provider_cli import CircularEvalRefused

    with pytest.raises(CircularEvalRefused):
        extraction_eval.run(None)


def test_eval_metrics_paths_are_per_provider():
    from eval import extraction_eval, triage_eval

    assert extraction_eval.metrics_path("heuristic").name == "extraction_accuracy_heuristic.json"
    assert triage_eval.metrics_path("anthropic").name == "triage_accuracy_anthropic.json"


def test_extraction_eval_runs_offline_against_the_heuristic():
    from eval import extraction_eval

    result = extraction_eval.run("heuristic")
    assert result["provider"] == "heuristic"
    assert result["n"] == len(result["truths"]) > 0
    assert 0.0 <= result["overall_accuracy"] <= 1.0
    assert result["in_sample"] is True  # the caveat must travel with the number


def test_triage_eval_runs_offline_against_the_heuristic():
    from eval import triage_eval

    result = triage_eval.run("heuristic")
    assert result["provider"] == "heuristic"
    assert result["with_thread"]["n"] + result["no_thread"]["n"] == result["n"]
    assert 0.0 <= result["accuracy"] <= 1.0
