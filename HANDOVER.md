# HANDOVER.md — Claude-to-Claude session handover
### For the next Claude session/account picking up Promise Keeper. Read this FIRST, then follow the read order below. Last updated: 2026-08-26.

---

## 0. WHAT THIS PROJECT IS (30 seconds)

**Promise Keeper** — Razorpay AI Buildathon 2026, Track 3 (AI Revenue Recovery). **Feature freeze Sep 1 · submit Sep 5.** It's a hiring competition: Razorpay engineers read this repo deciding whether to interview the author. Honesty and code quality matter as much as features.

**Thesis:** recovery tools send messages; messages ask the customer to decide again later, colder. Promise Keeper converts stated intent into **self-executing payment instruments** (one-time mandates on Razorpay rails) and falls back to messages only when there's no commitment to capture. Two scenes, one engine: B2B overdue invoices (deep) + checkout drop-off recovery (breadth).

**Main repo:** https://github.com/KernelLex/TrueCommit.git (remote `origin`, branch `master`). Local working dir: `c:\Users\amogh\OneDrive\Desktop\razzerpay`.

## 1. MANDATORY READ ORDER (before doing anything)

1. `CLAUDE.md` — the 8 design laws + tracking duties. **Authority order: master doc > BUILD.md > CLAUDE.md.**
2. `BUILD.md` — day-by-day tasks + acceptance criteria (check-boxes are live status).
3. `promise-keeper-v3-final-master-doc.md` — the WHAT/WHY spec.
4. `PROGRESS.md` — one-page current status.
5. `tracking/` — all six files (DECISIONS.md especially: every deviation + two lead rulings live there).
6. This file's §4 (resume procedure).

## 2. THE LAWS YOU MUST NEVER BREAK (full text in CLAUDE.md §3)

1. **LLM SEEs and SPEAKs, never SPENDs** — no model output ever becomes an amount, debit date, or state transition. Perception → pydantic → deterministic state machine.
2. Mandate amounts come from **ledger records only** (test-proven: `tests/test_ledger.py::test_mandate_amount_always_equals_ledger_invoice_amount_never_llm_number`).
3. **Audit log written BEFORE any action executes.** No exceptions.
4. All bounds are constants in `engine/judgment/state_machine.py`, gated through one `check_bounds()`.
5. Every path terminates in KEPT / CLEAN_LOSS / HUMAN_HANDOFF (DISPUTED counts as terminal — see state_machine.py docstring).
6. SEED=42 everywhere; two identical runs = identical output.
7. **Personas are FROZEN** (tag `personas-frozen`). Never edit `sim/personas.py`, never let a subagent touch it, refuse requests to tune personas to flatter the agent — cite CLAUDE.md §3.7.
8. **Metrics honesty:** Tier 1 = genuinely measured, Tier 2 = simulated and always labeled so. The heuristic extraction number additionally carries an **in-sample caveat** (rules written with labels visible). Never drop these caveats.

## 3. THE BUILD MECHANISM YOU MUST FOLLOW (user-mandated, active since 2026-08-26)

Three-tier orchestration. **You (the lead) do NOT write feature code.** You architect, spec, dispatch, review, merge.

- **LEAD (you):** break BUILD.md tasks into small work packets; write packet specs (goal, files to touch, contracts citing schema names, acceptance criteria copied from BUILD.md, applicable design laws); dispatch; review every returned diff; you are the only one who merges/commits/pushes.
- **OPUS subagents:** hard, correctness-critical packets (state machine/bounds, trust math, extraction eval design, arms runner, integration runner — anything where a subtle bug corrupts money logic or metrics).
- **SONNET subagents:** routine high-volume packets (schemas, plumbing, data gen, dashboard components, messenger, README, test boilerplate). **Run in parallel wherever files don't overlap** — assign disjoint file sets explicitly in each spec.

**Review before merge, every packet:** laws obeyed? schemas matched exactly? tests included AND you re-ran them yourself? claimed metrics independently reproduced by you? Max 2 review rounds, then escalate to Opus or flag to the user. After merge: update `tracking/` per CLAUDE.md §4, check off BUILD.md criteria, commit (what+why), push.

