# PROGRESS.md — Promise Keeper build status
### Last updated: 2026-08-30 (7-packet plan issued past the original cut order — debit-failure taxonomy [Packet 1] and debtor-level judgment [Packet 2] both done; red-team suite, acceptance learning, UI pass, live-channel demo, doc hygiene queued next, in that order) · Repo: github.com/KernelLex/TrueCommit · Freeze: Sep 1 · Submit: Sep 5

> **Handing over between Claude sessions? Read `HANDOVER.md` first** — it carries the build mechanism (three-tier orchestration), the laws, the resume procedure, and the in-flight packet specs.

The one-page answer to "where are we?". Detail lives in `tracking/` (BUILD_LOG = what broke, TRACK_BAR = the judging bar, AI_JUDGMENT = where AI is/isn't used, BUILD_QUALITY = tests/reproducibility, DECISIONS = every deviation, PROBLEM_TASTE = claims + sources).

---

## 🚦 STATUS SNAPSHOT — what's done, what's ASAP (2026-08-30, freeze is imminent)

**The pre-agreed cut order (Reserve failover → Scene 2 → Auditor → TTS voice) finished 2026-08-29 at 4/4.** On 2026-08-30 the user issued a NEW, larger 7-packet plan that goes past that original scope — this is deliberate, explicit, past-freeze-date-aware new work, not scope creep discovered by accident. Built in strict order, one packet at a time, each tested/reconciled/pushed and reported before the next starts:

**Packet status:**
1. ✅ **Debit-failure taxonomy** — DONE, 2026-08-30. See the dated section below for the full story.
2. ✅ **Debtor-level judgment** — DONE, 2026-08-30. Dispute freeze + mandate-refusal posture lifted to the debtor, explicit touch-budget allocation. See the dated section below.
3. ⏳ **Adversarial red-team suite** (4 exploit personas, quantified damage, README section) — queued next.
4. ⏳ **Acceptance learning** (2nd Beta posterior over mandate registration, break-even number, dashboard meter) — queued.
5. ⏳ **UI pass** (real browser check first, then make every packet above visible on screen) — queued.
6. ⏳ **Live channel demo path** (IVR → real mandate → Telegram confirmation; WhatsApp free-form half only, no sandbox ContentSid templates) — queued.
7. ⏳ **Doc hygiene** (4 stale PROGRESS.md contradictions, `whatsapp_meta.py` disposition) — to be done alongside, ~20 min.

**✅ Done before this plan (all tested, tracked, pushed) — still true, unaffected by the packets above except where noted:**
- Full Scene 1 pipeline: triage → extraction (85%/90% accuracy gates cleared on 2 providers) → trust → state machine (all 8 bounds) → mandate/link conversion → escalation ladder → human handoff/dispute — **now includes the debit-failure taxonomy's reason-aware routing, Packet 1.**
- Scene 2: Tier-0 reserve failover (0-touch) and the cause → instrument layer for the other 10 carts (friction/price_shock/comparison/unknown → link, timing → scheduled mandate, trust → delivery-secured mandate with the revoke branch shown) — deliberately NOT touched by the debit-failure taxonomy (`tracking/DECISIONS.md` 2026-08-30, Decision 3).
- Reliability mesh: Sentinel (retry/backoff/dead-letter/circuit breaker) + Auditor (accuracy self-monitoring, quarantine gate), both with independent RNG streams unaffected by any of this.
- RBI E-Mandate Framework compliance: pre-debit (T-1) and post-debit notices, both live in every mandate execution.
- Real-world integrations, opt-in and rate-limited: Razorpay TEST mode (payment links, mandate registration, full create→execute→revoke lifecycle, human-verified), Twilio voice/IVR + WhatsApp, Telegram real dispatch.
- Dashboard: Funnel, Entity Timeline (mandate lifecycle stepper, guardrail panel), Trust Curves, Human Review queue, System Health (Sentinel + Auditor widgets) — **Packet 5 will audit whether these actually reflect everything above on screen; not re-verified in a browser since 2026-08-26 per the user's own note.**
- 3-arm measured-vs-simulated comparison (`eval/run_arms.py` → `metrics.json`), mandate-acceptance sensitivity band, reproducibility locked at seed 42 — **Arm C's own figures moved with Packets 1 and 2** (see below), regenerated and re-verified, not just scope-note text.
- **690/690 tests passing**, cold start verified from a fresh clone in 2 commands, no secrets in repo (grepped clean).
- README expanded to BUILD.md's own Day-9 outline (verified stats, metrics tables, architecture diagram, real-vs-simulated table, limitations section) — **Packet 7 will fix 4 stale contradictions elsewhere in this file, not the README.**

**⏭ ASAP, in order:** finish the remaining 6 packets above (2 is next), THEN the pre-existing Day-9/10 items are still genuinely outstanding underneath all of this: the 30-second gif (needs a screen-recording tool this environment doesn't have), the `v1.0-freeze` tag (date-gated, Sep 1), the Day-10 submit checklist, and demo-day rehearsal (BUILD.md §6) — none of that has moved since 2026-08-29 and none of it is code.

---

## Continuing development (2026-08-30) — Packet 1: debit-failure taxonomy

**What was built:** a third real entry point into the judgment machine, alongside invoice-overdue and cart-abandoned — a bounced mandate execution, routed by a fixed 5-value NACH/eMandate reason enum (`engine/schemas.py`'s `DebitFailureReason`) to a distinct, audited recovery branch each: `insufficient_funds`/`amount_exceeds_limit` get the one allowed retry at a trust-derived delay then a trust-derived SHRUNK fallback link; `bank_downtime` retries silently at zero cost (no trust move, no retry spent); `account_closed_frozen` switches instrument permanently with no retry; `mandate_revoked` — the one genuine willingness signal — moves trust and escalates immediately.

**The CRITICAL MODELING POINT, checked and confirmed real:** `trust.py` WAS applying a full trust penalty uniformly on any exhausted-retry mandate failure, regardless of why it bounced — exactly the bug flagged as worth checking for. Fixed by extending the project's own existing precedent (mandate refusal is pending-neutral) to four of the five reasons; only `mandate_revoked` costs trust.

**A second, older bug found while testing the fix:** the exhausted-retry fallback had NEVER actually produced a payment link since this code was written — `entity.state = "LINKED"` was immediately overwritten by an unconditional `_escalate()` call, silently contradicting master doc §3.5's own spec ("...retry x1 -> payment link -> ladder resumes at current stage"). Fixed; no existing test had pinned the wrong behavior, so nothing broke, but 3 `test_integration.py` tests and 1 `test_run_arms.py` reconciliation needed their real, re-measured numbers.

**Numbers, measured and reconciled:** whole-world `recovered_inr` ₹23,36,494 → ₹23,55,494 (+₹19,000); Arm C's own invoice-only figure moved by the identical amount (₹23,24,347 → ₹23,43,347 — `metrics.json` regenerated for real, not just its scope-note). `distribution` KEPT 21→22 / HUMAN_HANDOFF 27→26. `promises` shifted as a genuine downstream consequence of inserting one new RNG draw into the shared persona stream (argued in full in `tracking/DECISIONS.md` why this draw belongs in the shared stream, unlike the Auditor's dedicated one). Reproducibility re-verified byte-identical across two fresh runs.

**Where the reason comes from, argued per the user's explicit ask:** bank-supplied ground truth in real production (a NACH return code on Razorpay's webhook), never an LLM inference; in this simulation (no live recurring-debit failures to observe), drawn from a new frozen per-persona table added to `sim/personas.py` — the first real edit to that file since the `personas-frozen` tag, argued explicitly in `tracking/DECISIONS.md` for why it does not violate the tag (it enriches WHY a failure happens, never touches WHETHER one does).

**Tests:** `tests/test_debit_failure.py`, 28 new tests, pin every reason's state-machine routing, trust delta, and dispatched action directly (not left to the stochastic 45-day run, which only exercises 2 of 5 reasons for real). 670/670 total. Full detail: `tracking/BUILD_LOG.md`/`DECISIONS.md`/`AI_JUDGMENT.md`/`TRACK_BAR.md` §2, all dated 2026-08-30.

**Reported and stopped per the user's own instruction, then continued into Packet 2 on "continue".**

---

## Continuing development (2026-08-30) — Packet 2: debtor-level judgment

**What was built:** trust was already scored per debtor; escalation state and negotiation posture were not, so a debtor with five overdue invoices really could get one disputed and frozen correctly while the ladder kept autonomously chasing the other four — the exact gap named. Two mechanisms close it: `Ledger.disputed_entities_by_debtor` freezes every outbound action on a debtor's OTHER entities while any one of them is disputed (independently per dispute — a second, unrelated dispute keeps the freeze on after the first resolves), and `Ledger.debtor_mandate_refused` bars a fresh mandate offer on any of a debtor's entities once ANY of them earns a refusal (explicit decline, 48h silence, or a `mandate_revoked`/`account_closed_frozen` debit failure). Both check in at the same priority tier as the merchant kill-switch in `_gate()`; `check_bounds()` gained exactly one new, default-`False` optional parameter, so every pre-existing call site is untouched. Then, because it falls out naturally: `engine/judgment/allocation.py` gives a debtor holding more open invoices than their remaining weekly touch budget a deterministic, trust-and-age-ranked attempt ORDER instead of letting alphabetical `entity_id` ordering pick the winner by accident.

**Two real bugs found by measuring the allocator, not by writing it.** First: the naive `trust + age` formula permanently starved every invoice but a debtor's single oldest one — trust is a DEBTOR-level value (identical across a debtor's own invoices) and age is fixed at data-load time, so a fixed invoice set ranks identically on every single beat for the whole run. Measured directly: 12 invoices across 8 debtors got zero touches for 45 days, recovery fell ~12% versus not allocating at all. Fixed with a rotation term measured relative to the least-touched entity in the eligible set. Second: the first version of `_run_outreach` pre-committed to a fixed top-N list and skipped calling `_outreach()` for the rest entirely — which also skipped the `outreach_sent` EVENT that moves an entity's state regardless of whether its message gets bound-blocked, delaying state progression for everyone else and pushing the pinned run's first Scene-1 mandate offer from day 7 to day 21. Fixed by reverting to "attempt everyone, just in priority order," letting the ledger's own unmodified touch-cap bound decide who actually gets through — which also fixes a third problem for free: a single entity's own reply can cascade past its message into a `mandate_offer` (both touch-counted), and a pre-committed list can't see that coming.

