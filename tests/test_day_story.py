"""Packet P10 — the Day Story surface: /debtors, /entities/{id}/conversation,
/entities/{id}/guardrail-checks, /entities/{id}/mandate-timeline,
/day/{n}/story, and the `stories` field POST /advance grew.

These routes exist to put the REAL simulation in front of a judge, so what they
are tested for is honesty as much as shape: that nothing on screen is invented
(a cart customer has no name and must say so), that the guardrail checklist a
day-story beat shows is the one the decision was really made from, that a
preview is a preview (it writes nothing), and that the two paths to a story
cannot drift apart.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.integration import day_story
from engine.integration.runner import WorldRunner
from sim.run import SIM_EPOCH

RUN_DAYS = 22
"""Long enough to reach a mandate flow, short enough that every test in this
module stays fast. Was 12 (day 7 offered one) until 2026-08-30's debtor-level
touch-budget allocation (`engine/judgment/allocation.py`): a debtor's several
open invoices now genuinely compete for the same scarce weekly budget instead
of the alphabetically-first one always winning it by accident, so the FIRST
Scene-1 mandate offer that survives the cascade (message + the extraction it
triggers both spend touch budget) lands on day 21 for this seed — measured,
not guessed. INV-001/Acme Traders (this file's long-standing demo entity)
no longer reliably completes a mandate flow within a short window at all
under fair competition; INV-043/Meenakshi Garments does (day 21, with a
real pre-seeded conversation thread), and is what mandate-flow-specific
tests below use instead. See tracking/BUILD_LOG.md/DECISIONS.md 2026-08-30."""


@pytest.fixture(scope="module")
def client():
    """One advanced world shared by the read-only tests below. Every route
    under test is read-only, so they cannot disturb each other — the two tests
    that need a pristine world build their own runner."""
    with TestClient(app) as c:
        c.post("/advance", json={"days": RUN_DAYS})
        yield c


# ---------------------------------------------------------------------------
# GET /debtors
# ---------------------------------------------------------------------------


def test_debtors_route_names_the_humans_behind_the_entity_ids(client):
    rows = client.get("/debtors").json()
    acme = rows["D-01"]
    assert acme["name"] == "Acme Traders"
    assert acme["label"] == "Acme Traders"
    assert acme["name_note"] is None
    assert "INV-001" in acme["entities"]
    # trust comes from the ledger's posterior, not from anywhere in the UI
    assert acme["trust_mean"] == pytest.approx(
        acme["trust_alpha"] / (acme["trust_alpha"] + acme["trust_beta"])
    )


def test_a_cart_customer_has_no_name_and_the_api_says_so_instead_of_inventing_one(client):
    """data/carts.json stores a customer_id and nothing else. A plausible
    business name here would be a fabrication printed next to a real amount
    (CLAUDE.md law 8)."""
    rows = client.get("/debtors").json()
    cust = rows["CUST-01"]
    assert cust["name"] is None
    assert cust["label"] == "CUST-01"  # the id itself, never a stand-in name
    assert "no business name is stored" in cust["name_note"]
    assert cust["entities"] == ["C-01"]

    # and no cart customer anywhere quietly acquired one
    for debtor_id, row in rows.items():
        if debtor_id.startswith("CUST-"):
            assert row["name"] is None and row["name_note"]


def test_every_debtor_the_ledger_knows_appears_exactly_once(client):
    rows = client.get("/debtors").json()
    entities = client.get("/entities").json()
    owned = {e for row in rows.values() for e in row["entities"]}
    assert {e["entity_id"] for e in entities} <= owned
    assert all(row["debtor_id"] == key for key, row in rows.items())


# ---------------------------------------------------------------------------
# GET /entities/{id}/conversation
# ---------------------------------------------------------------------------


def test_conversation_returns_the_real_thread_in_both_directions(client):
    body = client.get("/entities/INV-043/conversation").json()
    assert body["debtor_name"] == "Meenakshi Garments"
    assert body["channel"] == "wa"
    directions = {m["direction"] for m in body["messages"]}
    assert directions == {"in", "out"}
    assert all(m["text"] for m in body["messages"])
    # the run's own messages are separated from the dataset's seed history
    assert {m["origin"] for m in body["messages"]} == {"dataset", "run"}
    run_msgs = [m for m in body["messages"] if m["origin"] == "run"]
    assert any("auto-debit" in m["text"] for m in run_msgs)


def test_conversation_is_exactly_what_the_runner_holds_in_memory(client):
    """No re-ordering, no filtering, no re-wording: the route hands over
    `WorldRunner.threads[entity_id]`."""
    from api.main import runner

    body = client.get("/entities/INV-002/conversation").json()
    assert [m["id"] for m in body["messages"]] == [m.id for m in runner.threads["INV-002"]]
    assert [m["text"] for m in body["messages"]] == [m.text for m in runner.threads["INV-002"]]


def test_an_entity_with_no_thread_gets_an_honest_empty_conversation(client):
    """A Scene-2 Tier-0 reserve cart is a real ledger entity with genuinely no
    conversation at all — master doc §8.6's whole point is the 0 in "0
    touches", so the reserve pre-check recovers it silently, before any link
    or mandate is ever dispatched. (Non-reserve carts DO get a real thread
    now — master doc §3.3's cause -> instrument follow-through, built
    2026-08-29 — so this test moved off C-01 to keep testing the genuinely
    empty case rather than a since-fixed gap.) The route reports the empty
    case in words rather than 404-ing or faking a thread."""
    body = client.get("/entities/C-09/conversation").json()
    assert body["messages"] == []
    assert "no messages" in body["status"]
    assert body["debtor_name"] is None


def test_conversation_404s_on_an_unknown_entity(client):
    assert client.get("/entities/NOPE/conversation").status_code == 404


# ---------------------------------------------------------------------------
# GET /entities/{id}/guardrail-checks — the read-only preview
# ---------------------------------------------------------------------------


def test_guardrail_preview_lists_every_bound_with_its_real_numbers(client):
    body = client.get("/entities/INV-001/guardrail-checks?action_kind=mandate_offer").json()
    names = [c["name"] for c in body["checks"]]
    assert names[0] == "merchant_kill_switch"
    assert "max_touches_per_week" in names
    assert "mandate_amount_cap" in names
    assert body["allowed"] == all(c["passed"] for c in body["checks"])
    assert body["preview"] is True
    detail = {c["name"]: c["detail"] for c in body["checks"]}
    assert "/2 (limit 2)" in detail["max_touches_per_week"]
    assert "Rs." in detail["mandate_amount_cap"]


def test_guardrail_preview_infers_the_entitys_own_next_action_when_asked_for_none(client):
    body = client.get("/entities/INV-001/guardrail-checks").json()
    assert body["action_kind"] in {
        "message", "link", "mandate_offer", "mandate_execute", "voice", "evidence_packet",
    }
    assert body["params_source"]  # it always says where the params came from


def test_guardrail_preview_writes_absolutely_nothing(client):
    """A lens, not an action. If this route could audit, spend a touch or mint
    an Action, "just looking" would move the world."""
    from api.main import ledger

    before_audit = len(ledger.audit)
    before_gates = len(ledger.gate_log)
    before_touches = {k: list(v) for k, v in ledger.touches_by_debtor.items()}
    before_actions = ledger._action_seq

    for kind in ("message", "mandate_offer", "link", "mandate_execute", "voice"):
        assert client.get(f"/entities/INV-001/guardrail-checks?action_kind={kind}").status_code == 200
    assert client.get("/entities/INV-001/guardrail-checks?action_kind=message&stage=legal").status_code == 200

    assert len(ledger.audit) == before_audit
    assert len(ledger.gate_log) == before_gates
    assert ledger.touches_by_debtor == before_touches
    assert ledger._action_seq == before_actions


def test_guardrail_preview_shows_the_legal_stage_bound_refusing(client):
    body = client.get("/entities/INV-001/guardrail-checks?action_kind=message&stage=legal").json()
    legal = next(c for c in body["checks"] if c["name"] == "legal_stage_goes_to_merchant")
    assert legal["passed"] is False
    assert "merchant" in legal["detail"]
    assert body["allowed"] is False


def test_guardrail_preview_404s_on_an_unknown_entity(client):
    assert client.get("/entities/NOPE/guardrail-checks").status_code == 404


# ---------------------------------------------------------------------------
# GET /entities/{id}/mandate-timeline
# ---------------------------------------------------------------------------


def test_mandate_timeline_reconstructs_the_lifecycle_from_the_audit_trail(client):
    body = client.get("/entities/INV-043/mandate-timeline").json()
    steps = [s["step"] for s in body["steps"]]
    assert steps[0] == "offered"
    assert "offer_created" in steps
    assert "registered" in steps
    assert "debtor_response" in steps

    # every step points at the audit entry it was read out of
    audit_ids = {a["id"] for a in client.get("/entities/INV-043/audit").json()}
    assert all(s["audit_id"] in audit_ids for s in body["steps"])
    assert [s["ts"] for s in body["steps"]] == sorted(s["ts"] for s in body["steps"])


def test_execute_failed_surfaces_its_reason_like_debtor_refused_already_does():
    """Real gap found during Packet 5's UI pass (2026-08-30): the mandate
    lifecycle stepper's frontend renders ANY step's `detail.reason`
    generically, but `_mandate_step()`'s own `execute_failed` case dropped
    the debit-failure reason on the floor — the one thing packet 1's whole
    taxonomy (insufficient_funds/bank_downtime/mandate_revoked/
    account_closed_frozen/amount_exceeds_limit) exists to distinguish was
    invisible on the one screen built to show a mandate's lifecycle. Fixed
    to match the sibling `debtor_refused` case exactly."""
    from engine.schemas import AuditEntry

    entry = AuditEntry(
        id="AE-00099", entity_id="INV-TEST", layer="judgment",
        summary="mandate_execute_failed: MANDATED -> AT_RISK",
        detail={"event": "mandate_execute_failed", "payload": {"amount_inr": 40000, "reason": "insufficient_funds"}},
        ts=dt.datetime(2026, 8, 26, 9, 0, 0),
    )
    step, extra = day_story._mandate_step(entry)
    assert step == "execute_failed"
    assert extra["reason"] == "insufficient_funds"
    assert extra["amount_inr"] == 40000


def test_mandate_timeline_never_calls_a_simulated_step_real(client):
    body = client.get("/entities/INV-043/mandate-timeline").json()
    registered = next(s for s in body["steps"] if s["step"] == "registered")
    # this run is offline (PK_REAL_RAZORPAY unset), so the link is simulated and
    # must be labelled as such, with no REAL badge anywhere near it
    assert registered["nature"] == "razorpay_simulated"
    assert registered["real"] is False
    assert registered["detail"]["short_url"].startswith("https://rzp.io/sim/")
    assert all(s["real"] is False for s in body["steps"])
    # and the account-level gate is stated, not glossed
    assert "UPI/eMandate not enabled" in body["account_gate_note"]
    assert "SIMULATED in every run" in body["lifecycle_note"]


def test_an_execution_outcome_is_always_labelled_simulated(client):
    """TRACK_BAR §0: registration is real in TEST mode, execution never is —
    this sandbox account cannot authorize a token to charge."""
    from api.main import runner

    for entity_id in runner.ledger.entities:
        body = client.get(f"/entities/{entity_id}/mandate-timeline").json()
        for step in body["steps"]:
            if step["step"] in ("executed", "execute_failed", "revoked"):
                assert step["nature"] == "simulated_outcome"
                assert step["real"] is False


def test_an_entity_that_never_saw_a_mandate_gets_an_empty_timeline_not_an_error(client):
    """A silent debtor has no mandate lifecycle. The honest answer is an empty
    stepper with a sentence explaining why, not a 404 and not a fabricated
    "pending" step."""
    body = client.get("/entities/C-02/mandate-timeline").json()
    assert body["steps"] == []
    assert "no mandate was ever offered" in body["status"]
    assert client.get("/entities/C-02/mandate-timeline").status_code == 200


def test_mandate_timeline_404s_on_an_unknown_entity(client):
    assert client.get("/entities/NOPE/mandate-timeline").status_code == 404


def test_a_genuinely_real_registration_link_is_badged_real_and_carries_the_account_gate(monkeypatch):
    """The one step in the whole lifecycle that CAN be real, exercised on the
    real code path with a fake transport (no network in the suite).

    It has to come back `razorpay_real` with the live short_url AND the
    TRACK_BAR §0 sentence attached, because that is precisely where the real
    rail stops in this sandbox: the link is real, the approval behind it cannot
    complete, and the demo must say both in the same breath.
    """
    from engine.action import razorpay_client

    monkeypatch.setattr(
        razorpay_client, "create_payment_link",
        lambda amount_inr, description, customer: {
            "id": "plink_FAKE", "short_url": "https://rzp.io/rzp/FAKELINK",
        },
    )
    monkeypatch.setattr(
        razorpay_client, "create_mandate_registration_link",
        lambda max_amount_inr, description, customer, method="upi": {
            "id": "inv_FAKE", "short_url": "https://rzp.io/rzp/FAKEMANDATE",
        },
    )

    runner = WorldRunner(real_razorpay=True, real_tts=False)
    runner.advance(RUN_DAYS)

    real_steps = [
        step
        for entity_id in runner.ledger.entities
        for step in day_story.mandate_timeline(runner, entity_id)["steps"]
        if step["nature"] == "razorpay_real"
    ]
    assert len(real_steps) == 1, "the run's real-mandate budget is exactly one"
    step = real_steps[0]
    assert step["step"] == "registered"
    assert step["real"] is True
    assert step["detail"]["short_url"] == "https://rzp.io/rzp/FAKEMANDATE"
    assert step["detail"]["razorpay_mode"] == "test"
    assert step["gate_note"] == day_story.ACCOUNT_GATE_NOTE
    assert "UPI/eMandate not enabled" in step["gate_note"]

    # every OTHER mandate step in that same run is still labelled simulated —
    # one real registration does not make the lifecycle real
    others = [
        step
        for entity_id in runner.ledger.entities
        for step in day_story.mandate_timeline(runner, entity_id)["steps"]
        if step["nature"] != "razorpay_real"
    ]
    assert others and all(s["real"] is False for s in others)


# ---------------------------------------------------------------------------
# GET /day/{n}/story
# ---------------------------------------------------------------------------


def test_day_story_labels_every_block_with_a_real_name_amount_and_trust(client):
    story = client.get("/day/7/story").json()
    assert story["day"] == 7
    assert story["date"] == (SIM_EPOCH + dt.timedelta(days=7)).date().isoformat()
    assert story["simulated"] is True

    block = next(b for b in story["entities"] if b["entity_id"] == "INV-001")
    assert block["debtor_name"] == "Acme Traders"
    assert block["invoice_amount_inr"] == 40000
    assert block["trust"]["as_of_day"] == 7
    assert block["trust"]["mean"] == pytest.approx(
        block["trust"]["alpha"] / (block["trust"]["alpha"] + block["trust"]["beta"])
    )


def test_day_story_trust_is_the_posterior_from_that_day_not_todays(client):
    """The whole reason `WorldRunner.day_snapshots` exists. Showing today's
    number beside a week-old conversation would be a quietly wrong claim."""
    from api.main import runner

    story = client.get("/day/7/story").json()
    block = next(b for b in story["entities"] if b["entity_id"] == "INV-001")
    snapshot = runner.day_snapshots[7]["trust"]["D-01"]
    assert block["trust"]["alpha"] == snapshot.alpha
    assert block["trust"]["beta"] == snapshot.beta
    assert block["state_end_of_day"] == runner.day_snapshots[7]["entities"]["INV-001"].state


def test_day_story_beats_carry_the_actual_conversation_text(client):
    # INV-001 has no in/out message pair on any single day post-2026-08-30's
    # debtor-level allocation (see RUN_DAYS's own docstring above) — INV-043
    # genuinely completes its message/reply exchange on day 21.
    story = client.get("/day/21/story").json()
    block = next(b for b in story["entities"] if b["entity_id"] == "INV-043")
    messages = [b for b in block["beats"] if b["type"] == "message"]
    assert {m["direction"] for m in messages} == {"in", "out"}

    thread = {m["id"]: m["text"] for m in client.get("/entities/INV-043/conversation").json()["messages"]}
    for message in messages:
        assert message["text"] == thread[message["message_id"]]


def test_day_story_beats_stay_in_the_order_the_trail_recorded_them(client):
    """Every beat of a simulated day shares one timestamp on purpose, so the
    order has to come from the append order of the audit trail. An outbound
    message must appear before the reply it drew."""
    story = client.get("/day/21/story").json()
    block = next(b for b in story["entities"] if b["entity_id"] == "INV-043")
    kinds = [(b["type"], b.get("direction")) for b in block["beats"]]
    first_out = kinds.index(("message", "out"))
    first_in = kinds.index(("message", "in"))
    assert first_out < first_in

    audit_ids = [b["audit_id"] for b in block["beats"] if b["audit_id"]]
    trail = [a["id"] for a in client.get("/entities/INV-043/audit").json()]
    assert audit_ids == [i for i in trail if i in set(audit_ids)]


def test_a_blocked_action_surfaces_the_audited_reason_verbatim(client):
    """A block already carries its reason in the trail. The story shows THAT
    sentence, plus the checklist recorded at the same instant — and the two
    agree by construction because `_gate()` wrote both from one BoundsResult."""
    blocked = [
        (b, g)
        for day in range(RUN_DAYS)
        for b in client.get(f"/day/{day}/story").json()["entities"]
        for beat in b["beats"]
        if (g := beat["guardrail_summary"]) and g["status"] == "blocked"
    ]
    assert blocked, "the 12-day run must produce at least one bound block"
    for _, guardrail in blocked:
        assert guardrail["audited_reason"] == guardrail["reason"]
        assert any(not c["passed"] for c in guardrail["checks"])
        # the first failing check is the bound the audited reason names
        first_failure = next(c for c in guardrail["checks"] if not c["passed"])
        assert first_failure["name"].split("_")[0] in guardrail["reason"] or (
            first_failure["name"] == "terminal_state_stops_outbound"
            and "terminal state" in guardrail["reason"]
        )


def test_an_allowed_money_action_shows_the_checklist_it_really_passed(client):
    """Not a live re-evaluation against today's entity state: the params and
    every check come from the GateRecord written when the action was created."""
    from api.main import ledger

    story = client.get("/day/21/story").json()
    block = next(b for b in story["entities"] if b["entity_id"] == "INV-043")
    offer = next(
        beat["guardrail_summary"] for beat in block["beats"]
        if beat["guardrail_summary"] and beat["guardrail_summary"]["kind"] == "mandate_offer"
    )
    assert offer["status"] == "allowed"
    assert offer["params"]["amount_inr"] == 19000
    assert offer["passed"] == offer["total"]

    record = next(r for r in ledger.gate_log if r.action_id == offer["action_id"])
    assert [c.name for c in record.checks] == [c["name"] for c in offer["checks"]]
    assert [c.detail for c in record.checks] == [c["detail"] for c in offer["checks"]]
    assert record.ts.isoformat() == offer["recorded_at"]


def test_every_guardrail_summary_agrees_with_its_own_checklist(client):
    """The screen-level restatement of Part A's invariant: nothing rendered as
    "allowed" may contain a failing check, and nothing rendered as "blocked"
    may be all green."""
    for day in range(RUN_DAYS):
        for block in client.get(f"/day/{day}/story").json()["entities"]:
            for beat in block["beats"]:
                guardrail = beat["guardrail_summary"]
                if guardrail is None:
                    continue
                assert (guardrail["status"] == "allowed") == all(
                    c["passed"] for c in guardrail["checks"]
                )
                assert guardrail["checks"], "a rendered checklist is never empty"


def test_a_day_with_no_activity_is_an_honest_empty_story(client):
    """Day 5 stopped being quiet on 2026-08-29 once Scene 2's trust-cause
    cart's delivery-secured mandate resolves on day CART_BEAT_DAY(1) +
    DELIVERY_CONFIRM_OFFSET(4) = 5 (master doc §3.3) — moved to day 6, which
    genuinely has none."""
    story = client.get("/day/6/story").json()
    assert story["entities"] == []
    assert story["simulated"] is True
    assert "no audited activity" in story["status"]


def test_a_day_that_has_not_been_simulated_says_so_rather_than_erroring(client):
    story = client.get("/day/300/story").json()
    assert story["entities"] == []
    assert story["simulated"] is False
    assert "has not been simulated yet" in story["status"]
    assert story["world_day"] == RUN_DAYS


def test_a_negative_day_is_rejected(client):
    assert client.get("/day/-1/story").status_code == 422


def test_day_zero_carries_the_dataset_seed_thread_labelled_as_such(client):
    """The dataset's own conversation history lands on the simulated calendar
    too. It is shown (it is real) and labelled `origin: "dataset"` so nobody
    reads inherited thread history as something the agent did."""
    story = client.get("/day/0/story").json()
    block = next(b for b in story["entities"] if b["entity_id"] == "INV-001")
    origins = {b["origin"] for b in block["beats"] if b["type"] == "message"}
    assert "dataset" in origins


# ---------------------------------------------------------------------------
# POST /advance's `stories` — one builder, two doors
# ---------------------------------------------------------------------------


def test_advance_and_day_story_agree():
    """The `stories` POST /advance returns for day N and what GET /day/N/story
    serves for the same day must be the same JSON. Two code paths to the same
    screen would be two chances to disagree in front of a judge."""
    with TestClient(app) as c:
        body = c.post("/advance", json={"days": 9}).json()
        stories = body["stories"]
        assert sorted(stories, key=int) == [str(d) for d in range(9)]

        for day in range(9):
            served = c.get(f"/day/{day}/story").json()["entities"]
            assert stories[str(day)] == served, f"day {day} differs between /advance and /day/{day}/story"
            for advanced_block, served_block in zip(stories[str(day)], served):
                assert advanced_block["beats"] == served_block["beats"]


def test_advance_keeps_every_pre_existing_field():
    """`stories` is purely additive — no existing consumer of /advance breaks."""
    with TestClient(app) as c:
        body = c.post("/advance", json={"days": 1}).json()
    assert {"day", "new_events", "new_actions", "new_audit", "funnel_summary"} <= set(body)
    assert "stories" in body
    assert isinstance(body["funnel_summary"], dict)


def test_advance_stories_cover_exactly_the_days_just_simulated():
    with TestClient(app) as c:
        c.post("/advance", json={"days": 2})
        before = c.get("/world").json()["day"]
        body = c.post("/advance", json={"days": 3}).json()
    assert before == 2
    assert sorted(body["stories"], key=int) == [str(d) for d in range(before, before + 3)]


# ---------------------------------------------------------------------------
# the builder itself, off the HTTP path
# ---------------------------------------------------------------------------


def test_day_indexes_line_up_with_the_runners_own_clock():
    runner = WorldRunner(real_razorpay=False, real_tts=False)
    runner.advance(3)
    assert runner.day == 3
    assert day_story.day_of(runner._ts(2)) == 2
    assert day_story.date_of_day(0) == SIM_EPOCH.date()
    # advance(1) simulates day index 0, so the day that just happened is day-1
    assert day_story.build_day_story(runner, runner.day - 1)["day"] == 2


def test_the_story_never_invents_a_field_it_has_no_record_for():
    """A fresh runner: no days simulated, so no trust snapshot and no beats.
    Every absent value is null with a note beside it, never a placeholder."""
    runner = WorldRunner(real_razorpay=False, real_tts=False)
    story = day_story.build_day_story(runner, 0)
    assert story["entities"] == []
    assert story["simulated"] is False
    directory = day_story.debtor_directory(runner)
    for row in directory.values():
        assert row["trust_mean"] is None
        assert row["trust_note"]
