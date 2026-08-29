"""Packet P9 — the human side of the loop, driven through the API the way a
merchant drives it (master doc §2.3 confidence gates, §3.6 approval queue).

`tests/test_ledger.py` covers the gates as judgment-layer units. This file is
the other half: every one of these tests goes through a real HTTP route on a
real `WorldRunner`, because "the queue gets real approve/reject actions" is a
claim about the whole stack, not about a method signature.

The load-bearing ones, in order of how much they would hurt to lose:
  * a sub-0.90 extraction HOLDS the money action rather than sending it
  * approving re-runs `check_bounds()` AT CLICK TIME — a stale hold cannot be
    approved through a cap that has since been hit
  * the formal-notice draft cannot be sent by any route, with any body, ever
  * `human_resolution` has exactly one door, and `POST /events` is not it
"""

import pytest
from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _held(client, sendable=True, kind=None):
    rows = client.get("/review-queue").json()["held_actions"]
    for row in rows:
        if row["sendable"] is not sendable:
            continue
        if kind is not None and row["action"]["kind"] != kind:
            continue
        return row
    raise AssertionError(f"no pending held action matching sendable={sendable} kind={kind}: {rows}")


def _promise_at(client, entity_id: str, confidence: float, amount: int = 40000):
    """Drive one entity to the moment a money action is about to be decided,
    with a stated extraction confidence. Uses the manual event route on
    purpose: the point is that the gate lives in the LEDGER, so it fires
    whatever pushed the event in."""
    client.post("/events", json={"type": "invoice_triaged", "entity_id": entity_id, "payload": {}})
    client.post("/events", json={
        "type": "extraction_received", "entity_id": entity_id,
        "payload": {"amount_inr": amount, "confidence": confidence, "level": "L2"},
    })
    return client.post("/events", json={
        "type": "mandate_offer_requested", "entity_id": entity_id, "payload": {},
    })


# ---------------------------------------------------------------------------
# (a) the queue itself
# ---------------------------------------------------------------------------


def test_the_queue_is_honestly_empty_at_day_zero(client):
    body = client.get("/review-queue").json()
    assert body["counts"] == {
        "held_pending": 0, "held_resolved": 0, "handoffs": 0, "disputes": 0, "paused": 0,
    }
    assert body["held_actions"] == [] and body["handoffs"] == [] and body["disputes"] == []
    # the gates it enforces are reported, so the screen never has to hardcode them
    assert body["gates"] == {"money_action_confidence": 0.9, "clarify_confidence": 0.75}


def _force_a_formal_notice_draft(client, entity_id: str = "INV-001") -> None:
    """Drives one entity straight to ESCALATE_3 -> HUMAN_HANDOFF via the same
    manual event route `_promise_at` uses, so the queue always has a formal-
    notice draft to show regardless of whether the stochastic 45-day run's
    escalation ladder happens to reach that stage on its own this seed.

    Needed since 2026-08-30's debtor-level judgment (dispute freeze +
    touch-budget allocation): this seeded run's ladder no longer reaches
    ESCALATE_3 for ANY entity at all — every debtor either converts before
    then or is correctly frozen by an open dispute (measured, not assumed;
    see tracking/BUILD_LOG.md 2026-08-30). CLAUDE.md law 4's guarantee here
    is exactly as critical as ever, so the queue's own coverage of it must
    not depend on this run's particular luck. Call this BEFORE `/advance`,
    on a still-NEW entity — the ladder's bound-blocked fallback lands it on
    HUMAN_HANDOFF, so the later 45-day advance leaves it alone."""
    for _ in range(3):
        client.post("/events", json={"type": "promise_broken", "entity_id": entity_id, "payload": {}})
    client.post("/events", json={"type": "escalation_exhausted", "entity_id": entity_id, "payload": {}})


