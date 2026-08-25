# TRACK_BAR.md — The Bar (verbatim)

> "Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."

Status legend: ⬜ not started · 🟡 partial · ✅ done + proof link into the repo. Nothing ships in the video that isn't ✅ here.

---

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
- pytest file that tries to violate each one: `tests/test_state_machine.py` — 8 dedicated bound-violation tests, all passing, plus the dispute-from-any-state (11 starting states × 2 test forms) and 1000-random-sequence termination tests (48 tests total across `test_state_machine.py`/`test_ledger.py`/`test_trust.py`, all green)
- Proof link: `tests/test_state_machine.py`, verified 2026-08-26 (`pytest tests/` → 48 passed)

## 4. Audit trail — 🟡 partial
- Append-only event log implementation: ✅ `Ledger.audit: list[AuditEntry]` in `engine/judgment/ledger.py` — every state transition and every action (allowed or blocked) writes an `AuditEntry` before the action is returned to the caller (`tests/test_ledger.py::test_audit_entry_exists_before_action_is_returned`); not yet persisted to disk/DB, lives in-memory per `Ledger` instance
- Queryable via API: ✅ `GET /entities/{id}/audit` and `GET /audit` in `api/main.py`, backed by the same in-memory `Ledger`, dataset (60 invoices) loaded at startup
- Dashboard timeline screen (entity timeline, master doc §4.3): ⬜ not built (Day 7 scope)
- Proof link: `engine/judgment/ledger.py`, `api/main.py`, `tests/test_ledger.py`, `tests/test_api.py`

---

## Update discipline
Flip a row to ✅ only when the proof link points at something real in the repo (a passing test file, a committed metrics.json, a screenshot in the repo, a running screen). 🟡 is for partial/in-progress — use it rather than leaving ⬜ once work has started.
