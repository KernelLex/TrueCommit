"""Packet P15 — real contact identity (name + phone) attached to a debtor.

WHAT THESE TESTS ACTUALLY DEFEND
---------------------------------
No real call/SMS/WhatsApp message is ever placed by this project — there is
no telephony/SMS/WhatsApp-Business credential of any kind, unchanged by this
packet. A submitted contact only ever affects (a) what the audit trail /
dashboard displays, and (b) the `customer.contact` field sent to the REAL
Razorpay TEST API. So the load-bearing things to prove here are:

  * validation actually rejects a malformed phone / empty name, with a typed
    error the API turns into a 422 — nothing bad gets stored;
  * a contact submitted for one invoice resolves for every sibling invoice of
    the SAME debtor (the touch-cap's own per-debtor scoping, reused);
  * `resolve_contact()` is BYTE-IDENTICAL to the old hard-coded fallback when
    nothing has ever been submitted — this is what keeps the seeded 45-day
    run's numbers unchanged;
  * every real dispatch point (voice, sms, message, the real Razorpay call)
    actually reads through `resolve_contact()` rather than re-deriving its own
    answer, and labels which one it used (`operator_submitted` /
    `demo_fallback`) — never silently.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.action import razorpay_client
from engine.action.contacts import Contact, ContactBook, ContactError
from engine.integration.runner import (
    DEMO_CUSTOMER_CONTACT,
    DEMO_CUSTOMER_EMAIL,
    WorldRunner,
)

NOW = dt.datetime(2026, 8, 26, 9, 0)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. engine/action/contacts.py — validation and storage, in isolation
# ---------------------------------------------------------------------------


def test_submit_stores_a_normalized_contact_and_get_returns_it():
    book = ContactBook()
    contact = book.submit("D-01", "  Ramesh Traders  ", "+91 98123-45678", NOW)
    assert isinstance(contact, Contact)
    assert contact.name == "Ramesh Traders"  # stripped
    assert contact.phone == "+919812345678"  # spaces/hyphens stripped
    assert contact.submitted_at == NOW
    assert contact.source == "operator_submitted"
    assert book.get("D-01") == contact
    assert book.get("D-NOPE") is None


def test_all_returns_every_submitted_contact_keyed_correctly():
    book = ContactBook()
    book.submit("D-01", "Ramesh Traders", "9812345678", NOW)
    book.submit("CART-042", "Priya Rao", "8123456789", NOW)
    everyone = book.all()
    assert set(everyone) == {"D-01", "CART-042"}
    assert everyone["D-01"].name == "Ramesh Traders"


@pytest.mark.parametrize("bad_name", ["", "   ", "\t\n"])
def test_an_empty_or_whitespace_name_is_refused(bad_name):
    book = ContactBook()
    with pytest.raises(ContactError):
        book.submit("D-01", bad_name, "9812345678", NOW)


@pytest.mark.parametrize(
    "bad_phone",
    [
        "",
        "12345",              # too short
        "0812345678",         # leading zero
        "not-a-phone",        # letters
        "+91",                # nothing after the country code
        "98123456789012345",  # too long
    ],
)
def test_a_malformed_phone_is_refused(bad_phone):
    book = ContactBook()
    with pytest.raises(ContactError):
        book.submit("D-01", "Ramesh Traders", bad_phone, NOW)


@pytest.mark.parametrize(
    "good_phone,expected",
    [
        ("9812345678", "9812345678"),
        ("+919812345678", "+919812345678"),
        ("+91 98123 45678", "+919812345678"),
        ("98123-45678", "9812345678"),
    ],
)
def test_valid_phones_are_accepted_and_normalized(good_phone, expected):
    book = ContactBook()
    contact = book.submit("D-01", "Ramesh Traders", good_phone, NOW)
    assert contact.phone == expected


# ---------------------------------------------------------------------------
# 2. WorldRunner wiring: contact_key scoping + resolve_contact's fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def world():
    return WorldRunner(real_razorpay=False, real_tts=False)


def test_contact_key_is_the_debtor_id_for_a_known_invoice(world):
    debtor_id = world.invoices["INV-001"].debtor_id
    assert world._contact_key("INV-001") == debtor_id
    # siblings of the same debtor share the same key
    siblings = [eid for eid, inv in world.invoices.items() if inv.debtor_id == debtor_id]
    assert len(siblings) > 1
    assert len({world._contact_key(eid) for eid in siblings}) == 1


def test_contact_key_is_the_entity_id_itself_for_an_unregistered_entity(world):
    assert world._contact_key("CART-DOES-NOT-EXIST") == "CART-DOES-NOT-EXIST"


def test_resolve_contact_is_byte_identical_to_the_old_hardcoded_fallback_when_nothing_submitted(world):
    """The correctness requirement stated explicitly in the packet: the
    seeded 45-day run must not move when nobody has submitted a contact."""
    from data.generate import DEBTOR_BY_ID

    invoice = world.invoices["INV-001"]
    expected_name = DEBTOR_BY_ID.get(invoice.debtor_id, {}).get("name", "INV-001")

    resolved = world.resolve_contact("INV-001")
    assert resolved == {
        "name": expected_name,
        "contact": DEMO_CUSTOMER_CONTACT,
        "email": DEMO_CUSTOMER_EMAIL,
        "source": "demo_fallback",
    }


def test_resolve_contact_falls_back_to_entity_id_for_an_unknown_entity(world):
    resolved = world.resolve_contact("CART-999")
    assert resolved["name"] == "CART-999"
    assert resolved["contact"] == DEMO_CUSTOMER_CONTACT
    assert resolved["source"] == "demo_fallback"


def test_a_submitted_contact_resolves_for_the_entity_and_every_sibling_invoice(world):
    debtor_id = world.invoices["INV-001"].debtor_id
    siblings = sorted(eid for eid, inv in world.invoices.items() if inv.debtor_id == debtor_id)
    assert "INV-001" in siblings and len(siblings) > 1

    key = world._contact_key("INV-001")
    world.contacts.submit(key, "Ramesh Traders", "9812345678", NOW)

    for eid in siblings:
        resolved = world.resolve_contact(eid)
        assert resolved == {
            "name": "Ramesh Traders",
            "contact": "9812345678",
            "email": DEMO_CUSTOMER_EMAIL,  # email is never collected — stays synthetic
            "source": "operator_submitted",
        }


def test_a_submitted_contact_does_not_leak_to_an_unrelated_debtor(world):
    debtor_id = world.invoices["INV-001"].debtor_id
    other_entity = next(
        eid for eid, inv in world.invoices.items() if inv.debtor_id != debtor_id
    )
    world.contacts.submit(world._contact_key("INV-001"), "Ramesh Traders", "9812345678", NOW)
    assert world.resolve_contact(other_entity)["source"] == "demo_fallback"


# ---------------------------------------------------------------------------
# 3. Dispatch points actually read through resolve_contact()
# ---------------------------------------------------------------------------


def _drive_to_escalate_2(world: WorldRunner, entity_id: str) -> None:
    world._emit("invoice_triaged", entity_id, {}, 0)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_1 (message, touch 1)
    world._emit("promise_broken", entity_id, {}, 0)   # -> ESCALATE_2 (voice,   touch 2)


def test_manual_message_channel_needed_zero_new_dispatch_code(world):
    """THE specific claim the packet asked to be verified with a test, not
    just by reading the code: `manual_reminder(entity_id, "message", now)`'s
    Action, once passed through `dispatch_action()`, reaches the EXISTING,
    UNCHANGED `elif kind == "message":` branch of `WorldRunner._dispatch` and
    produces a real queued Messenger entry on the entity's actual thread
    channel (wa/email) carrying the standard `ESCALATION_TEXT` content —
    exactly what an autonomous `message` action at the same stage would
    produce, because it IS that same branch, not a new one."""
    from engine.integration.runner import ESCALATION_TEXT

    entity_id = "INV-001"
    thread_channel = world.channel_of.get(entity_id, "wa")
    amount = world._amount(entity_id)
    expected_text = ESCALATION_TEXT["firm"].format(amount=amount, entity_id=entity_id)

    out = world.ledger.manual_reminder(entity_id, "message", world.now())
    action = out["action"]
    assert action.kind == "message"
    assert action.params["manual"] is True
    assert action.params["stage"] == "firm"

    before = len(world.messenger.queue)
    world.dispatch_action(action)
    after = world.messenger.queue

    assert len(after) == before + 1, "the message must reach the Messenger queue, not just be constructed"
    queued = after[-1]
    assert queued.action_id == action.id
    assert queued.channel == thread_channel, "a manual message rides the entity's OWN thread channel (wa/email)"
    assert queued.text == expected_text, "standard ESCALATION_TEXT content — custom_text is not read for this kind"
    assert queued.status in ("sent", "delivered")

    # ...and it is genuinely on the entity's real conversation thread too
    thread_texts = [m.text for m in world.threads.get(entity_id, [])]
    assert expected_text in thread_texts


def test_a_manual_sms_reminder_carries_the_resolved_contact(world, monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    world.contacts.submit(world._contact_key("INV-001"), "Ramesh Traders", "9812345678", NOW)

    out = world.ledger.manual_reminder("INV-001", "sms", world.now())
    world.dispatch_action(out["action"])

    record = world.reminders[-1]
    assert record["contact_name"] == "Ramesh Traders"
    assert record["contact_phone"] == "9812345678"
    assert record["contact_source"] == "operator_submitted"
    # the ledger's own amount/template still decide the words, never the contact
    assert "9812345678" not in record["text"]
    assert "Ramesh Traders" not in record["text"]


def test_a_manual_message_reminder_carries_the_resolved_contact_in_the_audit_trail(world):
    world.contacts.submit(world._contact_key("INV-001"), "Ramesh Traders", "9812345678", NOW)
    out = world.ledger.manual_reminder("INV-001", "message", world.now())
    world.dispatch_action(out["action"])

    entries = [
        a for a in world.ledger.audit
        if a.entity_id == "INV-001" and a.detail.get("action_id") == out["action"].id
    ]
    assert entries, "the message dispatch must have audited a send"
    detail = entries[-1].detail
    assert detail["contact_name"] == "Ramesh Traders"
    assert detail["contact_phone"] == "9812345678"
    assert detail["contact_source"] == "operator_submitted"


def test_the_autonomous_voice_escalation_also_carries_the_resolved_contact(world, monkeypatch):
    """Autonomous dispatch points get the same treatment as manual ones — there
    is exactly one function (`resolve_contact`) every dispatch path reads."""
    class _FakeTTS:
        def __init__(self, text, lang):
            pass

        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"\xff\xf3\x84\xc4" + b"\x00" * 64)

    monkeypatch.setattr("gtts.gTTS", _FakeTTS)
    world.contacts.submit(world._contact_key("INV-001"), "Ramesh Traders", "9812345678", NOW)

    _drive_to_escalate_2(world, "INV-001")

    record = world.reminders[-1]
    assert record["channel"] == "voice"
    assert record["contact_name"] == "Ramesh Traders"
    assert record["contact_phone"] == "9812345678"
    assert record["contact_source"] == "operator_submitted"


def test_no_contact_submitted_means_every_dispatch_says_demo_fallback(world, monkeypatch):
    monkeypatch.setattr("gtts.gTTS", lambda *a, **k: None)
    out = world.ledger.manual_reminder("INV-001", "sms", world.now())
    world.dispatch_action(out["action"])
    record = world.reminders[-1]
    assert record["contact_source"] == "demo_fallback"
    assert record["contact_phone"] == DEMO_CUSTOMER_CONTACT


def test_real_razorpay_call_uses_the_resolved_contact_not_the_demo_default(monkeypatch):
    """`_real_razorpay_call`'s customer block must come from `resolve_contact()`
    — this is the mechanism that makes a submitted phone number actually reach
    Razorpay's `customer.contact` field, which the real sandbox genuinely
    reads (tracking/BUILD_LOG.md's recurring-digit rejection finding)."""
    captured = {}

    def fake_link(amount_inr, description, customer):
        captured["customer"] = dict(customer)
        return {"id": "plink_FAKE", "short_url": "https://rzp.io/rzp/FAKE"}

    monkeypatch.setattr(razorpay_client, "create_payment_link", fake_link)
    monkeypatch.setattr(razorpay_client, "create_mandate_registration_link", fake_link)

    world = WorldRunner(real_razorpay=True, real_tts=False)
    world.contacts.submit(world._contact_key("INV-001"), "Ramesh Traders", "9812345678", NOW)

    # Fire a link/mandate action directly against INV-001 so the FIRST real
    # call (the only one this run's budget allows) is against the entity we
    # just gave a contact to.
    action = world.ledger._emit_action(
        world.ledger._entity("INV-001"), "link", {"amount_inr": 40000},
        "test-forced link", world.now(),
    )
    world._dispatch(action, world.day)

    assert captured["customer"]["name"] == "Ramesh Traders"
    assert captured["customer"]["contact"] == "9812345678"
    assert captured["customer"]["email"] == DEMO_CUSTOMER_EMAIL


# ---------------------------------------------------------------------------
# 4. API: submit, list, and the entity-row / create-mandate-now fallback chain
# ---------------------------------------------------------------------------


def _sibling_invoices(client) -> tuple[str, list[str]]:
    from api.main import runner as api_runner

    by_debtor: dict[str, list[str]] = {}
    for eid, inv in api_runner.invoices.items():
        by_debtor.setdefault(inv.debtor_id, []).append(eid)
    debtor_id, ids = next((d, sorted(v)) for d, v in by_debtor.items() if len(v) > 1)
    return debtor_id, ids


def test_post_contact_stores_it_and_names_every_sibling_that_shares_it(client):
    _, siblings = _sibling_invoices(client)
    primary, *rest = siblings

    r = client.post(f"/entities/{primary}/contact", json={"name": "Ramesh Traders", "phone": "9812345678"})
    assert r.status_code == 200
    body = r.json()
    assert body["contact"]["name"] == "Ramesh Traders"
    assert body["contact"]["phone"] == "9812345678"
    assert body["contact"]["source"] == "operator_submitted"
    assert set(body["also_applies_to"]) == set(rest)

    # ...and it is genuinely visible on every sibling's resolved contact
    for eid in rest:
        row = client.get(f"/entities/{eid}").json()
        assert row["contact"]["name"] == "Ramesh Traders"
        assert row["contact"]["source"] == "operator_submitted"


def test_post_contact_audits_the_submission(client):
    from api.main import ledger as api_ledger

    _, siblings = _sibling_invoices(client)
    primary = siblings[0]
    before = len(api_ledger.audit)
    client.post(f"/entities/{primary}/contact", json={"name": "Ramesh Traders", "phone": "9812345678"})
    after = api_ledger.audit[before:]
    assert any(
        a.entity_id == primary and "operator submitted a real contact record" in a.summary
        for a in after
    )


def test_post_contact_rejects_a_malformed_phone_with_422(client):
    r = client.post("/entities/INV-001/contact", json={"name": "Ramesh Traders", "phone": "123"})
    assert r.status_code == 422


def test_post_contact_rejects_an_empty_name_with_422(client):
    r = client.post("/entities/INV-001/contact", json={"name": "   ", "phone": "9812345678"})
    assert r.status_code == 422


def test_post_contact_404s_on_a_truly_unknown_entity(client):
    r = client.post("/entities/NOPE-999/contact", json={"name": "Ramesh Traders", "phone": "9812345678"})
    assert r.status_code == 404


def test_get_contacts_lists_every_known_entity_with_its_resolved_contact(client):
    rows = client.get("/contacts").json()
    assert rows, "expected at least the invoice-backed entities"
    assert all(r["contact_source"] == "demo_fallback" for r in rows)
    ids = {r["entity_id"] for r in rows}
    assert "INV-001" in ids

    client.post("/entities/INV-001/contact", json={"name": "Ramesh Traders", "phone": "9812345678"})
    rows_after = {r["entity_id"]: r for r in client.get("/contacts").json()}
    assert rows_after["INV-001"]["contact_source"] == "operator_submitted"
    assert rows_after["INV-001"]["contact_phone"] == "9812345678"


def test_entities_route_carries_the_resolved_contact(client):
    rows = client.get("/entities").json()
    assert rows and all("contact" in r and r["contact"]["source"] == "demo_fallback" for r in rows)


def test_create_mandate_now_uses_the_real_submitted_contact_as_the_middle_fallback_rung(monkeypatch, client):
    """`create-mandate-now`'s fallback chain: explicit request body field >
    `resolve_contact()`'s real contact > demo constants. Only the middle rung
    changed in packet P15 — the explicit-override behaviour is untouched."""
    captured = {}

    def fake_create(amount_inr, description, customer, debit_date):
        captured["customer"] = dict(customer)
        return {
            "plan": {"id": "plan_FAKE"},
            "subscription": {"id": "sub_FAKE", "short_url": "https://rzp.io/i/fake"},
        }

    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", fake_create)

    client.post("/entities/INV-001/contact", json={"name": "Ramesh Traders", "phone": "9812345678"})

    # (1) nothing in the body -> uses the newly-submitted real contact
    r = client.post("/entities/INV-001/create-mandate-now", json={})
    assert r.status_code == 200
    assert captured["customer"]["name"] == "Ramesh Traders"
    assert captured["customer"]["contact"] == "9812345678"
    assert captured["customer"]["email"] == DEMO_CUSTOMER_EMAIL

    # (2) an explicit body field still overrides the real submitted contact
    r = client.post(
        "/entities/INV-001/create-mandate-now",
        json={"customer_contact": "+919999900000"},
    )
    assert r.status_code == 200
    assert captured["customer"]["contact"] == "+919999900000"
    assert captured["customer"]["name"] == "Ramesh Traders"  # not overridden -> still the real one


def test_create_mandate_now_falls_back_to_demo_constants_when_nothing_was_ever_submitted(monkeypatch, client):
    captured = {}

    def fake_create(amount_inr, description, customer, debit_date):
        captured["customer"] = dict(customer)
        return {
            "plan": {"id": "plan_FAKE"},
            "subscription": {"id": "sub_FAKE", "short_url": "https://rzp.io/i/fake"},
        }

    monkeypatch.setattr(razorpay_client, "create_mandate_via_subscription", fake_create)

    r = client.post("/entities/INV-002/create-mandate-now", json={})
    assert r.status_code == 200
    assert captured["customer"]["contact"] == DEMO_CUSTOMER_CONTACT
    assert captured["customer"]["email"] == DEMO_CUSTOMER_EMAIL