def test_a_45_day_run_fills_every_section_of_the_queue(client):
    _force_a_formal_notice_draft(client)
    client.post("/advance", json={"days": 45})
    body = client.get("/review-queue").json()

    assert body["counts"]["held_pending"] > 0, "no action was ever held for a human"
    assert body["counts"]["handoffs"] > 0 and body["counts"]["disputes"] > 0

    kinds = {h["action"]["kind"] for h in body["held_actions"]}
    # Both are genuine money-gate holds (`Ledger._decide_money_action`'s own
    # two candidates: the mandate offer, or the link it falls back to) —
    # which one a sub-0.90 extraction actually produces shifted once forcing
    # INV-001's escalation (above) rippled through the shared persona RNG
    # stream; either is equally valid proof the gate is exercised for real.
    assert kinds & {"mandate_offer", "link"}, "the money gate never held a mandate-adjacent action"
    assert any(h["sendable"] is False for h in body["held_actions"]), "no formal-notice draft"

    # every money-gate hold names the confidence that put it there
    for row in body["held_actions"]:
        if row["sendable"]:
            assert "money gate" in row["reason"] or "ambiguous" in row["reason"]

    # disputes carry the evidence-packet card the screen renders
    dispute = body["disputes"][0]
    assert dispute["evidence"] is not None
    assert "DISPUTE --" in dispute["evidence"]["card"]
    assert dispute["evidence"]["packet"]["thread_excerpt"]


# ---------------------------------------------------------------------------
# (b) held actions: the money gate + the approve click
# ---------------------------------------------------------------------------


def test_a_sub_090_extraction_holds_the_money_action_instead_of_sending_it(client):
    response = _promise_at(client, "INV-001", confidence=0.82)
    assert response.json() is None, "a gated money action must not come back as an emitted Action"

    row = _held(client)
    assert row["action"]["kind"] == "mandate_offer"
    assert row["action"]["bounds_checked"] is False, "a held action has NOT passed the gate yet"
    assert row["reason"] == "confidence 0.82 < 0.90 money gate"
    assert row["status"] == "pending"
    assert row["extraction_confidence"] == 0.82

    # nothing went out, and the entity spent no touch budget holding it
    assert api_main.runner.messenger.for_entity("INV-001") == []
    assert api_main.ledger.entities["INV-001"].touches == []


def test_an_extraction_at_the_gate_is_emitted_normally(client):
    """0.90 is the gate, not a hair under it: >= 0.90 acts unsupervised."""
    response = _promise_at(client, "INV-001", confidence=0.90)
    assert response.json()["kind"] == "mandate_offer"
    assert client.get("/review-queue").json()["counts"]["held_pending"] == 0


def test_approving_a_hold_emits_it_through_the_normal_path(client):
    _promise_at(client, "INV-001", confidence=0.82)
    row = _held(client)

    body = client.post(f"/review-queue/{row['id']}/approve").json()
    assert body["blocked"] is False
    emitted = body["emitted"]
    assert emitted["kind"] == "mandate_offer"
    assert emitted["bounds_checked"] is True, "the emitted action DID pass the gate"
    assert emitted["reason"].startswith("human approved:")
    # law 2 still holds through the human path: the amount is the ledger's
    assert emitted["params"]["amount_inr"] == api_main.ledger.entities["INV-001"].invoice_amount_inr

    # it really went out, and the touch was spent at CLICK time, not at hold time
    assert len(api_main.runner.messenger.for_entity("INV-001")) == 1
    assert len(api_main.ledger.entities["INV-001"].touches) == 1

    assert body["held"]["status"] == "approved"
    assert body["held"]["emitted_action_id"] == emitted["id"]
    assert client.get("/review-queue").json()["counts"]["held_pending"] == 0


