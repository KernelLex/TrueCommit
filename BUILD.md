# BUILD.md — Promise Keeper Execution Guide
### Companion to promise-keeper-v3-final-master-doc.md (the WHAT/WHY). This file is the HOW.
### Hand both files to your coding AI. Master doc = spec authority. This = task order.

---

## 0. GROUND RULES (read before any code)
1. **Master doc wins.** Any conflict between generated code and the master doc → doc is right.
2. **The LLM can SEE and SPEAK, never SPEND.** No LLM output ever becomes an amount, date-of-debit, or state transition directly. LLM → JSON → validated by pydantic → state machine decides.
3. **Every action writes to the audit log BEFORE it executes.** No exceptions.
4. **Fixed seed everywhere.** `SEED=42` in simulator, persona randomness, arm runs. Demo must be reproducible.
5. **Log real bugs in BUILD_LOG.md as you hit them** (date, what broke, fix). This is judging material — do not skip, do not fake.
6. **Feature freeze end of Day 7.** Days 8–10 = metrics, video, docs ONLY.

---

## 1. SETUP (Day 0, ~1 hour)
```bash
mkdir promise-keeper && cd promise-keeper
git init
python -m venv .venv && source .venv/bin/activate
pip install fastapi uvicorn pydantic anthropic httpx pytest python-dotenv
npm create vite@latest dashboard -- --template react
cp .env.example .env   # fill: ANTHROPIC_API_KEY, RAZORPAY_TEST_KEY_ID, RAZORPAY_TEST_KEY_SECRET
```
- Get Razorpay TEST keys: dashboard.razorpay.com → Settings → API Keys (test mode)
- Verify: one curl to create a test Payment Link. If it works, wiring day (Day 6) is de-risked.
- Create repo skeleton exactly as master doc §4.1.

---

## 2. DATA CONTRACTS (write these FIRST — Day 1 morning, engine/schemas.py)
Everything communicates through these. Coding AI: generate pydantic models for each.

```python
Event        {event_id, type: str, entity_id, payload: dict, ts: datetime}
Invoice      {id, debtor_id, amount_inr, issued, due, status, description}
Cart         {id, customer_id, amount_inr, items, drop_stage: summary|address|payment,
              drop_signals: [str], ts}
Message      {id, thread_id, direction: in|out, channel: wa|email, text, ts}
Extraction   {message_id, level: L1|L2|L3|L4|L5, amount_inr: int|None,
              date: date|None, condition: str|None, confidence: float}
CartCause    {cart_id, cause: friction|price_shock|trust|timing|comparison|unknown,
              confidence: float, evidence: [str]}
Promise      {id, debtor_id, invoice_id, amount_inr, due, status:
              pending|kept|broken|at_risk|renegotiated|disputed, source_msg}
TrustState   {debtor_id, alpha: float, beta: float, last_update}
Action       {id, entity_id, kind: link|mandate_offer|mandate_execute|message|
              voice|evidence_packet|human_handoff, params, reason: str,
              bounds_checked: bool, ts}
AuditEntry   {id, entity_id, layer: perception|judgment|action|sentinel|auditor,
              summary: str, detail: dict, ts}
```

---

## 3. DAY-BY-DAY TASKS + ACCEPTANCE CRITERIA

### DAY 1–2 — Dataset + Simulator
Build:
- `data/invoices.json`: 60 invoices, 12 debtors, amounts ₹8k–₹4.5L, age mix per master doc §3 (v2 doc §3.1)
- `data/carts.json`: 12 abandoned carts across drop stages with signals
  (2 with `reserve_active: true` for the Tier-0 beat)
- `data/conversations/`: ~95 messages. MUST include: all 5 levels, Hinglish
  ("bhai next Monday pakka"), one contradicting thread, one disputer, conditional
  promises, partial-payment offer
- `data/ground_truth.json`: hand-label every message (level/amount/date/cond)
  and every invoice (root cause) and every cart (cause)
- `sim/clock.py`: virtual day counter, `advance(n)` fires due events
- `sim/personas.py`: 6 personas with scripted response tables
  (probabilities from v2 doc §3.2), seeded RNG
Accept when:
- [ ] `python -m sim.run --days 45 --seed 42` replays identically twice (diff = empty)
- [ ] ground_truth covers 100% of messages/invoices/carts
- [ ] a second person (or your coding AI in "judge mode") reads 10 random
      conversations and agrees the labels are fair

### DAY 3 — Root-cause Triage (perception/triage.py)
- One Claude call per overdue invoice: inputs = invoice + payment records +
  delivery flag + thread-so-far → output = cause JSON
- Prompt: role + 5 causes defined + 6 few-shot examples + "output JSON only"
- `eval/triage_eval.py` → accuracy vs ground truth
Accept when: [ ] triage accuracy ≥ 90% (causes are easier than levels; below
90% = fix prompt, not model)

### DAY 4 — Promise Extractor (perception/extractor.py) ⚠ LOAD-BEARING WALL
- Claude call per inbound message with thread context → Extraction JSON
- Prompt: L1–L5 definitions with 8 few-shots INCLUDING Hinglish + contradiction
- Rule in prompt: "If date or amount is not explicit, do NOT invent — lower level"
- `eval/extraction_eval.py` → per-level precision/recall table → saved to metrics/
Accept when: [ ] level accuracy ≥ 85% on held-out labeled set
If below: iterate prompt (more few-shots for confused pair, tighten definitions).
Budget: max 1 extra half-day. Still failing → REDUCE ladder to 4 levels
(merge L2/L4) and re-eval. Document the decision in BUILD_LOG.md — that's a
failure-recovery story, not a shame.

