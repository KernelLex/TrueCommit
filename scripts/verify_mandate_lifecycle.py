"""The human-in-the-loop half of the mandate lifecycle verification.

Step 2 of the flow the production code can't automate (and honestly says so —
see razorpay_client.execute_mandate's docstring): a HUMAN opens a mandate
registration short_url in a real browser and authorizes it in Razorpay's
TEST-mode hosted flow. This script then:

  1. polls the customer's tokens until the authorization shows up,
  2. performs ONE REAL recurring charge against that token
     (POST /payments/create/recurring — the actual execute rail), and
  3. REALLY revokes the token (DELETE /customers/{id}/tokens/{token}),

turning "execute" and "revoke" from simulated-with-a-label into
verified-once-for-real. Results land in tracking/mandate_lifecycle_report.json
(redacted — no keys), and the outcome belongs in TRACK_BAR §0.

This is a VERIFICATION HARNESS, deliberately in scripts/ and not in
engine/action/razorpay_client.py: the production client's execute/revoke stay
simulated+labeled until this harness has proven the rail, and the amounts
here are fixed test constants, not ledger values — nothing in the judgment
layer calls this file.

Usage:
  ./.venv/Scripts/python.exe -m scripts.verify_mandate_lifecycle cust_XXXX
  (customer id printed when the registration links were created)
"""

import json
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KEY_ID = os.environ.get("RAZORPAY_TEST_KEY_ID", "")
KEY_SECRET = os.environ.get("RAZORPAY_TEST_KEY_SECRET", "")
BASE = "https://api.razorpay.com/v1"
CHARGE_PAISE = 100_00  # Rs.100 — a fixed harness constant, NOT a ledger amount
POLL_SECONDS = 15
POLL_MINUTES_MAX = 10

report: dict = {}


def main() -> None:
    if not KEY_ID.startswith("rzp_test_"):
        raise SystemExit("TEST keys only (rzp_test_...)")
    if len(sys.argv) < 2:
        raise SystemExit("usage: -m scripts.verify_mandate_lifecycle <customer_id>")
    customer_id = sys.argv[1]

    with httpx.Client(auth=(KEY_ID, KEY_SECRET), timeout=30) as c:
        print(f"Polling tokens for {customer_id} (every {POLL_SECONDS}s, up to {POLL_MINUTES_MAX} min).")
        print("Approve one of the registration links in your browser now if you haven't...")
        token = None
        deadline = time.time() + POLL_MINUTES_MAX * 60
        while time.time() < deadline:
            r = c.get(f"{BASE}/customers/{customer_id}/tokens")
            items = r.json().get("items", []) if r.status_code < 300 else []
            confirmed = [t for t in items if t.get("recurring_details", {}).get("status") in ("confirmed", "initiated") or t.get("recurring")]
            if confirmed:
                token = confirmed[0]
                break
            print(f"  no token yet ({len(items)} raw items) — waiting...")
            time.sleep(POLL_SECONDS)

        if token is None:
            report["outcome"] = "no_token_appeared"
            _write()
            raise SystemExit("No authorized token appeared. Approve the link and re-run.")

        token_id = token["id"]
        report["token"] = {"id": token_id, "method": token.get("method"), "recurring_status": token.get("recurring_details", {}).get("status")}
        print(f"TOKEN FOUND: {token_id} (method={token.get('method')}, status={token.get('recurring_details', {}).get('status')})")

        # REAL execute: order -> recurring payment against the token
        order = c.post(f"{BASE}/orders", json={"amount": CHARGE_PAISE, "currency": "INR", "payment_capture": True}).json()
        exec_resp = c.post(f"{BASE}/payments/create/recurring", json={
            "email": "amoghprashanth156@gmail.com", "contact": "+919123456780",
            "currency": "INR", "amount": CHARGE_PAISE,
            "order_id": order.get("id"), "customer_id": customer_id, "token": token_id,
            "recurring": "1", "description": "Promise Keeper lifecycle verification charge",
        })
        exec_body = exec_resp.json()
        report["execute"] = {"status_code": exec_resp.status_code, "response": exec_body}
        print(f"EXECUTE: HTTP {exec_resp.status_code} -> {json.dumps(exec_body)[:200]}")

        # REAL revoke: delete the token
        rev = c.delete(f"{BASE}/customers/{customer_id}/tokens/{token_id}")
        rev_body = rev.json() if rev.text else {}
        report["revoke"] = {"status_code": rev.status_code, "response": rev_body}
        print(f"REVOKE:  HTTP {rev.status_code} -> {json.dumps(rev_body)[:200]}")

        report["outcome"] = "complete"
        _write()


def _write() -> None:
    out = ROOT / "tracking" / "mandate_lifecycle_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report written: {out}")


if __name__ == "__main__":
    main()
