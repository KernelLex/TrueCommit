"""Integration-runner tests (packet P2) — the whole world driven through the
REAL pipeline by `WorldRunner.advance`.

These are the tests that make BUILD.md Day 7's "pressing Advance-Day visibly
moves money/promises/trust" checkable without a browser: if the funnel moves
here, it moves on screen, because the dashboard reads the same runner.

Everything below runs OFFLINE. `PK_REAL_RAZORPAY` is explicitly disabled for
every runner constructed here, and `test_no_network_is_attempted_by_default`
proves it by making any Razorpay call an outright failure.
"""

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from api.main import app
from engine.action import razorpay_client
from engine.integration.runner import FINAL_SWEEP_DAY, LINK_TIMEOUT_DAYS, WorldRunner
from engine.judgment.state_machine import (
    MAX_TOUCHES_PER_WEEK,
    TERMINAL_STATES,
    TOUCH_WINDOW_DAYS,
)

RUN_DAYS = 45


@pytest.fixture(scope="module")
def world() -> WorldRunner:
    """One 45-day run, shared read-only across the assertions below."""
    runner = WorldRunner(real_razorpay=False)
    runner.advance(RUN_DAYS)
    return runner


def _invoice_states(runner: WorldRunner) -> dict[str, str]:
    return {eid: runner.ledger.entities[eid].state for eid in runner.active_invoice_ids}


# ---------------------------------------------------------------------------
# (a) a 45-day run resolves the batch
# ---------------------------------------------------------------------------


def test_advance_45_days_drives_the_batch_to_terminal_states(world: WorldRunner):
    states = _invoice_states(world)
    assert len(states) == len(world.active_invoice_ids) > 0

    # CLAUDE.md law 5: every recovery path terminates, no silent deaths.
    unresolved = {eid: s for eid, s in states.items() if s not in TERMINAL_STATES}
    assert unresolved == {}, f"still open after {RUN_DAYS} days: {unresolved}"

    kept = [eid for eid, s in states.items() if s == "KEPT"]
    handed_over = [eid for eid, s in states.items() if s in ("DISPUTED", "HUMAN_HANDOFF")]
    assert len(kept) > 0, "no invoice was recovered — the happy path is broken"
    assert len(handed_over) > 0, "nothing reached a human — the stopping rules never fired"
    assert world.funnel_summary()["recovered_inr"] > 0


def test_the_45_day_distribution_is_the_number_the_docs_quote(world: WorldRunner):
    """The headline figure, pinned so it cannot move without a doc update.

    Every packet that has changed this number recorded the change openly
    (P8's per-debtor touch cap, P9's money gate); packet P11 is the first that
    changed the *audit trail* without moving the headline, and that too is a
    measured result rather than an assumption — see tracking/BUILD_LOG.md
    2026-08-26 (P11) for why fixing 9 false refusals recovered ₹0 more.
    If this test fails, the number in BUILD_QUALITY.md / TRACK_BAR.md /
    DECISIONS.md is now a lie and has to be re-measured, not re-asserted.
    """
    states = _invoice_states(world)
    distribution: dict[str, int] = {}
    for state in states.values():
        distribution[state] = distribution.get(state, 0) + 1

    assert distribution == {"KEPT": 21, "HUMAN_HANDOFF": 27, "DISPUTED": 3}

    summary = world.funnel_summary()
    assert summary["recovered_inr"] == 2_331_496
    active_value = sum(
        world.ledger.entities[eid].invoice_amount_inr for eid in world.active_invoice_ids
    )
    assert active_value == 6_971_068
    assert round(100 * summary["recovered_inr"] / active_value, 1) == 33.4

    assert summary["promises"] == {"broken": 14, "kept": 18, "pending": 6}
    assert summary["messages_sent"] == 100
    assert summary["held_actions_total"] == 4
    assert summary["dead_letter"] == 0
    assert world.bound_violations() == []


def test_advance_moves_money_promises_and_trust(world: WorldRunner):
    """The Day-7 acceptance criterion, asserted at API-model level."""
    summary = world.funnel_summary()
    assert summary["recovered_inr"] > 0                 # money
    assert summary["promises"].get("kept", 0) > 0       # promises
    assert summary["promises"].get("broken", 0) > 0
    assert len(world.ledger.trust) > 0                  # trust
    alphas = {t.alpha for t in world.ledger.trust.values()}
    betas = {t.beta for t in world.ledger.trust.values()}
    assert alphas != {2.0} and betas != {2.0}, "no trust posterior moved off the Beta(2,2) prior"


