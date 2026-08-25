"""Generates data/invoices.json, data/carts.json, data/conversations/*.json,
and data/ground_truth.json (BUILD.md Day 1-2).

Why a generator instead of hand-typed JSON: the ~95 conversation messages and
their ground-truth labels must never drift apart. Writing them as one Python
source of truth (MESSAGE + its label authored in the same literal) makes that
structurally impossible, and the 60 invoices / 12 carts still get real
hand-authored narrative for the ones behind a thread — only the filler
invoices with no conversation are assigned procedurally. This *is* the
hand-labeling: every label below was decided by the same act that wrote the
message it labels, not derived from it after the fact.

Deterministic: SEED=42 (CLAUDE.md law #6). Re-running overwrites identically.
"""

import calendar
import json
import random
from pathlib import Path

import datetime as dt

from engine.schemas import (
    Cart,
    CartItem,
    Invoice,
    Message,
)

SEED = 42
TODAY = dt.date(2026, 8, 26)
ROOT = Path(__file__).resolve().parent
CONV_DIR = ROOT / "conversations"

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def next_weekday(from_date: dt.date, target: int, min_offset: int = 1) -> dt.date:
    """Next date >= from_date + min_offset that falls on `target` weekday."""
    d = from_date + dt.timedelta(days=min_offset)
    while d.weekday() != target:
        d += dt.timedelta(days=1)
    return d


def month_end(from_date: dt.date) -> dt.date:
    last_day = calendar.monthrange(from_date.year, from_date.month)[1]
    end = dt.date(from_date.year, from_date.month, last_day)
    if end <= from_date:
        y, m = (from_date.year + 1, 1) if from_date.month == 12 else (from_date.year, from_date.month + 1)
        last_day = calendar.monthrange(y, m)[1]
        end = dt.date(y, m, last_day)
    return end


def iso(d: dt.date | None) -> str | None:
    return d.isoformat() if d else None


# ---------------------------------------------------------------------------
# Debtors — 12, mapped 2-each onto the 6 simulator personas (sim/personas.py)
# ---------------------------------------------------------------------------

DEBTORS = [
    {"id": "D-01", "name": "Acme Traders", "persona": "reliable_promiser", "enach_familiar": True},
    {"id": "D-02", "name": "Shree Ganesh Textiles", "persona": "reliable_promiser", "enach_familiar": True},
    {"id": "D-03", "name": "Bharat Hardware Co.", "persona": "serial_renegotiator", "enach_familiar": True},
    {"id": "D-04", "name": "Kumar Electricals", "persona": "serial_renegotiator", "enach_familiar": False},
    {"id": "D-05", "name": "Om Sai Enterprises", "persona": "silent_ghost", "enach_familiar": False},
    {"id": "D-06", "name": "Vardhan Packaging", "persona": "silent_ghost", "enach_familiar": False},
    {"id": "D-07", "name": "Silverline Furnishings", "persona": "disputer", "enach_familiar": True},
    {"id": "D-08", "name": "Patel Auto Spares", "persona": "disputer", "enach_familiar": False},
    {"id": "D-09", "name": "Meenakshi Garments", "persona": "cashflow_constrained", "enach_familiar": True},
    {"id": "D-10", "name": "Ravi Steel Works", "persona": "cashflow_constrained", "enach_familiar": False},
    {"id": "D-11", "name": "Nova Digital Solutions", "persona": "adversarial", "enach_familiar": True},
    {"id": "D-12", "name": "Krishna Timber Mart", "persona": "adversarial", "enach_familiar": False},
]
DEBTOR_BY_ID = {d["id"]: d for d in DEBTORS}

PERSONA_CAUSE_WEIGHTS = {
    "reliable_promiser": {"cashflow_delay": 0.7, "payment_failed": 0.2, "non_responsive": 0.1},
    "serial_renegotiator": {"cashflow_delay": 0.5, "payment_failed": 0.4, "dispute": 0.1},
    "silent_ghost": {"non_responsive": 0.8, "cashflow_delay": 0.2},
    "disputer": {"dispute": 0.55, "delivery_dispute": 0.35, "cashflow_delay": 0.1},
    "cashflow_constrained": {"cashflow_delay": 0.85, "payment_failed": 0.15},
    "adversarial": {"cashflow_delay": 0.5, "non_responsive": 0.4, "dispute": 0.1},
}


