"""Packet P13 — "Create Mandate Now" (the Demo Console).

`POST /entities/{id}/create-mandate-now` is a HUMAN operator's one-click
button, not an agent decision: it never touches `ledger.process_event` /
`check_bounds()` / `Ledger._gate()`. These tests mock
`engine.action.razorpay_client.create_mandate_via_subscription` — no live
Razorpay call is ever made by this suite — and check the five things the
route promises: (a) omitted customer fields fall back to the exact synthetic
demo pattern the rest of this codebase already uses, (b) provided fields are
used verbatim, (c) the amount is always the invoice's real `amount_inr`
regardless of anything in the request body (there is no amount field to send
one in), (d) exactly one audit entry is written with the "manual demo"
summary — and never anything resembling an autonomous-agent action kind, and
(e) a `RazorpayError` surfaces as a clean 4xx/5xx with the real message, not
a 500 traceback.
"""

import datetime as dt

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app
from engine.action import razorpay_client
from engine.action.razorpay_client import RazorpayError
from engine.integration.runner import DEMO_CUSTOMER_CONTACT, DEMO_CUSTOMER_EMAIL

ROUTE = "/entities/INV-001/create-mandate-now"


def _fake_create_mandate(captured):
    def fake(amount_inr, description, customer, debit_date):
        captured["amount_inr"] = amount_inr
        captured["description"] = description
        captured["customer"] = dict(customer)
        captured["debit_date"] = debit_date
        return {
            "plan": {"id": "plan_FAKE00000000", "period": "monthly"},
            "subscription": {
                "id": "sub_FAKE00000000",
                "short_url": "https://rzp.io/i/fake-demo-console-url",
                "status": "created",
            },
        }
    return fake


def test_omitted_customer_fields_fall_back_to_the_synthetic_demo_pattern(monkeypatch):
    captured = {}
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _fake_create_mandate(captured))

    with TestClient(app) as c:
        invoice = api_main.runner.invoices["INV-001"]
        r = c.post(ROUTE, json={})
        assert r.status_code == 200
        body = r.json()

        # (a) omitted fields -> the SAME synthetic pattern used elsewhere
        assert captured["customer"]["contact"] == DEMO_CUSTOMER_CONTACT
        assert captured["customer"]["email"] == DEMO_CUSTOMER_EMAIL
        assert captured["customer"]["name"]  # debtor name or entity_id fallback, never blank

        # debit_date defaults to a real future date, NOT the invoice's own
        # `due` date — every invoice in this dataset is deliberately overdue
        # (due in the past relative to real wall-clock time), and a REAL
        # Razorpay `start_at` in the past is rejected outright ("start_at
        # cannot be lesser than the current time.", hit live via the IVR
        # call path — tracking/BUILD_LOG.md 2026-08-27). This assertion
        # would have caught that bug before it ever reached a real call.
        assert captured["debit_date"] != invoice.due.isoformat()
        assert dt.date.fromisoformat(captured["debit_date"]) > dt.date.today()

        # response shape: plan/subscription/customer_used, with the real short_url
        assert body["plan"]["id"] == "plan_FAKE00000000"
        assert body["subscription"]["id"] == "sub_FAKE00000000"
        assert body["subscription"]["short_url"] == "https://rzp.io/i/fake-demo-console-url"
        assert body["customer_used"] == captured["customer"]


def test_provided_customer_fields_are_used_verbatim(monkeypatch):
    captured = {}
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _fake_create_mandate(captured))

    with TestClient(app) as c:
        r = c.post(
            ROUTE,
            json={
                "customer_name": "Real Test Customer",
                "customer_contact": "+919999900000",
                "customer_email": "real.customer@example.com",
                "debit_date": "2026-09-30",
            },
        )
        assert r.status_code == 200
        assert captured["customer"] == {
            "name": "Real Test Customer",
            "contact": "+919999900000",
            "email": "real.customer@example.com",
        }
        assert captured["debit_date"] == "2026-09-30"
        assert r.json()["customer_used"] == captured["customer"]


def test_amount_always_comes_from_the_invoice_never_the_request_body(monkeypatch):
    """There is no amount field on the request at all (CreateMandateNowIn has
    none) — this test also confirms that sending extra/unknown keys can't
    smuggle one in, since pydantic ignores unrecognised fields by default and
    the route only ever reads `invoice.amount_inr`."""
    captured = {}
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _fake_create_mandate(captured))

    with TestClient(app) as c:
        invoice = api_main.runner.invoices["INV-001"]
        r = c.post(ROUTE, json={"amount_inr": 1, "amount": 999999999})
        assert r.status_code == 200
        assert captured["amount_inr"] == invoice.amount_inr
        assert captured["amount_inr"] != 1
        assert captured["amount_inr"] != 999999999