def test_the_two_reserve_carts_recover_with_zero_touches(world: WorldRunner):
    """Master doc §8.6, the Tier-0 beat — the whole point is the 0 in
    'touches/recovery', so it is asserted from three independent angles."""
    reserve_carts = sorted(c.id for c in world.carts.values() if c.reserve_active)
    assert len(reserve_carts) == 2

    for cart_id in reserve_carts:
        entity = world.ledger.entities[cart_id]
        assert entity.state == "KEPT"
        assert entity.touches == []
        assert world.messenger.for_entity(cart_id) == []
        debit = [a for a in world.actions if a.entity_id == cart_id and a.kind == "mandate_execute"]
        assert len(debit) == 1
        assert debit[0].params["source"] == "reserve"
        # law 2: the debited amount is the ledger's record, not the event payload
        assert debit[0].params["amount_inr"] == world.carts[cart_id].amount_inr

    assert world.funnel_summary()["tier0_zero_touch_recoveries"] == reserve_carts


def test_real_perception_and_real_triage_ran(world: WorldRunner):
    """The runner is wired to the real extractor/triage, not to canned labels."""
    assert world.provider_name == "heuristic"
    assert len(world.triage) == len(world.active_invoice_ids) + len(world.disputed_invoice_ids)
    assert len(world.extractions) > 0
    levels = {e.level for e in world.extractions}
    assert {"L1", "L4"} <= levels, f"extractor never produced a spread of levels: {levels}"
    assert len(world.evidence_packets) > 0, "no dispute produced an evidence packet"


def test_messages_are_rail_labelled(world: WorldRunner):
    rails = {m.rail for m in world.messenger.queue}
    assert "mandate_link" in rails, "no mandate ever went out on the mandate rail"
    assert rails <= {"wa_native_payment", "mandate_link", "plain_link", "voice_note", "text_only"}
    for message in world.messenger.queue:
        assert message.status in ("sent", "delivered")


# ---------------------------------------------------------------------------
# (b) determinism (CLAUDE.md law 6)
# ---------------------------------------------------------------------------


def test_two_fresh_runners_produce_identical_audit_trails():
    first = WorldRunner(real_razorpay=False)
    second = WorldRunner(real_razorpay=False)
    first.advance(RUN_DAYS)
    second.advance(RUN_DAYS)

    assert first.audit_summaries() == second.audit_summaries()
    assert len(first.ledger.audit) > 0
    assert [(e.type, e.entity_id, e.ts) for e in first.events] == [
        (e.type, e.entity_id, e.ts) for e in second.events
    ]
    assert [(m.rail, m.text) for m in first.messenger.queue] == [
        (m.rail, m.text) for m in second.messenger.queue
    ]
    assert first.funnel_summary() == second.funnel_summary()


def test_the_same_days_split_differently_land_on_the_same_world():
    """"Advance 1 Day" pressed 12 times == "Run to Day 12" once."""
    one_shot = WorldRunner(real_razorpay=False)
    one_shot.advance(12)
    stepwise = WorldRunner(real_razorpay=False)
    for _ in range(12):
        stepwise.advance(1)
    assert one_shot.audit_summaries() == stepwise.audit_summaries()
    assert one_shot.funnel_summary() == stepwise.funnel_summary()


# ---------------------------------------------------------------------------
# (c) bounds held for every action the run produced
# ---------------------------------------------------------------------------


def test_no_action_bypassed_check_bounds(world: WorldRunner):
    assert len(world.actions) > 0
    assert all(a.bounds_checked for a in world.actions)

    # ...and every action in the audit trail is one the ledger actually issued,
    # i.e. nothing was constructed around the gate and logged afterwards.
    issued = {a.id: a for a in world.actions}
    logged = {e.detail["action_id"] for e in world.ledger.audit if "action_id" in e.detail}
    assert logged <= set(issued), f"audit references unissued actions: {logged - set(issued)}"
    assert all(issued[aid].bounds_checked for aid in logged)


def test_touch_cap_holds_in_the_message_queue(world: WorldRunner):
    """Bound #4 checked from the OUTPUT side: whatever the ledger decided, no
    entity actually received more than MAX_TOUCHES_PER_WEEK messages in any
    rolling 7-day window."""
    peaks = world.touch_windows()
    assert peaks, "no messages were queued at all"
    over = {eid: n for eid, n in peaks.items() if n > MAX_TOUCHES_PER_WEEK}
    assert over == {}, f"touch cap breached: {over}"
    assert world.bound_violations() == []


