# PROGRESS.md — Promise Keeper build status
### Last updated: 2026-08-27 (P16+P17 — real phone call + real Telegram message/audio, both confirmed live) · Repo: github.com/KernelLex/TrueCommit · Freeze: Sep 1 · Submit: Sep 5

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

## ✅ P10 MERGED (2026-08-26) — "Day Story": the real simulation, made visible

User ask: press "next day" and SEE it in the UI — real conversation text, guardrail checks in detail (not just pass/fail), the eMandate lifecycle end to end, real customer NAMES (not entity IDs), and trust SCORES. Diagnosed correctly before build: the conversation, the personas' real decisions, and debtor names **already existed server-side** — this is a visibility layer, not a new decision layer.

- **`check_bounds_detailed()`** — every bound constant checked with its real numbers ("touches this week: 1/2 (limit 2)"), proven mathematically identical to the real `check_bounds()` decision by a 5000-random-combination test. `check_bounds()` itself is byte-identical, untouched.
- **`GET /debtors`** (real names + live trust, cart customers get `name:null` + an honest note rather than an invented name) · **`GET /entities/{id}/conversation`** (the real thread, both directions) · **`GET /entities/{id}/guardrail-checks`** (a read-only preview — proven to write nothing) · **`GET /entities/{id}/mandate-timeline`** (reconstructed from the real audit trail, each step labeled by a 5-value *nature* field so "offered" / "registered" / "executed" are never blurred into one `real: bool`) · **`GET /day/{n}/story`** + additive `stories` on `/advance` (checklists recorded AT DECISION TIME, so a day-3 explanation never uses day-9 numbers).
- **`DayStoryScreen.jsx`** — debtor-name-first cards, chat bubbles, expandable guardrail checklists, a mandate lifecycle stepper with accurate REAL/SIMULATED badges.
- **433/433 tests**, lead independently re-ran the full suite + both safety-critical tests + the build before merging.

**Real bug the new visibility caught (not fixed here, deliberately — money-adjacent, needs its own review):** `Sentinel.mark_link_opened()` is fully built and tested but has zero call sites in the runner — every dispatched link/mandate times out as a "soft refusal" after 48h regardless of what actually happened, including mandates the debtor already confirmed. Confirmed via grep before merge. **P11 dispatched immediately after to fix it** (see below).

## ✅ P11 MERGED (2026-08-26) — link-open tracking fixed, audit trail is honest again
User gave the explicit go-ahead to review + merge. Lead independently re-ran the full suite (438/438) plus all 5 new tests individually, and by-hand reproduced the 45-day distribution and recovered-₹ two ways (raw entity counter first showed an apparent mismatch — 12 vs 3 DISPUTED, ₹23,24,347 vs ₹23,31,496 — both fully reconciled: the 9 invoices pre-disputed at day 0 sit outside the "51 active" cohort the report scoped to, and the ₹7,149 gap was the two Tier-0 reserve carts, which a quick invoice-only script had missed). Merged (`2b386be`).
- **The fix:** `mark_link_opened()` now actually gets called, at the exact moment a debtor replies to a tracked instrument, without weakening genuine 48h-silence handling (a dedicated test proves true silence still soft-refuses).
- **Honest correction on the record:** the lead predicted recovery would rise once fixed; measured result says it didn't move at all (₹23,31,496 both times) — P11 diagnosed exactly why (the false refusal always arrived after the money had already moved; no falsely-barred entity re-promised inside 45 days; `trust.update_refusal` is pending-neutral so no trust damage ever occurred either). What the fix actually bought: 9 fabricated refusal entries gone, 6 entities no longer wrongly flagged — an audit-trail-integrity win, not a recovery-₹ one, in this dataset. The latent money-bug risk (a barred entity that DOES re-promise) is now closed since there's no false bar left to trigger it.

## ✅ P12 MERGED (2026-08-26) — `razorpay_client.py` now speaks eMandate-via-Subscriptions
Lead re-ran the full suite (445/445) and independently verified the three "byte-unchanged" claims by diffing function bodies with docstrings stripped out (two false positives from a quick regex traced to an adjacent comment and unrelated new functions, then confirmed clean by direct reading). Merged (`aec5a4b`).

## ⚠ STALE-SERVER FOOTGUN FOUND AND FIXED (2026-08-26) — restart both servers together after any merge
User saw "Not Found" on Day Story and the Human-Review Queue in the browser. Root cause: the dashboard was talking to uvicorn/Vite processes started hours earlier, before P9/P10 (which added those exact routes) had even merged — Python doesn't hot-reload, so the old process genuinely had no `/review-queue` or `/day/{n}/story` routes. Found FOUR stale processes total (two duplicate uvicorns on :8010, two more on :8123, one stale Vite). All killed, fresh instances started, both new routes confirmed 200. **Lesson for every future demo session: after any merge, kill and restart both the API and the dashboard dev server together** — a survived-merge stale server is now a known, named failure mode, not a mystery to re-diagnose each time.

