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

## 2. Compliant escalation — ⬜ not started
- State machine stages: not written yet (Phase A, Day 5 scope)
- Merchant-review gate at legal/formal-notice stage (master doc §3.4: "legal threshold → generated formal notice goes to MERCHANT for review, agent NEVER sends legal communication itself"): to be enforced in `state_machine.py`
- Pre-debit reminders (T-1, mirrors RBI norms per master doc §3.6): to be enforced in the mandate-offer/execute flow
- Proof link: —

## 3. Stopping rules — ⬜ not started
- Bounds constants (master doc §3.4): max 2 touches/week, renegotiation cap 2, mandate cap ₹1,00,000, mandate amount must equal ledger invoice amount, 1 retry on execution failure, dispute → instant stop from any state, no mandate re-offer after refusal, legal notices go to merchant not auto-sent — to live as named constants at the top of `engine/judgment/state_machine.py`
- pytest file that tries to violate each one: `tests/test_state_machine.py`, not written yet
- Proof link: —

## 4. Audit trail — ⬜ not started
- Append-only event log implementation: `AuditEntry` schema defined in BUILD.md §2, not yet implemented as a persisted log
- Dashboard timeline screen (entity timeline, master doc §4.3): not built (Day 7 scope)
- Proof link: —

---

## Update discipline
Flip a row to ✅ only when the proof link points at something real in the repo (a passing test file, a committed metrics.json, a screenshot in the repo, a running screen). 🟡 is for partial/in-progress — use it rather than leaving ⬜ once work has started.