def test_touch_cap_is_enforced_per_debtor_not_just_per_invoice(world: WorldRunner):
    """Bound #4 as CLAUDE.md law 4 and master doc §3.4 actually word it: "max 2
    touches/week per DEBTOR", not per invoice.

    This test used to assert the opposite. Through packet P2 the cap was
    enforced against `entity.touches`, and an entity is one INVOICE — so a
    debtor holding five overdue invoices could collect six messages in a week
    with every individual action passing the gate. It was pinned in both
    directions (per-invoice <= 2 AND per-debtor > 2) precisely so that fixing
    it would break this test and force the claim to be re-stated. Packet P8
    fixed it (per-debtor window inside `check_bounds`), so the assertion is now
    the law itself. Recomputed from the message QUEUE, not from the ledger's
    counter, so it would still catch a gate that leaked.
    """
    per_debtor: dict[str, list[dt.datetime]] = {}
    for message in world.messenger.queue:
        invoice = world.invoices.get(message.entity_id)
        debtor = invoice.debtor_id if invoice else message.entity_id
        per_debtor.setdefault(debtor, []).append(message.ts)
    assert len(per_debtor) > 1, "a single-debtor run could never exercise the per-debtor scope"

    worst: dict[str, int] = {}
    for debtor, stamps in per_debtor.items():
        stamps.sort()
        worst[debtor] = max(
            (sum(1 for t in stamps[i:] if (t - start).days < TOUCH_WINDOW_DAYS)
             for i, start in enumerate(stamps)),
            default=0,
        )

    over = {d: n for d, n in worst.items() if n > MAX_TOUCHES_PER_WEEK}
    assert over == {}, f"per-debtor touch cap breached: {over}"
    assert worst == world.debtor_touch_windows()          # the runner's own report agrees
    assert max(world.touch_windows().values()) <= MAX_TOUCHES_PER_WEEK  # per invoice, implied
    # ...and at least one debtor really did hit the cap, so the bound was
    # exercised rather than merely never approached.
    assert max(worst.values()) == MAX_TOUCHES_PER_WEEK


def test_a_debtors_touch_budget_is_shared_across_their_invoices(world: WorldRunner):
    """The behavioural half of the same law, stated the way the product story
    states it: we throttle the human, not the invoice. On at least one day a
    debtor holding several open invoices had a touch to one of them BLOCKED —
    and the block is in the audit trail, which is what makes it a stopping rule
    rather than a dropped message."""
    blocks = [
        e for e in world.ledger.audit
        if e.layer == "sentinel" and e.summary.startswith("action blocked")
        and "max_touches_per_week" in str(e.detail.get("reason", ""))
    ]
    assert blocks, "the per-debtor cap never actually blocked anything in this run"
    assert all("debtor" in str(e.detail.get("reason", "")) for e in blocks)

    blocked_debtors = {e.detail["debtor_id"] for e in blocks}
    assert len(blocked_debtors) > 1
    # every blocked entity belongs to a debtor who holds more than one invoice
    for entry in blocks:
        siblings = [i for i in world.invoices.values() if i.debtor_id == entry.detail["debtor_id"]]
        assert len(siblings) > 1


def test_mandate_amounts_always_come_from_the_ledger_record(world: WorldRunner):
    """Law 2, end to end: extractions in this run really do carry amounts the
    'debtor' stated, and no money action ever used one of them."""
    money_actions = [a for a in world.actions if a.kind in ("mandate_offer", "mandate_execute")]
    assert money_actions
    for action in money_actions:
        amount = action.params.get("amount_inr")
        if amount is None:
            continue
        assert amount == world.ledger.entities[action.entity_id].invoice_amount_inr

    stated = [e for e in world.extractions if e.amount_inr is not None]
    assert stated, "no extraction stated an amount, so law 2 was never actually exercised"
    # The one payload key that could overwrite the ledger's own amount record
    # is never sent by the runner.
    assert all(
        "invoice_amount_inr" not in e.payload
        for e in world.events if e.type == "extraction_received"
    )


def test_disputes_stop_the_ladder_immediately(world: WorldRunner):
    disputed = [eid for eid, s in _invoice_states(world).items() if s == "DISPUTED"]
    assert disputed
    for entity_id in disputed:
        dispute_ts = min(
            e.ts for e in world.events if e.entity_id == entity_id and e.type == "dispute_raised"
        )
        later = [m for m in world.messenger.for_entity(entity_id) if m.ts > dispute_ts]
        assert later == [], f"{entity_id} got outbound messages after its dispute"