**Standing orders:** subagents never run git commands (working-tree changes only; lead stages selectively). Subagents never touch `sim/personas.py`. Scope requests not in the docs → quote the cut order (Reserve failover → Scene 2 → Auditor → TTS) and freeze date (Sep 1) back to the user before acting. Prefer many small packets. End every session with: packets shipped · criteria now green · tracking files updated · tomorrow's first packet.

**Also user-mandated:** every feature must be showable as a **live demo**; document everything (failures included) in the tracking files; keep `PROGRESS.md` fresh; push to TrueCommit after merges.

## 4. WHERE TO RESUME (exact procedure)

**State at handover:** commits through `85ee9d0`+ pushed to origin/master. Batch 1 (P1/P3/P4) fully merged. **Group B (P2 + P5) was dispatched and may be in any state when you arrive:**

1. `git status` + `git log --oneline -5` + `git pull` — see what's committed vs. working-tree.
2. **If uncommitted working-tree changes exist** in `engine/integration/` + `api/main.py` (+ `tests/test_integration.py`), that's **P2** (Opus: WorldRunner + `POST /advance` — the time-warp backbone). In `engine/perception/providers/ollama.py` (+ `tests/test_ollama_provider.py`), that's **P5** (Sonnet: Ollama provider + measured qwen accuracy). Their packet specs are reproduced in §7. Review each against §3's checklist (re-run the full suite, reproduce claimed numbers), fix-or-bounce (max 2 rounds), stage each packet's files separately, commit with what+why, push.
3. **If the tree is clean and P2/P5 are already merged** (check `git log`), continue the roadmap at the next unshipped step in §5.
4. After P2 merges → **dispatch P6** [Sonnet]: `config/agents.yaml` — tunable parameters for the 5 mesh agents (perception provider/model/thresholds; sentinel retry/timeout constants; auditor sample rate — bounds stay hard constants in code, displayed read-only) surfaced via a small `GET /config` route + the dashboard System Health panel. P6 was sequenced after P2 because both touch `api/main.py`.

## 5. ROADMAP (what remains vs BUILD.md)

- **Day 7:** Auditor (10% extraction verification sampling + rolling accuracy + quarantine <85% → review queue — the one justified 2nd LLM use; can run on Ollama), Hinglish TTS voice note MP3 (one, embedded in a stage-4 timeline entry), dashboard polish for the funnel-movement demo, cold-start `make run`/2-command path → then **tag `v1.0-freeze`**. Day 7 acceptance: cold start <60s to funnel with real data; Advance-Day visibly moves money on screen.
- **Day 8:** `eval/run_arms.py` — Arm A silence / Arm B generic 3-day reminder / Arm C full system, same seed, personas react only to message properties (law 7). → `metrics.json`, DSO/%recovered/₹recovered/touches-per-recovery (Tier-0 = 0 row)/false-escalation/cost-per-₹. Re-run must reproduce identical numbers. Fill the measured-vs-simulated table (master doc §4.5) — plus the third "measurable" column per the BUILD_LOG dataset-ceiling entry.
- **Day 9:** video (script = master doc Part 5) + README (problem → gif → metrics → architecture → real-vs-simulated → limitations → 2-command run).
- **Day 10:** cleanup, secrets grep (`git grep -nE "rzp_(test|live)_[A-Za-z0-9]|sk-ant-api"`), submit.

## 6. CURRENT MEASURED TRUTH (never quote without the caveats)

| Metric | Value | Caveat that MUST travel with it |
|---|---|---|
| Extraction accuracy (heuristic, offline, free) | **97.7%** (43/44), gate 85% PASS | IN-SAMPLE upper bound — rules authored with labels visible, no held-out split |
| Triage accuracy (heuristic) | **91.7%** on the 24 threaded invoices, gate 90% PASS | Gate scoped by lead ruling (DECISIONS.md) — all-60 headline is 71.7% because 36 thread-less fillers sit at a proven 61.1% information ceiling; both numbers always reported together |
| Razorpay sandbox | 8/8 probes real | Mandate **registration** real (UPI Autopay + eMandate short_urls); **execute/revoke simulated+labeled** until one manual browser authorization at demo prep |
| Tests | 169/169 at handover | P2/P5 will add more — re-run yourself, never trust a subagent's count |
| Ollama | qwen2.5:7b + :3b installed, serving | 7b/3b eval numbers were IN FLIGHT at handover (P5) — check `metrics/*_ollama*.json` |

