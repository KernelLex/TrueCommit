"""RBI E-Mandate Framework 2026 pre-/post-debit notices (Step 6, 2026-08-27).

Ledger-level tests here mirror tests/test_ledger.py's isolated-Ledger style;
`test_state_machine.py`-style checks confirm the exemption at the bounds
layer directly. Runner-level (full simulated dispatch) tests live in
tests/test_integration.py next to the other INV-001 mandate-lifecycle tests,
reusing that module's existing seeded `world` fixture rather than paying for
a second 45-day run.
"""

import datetime as dt

from engine.judgment import state_machine
from engine.judgment.ledger import Ledger
from engine.judgment.state_machine import EntityState, MAX_TOUCHES_PER_WEEK
from engine.schemas import Invoice

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


def make_invoice(**overrides) -> Invoice:
    base = dict(
        id="INV-951", debtor_id="D-95", amount_inr=55000,
        issued=dt.date(2026, 7, 1), due=dt.date(2026, 8, 13), status="overdue",
        description="test invoice", enach_familiar=True,
    )
    base.update(overrides)
    return Invoice(**base)


# ---------------------------------------------------------------------------
# Ledger.pre_debit_notice / post_debit_notice
# ---------------------------------------------------------------------------


def test_pre_debit_notice_carries_the_ledger_amount_and_execute_date():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(amount_inr=55000))
    action = ledger.pre_debit_notice("INV-951", dt.date(2026, 8, 28), NOW)
    assert action is not None
    assert action.kind == "mandate_pre_debit_notice"
    assert action.params["amount_inr"] == 55000
    assert action.params["execute_on"] == "2026-08-28"
    assert action.bounds_checked is True


def test_pre_debit_notice_uses_the_ledger_amount_not_a_caller_supplied_one():
    """Law 2: mandate amounts are copied from ledger records only. The method
    signature does not even accept an amount — there is nowhere for an
    invented number to enter."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice(amount_inr=72000))
    action = ledger.pre_debit_notice("INV-951", dt.date(2026, 8, 28), NOW)
    assert action.params["amount_inr"] == 72000


def test_pre_debit_notice_returns_none_for_an_unknown_entity():
    ledger = Ledger()
    assert ledger.pre_debit_notice("INV-does-not-exist", dt.date(2026, 8, 28), NOW) is None


def test_post_debit_notice_carries_the_ledger_amount_and_txn_ref():
    ledger = Ledger()
    ledger.register_invoice(make_invoice(amount_inr=55000))
    action = ledger.post_debit_notice("INV-951", "A-00042", NOW)
    assert action is not None
    assert action.kind == "mandate_post_debit_notice"
    assert action.params["amount_inr"] == 55000
    assert action.params["txn_ref"] == "A-00042"


def test_post_debit_notice_returns_none_for_an_unknown_entity():
    ledger = Ledger()
    assert ledger.post_debit_notice("INV-does-not-exist", "A-00042", NOW) is None


def test_pre_and_post_debit_notices_are_audited_before_they_are_returned():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    pre = ledger.pre_debit_notice("INV-951", dt.date(2026, 8, 28), NOW)
    post = ledger.post_debit_notice("INV-951", "A-00042", NOW)
    for action in (pre, post):
        matching = [a for a in ledger.audit if a.detail.get("action_id") == action.id]
        assert len(matching) == 1, "every returned action must have a matching audit entry"


def test_pre_and_post_debit_notices_are_never_blocked_by_an_exhausted_touch_cap():
    """The core claim: these are mandatory disclosures, not discretionary
    outreach, so a merchant's own MAX_TOUCHES_PER_WEEK budget cannot suppress
    them. Spend the whole weekly budget with ordinary outreach first, then
    confirm both notices still go through."""
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    ledger.process_event("invoice_triaged", "INV-951", {}, NOW)
    ledger.process_event("outreach_sent", "INV-951", {"stage": "gentle"}, NOW)
    ledger.process_event("extraction_received", "INV-951", {"amount_inr": 55000}, NOW)
    ledger.process_event("mandate_offer_requested", "INV-951", {}, NOW)
    assert len(ledger.touches_by_debtor["D-95"]) == MAX_TOUCHES_PER_WEEK, \
        "test setup must actually exhaust the budget for this to prove anything"

    pre = ledger.pre_debit_notice("INV-951", dt.date(2026, 8, 28), NOW)
    post = ledger.post_debit_notice("INV-951", "A-00042", NOW)
    assert pre is not None, "pre-debit notice was blocked by an exhausted touch budget"
    assert post is not None, "post-debit notice was blocked by an exhausted touch budget"


def test_pre_and_post_debit_notices_spend_no_touch_budget_themselves():
    ledger = Ledger()
    ledger.register_invoice(make_invoice())
    before = len(ledger.touches_by_debtor.get("D-95", []))
    ledger.pre_debit_notice("INV-951", dt.date(2026, 8, 28), NOW)
    ledger.post_debit_notice("INV-951", "A-00042", NOW)
    after = len(ledger.touches_by_debtor.get("D-95", []))
    assert after == before == 0


# ---------------------------------------------------------------------------
# state_machine.check_bounds: the two kinds are unconditionally allowed
# ---------------------------------------------------------------------------


def _entity(**overrides) -> EntityState:
    base = dict(entity_id="INV-951", state="MANDATED", invoice_amount_inr=55000)
    base.update(overrides)
    return EntityState(**base)


def test_check_bounds_allows_pre_debit_notice_even_from_a_terminal_state():
    """Every OTHER outbound kind is refused once an entity is terminal
    (OUTBOUND_KINDS' own rule). These two kinds are outside that set on
    purpose — even so, pin it directly against the gate itself, not just
    against the runner's own guard, so the exemption is provable at the one
    place CLAUDE.md law 4 says every bound lives."""
    entity = _entity(state="KEPT")
    result = state_machine.check_bounds(entity, "mandate_pre_debit_notice", {"amount_inr": 55000}, NOW)
    assert result.allowed is True


def test_check_bounds_allows_post_debit_notice_even_from_a_terminal_state():
    entity = _entity(state="DISPUTED")
    result = state_machine.check_bounds(entity, "mandate_post_debit_notice", {"amount_inr": 55000}, NOW)
    assert result.allowed is True


def test_check_bounds_allows_these_kinds_even_with_the_weekly_window_full():
    window = [NOW - dt.timedelta(days=1), NOW - dt.timedelta(days=2)]
    entity = _entity(touches=window)
    for kind in ("mandate_pre_debit_notice", "mandate_post_debit_notice"):
        result = state_machine.check_bounds(entity, kind, {"amount_inr": 55000}, NOW, debtor_touches=window)
        assert result.allowed is True, kind


def test_check_bounds_detailed_reports_no_checks_for_these_kinds():
    """The flip side of the dashboard fix in day_story.py: an empty checklist
    from `check_bounds_detailed` means no bound applies at all, which is
    exactly why the Day Story screen renders no guardrail panel for these two
    kinds rather than a misleading empty 'all green' one."""
    entity = _entity()
    for kind in ("mandate_pre_debit_notice", "mandate_post_debit_notice"):
        checks = state_machine.check_bounds_detailed(entity, kind, {"amount_inr": 55000}, NOW)
        assert checks == [], kind