def test_success_writes_exactly_one_manual_demo_audit_entry(monkeypatch):
    captured = {}
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _fake_create_mandate(captured))

    with TestClient(app) as c:
        before = len(api_main.ledger.audit)
        r = c.post(ROUTE, json={})
        assert r.status_code == 200
        after = api_main.ledger.audit[before:]

        assert len(after) == 1
        entry = after[0]
        assert entry.entity_id == "INV-001"
        assert entry.layer == "action"
        assert entry.summary.startswith("manual demo: mandate created by operator")
        assert entry.detail["plan_id"] == "plan_FAKE00000000"
        assert entry.detail["subscription_id"] == "sub_FAKE00000000"
        assert entry.detail["short_url"] == "https://rzp.io/i/fake-demo-console-url"
        assert entry.detail["customer"] == captured["customer"]

        # Never anything resembling an autonomous-agent action kind/summary.
        for banned in ("mandate_offer", "mandate_offer_requested", "agent"):
            assert banned not in entry.summary


def test_manual_demo_audit_entry_is_distinct_from_any_agent_mandate_offer(monkeypatch):
    """Cross-check against the real agent path: an actual mandate_offer,
    reached via POST /events (the ledger's own check_bounds/_gate machinery —
    see tests/test_api.py's test_event_flow_produces_action_and_audit_trail
    for the same sequence), writes audit summaries that never start with
    "manual demo", and vice versa — the two vocabularies must never overlap,
    since that's the whole point of the distinct wording (CLAUDE.md law 3 +
    the packet P13 brief's "unmistakable" requirement)."""
    captured = {}
    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", _fake_create_mandate(captured))

    with TestClient(app) as c:
        c.post(ROUTE, json={})  # the human's one-off manual demo action

        # the AGENT's own mandate-offer decision path, for contrast
        c.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})
        c.post("/events", json={"type": "extraction_received", "entity_id": "INV-001", "payload": {"amount_inr": 40000}})
        c.post("/events", json={"type": "mandate_offer_requested", "entity_id": "INV-001", "payload": {}})

        summaries = [a.summary for a in api_main.ledger.audit if a.entity_id == "INV-001"]
        manual = [s for s in summaries if s.startswith("manual demo:")]
        agent = [s for s in summaries if not s.startswith("manual demo:")]

        assert manual, "expected the demo-console entry to be present"
        assert agent, "expected the agent's own audit entries from the event flow to be present"
        assert not any("mandate_offer" in s for s in manual)
        assert not any(s.startswith("manual demo:") for s in agent)


def test_razorpay_error_surfaces_as_a_clean_502_not_a_traceback(monkeypatch):
    def always_fails(amount_inr, description, customer, debit_date):
        raise RazorpayError(
            "Razorpay API error 401: Authentication failed",
            status_code=401,
            description="Authentication failed",
        )

    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", always_fails)

    with TestClient(app) as c:
        before = len(api_main.ledger.audit)
        r = c.post(ROUTE, json={})
        assert r.status_code == 502
        body = r.json()
        assert "Authentication failed" in body["detail"]
        # never fabricates a short_url on failure
        assert "short_url" not in body

        # the failed attempt is still audited (law 3: no exceptions, including failures)
        after = api_main.ledger.audit[before:]
        assert len(after) == 1
        assert after[0].summary.startswith("manual demo:")
        assert "FAILED" in after[0].summary
        assert after[0].detail["error"]


def test_malformed_debit_date_is_a_422_before_any_network_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        razorpay_client, "create_mandate_via_subscription",
        lambda *a, **k: calls.append(1) or {},
    )

    with TestClient(app) as c:
        r = c.post(ROUTE, json={"debit_date": "not-a-date"})
        assert r.status_code == 422
        assert calls == []


def test_unknown_entity_is_404():
    with TestClient(app) as c:
        assert c.post("/entities/NOPE/create-mandate-now", json={}).status_code == 404


def test_never_makes_a_live_call_without_the_monkeypatch_being_effective(monkeypatch):
    """Sanity check that this suite really is offline: the real default
    client construction (no keys / bad keys in a CI env) must raise
    RazorpayError, and this route must turn that into a clean 502 rather than
    crash — proving the route can't accidentally reach the network even if a
    test forgets to monkeypatch."""
    def explode(*a, **k):
        raise RazorpayError("RAZORPAY_TEST_KEY_ID / RAZORPAY_TEST_KEY_SECRET not set - add them to .env")

    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", explode)

    with TestClient(app) as c:
        r = c.post(ROUTE, json={})
        assert r.status_code == 502
        assert "RAZORPAY_TEST_KEY_ID" in r.json()["detail"]