**The flagship demo entity moved, said out loud rather than quietly patched around.** INV-001/Acme Traders — this test suite's (and per earlier UI-demo-run notes, likely the dashboard's own) long-standing mandate-lifecycle example — no longer reliably reaches a mandate offer within a short window once its debtor's siblings compete fairly. That is the feature working as intended on the specific invoice that used to win purely by being first alphabetically, not a regression. `INV-043`/Meenakshi Garments is this run's real, deterministic full-lifecycle instance now; every test needing one was redirected to it, and if the dashboard's own demo script named INV-001 specifically, it needs the same swap.

**Numbers, measured and reconciled, a real decrease and not smoothed over:** whole-world `recovered_inr` ₹23,55,494 → ₹21,92,569 (a DECREASE of ~6.9%). Two honestly-argued causes, both in `tracking/DECISIONS.md`: the dispute freeze correctly removes some invoices from ever being chased again (the same shape of trade-off packet P8's touch cap already made), and the allocator's trust+age ranking doesn't predict which invoice will reply well on a GIVEN attempt (reply quality is drawn independently per entity per beat, law 7), so it sometimes spends a debtor's only touch on an invoice that replies vaguely instead of a sibling that would have converted. Arm C's own invoice-only figure moved by the identical amount; `metrics.json`/README's Tier-2 tables regenerated for real.

**Tests:** `tests/test_debtor_judgment.py`, 20 new tests, pin the dispute freeze, the mandate-refusal lift, and the allocator's scoring/rotation/determinism directly. ~15 pre-existing tests across `test_integration.py`/`test_day_story.py`/`test_reminders.py`/`test_review_queue.py`/`test_debit_failure.py`/`test_run_arms.py` needed re-measured pins or a hand-driven scenario where the natural run no longer organically produces one — none weakened, each documented at its own site. 690/690 total, reproducibility re-verified byte-identical, `bound_violations()` empty. Full detail: `tracking/BUILD_LOG.md`/`DECISIONS.md`/`AI_JUDGMENT.md`/`TRACK_BAR.md` §2, all dated 2026-08-30.

**Reported and stopped per the user's own instruction — Packet 3 (adversarial red-team suite) starts next, not started yet.**

---

## Track A / B gate (2026-08-27) — resolved: user picked Track A

After the 7-step corrections+bar pass (README, three-arm runner, cold start, RBI notices — all done, tested, pushed), the user was asked to pick ONE of Track A (IVR) or Track B (WhatsApp dual-path) for the Aug 31–Sep 1 window, not both. **Track A was picked.** Track A is now built (see "DONE" below); Track B stays exactly where it was left — researched and planned, zero code — and is out of scope unless separately picked later.

### 1. IVR (call → press 1/2 → real mandate or payment link) — ✅ DONE, live-verified through three real rounds, 2026-08-28
Built per the design already scoped via `AskUserQuestion` before the pause: `Ledger.ivr_select()`/`ivr_available_options()`, `POST /telephony/ivr-menu` + `/telephony/ivr-response` (Twilio-facing webhooks, always return valid TwiML, never an HTTP error), `POST /entities/{id}/call-ivr-now` (operator trigger, gated by `WorldRunner.real_telephony_contact` — same opt-in/credential/real-contact shape as every other real-dispatch path), `telephony.place_ivr_call()`. The real Razorpay call is made directly and unconditionally from the response webhook, bypassing the rate-limited autonomous `_payment_instrument()` path — the precedent packet P13's `create-mandate-now` console established. Multi-language menu and reviving Scene 2 stay explicitly out of scope (the user's own scoping choice, recorded before the pause). New dependency: `python-multipart` (Twilio webhooks POST form-encoded bodies; FastAPI/Starlette's form parser needs it regardless of encoding).

**Three real, live rounds of testing, three real bugs found and fixed, one genuine limitation found and correctly left alone — full story in `tracking/BUILD_LOG.md`/`DECISIONS.md`, 2026-08-28:**
1. **The mandate's debit date.** `invoice.due` (always in the past — every invoice here is deliberately overdue) was used as a REAL mandate's `start_at`; Razorpay's live API correctly rejected it. Fixed with `_real_future_debit_date()` (today + 7 real days, the exact offset already proven live in the P12 pivot). The identical latent bug existed in `create-mandate-now` too, fixed in the same pass.
2. **The opt-in flag had no effect on a freshly started server.** `PK_REAL_TELEPHONY=1` in `.env` never reached `os.environ` before `WorldRunner()` read it, because nothing had called `load_dotenv()` yet — `engine/action/telephony.py`'s own functions call it lazily on every use, but the flag-resolution functions in `runner.py` never did. Fixed with one `load_dotenv()` call at `api/main.py` module level, before the first `WorldRunner()` is ever constructed.
3. **The call worked, the keypress worked, but no confirmation message ever arrived.** Turns out `link`/`mandate_offer` dispatches have never genuinely reached a real phone anywhere in this codebase — only `voice`/`message` kinds ever check the real-WhatsApp gate. Fixed by wiring a real `telephony.send_whatsapp()` attempt into the IVR confirmation, gated the same way the call itself is, honest about failure either way.
4. **The genuine limitation, found on the third real attempt, correctly NOT fixed here:** the real WhatsApp send failed with `"Unable to create record: ContentSid Required"` — sending fresh outbound WhatsApp outside an active session window needs a pre-approved Content Template, exactly the constraint already researched as Track B before the gate. Building that dual-path now would be building Track B without it having been picked. The system stays honest about it regardless: the failure is audited, the real Razorpay object is unaffected, and the spoken confirmation says the link couldn't be messaged rather than claiming success.

32 new/updated IVR tests, 609/609 total.

**Not built, deliberately out of this pass's scope:** a dashboard button to trigger `call-ivr-now` (backend + tests only, matching what was actually asked); the Track B WhatsApp dual-path that would make the confirmation message actually land.

### 2. WhatsApp reliability (Track B) — NOT SELECTED, unchanged since the pause — RESEARCHED AND VERIFIED, PLANNED, ZERO CODE WRITTEN
User found and proposed a real architecture (their own research, independently verified this session, not taken on faith): Twilio's WhatsApp Sandbox ships with **3 pre-approved templates** (confirmed via live web search against Twilio's actual docs — "Appointment Reminders", "Order Notifications", one more not yet identified), sendable via `ContentSid` **outside** the 24-hour session window, no WABA/business verification needed; **confirmed from Twilio's own docs** that a debtor's reply to a templated message opens a real 24-hour free-form window. The referenced GitHub repo (`KernelLex/CropRadar-01`, all 5 branches checked) does **not** actually contain this mechanism — it's a reactive bot that never hits the window problem — but did surface a numbered-menu multi-language (English/Kannada) SMS pattern, likely the source of the earlier multi-language call idea.