## ✅ P16 + P17 — real phone call (Twilio) + real Telegram message/audio, both confirmed live (2026-08-27)
New scope, user-confirmed (same "build now, accept the freeze risk" pattern), across two closely-linked packets. Full narrative including every real bug found and fixed, and the exact error text at each step: `tracking/BUILD_LOG.md`. Scope/rationale record: `tracking/DECISIONS.md`.

**What the user asked for:** a real phone call and a real WhatsApp message reaching their own phone live, followed by actually approving the resulting mandate.

**WhatsApp hit a real wall at every option tried, in order:** Twilio's Sandbox needs a pre-approved message template for a business-initiated message outside a live session (`HTTP 400: ContentSid Required` — Meta's own policy, not Twilio's); Meta's own direct Cloud API was wired with real credentials but never live-tested with an actual send this session; Infobip's number verification failed with a security flag on every number tried; Exotel requires real company KYC a hackathon project cannot provide. **Telegram's Bot API had none of this** — free forever, no card, no business verification, no template approval — and became the real message/voice-note channel this deployed demo actually uses. WhatsApp stays the documented, intended real-world channel (merchants' actual debtors use WhatsApp) — this is a pragmatic demo choice, not a product pivot.

**The phone call took four real bugs to get working, confirmed live at the end — honestly, not on the first try after all four were fixed:**
1. Twilio trial accounts reject inline TwiML outright (`HTTP 400: Invalid or disallowed parameters... trial accounts have limited parameter access`) — found by isolating against Twilio's own public demo TwiML URL, which worked.
2. TwiML Bins (Twilio's own hosted-snippet feature, tried as the fast path to a URL) turned out to also be trial-gated (`HTTP 401` on the Serverless API they live under) — diagnosed for free, without spending call quota, by querying the API directly.
3. Built this project's own `GET/POST /telephony/twiml` webhook and exposed it via a real public URL using **Tailscale Funnel** (`https://supercom.tail5b897d.ts.net`) — then found and fixed two more real bugs: the route only accepted GET (Twilio defaults to POST when fetching a `url=`), and it returned `Content-Type: application/xml` instead of the `text/xml` Twilio's fetcher specifically requires.
4. A genuine methodology mistake caught along the way: repeated `curl` checks from this same machine kept succeeding because it's *on* the same Tailscale network as the tunnel, so requests were resolving to a private internal IP and never actually testing the real public path — caught via `curl -v` showing the true connection target, confirmed instead via a genuinely external fetch.

**End state:** confirmed live by the user — a real call spoke the real reminder text, and a real Telegram message + audio both landed correctly. One honest caveat, documented rather than glossed over: two attempts on the identical, already-fixed code failed immediately before the successful one, most likely intermittent tunnel-connection warm-up rather than a fifth undiscovered bug — not root-caused further past that point since Twilio's own detailed call debugger requires a paid account tier, and the user explicitly called a stop to further debugging once free diagnosis was exhausted.

**Guardrail discipline preserved on both new real-delivery paths, proven the same way P14 proved it for `sms`:** `_should_go_real_telephony()`/`_should_go_real_telegram()` never fire for an autonomous action regardless of any `.env` credential or opt-in flag — the full seeded 45-day run makes zero real calls/sends even with both opt-in flags on, asserted by tests that replace the real-send functions with ones that raise if ever called. A manual click only reaches either function after the ordinary `check_bounds()` gate has already allowed the touch — no private door, no softer rule for going real.

**A real bug found and fixed in the API response layer, not just the dispatch code:** a real Telegram send was succeeding (confirmed by a genuine Telegram `message_id` coming back) while `POST /remind-now`'s JSON response silently omitted any sign of it — `api/main.py`'s response-builder had a whitelist of fields to surface and nobody had added the two Telegram ones. Found by adding temporary debug prints to the live server and watching the real request trace; fixed with a two-line addition; pinned by a regression test that fails on the pre-fix code.

**Tests:** 525 → **554** passing (`tests/test_telephony.py` — 16, `tests/test_telegram_dispatch.py` — 11, three test-count net since some overlap in fixture-sharing). Seeded 45-day run re-verified byte-identical across two fresh processes; the run's own numbers (₹23,31,496 etc.) are completely untouched. No secrets leaked — `.env` holds four new real credential types (Twilio, Meta WhatsApp, Telegram), all gitignored, re-grepped clean across the whole repo.

## ✅ P15 MERGED — real contact directory (2026-08-27)
New scope, user-confirmed (same "build now, accept the freeze risk" pattern as P13/P14), and a locked design call: channel selection (voice vs message vs mandate_offer) stays purely stage-based — trust score is not a new input into it, so `check_bounds`/`state_machine.py`/`trust.py` are untouched by this packet.
- Every debtor had shared ONE synthetic fake contact (`+919812345678`) since Day 6. Now: `POST /entities/{id}/contact` {name, phone} stores a real contact keyed by **debtor_id** (a submission for one invoice auto-applies to every sibling invoice of that debtor — same per-debtor scoping as the touch cap, so nobody ends up with two contradictory numbers on file). `WorldRunner.resolve_contact()` is the single place every dispatch point — voice, SMS, WhatsApp message, and the real Razorpay mandate/link call — reads who to contact, returning the real submission or the exact old synthetic fallback, explicitly labeled `operator_submitted` vs `demo_fallback`.
- **Manual trigger now covers all three channels** (voice/WhatsApp-message/SMS) from a new dashboard **Contacts** panel — extending `MANUAL_REMINDER_CHANNELS` to include `"message"` needed zero new dispatch code (confirmed by test, not just claimed): a manual WhatsApp nudge rides the exact same `_dispatch` branch the autonomous ladder has used since Day 5, gated by the identical `check_bounds()` re-check `approve_held`/`remind-now` already established.
- **Live-verified end to end, not just unit-tested:** submitted a real contact for INV-002 → correctly propagated to siblings INV-001/003/061/062 (all under debtor D-01) → triggered voice, WhatsApp-message, and SMS reminders that all carried the real name/phone → created a REAL Razorpay TEST mandate (`https://rzp.io/rzp/vKOgUr7p`) whose `customer.contact` field genuinely carried the submitted number. An unrelated debtor's entities stayed on the demo fallback, confirming no cross-debtor leakage.
- **No real call, SMS, or WhatsApp message is ever placed** — still no telephony/SMS-gateway/WhatsApp-Business credential in this project. A submitted number only changes what the audit trail/dashboard shows and what Razorpay's real sandbox `customer.contact` field receives. Stated explicitly in the UI, not just in code comments.
- Seeded 45-day run unchanged (₹23,31,496 / 23 KEPT / 27 HUMAN_HANDOFF / 12 DISPUTED, `bound_violations()` empty) — verified byte-identical, since `resolve_contact()` returns the old hardcoded values verbatim when nobody has submitted anything.
- Tests: 486 → **525** passing. Dashboard build/lint clean. One real bug caught and fixed inside the packet itself: extending the channel enum made two PRE-EXISTING P14 tests' premise ("message is refused") false by construction — fixed by repointing them at `"email"` (a real but genuinely-unsupported value) and adding new tests pinning the new behavior. Full writeup: `tracking/BUILD_LOG.md`, 2026-08-27.
- **Known pre-existing gap, flagged not fixed:** `create-mandate-now`'s silent default `debit_date` (the dataset's fixed invoice due date) can trigger a real Razorpay 502 once enough wall-clock time has passed that the date is in the past — a P13 issue, unrelated to contacts, surfaced while live-testing this packet. The Demo Console's date picker already lets an operator override it; the default just isn't future-proof. Left for a future small fix.

## ✅ P13 + P14 MERGED — demo console + real SMS/voice reminders (2026-08-26)
New scope, user-confirmed, accepted schedule risk against Sep 1 — full rationale in `tracking/DECISIONS.md`. Both packets were dispatched concurrently into the same working directory (a process gap — see the new `tracking/BUILD_LOG.md` entry on it); reviewed by reading every changed hunk by hand, not just trusting each agent's self-report.
- **P13 [Sonnet] — "Create Mandate Now" demo console.** `POST /entities/{id}/create-mandate-now`: pick any entity, fill in real customer details or leave blank for auto-generated demo values, get a REAL Razorpay TEST-mode mandate registration link immediately. Deliberately NOT routed through `check_bounds` (a human inspecting the rail once, not the agent deciding) — but still fully audited via `runner.audit_manual()`, labeled unmistakably "manual demo," never confusable with an autonomous action in the trail. Amount always copied from the invoice record, never from the request body (law 2) — live-verified: a ₹22,000 ledger amount produced a real 2,200,000-paise subscription (`sub_TUNOE7FmJwKRiZ`, `short_url: rzp.io/rzp/PAarCIpz`). A past-dated `debit_date` correctly surfaced Razorpay's real rejection as a clean 502, audited as a failed attempt.
- **P14 [Opus] — real SMS + real AI-generated voice reminders.** New `sms` outbound kind + upgraded `voice` (gTTS, confirmed live in this environment — real playable Hinglish MP3s, e.g. a 62,016-byte file with a genuine MPEG frame-sync header, `demo_assets/voice_notes/`, served via a FastAPI static mount). Triggerable manually via `POST /entities/{id}/remind-now` (bounds-respecting, same click-time-gate pattern as P9's `approve_held` — `Ledger.manual_reminder()`) or autonomously (the existing `ESCALATE_2 → "voice"` ladder hook now produces real audio instead of a text placeholder). `sms` rides the exact same `MAX_TOUCHES_PER_WEEK` bound as `message`/`voice` — proven, not asserted, by a 2,000-random-input equivalence test. **Headline honest finding:** in the seeded 45-day run the autonomous voice escalation is attempted 4 times and refused all 4 times by the touch cap — by the time an entity reaches ESCALATE_2 its debtor's weekly budget is already spent, so the channel emits zero audio on autopilot. Real audio only comes from the operator's `remind-now` button or an entity with unspent budget — live-verified both ways (a spent-budget entity correctly blocked; a fresh entity produced a real MP3 on the first call). No phone is dialled and no SMS reaches a handset — no telephony/SMS-gateway credential exists in this project; every record says `dial_status`/`send_status: "simulated_..."` explicitly.
- **Verified independently before merge:** `pytest tests/` → 486/486 green; two fresh seeded 45-day runs byte-identical; dashboard `npm run build`/`npm run lint` clean; secret grep clean; both new routes hit live against the real Razorpay TEST API and the real gTTS endpoint (not mocked) with results matching what's written above.
- **Mandate rail** (packet P12, landed the same window): matches the live-verified shapes exactly — `create_plan` + `create_mandate_via_subscription` (`total_count: 1` for a true one-time debit, `start_at` schedules the charge for the invoice due date) · `check_mandate_execution` (a query, not a command — confirms via a real captured payment, never the laggy `subscription.status` field) · `revoke_mandate_token` (the real `DELETE`, already proven live). Old UPI `auth_links` method kept as the alternate rail, untouched.

## ✅ eMandate rail pivot — VERIFIED FULLY REAL end to end (2026-08-26, lead + user together)
User checked the Razorpay TEST dashboard directly and found netbanking eMandate enabled with no gate under **Subscriptions** settings (a different product surface than the registration-links API the Day-1 probe used) — UPI Autopay stays gated, left alone as instructed. Lead live-verified the complete lifecycle before any code changed, per explicit instruction:
- **Create → Authorize → Execute → Revoke: all real.** Real objects: `plan_TULfhYrG9rmMjR` / `sub_TULfqScOEmQ57p` → two Card attempts failed on Razorpay's known-flaky test-mode card-mandate path (diagnosed via the real payment error records, not the page's generic message) → Netbanking eMandate succeeded for real (`pay_TULmn2CWCOuWDu`, `status: captured`, real recurring token `token_TULmXon2Xf7bco`) → token genuinely revoked (`DELETE` → `{"deleted": true}`, confirmed gone).
- **Future-dated scheduling (`start_at`) verified accepted** — the part the actual product needs (debit on the invoice due date, not on approval), checked before writing any downstream code.
- One honest quirk found: `subscription.status` lags the real payment record — the payment object is the reliable source of truth, not the subscription's own status field.
- **This closes the "mandate execute/revoke realness" open risk entirely.** Full narrative + real IDs: `tracking/BUILD_LOG.md`. `TRACK_BAR.md` §0 and `DECISIONS.md` updated and pushed (`80adc7f`).


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
./.venv/Scripts/python.exe -m pytest tests/ -q          # 554 tests
./.venv/Scripts/python.exe -m sim.run --days 45 --seed 42   # deterministic world
./.venv/Scripts/python.exe -m uvicorn api.main:app          # API on :8000
./.venv/Scripts/python.exe -m scripts.verify_razorpay_sandbox  # live sandbox probes (needs .env keys)
```
**For a live real-call demo specifically** (not needed for anything else — the core app runs with none of this): `PK_REAL_TELEPHONY=1 PK_REAL_TELEGRAM=1` must be set as real process env vars at server launch (writing them into `.env` alone is not enough — see `tracking/BUILD_LOG.md`), and Twilio's real-call path additionally needs `PUBLIC_BASE_URL` in `.env` pointing at a real public URL for this app (a deployed instance, or `tailscale funnel --bg 8010` for a dev-machine tunnel).
