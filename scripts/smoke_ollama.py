"""Manual live smoke test for engine/perception/providers/ollama.py.

Runs the three perception tasks (extract / triage / cart_cause) against a
REAL local Ollama server, prints the results and timings, then demonstrates
the fallback beat: pointing the provider at an unreachable host and showing
the same call still succeeds by degrading to the heuristic provider, with the
degradation recorded on `provider.fallback_events`.

This makes real network calls to Ollama (no external network, no cost, no
secrets) — the pytest suite never does this (tests/test_ollama_provider.py
uses httpx.MockTransport for everything except one skip-unless-reachable
live test). Run this script directly when you want to eyeball real model
output on a running Ollama instance.

Run:
  ./.venv/Scripts/python.exe -m scripts.smoke_ollama
  PK_OLLAMA_MODEL=qwen2.5:3b ./.venv/Scripts/python.exe -m scripts.smoke_ollama
"""

import datetime as dt
import os
import time

from engine.perception.providers.ollama import DEFAULT_BASE_URL, OllamaProvider
from engine.schemas import Cart, CartItem, Invoice, Message

TS = dt.datetime(2026, 8, 27, 10, 0)


def _msg(text: str, mid: str = "M-SMOKE-1") -> Message:
    return Message(id=mid, thread_id="T-SMOKE", direction="in", channel="wa", text=text, ts=TS)


def _timed(label: str, fn) -> None:
    start = time.monotonic()
    result = fn()
    elapsed = time.monotonic() - start
    print(f"  [{elapsed:5.2f}s] {label}: {result!r}")


def main() -> int:
    base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("PK_OLLAMA_MODEL", "qwen2.5:7b")
    print(f"Ollama smoke test — base_url={base_url} model={model}\n")

    provider = OllamaProvider(base_url=base_url, model=model)

    print("1) extract:")
    _timed(
        '"boss month end tight, will clear 40k by Friday pakka"',
        lambda: provider.extract(
            _msg("boss month end tight, will clear 40k by Friday pakka"),
            [_msg("boss month end tight, will clear 40k by Friday pakka")],
        ),
    )

    print("\n2) triage:")
    invoice = Invoice(
        id="INV-SMOKE", debtor_id="D-SMOKE", amount_inr=40000, issued=dt.date(2026, 7, 1),
        due=dt.date(2026, 8, 13), status="overdue", description="smoke test invoice",
    )
    thread = [_msg("we initiated a transfer but it bounced, bank flagged a mismatch", mid="M-SMOKE-2")]
    _timed("payment-failure thread", lambda: provider.triage(invoice, thread))

    print("\n3) cart_cause:")
    cart = Cart(
        id="C-SMOKE", customer_id="CUST-SMOKE", amount_inr=2499,
        items=[CartItem(sku="S", name="n", qty=1, price_inr=2499)],
        drop_stage="payment", drop_signals=["otp_fail", "otp_fail"], ts=TS,
    )
    _timed("two OTP failures", lambda: provider.cart_cause(cart))

    provider.close()

    # -- fallback demo: point at an unreachable host, prove the call still succeeds --
    print("\n4) fallback demo (unreachable Ollama -> heuristic, call still succeeds):")
    broken = OllamaProvider(base_url="http://127.0.0.1:1", model=model)
    result = broken.extract(_msg("ok", mid="M-SMOKE-FALLBACK"), [_msg("ok", mid="M-SMOKE-FALLBACK")])
    print(f"  call succeeded via fallback: {result!r}")
    print(f"  provider.fallback_events: {broken.fallback_events}")
    broken.close()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