def test_link_timeouts_are_treated_as_soft_refusals(world: WorldRunner):
    """Sentinel: sent != delivered != opened. A mandate link nobody opened
    inside 48 virtual hours becomes a refusal signal, never an assumption."""
    timeouts = [a for a in world.ledger.audit if "soft refusal" in a.summary]
    assert timeouts, "no link ever timed out — the sentinel path is untested by this run"
    refusals = {e.entity_id for e in world.events if e.type == "mandate_refused"}
    assert refusals & {a.entity_id for a in timeouts}


# ---------------------------------------------------------------------------
# (c2) the link-open signal (packet P11) — the fix for P10's smoking gun
#
# `Sentinel.mark_link_opened()` shipped in the Day-6 action layer, was unit
# tested in tests/test_action_layer.py, and then had ZERO call sites in the
# runner. `link_timed_out()` was therefore true for EVERY instrument ever
# sent, so every dispatched mandate offer soft-refused itself two virtual days
# later regardless of what the debtor had actually done. The tests below pin
# both halves of the corrected behaviour: a reply cancels the window, and
# genuine silence still does not.
# ---------------------------------------------------------------------------


def _instrument_dispatches(world: WorldRunner, entity_id: str) -> list:
    return [
        a for a in world.ledger.audit
        if a.entity_id == entity_id and a.layer == "action"
        and a.summary.startswith(("mandate_offer dispatched", "link dispatched"))
    ]


def _opened(world: WorldRunner, entity_id: str) -> list:
    return [
        a for a in world.ledger.audit
        if a.entity_id == entity_id and a.layer == "sentinel" and "marked opened" in a.summary
    ]


def test_a_confirmed_mandate_is_never_later_recorded_as_refused(world: WorldRunner):
    """P10's smoking gun, INV-001 / Acme Traders, told as the trail tells it.

    BEFORE this fix the run read: day 7 mandate offered -> persona move
    `confirm_mandate` -> `mandate_confirmed` -> **day 9 "link never opened
    within 48h — treating as soft refusal" -> `mandate_refused: MANDATED ->
    LINKED`**. The debtor said yes and the system recorded a refusal two days
    later, set `entity.mandate_refused` (bound #7: no re-offer, ever) and fed
    `trust.update_refusal`.
    """
    entity_id = "INV-001"

    offered = _instrument_dispatches(world, entity_id)
    assert len(offered) == 1 and offered[0].summary.startswith("mandate_offer dispatched")
    action_id = offered[0].detail["action_id"]

    confirmed = [
        a for a in world.ledger.audit
        if a.entity_id == entity_id and a.detail.get("move") == "confirm_mandate"
    ]
    assert confirmed, "INV-001 is only the smoking gun because the debtor CONFIRMED"

    # the reply is what closed the window, and it closed THIS action's window
    opened = _opened(world, entity_id)
    assert len(opened) == 1
    assert opened[0].detail["action_id"] == action_id
    assert opened[0].ts >= offered[0].ts, "an instrument was opened before it was sent"
    assert world.sentinel.link_timed_out(action_id, world.now()) is False

    # ...so none of the four consequences of the bug happen any more
    assert not [a for a in world.ledger.audit
                if a.entity_id == entity_id and "soft refusal" in a.summary]
    assert not [e for e in world.events
                if e.entity_id == entity_id and e.type == "mandate_refused"]
    assert world.ledger.entities[entity_id].mandate_refused is False
    assert not [a for a in world.ledger.audit
                if a.entity_id == entity_id and a.summary == "mandate_refused: MANDATED -> LINKED"]

    # ...and the mandate proceeds to execute, straight out of MANDATED
    assert "mandate_execute_success: MANDATED -> KEPT" in {
        a.summary for a in world.ledger.audit if a.entity_id == entity_id
    }
    assert world.ledger.entities[entity_id].state == "KEPT"


def test_any_reply_opens_every_instrument_still_inside_its_window(world: WorldRunner):
    """The rule is "the debtor sent ANY message back", not "the debtor agreed" —
    there is no click event in a text-only world, so a reply IS the open signal
    (tracking/DECISIONS.md, packet P11). Asserted over the whole run rather than
    on one entity: every instrument whose debtor replied inside the window is
    marked opened, against the exact `action_id` `track_link_sent` was called
    with, and never before the send itself.
    """
    opened = [a for a in world.ledger.audit
              if a.layer == "sentinel" and "marked opened" in a.summary]
    assert opened, "no instrument was ever marked opened — the call site is gone again"

    opened_ids = {a.detail["action_id"] for a in opened}
    assert opened_ids <= set(world.sentinel.link_sent_at), "opened an id the sentinel never tracked"
    assert opened_ids == world.sentinel.link_opened
    assert len(opened_ids) == len(opened), "one instrument was marked opened twice"

    sent_at = {a.detail["action_id"]: a.ts for a in world.ledger.audit
               if a.layer == "action" and "action_id" in a.detail
               and a.summary.startswith(("mandate_offer dispatched", "link dispatched"))}
    for entry in opened:
        assert entry.ts >= sent_at[entry.detail["action_id"]]
        assert entry.detail["kind"] in ("link", "mandate_offer")

    # every debtor who confirmed a mandate had their offer opened, and none of
    # them is flagged as having refused one
    confirmers = {a.entity_id for a in world.ledger.audit
                  if a.detail.get("move") == "confirm_mandate"}
    assert len(confirmers) > 1
    assert confirmers <= {a.entity_id for a in opened}
    assert not [e for e in confirmers if world.ledger.entities[e].mandate_refused]