**One real concern flagged, not yet resolved with the user:** the 3 sandbox templates have fixed, generic wording ("your order has shipped") — repurposing them to mean "your invoice is overdue" is content that's literally false on its face, in tension with this project's own honesty ethos even for a demo. A **custom** template with real wording needs the account moved off sandbox to a real WhatsApp-enabled sender, which needs the same business-verification wall already hit with Exotel — not something money buys around (confirmed via Twilio's own docs).

---

## Continuing development (2026-08-28) — the Auditor

With the live-call debugging settled, the user asked to continue development as planned. Checked what was genuinely still unbuilt against CLAUDE.md's own pre-agreed cut order (Reserve failover beat → Scene 2 entirely → **Auditor** → TTS voice) rather than guess: Reserve failover and TTS voice were both already live, Scene 2 partially exists, and the Auditor (master doc §7.3 — the accuracy agent, `config/agents.yaml`'s own comment literally said "NOT WIRED... until the Auditor packet") was the one genuinely missing piece. Built rather than cut, since freeze is still a few days out.

### Auditor — ✅ DONE, 2026-08-28
`engine/action/auditor.py`, wired end to end: `Ledger.set_auditor_quarantine()` + `_decide_money_action`'s widened hold check, `WorldRunner.auditor` (a dedicated, independently-seeded RNG stream — see below), `config/agents.yaml`'s `auditor.*` section (moved from placeholder to actually wired, mirroring `build_sentinel()`), `GET /auditor`, and a live widget on the System Health dashboard screen (replacing its old "not wired until the Auditor packet" placeholder).

