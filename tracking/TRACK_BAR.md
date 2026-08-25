# TRACK_BAR.md — The Bar (verbatim)

> "Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."

Status legend: ⬜ not started · 🟡 partial · ✅ done + proof link into the repo. Nothing ships in the video that isn't ✅ here.

---

## 0. Razorpay sandbox capability baseline (drives the real-vs-simulated table) — ✅ verified 2026-08-26
Live probes with TEST keys (`scripts/verify_razorpay_sandbox.py` → `tracking/razorpay_sandbox_report.json`, 8/8 green):
- **REAL in test mode:** auth · Payment Links (real `short_url`) · Invoices · Customers · plain Orders · **mandate registration links for BOTH UPI Autopay and eMandate** (real registration `short_url`s issued — the crown-jewel rail is live in sandbox)
- **Needs one manual browser step or simulated+labeled:** mandate *execute* (recurring charge against an authorized token) and *revoke* (token delete) — both require a registration a human has authorized in the test checkout first. Plan: authorize one registration manually during demo prep so at least one execute is real; everything else simulated with explicit labels per BUILD.md Day 6.
- **Gotcha for Day-6 wiring:** `POST /orders` silently drops a `token:{}` block — use the `subscription_registration/auth_links` flow, not hand-rolled token orders.

## 1. Measured money recovered across a batch — ⬜ not started
- 3-arm run outputs (Arm A silence / Arm B generic reminder / Arm C full system): not run yet
- `metrics.json`: does not exist yet
- Screenshot: none yet
- Tier1/Tier2 honesty framing (CLAUDE.md §3 law 8): Tier 1 = genuinely measured (extraction accuracy, triage accuracy, false-escalation rate, cost per ₹). Tier 2 = simulated (3-arm recovery ₹), always labeled as simulation, never presented as real-world proof. Framing decided; nothing populated yet.
- Proof link: —

## 2. Compliant escalation — 🟡 partial
- State machine stages: ✅ implemented, `engine/judgment/state_machine.py` (NEW→TRIAGED→ENGAGED→PROMISED→MANDATED/LINKED→AT_RISK→ESCALATE_1..4→terminal, DISPUTED reachable from anywhere)
- Merchant-review gate at legal/formal-notice stage: ✅ enforced — `check_bounds()` blocks any `message` action with `params.stage=="legal"`; `ledger.py`'s escalation handler routes that block to a `human_handoff` action instead of silently dropping it
- Pre-debit reminders (T-1, mirrors RBI norms): ⬜ not yet implemented — needs the real action layer (Phase C, Razorpay wiring)
- Proof link: `engine/judgment/state_machine.py`, `engine/judgment/ledger.py`, `tests/test_state_machine.py::test_bound_legal_stage_never_auto_sent`

## 3. Stopping rules — ✅ done
- Bounds constants (master doc §3.4), all 8, as named constants at the top of `engine/judgment/state_machine.py`: `MAX_TOUCHES_PER_WEEK`, `RENEGOTIATION_CAP`, `MANDATE_AMOUNT_CAP`, mandate-must-equal-ledger-amount (enforced in `check_bounds`, not a constant per se), `RETRY_ON_EXECUTION_FAILURE`, dispute-instant-stop (`TERMINAL_STATES` + `OUTBOUND_KINDS` gating), legal-stage-to-merchant, no-mandate-reoffer-after-refusal (`mandate_refused` flag)
- **Scope of the touch cap = PER DEBTOR**, as CLAUDE.md law 4 and master doc §3.4 word it ("max_touches_per_week = 2 (per debtor/customer)"): `check_bounds()` counts the rolling 7-day window across every entity the debtor holds, taking that window as an argument so the gate stays a pure predicate. Through packet P2 it was enforced per invoice, which let a debtor with five overdue invoices collect six messages a week; fixed in P8 (worst rolling per-debtor window over a 45-day run: 6 → 2, at a real and openly reported cost to the recovery number — see BUILD_QUALITY and BUILD_LOG). Every message the system sends is now Action-backed and touch-counted; there is no outbound copy that skipped the gate.
- pytest file that tries to violate each one: `tests/test_state_machine.py` — 8 dedicated bound-violation tests plus a per-debtor violation test and one violation test per touch kind (a link/mandate offer is a contact too, so the cap can't be dodged by switching instrument), plus the dispute-from-any-state (11 starting states × 2 test forms) and 1000-random-sequence termination tests (62 tests total across `test_state_machine.py`/`test_ledger.py`/`test_trust.py`, all green)
- Proof link: `tests/test_state_machine.py`, `tests/test_ledger.py`, `tests/test_integration.py::test_touch_cap_is_enforced_per_debtor_not_just_per_invoice` (the queue-side proof, recomputed independently of the ledger's own counter), verified 2026-08-26 (`pytest tests/` → 271 passed)

## 4. Audit trail — 🟡 partial
- Append-only event log implementation: ✅ `Ledger.audit: list[AuditEntry]` in `engine/judgment/ledger.py` — every state transition and every action (allowed or blocked) writes an `AuditEntry` before the action is returned to the caller (`tests/test_ledger.py::test_audit_entry_exists_before_action_is_returned`); not yet persisted to disk/DB, lives in-memory per `Ledger` instance
- Queryable via API: ✅ `GET /entities/{id}/audit` and `GET /audit` in `api/main.py`, backed by the same in-memory `Ledger`, dataset (60 invoices) loaded at startup
- Covers the whole pipeline, not just judgment: ✅ since packet P2 the action / sentinel / perception layers write into the SAME append-only trail (`engine/integration/runner.py`, `AD-` id namespace). A 45-day `WorldRunner.advance(45)` produces **1,088 audit entries** across all four layers — triage cause, extraction level + confidence, every debtor move, every dispatch with its rail and copy, every bound block, every link timeout treated as a soft refusal. **156 of those entries are bound blocks** (149 of them the per-debtor touch cap), each carrying the reason and the debtor it protected — the stopping rules are visible in the trail as events, not as absences.
- Contains a REAL Razorpay URL: ✅ verified 2026-08-26 — `PK_REAL_RAZORPAY=1` + `WorldRunner().advance(3)` wrote entry `AD-00080` (`REAL Razorpay test-mode link: https://rzp.io/rzp/K3z43yQ`, `plink_TU8TBK4FoixASK`, `simulated: false`) and `AD-00076` (real mandate registration `https://rzp.io/rzp/wQTfueuU`, `inv_TU8TAWNYG0ztkp`). Opt-in and rate-limited: default = zero network calls.
- Dashboard timeline screen (entity timeline, master doc §4.3): ⬜ not built (Day 7 scope) — this is the only thing keeping this section at 🟡
- Proof link: `engine/judgment/ledger.py`, `engine/integration/runner.py`, `api/main.py`, `tests/test_ledger.py`, `tests/test_api.py`, `tests/test_integration.py`

---

## Update discipline
Flip a row to ✅ only when the proof link points at something real in the repo (a passing test file, a committed metrics.json, a screenshot in the repo, a running screen). 🟡 is for partial/in-progress — use it rather than leaving ⬜ once work has started.
