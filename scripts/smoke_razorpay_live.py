"""OPTIONAL manual live smoke test for engine/action/razorpay_client.py.

Creates exactly two REAL objects against the Razorpay TEST-mode API — one
Payment Link and one UPI mandate registration link — and prints their
short_urls. Never prints or writes RAZORPAY_TEST_KEY_ID / _SECRET.

Guarded: does nothing unless PK_LIVE_SMOKE=1 is set, so it can never run by
accident inside `pytest tests/` or a normal script invocation. This is the
one place in the repo that is *allowed* to touch the live sandbox — the rest
of the test suite (tests/test_razorpay_client.py) uses httpx.MockTransport
exclusively.

Run:
  PK_LIVE_SMOKE=1 ./.venv/Scripts/python.exe -m scripts.smoke_razorpay_live
"""

import os
import sys

from engine.action.razorpay_client import RazorpayClient, RazorpayError


def main() -> int:
    if os.environ.get("PK_LIVE_SMOKE") != "1":
        print("Skipped: set PK_LIVE_SMOKE=1 to run this live sandbox smoke test.")
        return 0

    try:
        client = RazorpayClient()
    except RazorpayError as e:
        print(f"Refusing to run: {e}")
        return 1

    customer = {"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"}

    print("Creating one real TEST-mode payment link...")
    link = client.create_payment_link(
        amount_inr=40000,
        description="Promise Keeper live smoke - payment link",
        customer=customer,
    )
    print(f"  payment_link short_url: {link['short_url']}")

    print("Creating one real TEST-mode UPI mandate registration link...")
    mandate = client.create_mandate_registration_link(
        max_amount_inr=100000,
        description="Promise Keeper live smoke - UPI mandate registration",
        customer=customer,
        method="upi",
    )
    print(f"  mandate_registration_link short_url: {mandate['short_url']}")

    client.close()
    print("\nDone. Both short_urls above are real, live TEST-mode Razorpay objects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