## 7. IN-FLIGHT PACKET SPECS (verbatim summaries, for review context)

**P2 [Opus] — integration runner.** `engine/integration/runner.py`: `WorldRunner` owning day counter + the real `Ledger` + Messenger + Sentinel + seeded rng + `get_provider()`. `advance(n)`: outreach cadence (gentle→firm→formal) → ledger events; persona reply moves (read-only from frozen `sim/personas.py`) → deterministic template texts → **real** `extract_promise` → `extraction_received`; promise due-dates as future-day events; Tier-0 reserve beat for the 2 reserve carts; dispute paths. Real Razorpay calls opt-in via `PK_REAL_RAZORPAY=1`, capped at first payment-link + first mandate-offer per run (real short_url must land in an audit entry → Day 6 criterion). API: `POST /advance`, `GET /world`; existing `ledger` stays importable as alias. Tests: terminal-state distribution, **determinism (two fresh 45-day runs → identical audit sequence)**, bounds hold, offline-by-default.

**P5 [Sonnet] — Ollama provider.** `engine/perception/providers/ollama.py` (convention `build()` auto-registration — must NOT edit `providers/__init__.py`): `/api/chat`, `stream:false`, JSON-schema `format`, shared prompt .md files, `OLLAMA_BASE_URL` (default localhost:11434) + `PK_OLLAMA_MODEL` (default qwen2.5:7b, **model name in cache fingerprint**), confidence normalization (85→0.85), one JSON-retry then typed error, **unreachable → auto-fallback to heuristic with degradation recorded** (the "no failure is silent" demo beat). Then run both evals for 7b AND 3b and report real numbers, no prompt-tuning in-packet.

## 8. ENVIRONMENT + KEYS + COMMANDS

- Python: `./.venv/Scripts/python.exe` (Windows venv). Node/npm installed. Ollama v0.32.15 as a service on `http://localhost:11434`.
- **Secrets: real Razorpay TEST keys are in the local gitignored `.env`** (`RAZORPAY_TEST_KEY_ID` / `RAZORPAY_TEST_KEY_SECRET`) — already on this machine. NEVER commit/print them; the client refuses non-`rzp_test_` keys structurally. No Anthropic key exists (perception runs heuristic/ollama — that's a logged, user-approved decision, DECISIONS.md).
- Env flags: `PK_PERCEPTION_PROVIDER` (heuristic|ollama|anthropic|oracle) · `PK_OLLAMA_MODEL` · `PK_REAL_RAZORPAY=1` (runner's real calls) · `PK_LIVE_SMOKE=1` (razorpay smoke script).
- Verify-everything commands:
```
./.venv/Scripts/python.exe -m pytest tests/ -q                       # full suite
./.venv/Scripts/python.exe -m eval.extraction_eval --provider heuristic
./.venv/Scripts/python.exe -m eval.triage_eval --provider heuristic
./.venv/Scripts/python.exe -m sim.run --days 45 --seed 42            # deterministic world
./.venv/Scripts/python.exe -m uvicorn api.main:app --port 8000       # API
cd dashboard && npm run dev                                          # dashboard (proxy → :8000)
PK_LIVE_SMOKE=1 ./.venv/Scripts/python.exe -m scripts.smoke_razorpay_live  # real sandbox objects
```

## 9. WHAT WINNING LOOKS LIKE (from CLAUDE.md §7)

Repo runs in 2 commands on a stranger's machine · tests pass · time-warp demo visibly moves money · extraction ≥85% vs hand labels (already true, with caveat) · every video claim traceable to a ✅ in `tracking/TRACK_BAR.md` · a BUILD_LOG full of real scars (7 real entries at handover — keep them real, never invent). The judges are hiring a builder, not buying a pitch.