def test_a_hold_approved_after_the_debtors_budget_is_spent_is_blocked(client):
    """THE test this packet exists for. `check_bounds()` runs on the click, not
    on the hold — so a hold that sat in the queue while the debtor's weekly
    budget was spent on their OTHER invoice cannot be approved through the cap.

    INV-001 and INV-002 both belong to D-01, and the cap is per DEBTOR.
    """
    _promise_at(client, "INV-001", confidence=0.82)
    row = _held(client)

    # the debtor's week fills up elsewhere while the hold waits
    client.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-002", "payload": {}})
    for _ in range(2):
        client.post("/events", json={
            "type": "outreach_sent", "entity_id": "INV-002", "payload": {"stage": "gentle"},
        })
    assert len(api_main.ledger.touches_by_debtor["D-01"]) == 2

    body = client.post(f"/review-queue/{row['id']}/approve").json()
    assert body["blocked"] is True
    assert body["emitted"] is None
    assert "max_touches_per_week" in body["block_reason"]
    assert body["held"]["status"] == "blocked"

    # the human's click is in the trail, and so is the bound that refused it
    audit = client.get("/entities/INV-001/audit").json()
    summaries = [a["summary"] for a in audit]
    assert "human approved held action" in summaries
    assert "human-approved action blocked at click time" in summaries
    assert api_main.runner.messenger.for_entity("INV-001") == []


def test_rejecting_a_hold_falls_back_the_way_a_bounds_block_does(client):
    _promise_at(client, "INV-001", confidence=0.82)
    row = _held(client)

    body = client.post(f"/review-queue/{row['id']}/reject").json()
    assert body["held"]["status"] == "rejected"
    fallback = body["fallback"]
    assert fallback is not None and fallback["kind"] == "link"
    assert fallback["bounds_checked"] is True
    assert "human rejected the mandate offer" in fallback["reason"]

    assert "human rejected held action" in [
        a["summary"] for a in client.get("/entities/INV-001/audit").json()
    ]
    # the fallback is NOT re-held — the click WAS the human review
    assert client.get("/review-queue").json()["counts"]["held_pending"] == 0


def test_a_decided_queue_item_cannot_be_decided_twice(client):
    _promise_at(client, "INV-001", confidence=0.82)
    row = _held(client)
    assert client.post(f"/review-queue/{row['id']}/approve").status_code == 200
    for verb in ("approve", "reject", "mark-handled"):
        r = client.post(f"/review-queue/{row['id']}/{verb}")
        assert r.status_code == 409
        assert "already approved" in r.json()["detail"]


def test_unknown_ids_are_404_not_silent_no_ops(client):
    assert client.post("/review-queue/H-9999/approve").status_code == 404
    assert client.post("/review-queue/H-9999/reject").status_code == 404
    assert client.post("/review-queue/H-9999/mark-handled").status_code == 404
    assert client.post("/entities/NOPE/pause").status_code == 404
    assert client.post(
        "/entities/NOPE/resolve-handoff", json={"resolution": "recovered"}
    ).status_code == 404


# ---------------------------------------------------------------------------
# (c) the formal-notice draft: in the queue, never on the wire
# ---------------------------------------------------------------------------


def test_the_formal_notice_draft_can_never_be_sent_by_any_api_call(client):
    """CLAUDE.md law 4 is not "held for approval", it is NEVER. The draft
    reaches the merchant as a queue item with no approve path at all; they send
    it themselves, outside the system, and mark it handled."""
    _force_a_formal_notice_draft(client)
    client.post("/advance", json={"days": 45})
    draft = _held(client, sendable=False)
    assert draft["label"] == "formal_notice_draft"
    assert draft["action"]["kind"] == "message"
    assert draft["action"]["params"]["stage"] == "legal"
    assert "never sends legal communication" in draft["reason"]

    for verb in ("approve", "reject"):
        r = client.post(f"/review-queue/{draft['id']}/{verb}")
        assert r.status_code == 403, f"/{verb} must refuse a legal draft"
        assert "never sends legal communication" in r.json()["detail"]

    # the general-purpose event route is not a back door either — the bound
    # blocks a legal-stage message wherever it is asked for
    assert client.post("/events", json={
        "type": "outreach_sent", "entity_id": draft["entity_id"], "payload": {"stage": "legal"},
    }).json() is None

    handled = client.post(
        f"/review-queue/{draft['id']}/mark-handled", json={"note": "sent by registered post"}
    ).json()
    assert handled["emitted"] is None
    assert handled["held"]["status"] == "handled"
    assert handled["held"]["resolution_note"] == "sent by registered post"

    # ...and after all of that, nothing legal ever reached the wire
    assert not [
        a for a in api_main.runner.actions
        if a.kind == "message" and a.params.get("stage") == "legal"
    ]
    assert not [m for m in api_main.runner.messenger.queue if "merchant review required" in m.text]


