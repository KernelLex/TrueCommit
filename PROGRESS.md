# PROGRESS.md — Promise Keeper build status
### Last updated: 2026-08-26 · Repo: github.com/KernelLex/TrueCommit · Freeze: Sep 1 · Submit: Sep 5

The one-page answer to "where are we?". Detail lives in `tracking/` (BUILD_LOG = what broke, TRACK_BAR = the judging bar, AI_JUDGMENT = where AI is/isn't used, BUILD_QUALITY = tests/reproducibility, DECISIONS = every deviation, PROBLEM_TASTE = claims + sources).

---

## ✅ DONE

### Foundation (BUILD.md Day 0–2)
- Repo, venv, deps, Vite/React dashboard scaffold, `.env.example`, secrets hygiene (keys live only in gitignored `.env`; tracked files grep clean)
- **`engine/schemas.py`** — all 10 data contracts (pydantic v2), smoke-tested, validation rejects bad data
- **Dataset**: 60 invoices / 12 debtors, 12 carts (2 with reserves), 24 conversation threads / 93 messages (all 5 promise levels, Hinglish, contradiction, disputes, partial payments), `ground_truth.json` with 100% hand-label coverage — generated deterministically, byte-identical across runs
- **Simulator**: seeded virtual clock + 6 frozen persona behavior tables (incl. adversarial never-payer) — `python -m sim.run --days 45 --seed 42` → 408-event deterministic log, replay-identical. Tagged `personas-frozen`

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

### Tests: **76/76 passing** · Reproducibility: verified · Secrets: clean · Pushed: master + tag on TrueCommit

---

## 🔄 IN FLIGHT (batch 1 — three-tier build: Fable lead / Opus hard / Sonnet routine)

| Packet | Tier | What | Live demo it produces |
|---|---|---|---|
| P1 | Opus | Provider-pluggable perception (heuristic rules provider free+offline TODAY, ollama/anthropic slots, oracle demo-mode) + per-provider eval harness | Run the extraction eval live, real accuracy table, zero cost |
| P3 | Sonnet ∥ | Real Razorpay client for every sandbox-verified endpoint; execute/revoke simulated+labeled | Live mandate-registration short_url lands in the audit trail |
| P4 | Sonnet ∥ | Dashboard v1: funnel, entity audit timeline, Beta trust curves, review queue, system health, time-warp buttons | The money-moves-on-screen beat |
| — | env | Ollama installing + qwen2.5:7b & :3b pulling (local LLM, free, works hosted via `OLLAMA_BASE_URL`) | Perception on a real local model |

## ⏭ NEXT (approved, queued behind batch 1)
- **P2 [Opus]**: integration runner — sim clock → perception (cached) → ledger → real actions, driven by `POST /advance` (the time-warp backbone)
- **P6 [Sonnet]**: `config/agents.yaml` — tunable parameters for all 5 mesh agents surfaced in System Health
- **P5 [Sonnet]**: Ollama provider (dispatches when the model pulls finish)
- Then: Day 7 auditor + TTS + freeze tag → Day 8 three-arm metrics lock → Day 9 video + README → Day 10 submit

## ⚠ OPEN RISKS
- Heuristic/local-LLM extraction accuracy vs the 85% gate — will be **measured, not guessed**; BUILD.md's level-merge fallback documented if low
- Mandate execute/revoke realness — needs the one manual registration authorization during demo prep
- Dashboard funnel shows real movement only after P2 lands (states are mostly NEW until the integration runner drives them)

---

## How to run what exists today
```
./.venv/Scripts/python.exe -m pytest tests/ -q          # 76 tests
./.venv/Scripts/python.exe -m sim.run --days 45 --seed 42   # deterministic world
./.venv/Scripts/python.exe -m uvicorn api.main:app          # API on :8000
./.venv/Scripts/python.exe -m scripts.verify_razorpay_sandbox  # live sandbox probes (needs .env keys)
```
