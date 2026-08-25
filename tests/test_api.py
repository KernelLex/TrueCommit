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