# ---------------------------------------------------------------------------
# (d) human_resolution: one door only
# ---------------------------------------------------------------------------


def test_resolve_handoff_closes_a_handoff_both_ways(client):
    client.post("/advance", json={"days": 45})
    queue = client.get("/review-queue").json()
    recovered, written_off = queue["handoffs"][0]["entity_id"], queue["handoffs"][1]["entity_id"]

    a = client.post(f"/entities/{recovered}/resolve-handoff", json={"resolution": "recovered"})
    assert a.status_code == 200 and a.json()["entity"]["state"] == "KEPT"

    b = client.post(f"/entities/{written_off}/resolve-handoff", json={"resolution": "written_off"})
    assert b.status_code == 200 and b.json()["entity"]["state"] == "CLEAN_LOSS"

    # both leave the queue, and both are in the trail before they moved
    after = client.get("/review-queue").json()
    assert {recovered, written_off}.isdisjoint({h["entity_id"] for h in after["handoffs"]})
    assert after["counts"]["handoffs"] == queue["counts"]["handoffs"] - 2
    assert "human resolution: recovered" in [
        e["summary"] for e in client.get(f"/entities/{recovered}/audit").json()
    ]


def test_resolve_handoff_closes_a_dispute_too(client):
    client.post("/advance", json={"days": 45})
    disputed = client.get("/review-queue").json()["disputes"][0]["entity_id"]
    r = client.post(f"/entities/{disputed}/resolve-handoff", json={"resolution": "written_off"})
    assert r.json()["entity"]["state"] == "CLEAN_LOSS"


def test_resolve_handoff_refuses_anything_that_is_not_an_open_handoff(client):
    """The route is not a general "set this entity's state" primitive: a live
    entity mid-ladder, and an entity already closed as KEPT, are both refused."""
    client.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})
    r = client.post("/entities/INV-001/resolve-handoff", json={"resolution": "recovered"})
    assert r.status_code == 409 and "not an open handoff" in r.json()["detail"]

    client.post("/events", json={"type": "promise_kept", "entity_id": "INV-001", "payload": {}})
    assert client.get("/entities/INV-001").json()["state"] == "KEPT"
    assert client.post(
        "/entities/INV-001/resolve-handoff", json={"resolution": "written_off"}
    ).status_code == 409

    assert client.post(
        "/entities/INV-001/resolve-handoff", json={"resolution": "somethingelse"}
    ).status_code == 422


def test_human_resolution_cannot_be_injected_through_the_general_event_route(client):
    """The exception to terminal-state immutability has exactly one door. If
    `POST /events` could fire it, "terminal states are immutable except from
    the review queue" would be a comment, not a property."""
    client.post("/advance", json={"days": 45})
    handoff = client.get("/review-queue").json()["handoffs"][0]["entity_id"]

    r = client.post("/events", json={
        "type": "human_resolution", "entity_id": handoff, "payload": {"resolution": "recovered"},
    })
    assert r.status_code == 400
    assert "resolve-handoff" in r.json()["detail"]
    assert client.get(f"/entities/{handoff}").json()["state"] == "HUMAN_HANDOFF"


# ---------------------------------------------------------------------------
# (e) the merchant kill-switch
# ---------------------------------------------------------------------------


def test_pause_and_unpause_are_audited_in_both_directions(client):
    assert client.post("/entities/INV-001/pause").json() == {
        "entity_id": "INV-001", "paused": True, "state": "NEW",
    }
    body = client.get("/review-queue").json()
    assert body["counts"]["paused"] == 1
    assert body["paused"][0]["entity_id"] == "INV-001"

    assert client.post("/entities/INV-001/unpause").json()["paused"] is False
    assert client.get("/review-queue").json()["counts"]["paused"] == 0

    summaries = [a["summary"] for a in client.get("/entities/INV-001/audit").json()]
    assert "thread paused by merchant (kill-switch)" in summaries
    assert "thread unpaused by merchant" in summaries


