from fastapi.testclient import TestClient

from api.main import app


def test_health_loads_dataset_at_startup():
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["invoices_loaded"] == 60
        assert body["reserves_active"] == 2


def test_event_flow_produces_action_and_audit_trail():
    with TestClient(app) as c:
        c.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})
        c.post("/events", json={"type": "extraction_received", "entity_id": "INV-001", "payload": {"amount_inr": 40000}})
        r = c.post("/events", json={"type": "mandate_offer_requested", "entity_id": "INV-001", "payload": {}})
        assert r.status_code == 200
        action = r.json()
        assert action["kind"] == "mandate_offer"
        assert action["params"]["amount_inr"] == 40000  # from the ledger, not the event payload

        entity = c.get("/entities/INV-001").json()
        assert entity["state"] == "MANDATED"

        audit = c.get("/entities/INV-001/audit").json()
        assert len(audit) >= 1

        trust = c.get("/trust/D-01").json()
        assert trust["alpha"] == 2.0 and trust["beta"] == 2.0


def test_unknown_entity_is_404():
    with TestClient(app) as c:
        assert c.get("/entities/NOPE").status_code == 404
        assert c.get("/trust/NOPE").status_code == 404


def test_list_entities_returns_every_loaded_invoice():
    with TestClient(app) as c:
        r = c.get("/entities")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 60
        ids = {row["entity_id"] for row in rows}
        assert "INV-001" in ids
        # shape matches the single-entity endpoint (EntityState.model_dump_json)
        row = next(row for row in rows if row["entity_id"] == "INV-001")
        assert "state" in row and "invoice_amount_inr" in row


def test_review_queue_routes_exist_and_report_the_gates_they_enforce():
    """Shape check for the P9 surface. The behaviour behind each route is
    covered in tests/test_review_queue.py; this is the "is it wired into THIS
    app" half, alongside the other route-listing tests in this file."""
    with TestClient(app) as c:
        body = c.get("/review-queue").json()
        assert set(body) >= {
            "held_actions", "handoffs", "disputes", "paused", "counts", "gates",
        }
        # the screen never hardcodes a threshold — it reads the ones in force
        assert body["gates"]["money_action_confidence"] == 0.90
        assert body["gates"]["clarify_confidence"] == 0.75

        paths = {r.path for r in app.routes}
        assert {
            "/review-queue",
            "/review-queue/{held_id}/approve",
            "/review-queue/{held_id}/reject",
            "/review-queue/{held_id}/mark-handled",
            "/entities/{entity_id}/resolve-handoff",
            "/entities/{entity_id}/pause",
            "/entities/{entity_id}/unpause",
        } <= paths


def test_the_manual_event_route_refuses_the_terminal_state_exception():
    """`POST /events` stays the general-purpose injection door for every event
    the pipeline produces — and is closed to the one event that can move a
    terminal state, which has its own route with its own preconditions."""
    with TestClient(app) as c:
        r = c.post("/events", json={
            "type": "human_resolution", "entity_id": "INV-001",
            "payload": {"resolution": "recovered"},
        })
        assert r.status_code == 400
        assert "resolve-handoff" in r.json()["detail"]
        # every other event type still goes through untouched
        assert c.post("/events", json={
            "type": "invoice_triaged", "entity_id": "INV-001", "payload": {},
        }).status_code == 200


def test_list_trust_matches_single_debtor_reads():
    with TestClient(app) as c:
        # INV-006 -> D-02, untouched by the earlier tests in this module (they
        # only exercise INV-001 / D-01), so this is a clean promise_kept path.
        c.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-006", "payload": {}})
        c.post("/events", json={"type": "extraction_received", "entity_id": "INV-006", "payload": {"amount_inr": 185000}})
        c.post("/events", json={"type": "promise_kept", "entity_id": "INV-006", "payload": {}})

        rows = c.get("/trust").json()
        assert isinstance(rows, list) and len(rows) >= 1
        row = next(r for r in rows if r["debtor_id"] == "D-02")
        assert row == c.get("/trust/D-02").json()
        assert row["alpha"] == 3.0  # prior 2.0 + 1 kept promise
        assert row["beta"] == 2.0
