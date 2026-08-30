"""Red-team packet (2026-08-30, see `eval/red_team.py`) — mitigation tests.

Four exploit personas are quantified in `eval/red_team.py`; that file's own
module docstring carries the full mitigation classification and reasoning.
Summary: promise-farmer is mitigated NEW this packet (a promise-horizon cap);
mandate-then-revoke turns out to already be mitigated, as a side effect of
Packets 1+2's debtor-wide mandate-refusal bar, verified rather than
re-implemented here; dispute-shield and serial-refuser are honestly
documented as unfixable without weakening a compliance bound (the freeze IS
the fix; the touch cap + the pending-neutral refusal rule are the two
bounds this project will not walk back — an allocation-layer mitigation for
serial-refuser was investigated and found structurally incapable of
reducing total touch count, see `eval/red_team.py`'s docstring and
tracking/BUILD_LOG.md 2026-08-30 for why). See the README section "How this
system can be gamed and what it costs" for the full narrative and the
measured rupee damage.

This file pins the ONE new mitigation (the promise-horizon cap) at the unit
level, independent of the red-team script's own stochastic numbers:
`state_machine.cap_promise_due_day` / `cap_promise_due_date` — a
promise-farmer cannot make itself invisible to both the ladder and the idle
sweep forever by claiming an absurd future due date. It also runs the full
red-team suite end to end for reproducibility.
"""

import datetime as dt

from engine.integration.runner import SIM_EPOCH, WorldRunner
from engine.judgment.ledger import Ledger
from engine.judgment.state_machine import MAX_PROMISE_HORIZON_DAYS
from engine.schemas import Extraction, Invoice

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-RT", debtor_id="D-RT", amount_inr=40000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


# ---------------------------------------------------------------------------
# 1. Promise-horizon cap
# ---------------------------------------------------------------------------


def test_runner_due_day_caps_a_far_future_extracted_date():
    """The operationally meaningful enforcement point: `_due_day()` is what
    `_book_promise()` actually schedules `promise_due` against."""
    runner = WorldRunner(real_razorpay=False, real_tts=False)
    day = 5
    far_future = (SIM_EPOCH + dt.timedelta(days=10_000)).date()
    extraction = Extraction(message_id="m1", level="L4", date=far_future, confidence=0.9)
    due_day = runner._due_day(extraction, day, offset=7)
    assert due_day == day + MAX_PROMISE_HORIZON_DAYS


def test_runner_due_day_leaves_a_near_term_extracted_date_untouched():
    runner = WorldRunner(real_razorpay=False, real_tts=False)
    day = 5
    near = (SIM_EPOCH + dt.timedelta(days=day + 10)).date()
    extraction = Extraction(message_id="m2", level="L4", date=near, confidence=0.9)
    due_day = runner._due_day(extraction, day, offset=7)
    assert due_day == day + 10


def test_ledger_caps_a_directly_injected_far_future_promise():
    """Defense in depth: `_update_promise()` never sees `WorldRunner`'s
    day-integer clock, only an ISO date string in the payload — a promise
    built from a directly-injected event (not the simulator's own scheduling
    path) still gets the same ceiling."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    far_future = (NOW.date() + dt.timedelta(days=10_000)).isoformat()
    ledger.process_event(
        "extraction_received", "INV-RT",
        {"amount_inr": 40000, "invoice_amount_inr": 40000, "due": far_future, "message_id": "m1"},
        NOW,
    )
    promises = [p for p in ledger.promises.values() if p.invoice_id == "INV-RT"]
    assert len(promises) == 1
    assert promises[0].due == NOW.date() + dt.timedelta(days=MAX_PROMISE_HORIZON_DAYS)


def test_ledger_leaves_a_near_term_directly_injected_promise_untouched():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    near = (NOW.date() + dt.timedelta(days=10)).isoformat()
    ledger.process_event(
        "extraction_received", "INV-RT",
        {"amount_inr": 40000, "invoice_amount_inr": 40000, "due": near, "message_id": "m1"},
        NOW,
    )
    promises = [p for p in ledger.promises.values() if p.invoice_id == "INV-RT"]
    assert promises[0].due == NOW.date() + dt.timedelta(days=10)


# ---------------------------------------------------------------------------
# 2. The full red-team suite, run end to end — pins that all four exploits
#    (and the promise-farmer mitigation proof) run cleanly against the real
#    WorldRunner and produce the reproducible, non-trivial numbers the
#    README's "how this system can be gamed" section quotes.
# ---------------------------------------------------------------------------


def test_promise_farmer_mitigation_proof_shows_the_before_and_after():
    """`run_promise_farmer()`'s own 45-day snapshot cannot show the fix
    (MAX_PROMISE_HORIZON_DAYS=60 > the 45-day window) — this is the function
    that actually proves it, by running long enough to see the difference:
    with today's real cap, everyone farmed must resolve; with the cap
    patched back to a pass-through (pre-mitigation behavior), a claim of
    hundreds of days out is still that far out."""
    from eval.red_team import run_promise_farmer_mitigation_proof

    result = run_promise_farmer_mitigation_proof(seed=42, proof_window_days=100)
    assert result["with_cap_non_terminal_count"] == 0
    assert result["without_cap_non_terminal_count"] > 0


def test_red_team_suite_runs_end_to_end_and_is_reproducible():
    from eval.red_team import run_all

    first = run_all(seed=42)
    second = run_all(seed=42)
    assert first == second

    assert first["dispute_shield"]["frozen_zero_touch_handoffs"] > 0
    assert first["promise_farmer"]["non_terminal_count"] > 0
    assert first["serial_refuser"]["total_touches_spent"] > 0
    assert first["mandate_then_revoke"]["debtors_now_barred_from_any_future_mandate"] > 0
