"""Day-1 priority #1 (CLAUDE.md §5): verify what Razorpay TEST mode actually
supports for the objects Promise Keeper needs — payment links, invoices,
customers, and above all the One-Time Mandate / UPI Autopay lifecycle
(create, register, execute, revoke). The findings drive the honest
real-vs-simulated table (master doc §4.5); nothing gets claimed as "real
rail" in the demo unless this probe shows the sandbox actually does it.

Reads keys from .env (gitignored). Never prints or writes the secret.
Writes a redacted JSON report to tracking/razorpay_sandbox_report.json.

Run: .venv/Scripts/python.exe -m scripts.verify_razorpay_sandbox
"""

import json
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KEY_ID = os.environ.get("RAZORPAY_TEST_KEY_ID", "")
KEY_SECRET = os.environ.get("RAZORPAY_TEST_KEY_SECRET", "")
BASE = "https://api.razorpay.com/v1"

report: dict[str, dict] = {}


def probe(name: str, method: str, path: str, payload: dict | None = None) -> dict:
    """One API probe. Records status + response body (or error) in the report."""
    try:
        with httpx.Client(auth=(KEY_ID, KEY_SECRET), timeout=30) as client:
            if method == "GET":
                resp = client.get(f"{BASE}{path}")
            else:
                resp = client.post(f"{BASE}{path}", json=payload)
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        entry = {"status_code": resp.status_code, "ok": resp.status_code < 300, "response": body}
    except Exception as e:
        entry = {"status_code": None, "ok": False, "response": {"transport_error": str(e)[:300]}}
    report[name] = entry
    flag = "OK " if entry["ok"] else "FAIL"
    print(f"[{flag}] {name}: HTTP {entry['status_code']}")
    return entry


def main() -> None:
    if not KEY_ID or not KEY_SECRET:
        raise SystemExit("RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET not set in .env")
    if not KEY_ID.startswith("rzp_test_"):
        raise SystemExit("Refusing to run: key is not a TEST-mode key (must start with rzp_test_)")

    ts = int(time.time())

    # 1. Auth sanity check — cheapest read-only call
    probe("auth_check", "GET", "/payments?count=1")

    # 2. Payment Link (BUILD.md Day 0: "one curl to create a test Payment Link
    #    — if it works, wiring day is de-risked")
    probe("payment_link_create", "POST", "/payment_links", {
        "amount": 4000000,  # Rs.40,000 in paise — mirrors the INV-001 demo beat
        "currency": "INR",
        "description": "Promise Keeper sandbox probe - INV-001 demo invoice",
        "customer": {"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        "notify": {"sms": False, "email": False},
    })

    # 3. Invoice object
    probe("invoice_create", "POST", "/invoices", {
        "type": "invoice",
        "description": "Promise Keeper sandbox probe invoice",
        "customer": {"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        "line_items": [{"name": "Aug supplies", "amount": 4000000, "currency": "INR", "quantity": 1}],
    })

    # 4. Customer object (needed for any mandate/token flow)
    customer = probe("customer_create", "POST", "/customers", {
        "name": "Acme Traders", "contact": "+919000000001",
        "email": "acme@example.com", "fail_existing": "0",
    })
    customer_id = customer["response"].get("id") if customer["ok"] else None

    # 5. THE CROWN JEWEL PROBE — UPI Autopay / eMandate registration link.
    #    This is the rail the scheduled + delivery-secured mandates ride on.
    probe("mandate_registration_link_upi", "POST", "/subscription_registration/auth_links", {
        "customer": {"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        "type": "link",
        "amount": 100,  # Rs.1 — UPI Autopay registration requires a minimum charge; 0 is rejected (found the hard way, see BUILD_LOG)
        "currency": "INR",
        "description": "Promise Keeper mandate registration probe (UPI Autopay)",
        "subscription_registration": {
            "method": "upi",
            "max_amount": 10000000,  # Rs.1,00,000 — the demo mandate cap
            "expire_at": ts + 60 * 60 * 24 * 30,
            "frequency": "as_presented",
        },
    })

    # 5b. Same probe on the eNACH/emandate rail (bank-account variant)
    probe("mandate_registration_link_emandate", "POST", "/subscription_registration/auth_links", {
        "customer": {"name": "Acme Traders", "contact": "+919000000001", "email": "acme@example.com"},
        "type": "link",
        "amount": 100,
        "currency": "INR",
        "description": "Promise Keeper mandate registration probe (eMandate)",
        "subscription_registration": {
            "method": "emandate",
            "auth_type": "netbanking",
            "max_amount": 10000000,
            "expire_at": ts + 60 * 60 * 24 * 30,
        },
    })

    # 6. Recurring order with a token block (the order+mandate object pairing
    #    the master doc's §3.2 flow creates before sending a registration link)
    order_payload = {
        "amount": 4000000, "currency": "INR", "method": "upi",
        "payment_capture": True,
        "token": {
            "max_amount": 10000000,
            "expire_at": ts + 60 * 60 * 24 * 30,
            "frequency": "monthly",
        },
    }
    if customer_id:
        order_payload["customer_id"] = customer_id
    probe("recurring_order_with_token", "POST", "/orders", order_payload)

    # 7. Plain order (baseline — should always work in test mode)
    probe("plain_order_create", "POST", "/orders", {
        "amount": 4000000, "currency": "INR", "receipt": "pk-probe-001",
    })

    out_path = ROOT / "tracking" / "razorpay_sandbox_report.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport written: {out_path}")

    ok = sum(1 for r in report.values() if r["ok"])
    print(f"{ok}/{len(report)} probes succeeded")


if __name__ == "__main__":
    main()