Samples a fraction of extractions (default 10%), verifies each against the original message, tracks a rolling agreement rate over the last N samples (default 10), and quarantines the extractor below threshold (default 85%) — every money-adjacent action then routes to human review regardless of that read's own confidence, through the SAME held-action queue the ordinary confidence gate already uses. Every quarantine/restore event is a real, ordered audit-trail entry, written before the flag flips.

**Default verification is zero-LLM, deliberately** — `heuristic_cross_check` re-extracts the same message via the already-tested heuristic provider and compares levels, matching this project's own heuristic-first/LLM-opt-in pattern for every other perception task. Honest caveat stated in the code and in `tracking/AI_JUDGMENT.md`: when heuristic IS the active provider (this project's default), that comparison is a trivial self-agreement check — confirmed live on a real 45-day run (`sample_count: 10, rolling_agreement: 1.0`). The genuine 2nd-pass LLM path the master doc names, `llm_verify()`, is real and callable (uses the already-written `verify.md` prompt) but not the default.

**The one real design risk, caught before it became a bug:** the Auditor's sampling RNG must never be the shared `WorldRunner.rng` that drives every persona decision in sequence — interleaving sampling draws into it would have silently shifted every persona draw after the first sample and changed the pinned 45-day numbers as a side effect of adding a system-health feature. Fixed by construction with a dedicated `random.Random(seed)` instance; verified directly (not assumed) that every pre-existing pinned-number test stays green with the Auditor wired into every `WorldRunner`. Full reasoning in `tracking/DECISIONS.md`.