def test_true_silence_still_soft_refuses_after_48h(world: WorldRunner):
    """The half of `link_timed_out()` that was ALWAYS right, and must stay right:
    a debtor who sends nothing back is a soft refusal at 48h. INV-003 is the run's
    own instance — day 21 mandate offered, persona move `ignore` (which sends no
    message at all), day 23 soft refusal -> `mandate_refused: MANDATED -> LINKED`.
    """
    entity_id = "INV-003"

    offered = _instrument_dispatches(world, entity_id)
    assert len(offered) == 1
    action_id = offered[0].detail["action_id"]

    assert [a for a in world.ledger.audit
            if a.entity_id == entity_id and a.detail.get("move") == "ignore"]
    assert _opened(world, entity_id) == [], "silence must never be read as an open"

    # No inbound message AFTER the offer went out and before the window closed.
    # Ordered by position in the thread, not by timestamp: `_ts()` is day-granular
    # on purpose, so the promise that EARNED this offer shares a timestamp with
    # it and only the trail says which came first. `thread_message_id` (packet
    # P10) is what makes that resolvable at all.
    timeout = next(a for a in world.ledger.audit
                   if a.entity_id == entity_id and "soft refusal" in a.summary)
    assert timeout.detail["action_id"] == action_id
    assert (timeout.ts - offered[0].ts).days == LINK_TIMEOUT_DAYS

    thread = world.threads[entity_id]
    sent_at = next(i for i, m in enumerate(thread)
                   if m.id == offered[0].detail["thread_message_id"])
    assert [m for m in thread[sent_at + 1:] if m.direction == "in" and m.ts <= timeout.ts] == []

    # the sentinel's own verdict is unchanged, and the ladder acted on it
    assert world.sentinel.link_timed_out(action_id, timeout.ts) is True
    assert [e.payload["reason"] for e in world.events
            if e.entity_id == entity_id and e.type == "mandate_refused"] == [
        "mandate link never opened (soft refusal)"
    ]
    assert world.ledger.entities[entity_id].mandate_refused is True


def test_the_only_refusals_left_are_ones_a_debtor_actually_made(world: WorldRunner):
    """The measured blast radius of the bug, pinned as a number. Before the fix a
    45-day run produced 13 `mandate_refused` events, 10 of them manufactured by
    the timeout — barring 10 entities from any future mandate offer (bound #7)
    when only 4 had earned it. After it: 3 explicit debtor refusals
    (`refuse_but_promise`) + 1 genuine 48h silence."""
    refusals = [e for e in world.events if e.type == "mandate_refused"]
    by_reason: dict[str, int] = {}
    for event in refusals:
        by_reason[event.payload["reason"]] = by_reason.get(event.payload["reason"], 0) + 1

    assert by_reason == {
        "debtor declined auto-debit": 3,
        "mandate link never opened (soft refusal)": 1,
    }
    barred = sorted(eid for eid, e in world.ledger.entities.items() if e.mandate_refused)
    assert barred == ["INV-002", "INV-003", "INV-007", "INV-011"]

    # ...and every one of them traces to a real debtor move in the trail
    moves = {a.entity_id: a.detail["move"] for a in world.ledger.audit
             if a.summary.startswith("debtor mandate move:")}
    assert {moves[eid] for eid in barred} == {"refuse_but_promise", "ignore"}


def test_termination_sweep_only_fires_after_the_touch_schedule_is_done(world: WorldRunner):
    swept = [e for e in world.events if e.type == "escalation_exhausted"]
    assert swept, "the termination backstop never ran"
    earliest = min(e.ts for e in swept)
    from engine.integration.runner import SIM_EPOCH

    assert (earliest - SIM_EPOCH).days >= FINAL_SWEEP_DAY


# ---------------------------------------------------------------------------
# (d) the /advance endpoint
# ---------------------------------------------------------------------------


