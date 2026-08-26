# PROGRESS.md — Promise Keeper build status
### Last updated: 2026-08-26 (post-demo) · Repo: github.com/KernelLex/TrueCommit · Freeze: Sep 1 · Submit: Sep 5

> **Handing over between Claude sessions? Read `HANDOVER.md` first** — it carries the build mechanism (three-tier orchestration), the laws, the resume procedure, and the in-flight packet specs.

The one-page answer to "where are we?". Detail lives in `tracking/` (BUILD_LOG = what broke, TRACK_BAR = the judging bar, AI_JUDGMENT = where AI is/isn't used, BUILD_QUALITY = tests/reproducibility, DECISIONS = every deviation, PROBLEM_TASTE = claims + sources).

---

## ✅ DONE

### Foundation (BUILD.md Day 0–2)
- Repo, venv, deps, Vite/React dashboard scaffold, `.env.example`, secrets hygiene (keys live only in gitignored `.env`; tracked files grep clean)
- **`engine/schemas.py`** — all 10 data contracts (pydantic v2), smoke-tested, validation rejects bad data
- **Dataset**: 60 invoices / 12 debtors, 12 carts (2 with reserves), 24 conversation threads / 93 messages (all 5 promise levels, Hinglish, contradiction, disputes, partial payments), `ground_truth.json` with 100% hand-label coverage — generated deterministically, byte-identical across runs
- **Simulator**: seeded virtual clock + 6 frozen persona behavior tables (incl. adversarial never-payer) — `python -m sim.run --days 45 --seed 42` → 463-event deterministic log, replay-identical. Tagged `personas-frozen`

### Judgment layer — the zero-LLM heart (Day 5)
- **Trust**: Beta(2,2) posterior, +1α kept / +1β broken, 60-day half-life decay, refusal = neutral
- **State machine**: full escalation ladder, **all 8 hard bounds as constants** gated through one `check_bounds()`; termination guaranteed by construction (step-cap backstop)
- **Ledger**: audit-log-written-BEFORE-action enforced; mandate amounts can ONLY come from ledger records (a test feeds a wrong "LLM" amount and proves it's ignored); Tier-0 reserve recovery (0 touches)
- Proof: dispute-from-any-state × 11 states, 1000 random event sequences all terminate, every bound has a violation test that gets blocked

### Action + perception scaffolding (Day 6 slice)
- Messenger (rail-labeled sim WA/email queue), Sentinel (retry ×3, dead-letter queue, link-open timer, circuit breaker), evidence-packet builder
- Perception modules + all 5 prompt files + eval harnesses — built, tested, offline-safe

### API
- FastAPI wired to the real judgment layer; dataset loads at startup; events in → state/actions/audit out. Verified with real `uvicorn` + curl

### Day-1 priorities (CLAUDE.md §5) — all three ✅
1. **Razorpay sandbox VERIFIED (8/8 live probes)** — the crown-jewel result: **mandate registration links issue real short_urls in test mode for BOTH UPI Autopay and eMandate**, plus real Payment Links / Invoices / Customers / Orders. Execute/revoke need one human-authorized registration (one browser step at demo prep; simulated + labeled until then). Report: `tracking/razorpay_sandbox_report.json`
2. Schemas before feature code ✅
3. Personas frozen + tagged before agent work ✅

### Tests (at Phase-A close): **76/76 passing** (now 271 — see below) · Reproducibility: verified · Secrets: clean · Pushed: master + tag on TrueCommit

---

## ✅ BATCH 1 MERGED (three-tier build: Fable lead / Opus hard / Sonnet routine)

| Packet | Tier | Shipped | Key result |
|---|---|---|---|
| P1 ✅ | Opus | Provider-pluggable perception: `heuristic` (free/offline, tunable `HeuristicParams`) / `anthropic` / `oracle` (demo-only, evals refuse it) + file cache + per-provider evals | **Extraction 97.7% PASS** (in-sample caveat carried); **triage 91.7% PASS on threaded invoices** (gate scoped by lead ruling — 36 thread-less fillers proven at a 61.1% info ceiling, nothing tuned); 2 real contradictions found + resolved |
| P3 ✅ | Sonnet | Real Razorpay TEST client: payment links / invoices / customers / **mandate registration links (UPI + eMandate)**; execute/revoke labeled `simulated:true` | Live smoke made real short_urls: payment link `rzp.io/rzp/Ux0hpma`, UPI mandate registration `rzp.io/rzp/yP4UiEA` |
| P4 ✅ | Sonnet | Dashboard v1: Funnel (Tier-2 badge), Entity audit Timeline, SVG Beta Trust Curves, Review Queue, System Health (bounds card), time-warp buttons | All five screens live against the API; zero invented numbers (grep-verified); graceful 404 toast until `/advance` exists |
| env ✅ | — | Ollama v0.32.15 + qwen2.5:7b & :3b installed and smoke-tested | Structured-output extraction answered correctly in ~3s on CPU |

**Tests: 169/169** · Every packet reviewed by lead against the 8 design laws before merge; numbers independently reproduced.

## ✅ GROUP B + FOLLOW-UPS ALL MERGED (P2 · P5 · P7 · P8 · P6)

| Packet | Tier | Key result |
|---|---|---|
| P2 ✅ | Opus | **The time-warp is alive**: `POST /advance` drives personas → real perception → real ledger/bounds → dispatched actions. Cross-process-deterministic 45-day world; real Razorpay payment-link + mandate-registration URLs landed in the audit trail (opt-in, rate-limited); Sentinel survived its first genuine remote failure (live 400 → retry ×3 → dead-letter → run continued) |
| P5 ✅ | Sonnet | Ollama provider (fallback-to-heuristic when unreachable, confidence normalization, model-in-cache-fingerprint) + first out-of-sample measurement — which **caught a real bug**: the prompts never stated today's date |
| P7 ✅ | Opus | **Extraction gate CLEARED on a free local model: qwen2.5:7b 77.3% → 88.6%** (gate 85%; lead re-ran independently, confirmed; three-run nondeterminism disclosed: 90.9/88.6/88.6, all clear). Five-level ladder preserved — no merge fallback needed. A triage change that traded the gated metric for an ungated headline was measured and **reverted** |
| P8 ✅ | Opus | **Compliance made true**: touch cap now per-DEBTOR as the law says (worst window 6→2); gentle nudges are real bounds-checked Actions; sim clock day-0 bug fixed. Honest cost: 45-day recovery 58.2% → **36.6%** — the compliant number is the number |
| P6 ✅ | Sonnet | `config/agents.yaml` — sentinel + cache knobs genuinely wired (test-proven), bounds structurally un-overridable (`extra="forbid"`), System Health shows live provider/model/degradations/dead-letters/cache counters |

**Tests: 271/271** · Every packet lead-reviewed against the 8 laws; every headline number independently reproduced before merge.

## ⏭ NEXT (Day 7 → 10)
- **Day 7 batch**: Auditor (10% verification sampling on Ollama + rolling accuracy + <85% quarantine → review queue) · instrument-over-nudge touch-budget priority (lead-ruled: master doc's own recovery hierarchy applied to the scarce per-debtor budget, before/after reported) · Hinglish TTS voice note · dashboard funnel-movement polish + cold-start 2-command path → **tag `v1.0-freeze`** (Sep 1)
- **Day 8**: three-arm runner (silence / generic reminder / full system, same seed) → metrics.json, DSO/₹recovered/touches-per-recovery (Tier-0 = 0 row)/false-escalation/cost-per-₹, reproducibility-locked
- **Day 9**: video (master doc Part 5 script) + README · **Day 10**: cleanup + submit

## ✅ P9 MERGED (2026-08-26) — human-in-the-loop is real

Lead-reviewed on branch, independently reproduced (394/394 tests, click-time `check_bounds` on stale-hold approval, `human_resolution` unreachable via the general event route, pause honored by the advance loop, 45-day distribution + held-queue contents exact), merged to master, pushed (`86513c2`).

- **Confidence gates (master doc §2.3), finally wired:** any mandate/link action born from an extraction confidence <0.90 is held, not sent — `GET /review-queue` lists it, `POST /review-queue/{id}/approve` re-runs `check_bounds()` **at click time** (a stale hold can't dodge a cap hit since creation), `reject` falls back to the link path.
- **Formal-notice stage:** refuses *both* approve and reject — "mark handled" only. The agent cannot send legal communication even on a human click (law 4, now literally unbreakable via any API path).
- **Handoff resolution:** `POST /entities/{id}/resolve-handoff` {recovered|written_off} — the ONE way a terminal state can still move, gated to the API layer only (proven unreachable from the event stream / 1000-walk pool).
- **Merchant pause switch:** `POST /entities/{id}/pause` / `/unpause` — paused threads are skipped by the runner, audited both ways.
- **The finding worth telling judges:** an unattended queue (nobody clicks fast) recovers ₹23,31,496 because the debtor's weekly touch budget is already spent by the time anyone looks; an attentive merchant who waits for budget gets **₹31,74,725** — bounds working exactly as designed on real data, both numbers labeled.

## ⏸ PARKED (user decision 2026-08-26 — resume only on user signal)
- **Mandate flow-real enablement**: this test account lacks UPI/eMandate (account-level gate, needs business KYC the user rightly won't fabricate, or an organizer-provisioned hackathon account). Everything documented (BUILD_LOG + TRACK_BAR §0 API-real vs flow-real); harness armed (`scripts/verify_mandate_lifecycle.py`); demo + submission fully intact without it. Optional upgrade path: ask buildathon organizers for an enabled test account.

## ⚠ OPEN RISKS
- Local-model nondeterminism: 7b flips one message across runs (88.6% quoted as the floor, gate cleared in all observed runs) — mitigation: quote the range, cache pins demo behavior
- Mandate execute/revoke realness — one manual registration authorization during demo prep (path documented in the client docstring)
- qwen2.5:3b remains below gate (70.5%) — 7b is the demo model; 3b stays as the honest comparison row

## 🎬 UI DEMO RUN (2026-08-26, live browser session against the fully-merged build)
Dashboard served at `localhost:5173` (Vite, proxying `/api` → uvicorn on 8010 — `PK_API_PORT` env added to vite.config.js because Windows svchost squats 8000 on this machine), API started with `PK_REAL_RAZORPAY=1`. Verified live in the browser:
- Funnel at day 3 after one `advance(3)`: ₹1,04,149 recovered, 9 disputed with evidence packets, Tier-2 badge visible; Advance-Day buttons move money/promises/trust on screen — **BUILD.md Day 7's "visibly moves on screen" criterion now demonstrated in the real UI**
- Entity Timeline shows two REAL Razorpay objects created during the session: payment link `rzp.io/rzp/O0fRy0zV` (INV-006, ₹1,85,000) and UPI mandate registration `rzp.io/rzp/Gz5jTAP4` (INV-011, ₹97,000) — both clickable, both live sandbox pages
- Trust curves, review queue (9 evidence packets), and System Health (live provider/cache/dead-letter + agents.yaml parameters card) all rendering from the API only
Run it yourself: `PK_REAL_RAZORPAY=1 ./.venv/Scripts/python.exe -m uvicorn api.main:app --port 8010` + `cd dashboard && PK_API_PORT=8010 npm run dev` → open localhost:5173.

## 🎬 SANDBOX DEMO RUN (2026-08-26, from a clean worktree at commit 85ee9d0 — reproducible by anyone)
All eight beats executed live against the merged code; every number below was produced during the run, not quoted from memory:
1. **Test suite**: 169/169 passed
2. **Extraction eval** (heuristic, offline, zero cost): 97.7% PASS with per-level P/R table + in-sample caveat printed by the tool itself
3. **Triage eval**: 71.7% headline / **91.7% on threaded invoices** / 61.1% info-ceiling split printed by the tool itself
4. **Determinism**: two 45-day seeded sim runs → byte-identical (diff empty), 463 events
5. **Full lifecycle through the real pipeline** (TestClient): INV-001 triage → Hinglish L1 extraction → bounds-checked mandate offer → executed → KEPT, trust Beta(2,2)→(3,2); INV-031 dispute → evidence packet → ladder frozen (follow-up event returns no action, state terminal); INV-047 ₹4.5L mandate **blocked by the cap** with the block reason in the audit trail + automatic link fallback; INV-001's full audit timeline printed (every entry logged before its action executed)
6. **Tier-0 reserve beat**: cart C-09 payment_failed → reserve pre-check → KEPT with **0 touches**
7. **Real Razorpay objects created live**: payment link `rzp.io/rzp/EfWzyui` + UPI mandate registration `rzp.io/rzp/nx7meJ3I` (TEST mode, real short_urls)
8. **Local LLM live**: qwen2.5:7b classified the L3 partial-payment message correctly in 1.8s on CPU (with the field-scramble finding noted above, recorded honestly)

---

## How to run what exists today
```
./.venv/Scripts/python.exe -m pytest tests/ -q          # 394 tests
./.venv/Scripts/python.exe -m sim.run --days 45 --seed 42   # deterministic world
./.venv/Scripts/python.exe -m uvicorn api.main:app          # API on :8000
./.venv/Scripts/python.exe -m scripts.verify_razorpay_sandbox  # live sandbox probes (needs .env keys)
```