22 new tests (`tests/test_auditor.py` 17 + `tests/test_config.py` +5), 630/630 total. Live-verified against a real 45-day run and against the dashboard's actual dev proxy (`GET /api/auditor` through Vite → the real backend) — not just the API in isolation, though the rendered page itself could not be visually confirmed in this environment (no browser/screenshot tool available this session).

**Recommended plan given that (proposed, not yet confirmed by the user):** build the real dual-path `Messenger` architecture (bounds pass → is a 24h window open, tracked via the existing `_inbound()` hook P11 already uses for link-open tracking → free-form if open, template-path if not) as a genuine, production-correct architecture story for the README — but use the generic sandbox template **only as a mechanism proof**, clearly labeled as a stand-in for a future real approved template, and keep **Telegram as the primary real-content real-delivery channel** for anything that must say the true reminder text right now. One concrete lookup still needed before any code: the real `ContentSid` (`HX...`) values, via Twilio's Content API or console — not guessed.

**Separately, still open:** `engine/action/whatsapp_meta.py` (Meta direct API client) exists on disk, **untracked, uncommitted, not live-verified** — the one test attempt failed on an expired ~24h temporary access token. Not the recommended path anymore (Twilio's sandbox already has real quota and the actual fix is templates, not a second provider) — kept on disk in case it's wanted later, not deleted.

### 3. Deployment / Ollama — answered, no action taken, ties to the existing memory note
Confirmed for the user: deploying to a host like Render does **not** carry Ollama with it — Ollama runs on this laptop only, and a deployed server can't reach `localhost:11434` unless tunnelled (Tailscale, already installed for the Twilio webhook, could be extended to Ollama's port too). Standing recommendation restated, not changed: keep `heuristic` (offline, zero-dependency, 97.7% in-sample) as what the **deployed** instance actually depends on by default; any LLM (tunnelled Ollama, Groq, Ollama Cloud) stays an opt-in flourish, never what an unattended judge's visit relies on. Full detail already in memory (`project_ollama_cloud_deployment.md`), not re-litigated here.