def test_advance_endpoint_moves_the_funnel():
    with TestClient(app) as client:
        assert client.get("/world").json()["day"] == 0
        before = client.get("/funnel").json()
        assert before["recovered_inr"] == 0

        response = client.post("/advance", json={"days": 20})
        assert response.status_code == 200
        body = response.json()

        assert set(body) >= {"day", "new_events", "new_actions", "funnel_summary"}
        assert body["day"] == 20
        assert body["new_events"] > 0
        assert body["new_actions"] > 0

        after = body["funnel_summary"]
        assert after["recovered_inr"] > before["recovered_inr"]
        assert after["states"] != before["states"]
        assert after["messages_sent"] > 0
        assert client.get("/funnel").json() == after
        assert client.get("/world").json()["day"] == 20


def test_advance_defaults_to_one_day_and_keeps_the_old_routes_working():
    with TestClient(app) as client:
        assert client.post("/advance").json()["day"] == 1
        assert client.get("/health").json() == {
            "status": "ok", "invoices_loaded": 60, "reserves_active": 2
        }
        # POST /events still injects manually, on the same ledger the runner uses
        client.post("/events", json={"type": "invoice_triaged", "entity_id": "INV-001", "payload": {}})
        assert client.get("/entities/INV-001").json()["state"] != "NEW"
        assert len(client.get("/audit", params={"limit": 5}).json()) == 5

    with TestClient(app) as client:  # a new lifespan is a clean day 0
        assert client.get("/world").json()["day"] == 0
        assert client.get("/funnel").json()["recovered_inr"] == 0


@pytest.mark.parametrize("days", [0, -1, 400])
def test_advance_rejects_out_of_range_days(days: int):
    with TestClient(app) as client:
        assert client.post("/advance", json={"days": days}).status_code == 422


# ---------------------------------------------------------------------------
# (d2) the human in the loop, inside a real run (packet P9)
# ---------------------------------------------------------------------------


def test_the_45_day_run_really_holds_money_actions_for_a_human(world: WorldRunner):
    """The money gate, exercised by the run rather than by a fixture: the
    heuristic extractor's L3 reads land at confidence 0.78, under the 0.90 gate,
    and every money action they would have triggered is in the queue instead of
    on the wire."""
    held = world.ledger.held_actions
    assert held, "a 45-day run held nothing — the money gate never fired"

    money = [h for h in held if h.sendable]
    assert money and all("money gate" in h.reason for h in money)
    assert {h.action.kind for h in money} <= {"mandate_offer", "link"}

    for hold in held:
        assert hold.action.bounds_checked is False
        assert hold.action.id not in {a.id for a in world.actions}, "a held action reached the wire"
        assert hold.action.params.get("amount_inr") in (
            None, world.ledger.entities[hold.entity_id].invoice_amount_inr,
        ), "law 2 still holds for an action nobody has approved yet"

    # every hold is in the append-only trail, and none of them looks emitted
    logged = {e.detail["held_id"] for e in world.ledger.audit
              if e.summary == "action held for human approval"}
    assert logged == {h.id for h in held}


def test_the_formal_notice_draft_reaches_the_queue_and_never_the_wire(world: WorldRunner):
    """Master doc §3.6's second reason the queue exists. The bound already
    refused to SEND it; without the queue the merchant would simply never see
    the draft, and "compliant escalation" would end in a silence."""
    drafts = [h for h in world.ledger.held_actions if not h.sendable]
    assert drafts, "the ladder reached ESCALATE_3 but no draft reached the merchant"
    for draft in drafts:
        assert draft.action.params["stage"] == "legal"
        assert world.ledger.entities[draft.entity_id].state == "HUMAN_HANDOFF"

    assert not [a for a in world.actions if a.params.get("stage") == "legal"]
    assert not [m for m in world.messenger.queue if "merchant review required" in m.text]


def test_the_runner_never_emits_a_human_resolution_event(world: WorldRunner):
    """The containment half of the terminal-state exception (see
    `tests/test_state_machine.py::ALL_EVENT_TYPES`). The pipeline's event
    vocabulary does not contain it, so the fuzz pool that excludes it is
    modelling the real thing."""
    from engine.judgment import state_machine as sm

    assert sm.HUMAN_RESOLUTION_EVENT not in {e.type for e in world.events}
    assert not [
        a for a in world.ledger.audit if a.summary.startswith("human resolution:")
    ]


