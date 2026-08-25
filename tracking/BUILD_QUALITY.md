# BUILD_QUALITY.md — "does it run, is it structured, would you trust it"

Kept truthful. Update whenever a check is actually re-run — don't carry forward a stale ✅.

---

## Cold start
- Runs in 2 commands on a stranger's machine: ⬜ not yet possible (backend/dashboard not wired) — last verified: never
- Target commands (per README §, to be finalized Day 9): TBD, likely `make run` or equivalent two-step

## Tests
- Test count passing: 0 / 0 (no tests written yet — Phase A will add `tests/test_state_machine.py` and dataset/simulator determinism checks)
- Last full `pytest` run: not yet run

## Secrets hygiene
- Grep check for `rzp_` / `sk-ant` in tracked files: not yet run (no code committed yet)
- Last check date: —
- `.env` gitignored: ✅ (`.gitignore` created Phase A setup, 2026-08-26)
- `.env.example` contains placeholders only, no real values: ✅ (verified at creation, 2026-08-26)

## Reproducibility
- Two seeded simulator runs diffed (`python -m sim.run --days 45 --seed 42` × 2): ⬜ not yet run (simulator not written)

## BUILD.md acceptance criteria — status
| Day | Criterion | Status |
|---|---|---|
| 1–2 | `sim.run --days 45 --seed 42` replays identically twice | ⬜ |
| 1–2 | ground_truth covers 100% of messages/invoices/carts | ⬜ |
| 1–2 | judge-mode read of 10 random conversations agrees labels are fair | ⬜ |
| 3 | triage accuracy ≥90% | ⬜ blocked on ANTHROPIC_API_KEY |
| 4 | extraction level accuracy ≥85% | ⬜ blocked on ANTHROPIC_API_KEY |
| 5 | every bound has a violation test that fails correctly | ⬜ |
| 5 | dispute from any state → DISPUTED, no further outbound actions | ⬜ |
| 5 | 1000 random event sequences all terminate in KEPT/CLEAN_LOSS/HUMAN_HANDOFF | ⬜ |
| 6 | real test-mode Payment Link URL in audit trail | ⬜ blocked on Razorpay TEST keys |
| 6 | network-kill mid-run → dead-letter, resume works | ⬜ |
| 7 | cold start → dashboard funnel with real data in <60s | ⬜ |
| 7 | Advance-Day visibly moves money/promises/trust on screen | ⬜ |
| 7 | `v1.0-freeze` tag | ⬜ |
| 8 | re-running arms reproduces identical numbers | ⬜ |
| 9 | a friend can run it from README alone | ⬜ |

---

## Phase A progress log
- 2026-08-26: repo skeleton, git init, `.gitignore`, `.env.example`, spec docs (`CLAUDE.md`, `BUILD.md`, master doc) committed to repo root. venv + Python deps installing. Dashboard scaffolded via `npm create vite`.
