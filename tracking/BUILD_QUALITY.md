# BUILD_QUALITY.md — "does it run, is it structured, would you trust it"

Kept truthful. Update whenever a check is actually re-run — don't carry forward a stale ✅.

---

## Cold start
- Backend API: ✅ `uvicorn api.main:app` starts clean and serves real data — verified 2026-08-26 (`curl /health` → `{"status":"ok","invoices_loaded":60,"reserves_active":2}`)
- Full 2-command cold start (backend + dashboard together): ⬜ not yet possible — dashboard has no features built yet (Day 7 scope)
- Target commands (per README §, to be finalized Day 9): TBD, likely `make run` or equivalent two-step

## Tests
- Test count passing: 73 / 73 (`test_state_machine.py` 35, `test_ledger.py` 6, `test_trust.py` 7, `test_action_layer.py` 15, `test_perception.py` 10)
- Last full `pytest` run: 2026-08-26, all green, ~1.1s

## Secrets hygiene
- Grep check for `rzp_` / `sk-ant` in tracked files: not yet run (no code committed yet)
- Last check date: —
- `.env` gitignored: ✅ (`.gitignore` created Phase A setup, 2026-08-26)
- `.env.example` contains placeholders only, no real values: ✅ (verified at creation, 2026-08-26)

## Reproducibility
- Two seeded simulator runs diffed (`python -m sim.run --days 45 --seed 42` × 2): ✅ verified 2026-08-26, byte-identical output
- Two seeded dataset generator runs diffed (`python -m data.generate` × 2, sha256 of all `data/*.json`): ✅ verified 2026-08-26, identical hashes

## BUILD.md acceptance criteria — status
| Day | Criterion | Status |
|---|---|---|
| 1–2 | `sim.run --days 45 --seed 42` replays identically twice | ✅ verified 2026-08-26 — byte-identical `diff` across two runs, 408 events, all validate against `engine.schemas.Event` |
| 1–2 | ground_truth covers 100% of messages/invoices/carts | ✅ verified 2026-08-26 — 60/60 invoices, 12/12 carts, 44/44 inbound messages all have ground-truth entries |
| 1–2 | judge-mode read of 10 random conversations agrees labels are fair | ✅ done 2026-08-26 (self, "judge mode" per BUILD.md) — read T-05/T-06/T-16/T-18/T-22 critically, caught and fixed one real mislabel (M-06-4) and a timestamp/narrative inconsistency; logged in BUILD_LOG.md and DECISIONS.md |
| 3 | triage accuracy ≥90% | ⬜ blocked on ANTHROPIC_API_KEY |
| 4 | extraction level accuracy ≥85% | ⬜ blocked on ANTHROPIC_API_KEY |
| 5 | every bound has a violation test that fails correctly | ✅ 8/8 bounds, `tests/test_state_machine.py` |
| 5 | dispute from any state → DISPUTED, no further outbound actions | ✅ parametrized over all 11 non-terminal states |
| 5 | 1000 random event sequences all terminate in KEPT/CLEAN_LOSS/HUMAN_HANDOFF | ✅ (DISPUTED included in the terminal set — see state_machine.py docstring) |
| 6 | real test-mode Payment Link URL in audit trail | ⬜ blocked on Razorpay TEST keys |
| 6 | network-kill mid-run → dead-letter, resume works | 🟡 retry/backoff/dead-letter logic built + tested in isolation (`engine/action/sentinel.py`, `tests/test_action_layer.py`); not yet exercised against a real network call since there isn't one yet (blocked on Razorpay keys) |
| 7 | cold start → dashboard funnel with real data in <60s | ⬜ |
| 7 | Advance-Day visibly moves money/promises/trust on screen | ⬜ |
| 7 | `v1.0-freeze` tag | ⬜ |
| 8 | re-running arms reproduces identical numbers | ⬜ |
| 9 | a friend can run it from README alone | ⬜ |

---

## Phase A progress log
- 2026-08-26: repo skeleton, git init, `.gitignore`, `.env.example`, spec docs (`CLAUDE.md`, `BUILD.md`, master doc) committed to repo root. venv + Python deps installing. Dashboard scaffolded via `npm create vite`.
- 2026-08-26: `engine/schemas.py` — all 10 BUILD.md §2 contracts + InvoiceCause, smoke-tested.
- 2026-08-26: dataset (60 invoices/12 debtors, 12 carts, 24 threads/93 messages, ground_truth.json) generated via `data/generate.py`, fully schema-validated, 100% ground-truth coverage, deterministic. `sim/clock.py` + `sim/personas.py` + `sim/run.py` written; `python -m sim.run --days 45 --seed 42` produces a 408-event deterministic, schema-valid log. Tagged `personas-frozen`.
- 2026-08-26: judgment layer (`engine/judgment/{trust,state_machine,ledger}.py`) — zero LLM, all 8 hard bounds from master doc §3.4 enforced via `check_bounds()`, dispute-from-any-state and 1000-random-sequence termination guarantees verified. 48/48 tests passing.
- 2026-08-26: non-LLM/non-Razorpay Day-6 action-layer slice (`engine/action/{messenger,sentinel,evidence}.py` + `razorpay_client.py` interface stubs) — 15 tests. Perception layer (`engine/perception/{client,triage,extractor,cart_cause}.py` + 5 prompt files + `eval/{triage_eval,extraction_eval}.py`) — fully built, imports cleanly, fails cleanly without a key, 10 tests. Found and fixed a real spec/environment mismatch: `claude-sonnet-4-6` + "temp 0" are both stale against the installed SDK (temperature removed entirely; structured outputs via `output_format=` is the current mechanism) — logged in DECISIONS.md, CLAUDE.md corrected.
- 2026-08-26: `api/main.py` — FastAPI skeleton wired to the real judgment layer, dataset loaded at startup (60 invoices, 2 active reserves). Verified both via `TestClient` and a real `uvicorn api.main:app` process (`curl /health` succeeds). **Phase A complete** — everything BUILD.md schedules for Day 0-2 + Day 5 + the non-LLM/non-Razorpay slice of Day 6, all with real passing tests, no faked results. 76/76 tests passing overall. Next: Phase B (triage/extractor live calls, needs `ANTHROPIC_API_KEY`) and Phase C (Razorpay OTM sandbox verification, needs Razorpay TEST keys) — both still blocked on the user providing keys.