def test_a_paused_thread_sends_nothing_even_when_the_ladder_asks(client):
    client.post("/entities/INV-001/pause")
    client.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})
    assert client.post("/events", json={
        "type": "outreach_sent", "entity_id": "INV-001", "payload": {"stage": "gentle"},
    }).json() is None

    blocked = [
        a for a in client.get("/entities/INV-001/audit").json()
        if a["summary"].startswith("action blocked")
    ]
    assert blocked and blocked[-1]["detail"]["reason"] == "thread paused by merchant (kill-switch)"
    assert api_main.runner.messenger.for_entity("INV-001") == []

    # ...and unpausing hands the thread straight back
    client.post("/entities/INV-001/unpause")
    assert client.post("/events", json={
        "type": "outreach_sent", "entity_id": "INV-001", "payload": {"stage": "gentle"},
    }).json()["kind"] == "message"


def test_a_paused_thread_is_skipped_by_the_advance_loop(client, monkeypatch):
    """The dashboard's one-click kill-switch, exercised through the same button
    the demo presses. Paused before day 0, so no touch beat ever reaches it.

    gTTS is stubbed (not disabled — the live app's own `WorldRunner()`
    intentionally defaults `real_tts=True`, and this test drives that real
    app instance through the API, not a test-controlled one). Without the
    stub: pausing INV-001 shifts the shared RNG stream for every other
    entity (the paused one never draws), and on this seed that walks the run
    into producing a real `voice` action — a real network call to Google's
    gTTS endpoint that, on a machine where the IPv6 route to Google
    black-holes, blocks for ~20s waiting out that timeout before falling
    back to IPv4. Found live 2026-08-28 — see tracking/BUILD_LOG.md."""
    class _FakeTTS:
        def __init__(self, text: str, lang: str) -> None:
            pass

        def save(self, path: str) -> None:
            with open(path, "wb") as fh:
                fh.write(b"\xff\xf3\x84\xc4" + b"\x00" * 512)

    monkeypatch.setattr("gtts.gTTS", _FakeTTS)
    client.post("/entities/INV-001/pause")
    client.post("/advance", json={"days": 45})

    assert api_main.runner.messenger.for_entity("INV-001") == []
    skips = [
        a for a in client.get("/entities/INV-001/audit").json()
        if a["summary"].startswith("outreach skipped: thread paused")
    ]
    assert skips, "the skip must be visible in the trail — a paused thread is quiet, not dead"

    # and it still TERMINATES (law 5): pausing stops outreach, not resolution
    assert client.get("/entities/INV-001").json()["state"] == "HUMAN_HANDOFF"


# ---------------------------------------------------------------------------
# (f) the clarify gate, over the wire
# ---------------------------------------------------------------------------


def test_a_sub_075_extraction_asks_exactly_one_question_then_queues(client):
    """The heuristic provider's confidence table never dips below 0.78 on a
    level the runner books as a promise, so a 45-day heuristic run produces
    ZERO clarify messages (recorded honestly in tracking/BUILD_QUALITY.md).
    That is a property of one provider's numbers, not of the wiring — this test
    pushes a genuinely ambiguous confidence through the real route and proves
    the wire is live end to end."""
    client.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})

    first = client.post("/events", json={
        "type": "extraction_received", "entity_id": "INV-001",
        "payload": {"amount_inr": 40000, "confidence": 0.61, "level": "L4"},
    }).json()
    assert first["kind"] == "message" and first["params"]["stage"] == "clarify"
    assert first["bounds_checked"] is True
    # ...and it is a real outbound touch, not a free look at the thread
    assert len(api_main.ledger.entities["INV-001"].touches) == 1

    second = client.post("/events", json={
        "type": "extraction_received", "entity_id": "INV-001",
        "payload": {"amount_inr": 40000, "confidence": 0.58, "level": "L4"},
    }).json()
    assert second is None, "the agent never asks a second clarifying question by itself"

    row = _held(client)
    assert row["action"]["params"]["stage"] == "clarify"
    assert "still ambiguous after clarification" in row["reason"]