def test_no_action_is_ever_constructed_outside_the_ledger(world: WorldRunner):
    """Every Action id in the run — emitted or held — comes out of the ledger's
    single `_action_seq`, so the ids form one gapless sequence. A runner or API
    layer that minted its own would show up here as a duplicate or a gap."""
    ids = [a.id for a in world.actions] + [h.action.id for h in world.ledger.held_actions]
    numbers = sorted(int(i.split("-")[1]) for i in ids)
    assert len(numbers) == len(set(numbers))
    assert numbers == list(range(1, len(numbers) + 1))


def test_pausing_a_thread_stops_its_outreach_across_a_whole_run():
    """The merchant kill-switch, measured against a control run of the same
    world: the paused invoice receives literally nothing for 45 days, while the
    unpaused copy of it is chased normally."""
    control = WorldRunner(real_razorpay=False)
    control.advance(RUN_DAYS)
    entity_id = next(
        eid for eid in control.active_invoice_ids if control.messenger.for_entity(eid)
    )

    paused = WorldRunner(real_razorpay=False)
    paused.ledger.set_paused(entity_id, True, paused.now())
    paused.advance(RUN_DAYS)

    assert control.messenger.for_entity(entity_id), "control run says this invoice IS normally chased"
    assert paused.messenger.for_entity(entity_id) == []
    assert paused.ledger.entities[entity_id].touches == []

    skips = [
        a for a in paused.ledger.audit
        if a.entity_id == entity_id and a.summary.startswith("outreach skipped: thread paused")
    ]
    assert skips, "a paused thread must be visibly quiet in the trail, not silently dead"

    # law 5 still holds: pausing stops outreach, it does not stop TERMINATION
    assert paused.ledger.entities[entity_id].state in TERMINAL_STATES


def test_the_confidence_gates_are_deterministic(world: WorldRunner):
    """CLAUDE.md law 6 covers the gates too: given the heuristic provider, the
    same run holds the same actions for the same reasons, in the same order."""
    second = WorldRunner(real_razorpay=False)
    second.advance(RUN_DAYS)
    assert [(h.id, h.entity_id, h.action.kind, h.reason, h.sendable) for h in second.ledger.held_actions] == [
        (h.id, h.entity_id, h.action.kind, h.reason, h.sendable) for h in world.ledger.held_actions
    ]
    assert second.ledger.clarify_count == world.ledger.clarify_count


def test_a_low_confidence_extraction_reaches_the_clarify_gate_in_the_real_pipeline():
    """The heuristic provider's confidence table bottoms out at 0.78 for the
    levels the runner books as promises, so a 45-day heuristic run produces
    ZERO clarify messages — recorded honestly in tracking/BUILD_QUALITY.md.
    That is a property of one provider's numbers, not of the wiring, so the
    wire is proven here by pushing a genuinely ambiguous read through the real
    runner: real ledger, real bounds, real dispatch, real message on the rail.
    """
    runner = WorldRunner(real_razorpay=False)
    runner.advance(1)
    # ...on a debtor with budget left this week, so the only thing that can
    # decide the outcome here is the confidence gate.
    now = runner.now()
    entity_id = next(
        eid for eid in runner.active_invoice_ids
        if runner.ledger.entities[eid].state not in TERMINAL_STATES
        and sum(1 for t in runner.ledger._debtor_touches(eid)
                if (now - t).days < TOUCH_WINDOW_DAYS) < MAX_TOUCHES_PER_WEEK
    )

    action = runner.ledger.process_event(
        "extraction_received", entity_id,
        {"amount_inr": 40000, "confidence": 0.55, "level": "L4"}, runner.now(),
    )
    assert action is not None and action.params["stage"] == "clarify"
    runner.dispatch_action(action)

    sent = runner.messenger.for_entity(entity_id)[-1]
    assert "which date" in sent.text
    assert f"Rs.{runner.invoices[entity_id].amount_inr:,}" in sent.text, "the LEDGER's figure, not the read one"
    assert runner.ledger.clarify_count[entity_id] == 1

    # ...and the agent will not ask again by itself
    assert runner.ledger.process_event(
        "extraction_received", entity_id,
        {"amount_inr": 40000, "confidence": 0.51, "level": "L4"}, runner.now(),
    ) is None
    assert runner.ledger.pending_held_actions()[-1].reason.startswith("still ambiguous")


# ---------------------------------------------------------------------------
# (e) offline by default
# ---------------------------------------------------------------------------


def test_real_razorpay_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("PK_REAL_RAZORPAY", raising=False)
    assert WorldRunner().real_razorpay is False
    monkeypatch.setenv("PK_REAL_RAZORPAY", "1")
    assert WorldRunner().real_razorpay is True
    monkeypatch.setenv("PK_REAL_RAZORPAY", "0")
    assert WorldRunner().real_razorpay is False