### If Track B (WhatsApp) is ever picked up
Needs a ContentSid lookup (Twilio Content API/console, not guessed) plus a decision on the honesty question flagged above (generic sandbox template wording vs. the real reminder text) before any code — nothing about that has changed since the pause.

---

## Continuing development (2026-08-29) — Scene 2's cause → instrument follow-through

User asked to continue and build all remaining features. Rather than guess what was left, audited by grepping for genuinely-unbuilt mechanisms the same way the network-kill/circuit-breaker gap was found earlier: `grep -n "cart_abandoned\|CartCauseType\|friction\|price_shock" engine/judgment/ledger.py engine/judgment/state_machine.py` returned zero matches. `WorldRunner._cart_beats()` triaged every cart's cause and then did nothing further for the 10 non-reserve carts — master doc §3.3's "cause triage → matching instrument" promise held for the 2 Tier-0 reserve carts only. **This is explicitly the pre-agreed cut-order item #2** (Reserve failover → **Scene 2** → Auditor → TTS voice, all four now built) — flagged to the user before building, then built since the request to build everything stood.

### Scene 2 cause → instrument routing — ✅ DONE, 2026-08-29
Read master doc §3.3 (full Scene 2 flow) and §8.5 (the WhatsApp rail-labeling scope) before writing any code, per CLAUDE.md's file-authority order. Built the mapping as fixed code, never a model decision (law 1): `state_machine.transition()` routes `cart_abandoned` to `PROMISED` (timing/trust — a capturable commitment) or straight to `LINKED` (friction/price_shock/comparison/unknown — "NO discount, NO mandate"), explicitly excluding `reserve_active` carts so Tier-0's pre-check still runs first. `Ledger._decide_money_action`/`_decide_action` pick the actual instrument from the cause: `trust` → a new `delivery_secured_mandate` rail ("pay nothing today... cancelled instantly if returned"), `timing` → the existing `scheduled_mandate`/`mandate_link` rail, everything else → a plain link, no mandate ever considered. `WorldRunner._resolve_cart_mandate` scripts the two mandate-bearing causes to a real, deterministic outcome (no persona exists for cart customers, so nothing here draws on the shared RNG stream): C-05 (timing) executes and recovers; of the dataset's 2 `trust` carts, C-07 shows the happy path (delivery confirmed day+4 → mandate executes) and C-08 shows the revoke branch (`delivery_rejected` → `CLEAN_LOSS`, a transition that already existed in `state_machine.py` with zero callers until now) — the master doc explicitly wants both branches demonstrated. `_sweep_idle` now covers non-reserve carts too, so a friction/price-shock/comparison/unknown cart whose link is never opened reaches `HUMAN_HANDOFF` rather than sitting open forever (law 5, extended to Scene 2).