def weighted_choice(rng: random.Random, weights: dict) -> str:
    items = list(weights.items())
    total = sum(w for _, w in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for k, w in items:
        upto += w
        if r <= upto:
            return k
    return items[-1][0]


def flags_for_cause(rng: random.Random, cause: str) -> tuple[bool, bool]:
    """(delivery_confirmed, payment_failed_attempt) — matches `cause` ~90% of
    the time, occasionally noisy so triage isn't trivially deducible from
    flags alone (a 90%-accuracy gate should mean something)."""
    if cause == "payment_failed":
        pfa, dc = True, True
    elif cause == "delivery_dispute":
        pfa, dc = False, False
    else:
        pfa, dc = False, True
    if rng.random() < 0.1:
        pfa = not pfa
    if rng.random() < 0.1:
        dc = not dc
    return dc, pfa


# ---------------------------------------------------------------------------
# Story invoices: the ones behind a conversation thread. Hand-picked fields.
# amount_inr, days_overdue (>0 overdue, <0 not yet due), cause.
# ---------------------------------------------------------------------------

STORY_INVOICES = {
    "INV-001": dict(debtor="D-01", amount=40000, days_overdue=12, cause="cashflow_delay", desc="Aug supplies — packaging materials"),
    "INV-002": dict(debtor="D-01", amount=22000, days_overdue=6, cause="cashflow_delay", desc="Jul restock — trims and buttons"),
    "INV-006": dict(debtor="D-02", amount=185000, days_overdue=9, cause="cashflow_delay", desc="Loom yarn consignment, Jul batch"),
    "INV-007": dict(debtor="D-02", amount=63000, days_overdue=4, cause="cashflow_delay", desc="Dye lot #44"),
    "INV-011": dict(debtor="D-03", amount=97000, days_overdue=28, cause="payment_failed", desc="Bulk fasteners order #B-220"),
    "INV-012": dict(debtor="D-03", amount=54000, days_overdue=18, cause="cashflow_delay", desc="Power tools restock"),
    "INV-016": dict(debtor="D-04", amount=310000, days_overdue=22, cause="payment_failed", desc="Wiring + switchgear, site 3"),
    "INV-017": dict(debtor="D-04", amount=76000, days_overdue=15, cause="cashflow_delay", desc="Conduit + fittings"),
    "INV-021": dict(debtor="D-05", amount=8500, days_overdue=35, cause="non_responsive", desc="Stationery + printing, June"),
    "INV-022": dict(debtor="D-05", amount=41000, days_overdue=40, cause="non_responsive", desc="Signage order #S-9"),
    "INV-026": dict(debtor="D-06", amount=132000, days_overdue=33, cause="non_responsive", desc="Corrugated box order, Jun batch"),
    "INV-031": dict(debtor="D-07", amount=215000, days_overdue=20, cause="dispute", desc="Custom upholstery — office set"),
    "INV-032": dict(debtor="D-07", amount=89000, days_overdue=25, cause="dispute", desc="Curtain fabric, showroom order"),
    "INV-036": dict(debtor="D-08", amount=58000, days_overdue=14, cause="dispute", desc="Brake pad consignment #BP-77"),
    "INV-041": dict(debtor="D-09", amount=27000, days_overdue=8, cause="cashflow_delay", desc="Zipper + lining stock"),
    "INV-042": dict(debtor="D-09", amount=145000, days_overdue=17, cause="cashflow_delay", desc="Export order deposit balance"),
    "INV-046": dict(debtor="D-10", amount=68000, days_overdue=11, cause="cashflow_delay", desc="MS angle + sheet stock"),
    "INV-047": dict(debtor="D-10", amount=450000, days_overdue=5, cause="cashflow_delay", desc="Structural steel, godown project"),
    "INV-051": dict(debtor="D-11", amount=124000, days_overdue=31, cause="cashflow_delay", desc="Annual SaaS licence renewal"),
    "INV-052": dict(debtor="D-11", amount=39000, days_overdue=19, cause="non_responsive", desc="API integration retainer, Jul"),
    "INV-056": dict(debtor="D-12", amount=61000, days_overdue=26, cause="cashflow_delay", desc="Plywood + veneer stock"),
    "INV-057": dict(debtor="D-12", amount=93000, days_overdue=44, cause="non_responsive", desc="Teak consignment #T-15"),
    "INV-003": dict(debtor="D-01", amount=31000, days_overdue=7, cause="cashflow_delay", desc="Sep restock — thread and interlining"),
    "INV-043": dict(debtor="D-09", amount=19000, days_overdue=10, cause="cashflow_delay", desc="Elastic + hook stock"),
}

INVOICE_STATUS_FOR_CAUSE = {
    "dispute": "disputed",
    "delivery_dispute": "disputed",
}


def build_invoice(rng: random.Random, inv_id: str, debtor_id: str, amount: int, days_overdue: int,
                   cause: str, desc: str) -> tuple[Invoice, str]:
    due = TODAY - dt.timedelta(days=days_overdue)
    issued = due - dt.timedelta(days=30)
    status = INVOICE_STATUS_FOR_CAUSE.get(cause, "open" if days_overdue < 0 else "overdue")
    dc, pfa = flags_for_cause(rng, cause)
    debtor = DEBTOR_BY_ID[debtor_id]
    inv = Invoice(
        id=inv_id, debtor_id=debtor_id, amount_inr=amount, issued=issued, due=due,
        status=status, description=desc, delivery_confirmed=dc,
        payment_failed_attempt=pfa, enach_familiar=debtor["enach_familiar"],
    )
    return inv, cause


def build_all_invoices(rng: random.Random) -> tuple[list[Invoice], dict[str, str]]:
    invoices: list[Invoice] = []
    causes: dict[str, str] = {}

    for inv_id, spec in STORY_INVOICES.items():
        inv, cause = build_invoice(rng, inv_id, spec["debtor"], spec["amount"], spec["days_overdue"], spec["cause"], spec["desc"])
        invoices.append(inv)
        causes[inv_id] = cause

    story_count_per_debtor = {d["id"]: 0 for d in DEBTORS}
    for spec in STORY_INVOICES.values():
        story_count_per_debtor[spec["debtor"]] += 1

    next_num = 61  # filler ids continue past the highest story id number, reindexed at the end
    AGE_BUCKETS = [-10, -3, 3, 8, 15, 25, 40, 55, 75]
    DESC_POOL = [
        "Raw material restock", "Monthly service retainer", "Packaging consignment",
        "Spare parts order", "Freight + handling", "Bulk stationery order",
        "Equipment rental, Q3", "Fabric/material lot", "Finishing job #{}",
        "Component supply, batch {}",
    ]

    filler_rows = []
    for d in DEBTORS:
        need = 5 - story_count_per_debtor[d["id"]]
        for _ in range(need):
            filler_rows.append(d["id"])

    for debtor_id in filler_rows:
        debtor = DEBTOR_BY_ID[debtor_id]
        cause = weighted_choice(rng, PERSONA_CAUSE_WEIGHTS[debtor["persona"]])
        amount = int(rng.choice([
            rng.randint(8_000, 30_000),
            rng.randint(30_000, 120_000),
            rng.randint(120_000, 450_000),
        ]))
        days_overdue = rng.choice(AGE_BUCKETS)
        desc_template = rng.choice(DESC_POOL)
        desc = desc_template.format(rng.randint(10, 99)) if "{}" in desc_template else desc_template
        inv_id = f"INV-{next_num:03d}"
        next_num += 1
        inv, cause = build_invoice(rng, inv_id, debtor_id, amount, days_overdue, cause, desc)
        invoices.append(inv)
        causes[inv_id] = cause

    return invoices, causes


# ---------------------------------------------------------------------------
# Carts (Scene 2) — 12, hand-authored, 2 with reserve_active
# ---------------------------------------------------------------------------

def build_carts() -> tuple[list[Cart], dict[str, str]]:
    rows = [
        # id, customer, amount, items, drop_stage, signals, ts_offset_days, reserve_active, cause
        ("C-01", "CUST-01", 2499, [("SKU-SHIRT-M", "Cotton Shirt - M", 1, 2499)], "payment", ["otp_fail", "otp_fail"], -1, False, "friction"),
        ("C-02", "CUST-02", 8990, [("SKU-SHOE-42", "Running Shoes - 42", 1, 8990)], "payment", ["upi_intent_timeout"], -2, False, "friction"),
        ("C-03", "CUST-03", 15499, [("SKU-BAG-01", "Backpack", 1, 15499)], "summary", ["viewed_shipping_fee", "left_after_shipping_shown"], -3, False, "price_shock"),
        ("C-04", "CUST-04", 3200, [("SKU-KURTA-L", "Kurta - L", 1, 3200)], "address", ["viewed_shipping_fee"], -1, False, "price_shock"),
        ("C-05", "CUST-05", 2499, [("SKU-SHIRT-M", "Cotton Shirt - M", 1, 2499)], "summary", ["salary_mentioned_in_support_chat"], -2, False, "timing"),
        ("C-06", "CUST-06", 47990, [("SKU-TV-43", "43-inch TV", 1, 47990)], "payment", ["compared_prices_other_tab", "returned_after_2h"], -4, False, "comparison"),
        ("C-07", "CUST-07", 2499, [("SKU-SHIRT-M", "Cotton Shirt - M", 1, 2499)], "payment", ["first_time_buyer", "no_saved_card", "cod_unavailable_pincode"], -1, False, "trust"),
        ("C-08", "CUST-08", 6499, [("SKU-WATCH-01", "Analog Watch", 1, 6499)], "payment", ["first_time_buyer", "reviews_page_revisited_3x"], -2, False, "trust"),
        ("C-09", "CUST-09", 1899, [("SKU-TEE-S", "Graphic Tee - S", 1, 1899)], "payment", ["card_declined_insufficient_funds"], -1, True, "friction"),
        ("C-10", "CUST-10", 5250, [("SKU-JEANS-32", "Jeans - 32", 1, 5250)], "payment", ["bank_server_error"], -2, True, "friction"),
        ("C-11", "CUST-11", 22990, [("SKU-JACKET-L", "Winter Jacket - L", 1, 22990)], "summary", ["no_signal_low_activity"], -6, False, "unknown"),
        ("C-12", "CUST-12", 3990, [("SKU-CAP-01", "Cap Combo", 2, 1995)], "address", ["pincode_serviceability_check_failed_then_retried"], -3, False, "unknown"),
    ]
    carts, causes = [], {}
    for cid, cust, amount, items, stage, signals, day_off, reserve, cause in rows:
        ts = dt.datetime.combine(TODAY + dt.timedelta(days=day_off), dt.time(hour=19, minute=30))
        cart = Cart(
            id=cid, customer_id=cust, amount_inr=amount,
            items=[CartItem(sku=s, name=n, qty=q, price_inr=p) for s, n, q, p in items],
            drop_stage=stage, drop_signals=signals, ts=ts, reserve_active=reserve,
        )
        carts.append(cart)
        causes[cid] = cause
    return carts, causes


# ---------------------------------------------------------------------------
# Conversation threads (Scene 1) — hand-authored, ground truth attached inline
# level ladder (see tracking/DECISIONS.md for why these 5):
#   L1 firm + unconditional (explicit amount AND date)
#   L2 firm but partially specific (only date OR only amount explicit)
#   L3 conditional OR structured/partial (can't be captured by one amount+date
#      pair alone — either contingent on a stated external condition, or a
#      split/partial-payment offer; `condition` carries the qualifying detail
#      either way)
#   L4 vague/soft acknowledgment (no concrete amount/date)
#   L5 no commitment (silence-equivalent / deflection / dispute / refusal)
# Only INBOUND messages get a ground-truth Extraction — outbound are agent
# drafts, nothing to extract.
# ---------------------------------------------------------------------------

def build_threads() -> list[dict]:
    threads = []

    def add(thread_id, inv_id, channel, msgs):
        debtor_id = STORY_INVOICES[inv_id]["debtor"] if inv_id.startswith("INV-0") and inv_id in STORY_INVOICES else None
        threads.append({"thread_id": thread_id, "invoice_id": inv_id, "debtor_id": debtor_id, "channel": channel, "msgs": msgs})

    d0 = TODAY

    # --- T-01 Reliable Promiser, Hinglish, clean L1 (the master doc's own worked example) ---
    fri = next_weekday(d0, FRI)
    add("T-01", "INV-001", "wa", [
        ("out", "Hi Acme Traders, invoice INV-001 for Rs.40,000 is now 12 days overdue. Could you let us know when we can clear it?", None),
        ("in", "boss month end tight, will clear 40k by Friday pakka", dict(level="L1", amount_inr=40000, date=iso(fri), condition=None)),
        ("out", "Understood. I can set up a one-time mandate so Rs.40,000 auto-debits Friday — no need to remember. Want that, or will you pay manually?", None),
        ("in", "haan set it up, easier for me", dict(level="L1", amount_inr=40000, date=iso(fri), condition=None)),
        ("out", "Done — mandate registration link sent. You'll get a reminder the day before.", None),
    ])

    # --- T-02 Reliable Promiser, refuses mandate but promises manually (still L1) ---
    mon = next_weekday(d0, MON, min_offset=3)
    add("T-02", "INV-002", "wa", [
        ("out", "Reminder: INV-002 (Rs.22,000) is 6 days overdue.", None),
        ("in", "will pay 22000 by Monday, but I don't do auto-debit setups, I'll just transfer manually", dict(level="L1", amount_inr=22000, date=iso(mon), condition=None)),
        ("out", "No problem — manual is fine. We'll send a reminder Sunday evening and confirm once it lands.", None),
        ("in", "sounds good", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-03 Reliable Promiser, L2: date explicit, amount not restated ---
    me = month_end(d0)
    add("T-03", "INV-006", "email", [
        ("out", "Invoice INV-006 (Rs.1,85,000) is 9 days past due. Please advise on timing.", None),
        ("in", "We will clear the outstanding by month end, cash flow is tied up in a large receivable that's due any day now.", dict(level="L2", amount_inr=None, date=iso(me), condition=None)),
        ("out", "Noted, thank you. We'll check in closer to month end.", None),
        ("in", "Appreciate it.", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-04 Reliable Promiser, quick clean L1, kept-path flavor ---
    thu = next_weekday(d0, THU)
    add("T-04", "INV-007", "wa", [
        ("out", "INV-007, Rs.63,000, 4 days overdue — any update?", None),
        ("in", "yes paying 63000 this Thursday, slight delay from our side sorry", dict(level="L1", amount_inr=63000, date=iso(thu), condition=None)),
        ("out", "Thanks for the heads up, Thursday works.", None),
    ])

    # --- T-05 Serial Renegotiator, CONTRADICTING thread ---
    add("T-05", "INV-011", "wa", [
        ("out", "INV-011 (Rs.97,000) is now 28 days overdue — this is our third follow-up.", None),
        ("in", "this is already paid, check again", dict(level="L5", amount_inr=None, date=None, condition=None)),
        ("out", "Our records show no payment received against INV-011 — could you share a UTR/reference number?", None),
        ("in", "actually let me check with accounts, might not have gone through", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("in", "ok it didn't go through, will redo the transfer for 97000 by next Wednesday", dict(level="L1", amount_inr=97000, date=iso(next_weekday(d0, WED, min_offset=2)), condition=None)),
    ])

    # --- T-06 Serial Renegotiator, second broken promise -> renegotiation ---
    add("T-06", "INV-012", "wa", [
        ("out", "Following up on the Rs.54,000 you said you'd clear last week for INV-012 — we haven't received it.", None),
        ("in", "sorry missed it, can I do half now and half in 2 weeks?", dict(level="L3", amount_inr=27000, date=None, condition="half (27000) now, remaining half in ~2 weeks — exact date not given")),
        ("out", "That works — please send the first Rs.27,000 and confirm a firm date for the balance.", None),
        ("in", "sending 27000 today, rest by the 15th", dict(level="L3", amount_inr=27000, date=None, condition="Rs.27,000 (this tranche) sent today; remaining Rs.27,000 due by the 15th")),
    ])

    # --- T-07 Serial Renegotiator, payment_failed narrative ---
    add("T-07", "INV-016", "email", [
        ("out", "INV-016 (Rs.3,10,000) is 22 days overdue, please confirm status.", None),
        ("in", "We initiated a transfer of 310000 on the 10th but it seems to have bounced, bank flagged a mismatch. Redoing it by this Friday.", dict(level="L1", amount_inr=310000, date=iso(next_weekday(d0, FRI)), condition=None)),
        ("out", "Understood, technical issues happen — please share the new UTR once sent.", None),
        ("in", "will do", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-08 Serial Renegotiator, vague then firms up ---
    add("T-08", "INV-017", "wa", [
        ("out", "INV-017, Rs.76,000, 15 days overdue.", None),
        ("in", "trying my best on this one, things are tight right now", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Understood. Can you give us a rough date so we can plan?", None),
        ("in", "ok let's say 76000 by the 5th of next month", dict(level="L1", amount_inr=76000, date=iso(dt.date(d0.year + (1 if d0.month == 12 else 0), (d0.month % 12) + 1, 5)), condition=None)),
    ])

    # --- T-09 Silent Ghost, single reminder, zero reply ---
    add("T-09", "INV-021", "wa", [
        ("out", "Hi Om Sai Enterprises, invoice INV-021 (Rs.8,500) is 35 days overdue. Please respond at your earliest.", None),
    ])

    # --- T-10 Silent Ghost, brief non-committal then silence ---
    add("T-10", "INV-022", "wa", [
        ("out", "INV-022 (Rs.41,000) is 40 days overdue, no response to our last 2 messages.", None),
        ("in", "ok", dict(level="L5", amount_inr=None, date=None, condition=None)),
        ("out", "Can you give us a date we can expect payment by?", None),
    ])

    # --- T-11 Silent Ghost, single reminder, zero reply ---
    add("T-11", "INV-026", "email", [
        ("out", "Invoice INV-026 (Rs.1,32,000) is 33 days overdue — this is our second notice. Please respond.", None),
    ])

    # --- T-12 Disputer, clean example ---
    add("T-12", "INV-031", "wa", [
        ("out", "INV-031 (Rs.2,15,000) is 20 days overdue.", None),
        ("in", "we're not paying this, the upholstery set arrived with 3 damaged panels, raised this with your team weeks ago", dict(level="L5", amount_inr=None, date=None, condition=None)),
        ("out", "I'm sorry to hear that — logging this as a dispute and pulling the delivery/quality records now. Someone from our team will reach out with next steps, no further payment reminders will go out on this invoice until it's resolved.", None),
        ("in", "fine, waiting to hear back", dict(level="L5", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-13 Disputer, escalates to formal dispute ---
    add("T-13", "INV-032", "email", [
        ("out", "INV-032 (Rs.89,000) is 25 days overdue, please advise.", None),
        ("in", "This fabric lot does not match the sample approved in March. We are disputing this invoice in full and considering a return.", dict(level="L5", amount_inr=None, date=None, condition=None)),
        ("out", "Understood, marking this as disputed and routing to our team with the full order history for review.", None),
    ])

    # --- T-14 Disputer, baseless dispute (delivery_confirmed=True) ---
    add("T-14", "INV-036", "wa", [
        ("out", "INV-036 (Rs.58,000) is 14 days overdue.", None),
        ("in", "not paying, we never received this order", dict(level="L5", amount_inr=None, date=None, condition=None)),
        ("out", "Our delivery record shows INV-036 was signed for on receipt — I'll attach the POD and have this reviewed as a dispute.", None),
        ("in", "send the POD then, will check internally", dict(level="L5", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-15 Cashflow-Constrained, CONDITIONAL promise (L3) ---
    add("T-15", "INV-041", "wa", [
        ("out", "INV-041 (Rs.27,000) is 8 days overdue.", None),
        ("in", "we'll clear this once our client's payment comes in, they said it's processing — should be this week hopefully", dict(level="L3", amount_inr=27000, date=None, condition="contingent on debtor's own client payment clearing, expected 'this week' but unconfirmed")),
        ("out", "Understood — please keep us posted, and let us know the moment that clears.", None),
    ])

    # --- T-16 Cashflow-Constrained, PARTIAL-PAYMENT offer ---
    add("T-16", "INV-042", "wa", [
        ("out", "INV-042 (Rs.1,45,000) is 17 days overdue, this is a large one — can we get an update?", None),
        ("in", "cash is tight, I can send 45000 now and the remaining 100000 by month end", dict(level="L3", amount_inr=45000, date=iso(me), condition="partial payment: Rs.45,000 now, remaining Rs.1,00,000 by month end")),
        ("out", "That works — please send the Rs.45,000 today and we'll follow up on the balance closer to month end.", None),
        ("in", "sending now", dict(level="L2", amount_inr=45000, date=None, condition=None)),
    ])

    # --- T-17 Cashflow-Constrained, vague hedging L4, long back and forth ---
    add("T-17", "INV-046", "wa", [
        ("out", "INV-046 (Rs.68,000) is 11 days overdue.", None),
        ("in", "it's on our radar, just going through a slow patch", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Understood — can you give us even a rough week to target?", None),
        ("in", "maybe next week, can't promise exactly", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("in", "ok let's commit to 68000 by next Tuesday", dict(level="L1", amount_inr=68000, date=iso(next_weekday(d0, TUE, min_offset=4)), condition=None)),
    ])

    # --- T-18 Cashflow-Constrained, L2 -> L1 progression ---
    add("T-18", "INV-047", "email", [
        ("out", "INV-047 (Rs.4,50,000) is 5 days overdue — flagging early given the amount.", None),
        ("in", "We expect to clear this by the second week of next month.", dict(level="L2", amount_inr=None, date=iso(dt.date(d0.year + (1 if d0.month == 12 else 0), (d0.month % 12) + 1, 10)), condition=None)),
        ("out", "Thanks — could you confirm an exact date closer to then so we can plan cash flow on our end too?", None),
        ("in", "Let's fix it at the 10th, Rs.4,50,000 in full.", dict(level="L1", amount_inr=450000, date=iso(dt.date(d0.year + (1 if d0.month == 12 else 0), (d0.month % 12) + 1, 10)), condition=None)),
    ])

    # --- T-19 Adversarial, strings along, never firms up ---
    add("T-19", "INV-051", "wa", [
        ("out", "INV-051 (Rs.1,24,000) is 31 days overdue, this has gone unanswered a few times now.", None),
        ("in", "yeah sorry, been swamped, will sort it soon", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Can you give us a specific date, even a rough one?", None),
        ("in", "let's say this week", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Which day this week works for the full Rs.1,24,000?", None),
        ("in", "will confirm by tomorrow", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-20 Adversarial, vague, deflects to a new excuse ---
    add("T-20", "INV-052", "wa", [
        ("out", "INV-052 (Rs.39,000) is 19 days overdue.", None),
        ("in", "our accountant is out this week, will handle it once she's back", dict(level="L4", amount_inr=None, date=None, condition="contingent on accountant returning — no date given for return")),
        ("out", "Understood — when is she expected back?", None),
        ("in", "should be soon, not 100% sure", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("in", "let's just say check with us next week", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-21 Adversarial, agrees to a mandate then never actually confirms specifics ---
    add("T-21", "INV-056", "wa", [
        ("out", "INV-056 (Rs.61,000) is 26 days overdue.", None),
        ("in", "yes we intend to pay, just need a few more days", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Would a payment link work better, or would you prefer we set a reminder for a specific date?", None),
        ("in", "link is fine I guess, will use it when ready", dict(level="L4", amount_inr=None, date=None, condition=None)),
        ("out", "Sent the payment link — let us know if you hit any issues.", None),
    ])

    # --- T-22 Adversarial, breaks an explicit promise, re-promises (tests renegotiation cap) ---
    add("T-22", "INV-057", "email", [
        ("out", "INV-057 (Rs.93,000) is 44 days overdue — please respond, this has been outstanding a long time.", None),
        ("in", "Apologies for the delay. We will process Rs.93,000 by this Friday without fail.", dict(level="L1", amount_inr=93000, date=iso(next_weekday(d0, FRI)), condition=None)),
        ("out", "Following up — Friday's date passed without payment landing, could you update us?", None),
        ("in", "Sorry, ran into an unexpected issue. Will process it early next week instead.", dict(level="L2", amount_inr=None, date=iso(next_weekday(d0, MON, min_offset=8)), condition=None)),
        ("in", "To be safe, let's say Rs.93,000 by next Wednesday.", dict(level="L1", amount_inr=93000, date=iso(next_weekday(d0, WED, min_offset=9)), condition=None)),
    ])

    # --- T-23 Reliable Promiser, clean L2: amount explicit, date vague-only ---
    add("T-23", "INV-003", "wa", [
        ("out", "INV-003 (Rs.31,000) is 7 days overdue.", None),
        ("in", "will clear the full 31000, just need a little more time, maybe early next week", dict(level="L2", amount_inr=31000, date=None, condition=None)),
        ("out", "That's fine — let us know once you have a firmer date.", None),
        ("in", "will do, thanks for the patience", dict(level="L4", amount_inr=None, date=None, condition=None)),
    ])

    # --- T-24 Cashflow-Constrained, second clean L3 conditional example ---
    add("T-24", "INV-043", "wa", [
        ("out", "INV-043 (Rs.19,000) is 10 days overdue.", None),
        ("in", "we can pay as soon as our fabric supplier refunds an overcharge, they confirmed it's coming but no date yet", dict(level="L3", amount_inr=19000, date=None, condition="contingent on a pending refund from debtor's own supplier, timing unconfirmed")),
        ("out", "Understood, please update us as soon as that refund lands.", None),
    ])

    return threads


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def main() -> None:
    rng = random.Random(SEED)

    invoices, invoice_causes = build_all_invoices(rng)
    carts, cart_causes = build_carts()
    threads = build_threads()

    ROOT.mkdir(parents=True, exist_ok=True)
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    for old in CONV_DIR.glob("*.json"):
        old.unlink()

    (ROOT / "invoices.json").write_text(
        json.dumps([json.loads(inv.model_dump_json()) for inv in invoices], indent=2), encoding="utf-8"
    )
    (ROOT / "carts.json").write_text(
        json.dumps([json.loads(c.model_dump_json()) for c in carts], indent=2), encoding="utf-8"
    )

    message_gt: dict[str, dict] = {}
    total_messages = 0
    for t in threads:
        base_ts = dt.datetime.combine(TODAY, dt.time(hour=10, minute=0))
        out_msgs = []
        for i, (direction, text, gt) in enumerate(t["msgs"]):
            mid = f"M-{t['thread_id'][2:]}-{i + 1}"
            ts = base_ts + dt.timedelta(days=i, hours=(i % 3) * 3)
            msg = Message(
                id=mid, thread_id=t["thread_id"], direction=direction,
                channel=t["channel"], text=text, ts=ts,
            )
            out_msgs.append(json.loads(msg.model_dump_json()))
            total_messages += 1
            if direction == "in":
                assert gt is not None, f"{mid} is inbound but has no ground truth"
                message_gt[mid] = {"level": gt["level"], "amount_inr": gt["amount_inr"], "date": gt["date"], "condition": gt["condition"]}
        (CONV_DIR / f"{t['thread_id']}.json").write_text(
            json.dumps({"thread_id": t["thread_id"], "invoice_id": t["invoice_id"], "channel": t["channel"], "messages": out_msgs}, indent=2),
            encoding="utf-8",
        )

    ground_truth = {
        "messages": message_gt,
        "invoices": {k: {"cause": v} for k, v in invoice_causes.items()},
        "carts": {k: {"cause": v} for k, v in cart_causes.items()},
    }
    (ROOT / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    print(f"invoices: {len(invoices)}  (story={len(STORY_INVOICES)}, filler={len(invoices) - len(STORY_INVOICES)})")
    print(f"carts: {len(carts)}  (reserve_active={sum(1 for c in carts if c.reserve_active)})")
    print(f"threads: {len(threads)}  messages: {total_messages}  (labeled inbound: {len(message_gt)})")
    levels = {}
    for v in message_gt.values():
        levels[v["level"]] = levels.get(v["level"], 0) + 1
    print(f"level distribution: {levels}")


if __name__ == "__main__":
    main()