def test_no_network_is_attempted_by_default(monkeypatch):
    """Make every Razorpay entry point explode, then run the world. If a
    single call were attempted, this test would fail loudly.

    Packet P14 widened this to cover gTTS as well. Text-to-speech hits the
    public internet, and the seeded run happens to make no voice notes at all
    (every ESCALATE_2 voice action is refused by the touch cap — see
    tracking/BUILD_LOG.md). That is a fact about the current dataset and cadence,
    not a guarantee, so it is asserted here rather than relied on: a future
    change that starts generating audio during a run would otherwise make the
    whole suite quietly network-dependent.
    """

    def explode(*args, **kwargs):
        raise AssertionError("a Razorpay call was attempted with PK_REAL_RAZORPAY unset")

    def explode_tts(*args, **kwargs):
        raise AssertionError("a gTTS network call was attempted during a plain 45-day run")

    monkeypatch.delenv("PK_REAL_RAZORPAY", raising=False)
    monkeypatch.setattr(razorpay_client, "create_payment_link", explode)
    monkeypatch.setattr(razorpay_client, "create_mandate_registration_link", explode)
    monkeypatch.setattr(razorpay_client, "RazorpayClient", explode)
    monkeypatch.setattr("gtts.gTTS", explode_tts)

    runner = WorldRunner()
    runner.advance(RUN_DAYS)

    assert runner.funnel_summary()["recovered_inr"] > 0
    instruments = [
        e for e in runner.ledger.audit
        if e.layer == "action" and "short_url" in e.detail
    ]
    assert instruments, "no payment instrument was ever dispatched"
    assert all(e.detail["simulated"] is True for e in instruments)
    assert all(str(e.detail["short_url"]).startswith("https://rzp.io/sim/") for e in instruments)
    assert runner.sentinel.dead_letter == []


def test_real_razorpay_budget_is_one_link_and_one_mandate_per_run(monkeypatch):
    """Opt-in mode is rate-limited BY DESIGN: exactly one real payment link and
    one real mandate registration per run, however long the run. Verified with
    fakes so the suite still makes no network calls."""
    calls: list[str] = []

    def fake_link(amount_inr, description, customer):
        calls.append("link")
        return {"id": "plink_FAKE", "short_url": "https://rzp.io/rzp/FAKELINK"}

    def fake_mandate(max_amount_inr, description, customer, method="upi"):
        calls.append("mandate")
        return {"id": "inv_FAKE", "short_url": "https://rzp.io/rzp/FAKEMANDATE"}

    monkeypatch.setattr(razorpay_client, "create_payment_link", fake_link)
    monkeypatch.setattr(razorpay_client, "create_mandate_registration_link", fake_mandate)

    runner = WorldRunner(real_razorpay=True)
    runner.advance(RUN_DAYS)

    assert calls.count("link") == 1
    assert calls.count("mandate") == 1

    real = [e for e in runner.ledger.audit if e.detail.get("simulated") is False]
    urls = {e.detail["short_url"] for e in real}
    assert urls == {"https://rzp.io/rzp/FAKELINK", "https://rzp.io/rzp/FAKEMANDATE"}
    # ...and everything after the budget still went out, on the simulated rail
    simulated = [e for e in runner.ledger.audit
                 if e.layer == "action" and e.detail.get("simulated") is True]
    assert len(simulated) > len(real)


def test_a_failing_razorpay_call_retries_then_dead_letters_then_falls_back(monkeypatch):
    """Sentinel wrap on the real path: retry x3 with backoff, land in the
    dead-letter queue, and keep the run alive on the simulated rail. Nothing
    is lost and nothing is silent (BUILD.md Day 6)."""
    attempts: list[int] = []

    def always_fails(*args, **kwargs):
        attempts.append(1)
        raise razorpay_client.RazorpayError("Razorpay API error 400: synthetic failure", status_code=400)

    monkeypatch.setattr(razorpay_client, "create_payment_link", always_fails)
    monkeypatch.setattr(razorpay_client, "create_mandate_registration_link", always_fails)

    runner = WorldRunner(real_razorpay=True)
    runner.advance(10)

    assert len(attempts) > 0
    assert len(runner.sentinel.dead_letter) > 0
    failures = [e for e in runner.ledger.audit if e.layer == "sentinel" and "Razorpay" in e.summary]
    assert [e.detail["backoff_minutes"] for e in failures][:3] == [1, 5, 15]
    assert any("dead_letter" in e.summary for e in failures)
    # the world kept moving on the simulated rail
    assert runner.funnel_summary()["messages_sent"] > 0
    assert runner.funnel_summary()["recovered_inr"] > 0