### DAY 5 — Judgment Layer (ZERO LLM)
- `judgment/ledger.py`: promise lifecycle + status transitions
- `judgment/trust.py`: Beta(2,2) prior, +1 α kept / +1 β broken, exponential
  decay (half-life 60 virtual days), mandate refusal = pending-neutral (v3 §3.2)
- `judgment/state_machine.py`: transitions table + ALL bounds from master doc
  §3.4 as constants at top of file + `check_bounds()` gate before every action
- Reserve pre-check: payment_failed event → `if customer.reserve_active: tier0_recover()`
Accept when:
- [ ] pytest: every bound has a test that tries to violate it and fails
- [ ] pytest: dispute event from ANY state → DISPUTED, no further outbound actions
- [ ] pytest: no path loops (walk 1000 random event sequences → all terminate
      in KEPT / CLEAN_LOSS / HUMAN_HANDOFF)

### DAY 6 — Action Layer + Razorpay wiring
- `action/razorpay_client.py`: test-mode create Payment Link, create Invoice,
  create order + mandate-offer object, execute/revoke (mandate lifecycle events
  simulated where test mode doesn't support them — LABEL which in code comments)
- `action/messenger.py`: message queue with channel + rail label
  (wa_native_payment | mandate_link | plain_link) per §8.5
- `action/evidence.py`: dispute packet = invoice + thread + delivery flag +
  1-line Claude summary → JSON + rendered card
- Sentinel v1: retry ×3 w/ backoff, dead-letter queue table, link-open timer
Accept when:
- [ ] real test-mode Payment Link URL appears in audit trail for a demo invoice
- [ ] killing the network mid-run → actions land in dead-letter, nothing lost,
      resume works

### DAY 7 — Dashboard + Auditor + FREEZE
- Screens per master doc §4.3: Funnel / Entity timeline / Trust curves /
  Human-review queue / Metrics / System Health
- TIME-WARP: "Advance 1 Day ▶" + "Run to Day 45 ⏩" buttons calling sim clock
- Auditor: 10% sampling verification call + rolling accuracy widget +
  quarantine flag (<85% → money actions to review queue)
- Generate 1 Hinglish TTS voice note MP3, embed in stage-4 timeline entry
Accept when:
- [ ] cold start → `make run` → browser shows funnel with real data in <60s
- [ ] pressing Advance-Day visibly moves money/promises/trust on screen
- [ ] FEATURE FREEZE → tag `v1.0-freeze` in git

### DAY 8 — Metrics lock
- `eval/run_arms.py`: Arm A silence / Arm B generic 3-day reminder /
  Arm C full system. Same seed. → metrics.json + screenshots
- Compute: DSO, %recovered, ₹recovered, touches/recovery (Tier-0 = 0 row),
  false-escalation rate, cost per ₹ (meter LLM tokens + message count)
- Fill "measured vs simulated" table (master doc §4.5) with real numbers
Accept when: [ ] re-running arms reproduces identical numbers

### DAY 9 — Video + README
- Record per master doc Part 5 script. Screen-record dashboard, voiceover after.
- README: problem (2 stats) → 30s gif → metrics tables → architecture diagram →
  what's-real-vs-simulated → limitations → run instructions (2 commands)
Accept when: [ ] a friend who knows nothing about this can run it from README alone

### DAY 10 — Buffer + submit
- Repo cleanup, .env.example check (NO real keys committed — grep for rzp_test/sk-ant)
- Submit: repo link + video + architecture doc + track selection referencing the
  three named directions

---

## 4. LLM CALL BUDGET (keep costs/latency sane)
~95 messages × (1 extraction + 0.1 auditor) + 60 triage + 12 cart-cause +
~120 drafts ≈ ~350 calls/full run. At Sonnet pricing this is pocket change,
but CACHE perception results keyed by message_id → arms B/C reuse them, and
re-runs are instant. Drafting uses templates with slots; the LLM never
regenerates amounts/dates (they interpolate from ledger).

## 5. PROMPTS — WHERE THEY LIVE
`engine/perception/prompts/` — one .md file per prompt (triage / extract /
cart_cause / verify / draft). Version them in git. Every prompt ends with:
"Respond with JSON only, matching this schema: {...}. If information is not
explicit in the text, use null. Never guess amounts or dates."

## 6. DEMO-DAY CHECKLIST
- [ ] `make demo` = seeded run to Day 12, dashboard open, ready to time-warp live
- [ ] Pre-recorded video as backup for every live segment
- [ ] The 5 money-shots ready: L1 extraction live · mandate refusal + trust dip ·
      dispute → evidence packet · Tier-0 reserve heal (0 touches) · 3-arm table
- [ ] Answers rehearsed for: "why not more agents?" / "what if extraction is
      wrong?" / "how is this different from Chargebee dunning?" /
      "would a B2B AP team really tap a mandate?" (→ SME segmentation answer)
      / "what broke during the build?" (→ BUILD_LOG, pick your best story)