Two real bugs caught mid-build by the new tests, not shipped: a reserve cart's cause (`friction`) was routing it to `LINKED` and dispatching a real link before Tier-0's pre-check ever got a chance to run (fixed by excluding `reserve_active` from the new transition); and the scripted mandate confirmations weren't marking their own instrument "opened," so the Sentinel's 48h timer soft-refused a mandate this method had just confirmed directly (fixed by appending a synthetic approval reply and marking it opened, mirroring `_offer_instrument`'s existing `_inbound()` call for Scene 1). Full story: `tracking/BUILD_LOG.md`/`DECISIONS.md`, 2026-08-29.

**Numbers that moved, measured not asserted:** whole-world `recovered_inr` ₹23,31,496 → ₹23,36,494 (+₹4,998 = the two now-recovering carts), `messages_sent` 111 → 124. Arm C's own invoice-only Tier-2 figure in `metrics.json`/README (₹23,24,347) is genuinely unchanged — Scene 2 was always out of that report's scope; only its `scope_note` reconciliation text changed. 5 new tests pin the cause → instrument mapping and both trust-cart branches against the real 12-cart dataset; 642/642 total, reproducibility re-verified byte-identical across two fresh runs.

**Arbitrary modeling calls, logged rather than hidden** (`tracking/DECISIONS.md`): which of the 2 trust carts gets which branch (resolved by sorted cart id, not a coin flip), and leaving the plain-link causes' conversion entirely unmodeled (closed by the idle sweep/link-timeout instead of an invented conversion rate — no persona table exists for cart customers to draw one from honestly).

**Not built, deliberately out of this pass's scope:** any dashboard-visible distinction for the new `delivery_secured_mandate` rail beyond what already renders generically (no screen currently special-cases rail values); a persona/behavior model for cart customers (Scene 2 stays lighter than Scene 1 by design, per the cut order).

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

## ⏭ NEXT (Day 7 → 10) — SUPERSEDED, kept for history only
**All of Day 7/8/9's engineering items below shipped since this was written** (Auditor, three-arm runner, README, dashboard polish, cold start, plus Scene 2 and Track A which weren't even anticipated here). **See the 🚦 STATUS SNAPSHOT at the top of this file for what's actually next as of 2026-08-29.**
- ~~**Day 7 batch**: Auditor (10% verification sampling on Ollama + rolling accuracy + <85% quarantine → review queue) · instrument-over-nudge touch-budget priority (lead-ruled: master doc's own recovery hierarchy applied to the scarce per-debtor budget, before/after reported) · Hinglish TTS voice note · dashboard funnel-movement polish + cold-start 2-command path → **tag `v1.0-freeze`** (Sep 1)~~
- ~~**Day 8**: three-arm runner (silence / generic reminder / full system, same seed) → metrics.json, DSO/₹recovered/touches-per-recovery (Tier-0 = 0 row)/false-escalation/cost-per-₹, reproducibility-locked~~
- ~~**Day 9**: video (master doc Part 5 script) + README~~ · **Day 10**: cleanup + submit — still genuinely pending, see snapshot above

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


## ⚠ OPEN RISKS
*(Corrected 2026-08-27 — this section and the "PARKED" section above it directly contradicted the eMandate rail pivot section immediately above both: that section says mandate execute/revoke realness was closed entirely on 2026-08-26; these two sections still said it was blocked, needed KYC, and was an open risk. Both were pre-pivot text nobody deleted when the pivot resolved them — a real contradiction a judge reading top to bottom would have hit on the project's single most important claim. The PARKED section is removed; nothing in it needs a heading of its own any more.)*
- Local-model nondeterminism: 7b flips one message across runs (88.6% quoted as the floor, gate cleared in all observed runs) — mitigation: quote the range, cache pins demo behavior
- qwen2.5:3b remains below gate (70.5%) — 7b is the demo model; 3b stays as the honest comparison row
- **UPI Autopay stays account-gated** (needs business KYC this project won't fabricate) — this is a minor, non-blocking fact, not an open risk: netbanking eMandate (the primary rail since the pivot) is fully real end to end and needs no gate. UPI Autopay is kept, unchanged, only as the alternate rail in case the gate ever lifts.

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
