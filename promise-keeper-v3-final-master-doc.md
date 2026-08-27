# PROMISE KEEPER — FINAL MASTER BUILD DOC (v3)
### One commitment engine · Two markets (B2B receivables + checkout recovery) · Three instruments
### Razorpay AI Buildathon 2026 · Track 3 · Feature-freeze Sep 1, submit Sep 5

---

# PART 1 — WHAT WE'RE BUILDING AND WHY IT MATTERS

## 1.1 The thesis (memorize this)
Revenue recovery tools send messages. Messages ask the customer to decide again, later, at a colder moment. **Promise Keeper converts stated intent into payment instruments** — commitments that execute themselves — and only falls back to messages when no commitment exists to capture.

## 1.2 The two scenes, one engine
- **Scene 1 (deep build): B2B receivables.** Overdue invoices → root-cause triage → promise extraction (L1–L5) → trust-weighted escalation → promise-to-mandate conversion → recovery.
- **Scene 2 (breadth proof): Checkout drop-off recovery.** Abandoned carts → cause triage from drop-stage signals → conversational confirm → deploy the matching commitment instrument.

## 1.3 The three commitment instruments (all on Razorpay's One-Time Mandate rail)
| Instrument | Target abandoner/debtor | Mechanic |
|---|---|---|
| **Scheduled mandate** | "Payday is Friday" (timing) | Approve once → auto-debit + auto-order on date |
| **Price-triggered mandate** | "Waiting for the sale" (emo: ROADMAP ONLY — needs price-feed sim) | Approve at target price → executes if price drops, expires free otherwise |
| **Delivery-secured mandate** | "Don't trust online payment" (trust) — THE CROWN JEWEL | Debit scheduled at checkout, executed only on delivery confirmation, cancelled on return. COD's safety without COD's RTO disaster. *(Corrected 2026-08-27 — this project's live-verified mandate rail is netbanking eMandate, which schedules a future debit and does not lien/block funds the way UPI OTM/SBMD/Reserve Pay would; see `tracking/PROBLEM_TASTE.md`.)* |

Demo scheduled + delivery-secured. Mention price-triggered as roadmap.

## 1.4 Judging criteria mapping (build everything against these four)
| Criterion | Our answer |
|---|---|
| **Problem taste** — did you pick something that matters | ₹8.1 lakh crore locked MSME receivables + ~70% cart abandonment; Razorpay named both directions in the track brief; we use their newest rail (OTM) in ways their product team hasn't shipped |
| **Build quality** — does it run, is it structured, would you trust it | Deterministic state machine for all money actions, typed JSON contracts between components, fixed-seed reproducible simulation, one-command run, tests on the extractor |
| **AI judgment** — right tool in right place, and where you chose NOT to use one | LLM ONLY for perception (classify/extract) and language (drafting). ZERO AI in: escalation transitions, trust math, mandate creation, bounds enforcement, money movement. Section 3.2 is the explicit "where we didn't use AI" list — say it in the video |
| **Failure recovery** — what broke and what you did | Every playbook has a fallback chain (no dead ends, no infinite loops) + keep a real BUILD_LOG.md of actual bugs hit during development and their fixes — present it honestly |

## 1.5 The track bar (verbatim, non-negotiable)
"Don't just identify the problem. **Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail.**" — 3-arm batch comparison (Tier 1 measured / Tier 2 simulated split), escalation ladder with hard caps, stopping rules table (Section 5.4), audit trail as first-class UI.

---

# PART 2 — AI MODELS: WHAT WE USE AND WHERE

## 2.1 Model selection
| Job | Model | Why | Settings |
|---|---|---|---|
| Promise extraction (L1–L5 + amount/date/condition) | **Claude Sonnet (claude-sonnet-4-6) via Anthropic API** | Best structured-output reliability for nuanced Hinglish/ambiguous text; one model everywhere = simpler, defensible | temp 0, JSON schema enforced, few-shot with 8 labeled examples in prompt |
| Cart-abandonment cause inference (Scene 2) | Same Claude Sonnet, different prompt | Same perception job, different domain | temp 0, JSON out |
| Message drafting (nudges, notices, mandate offers) | Same Claude Sonnet | Tone control per escalation stage | temp 0.3, template-constrained (model fills slots, never invents amounts/dates — those come from the ledger) |
| Dispute summary (evidence packet one-liner) | Same Claude Sonnet | trivial | temp 0 |
| Hinglish voice note | Any TTS (Sarvam AI if accessible — India-focused; else ElevenLabs/OpenAI TTS free tier) | One pre-generated MP3, embedded in dashboard | n/a |

**One LLM, four prompts. No fine-tuning, no RAG, no agents-calling-agents.** If asked why no fancier stack: "the hard part of this problem is judgment boundaries, not model capability — a bigger stack adds failure modes, not accuracy." That sentence is an AI-judgment point-scorer.

## 2.2 Where we deliberately do NOT use AI (say this list out loud in the video)
1. Escalation stage transitions — deterministic state machine
2. Trust score — Beta posterior math (closed-form, auditable)
3. Mandate creation/amounts — copied EXACTLY from ledger records, never LLM-generated
4. Bounds enforcement (caps, cooldowns, stop rules) — hard code, cannot be prompted around
5. Money movement — Razorpay APIs only, triggered by state machine only
6. Metrics computation — plain Python, reproducible

**The design law: the LLM can SEE and SPEAK, never SPEND.** If the LLM hallucinated tomorrow, the worst case is an awkward message — never a wrong debit. That's the blast-radius answer.

## 2.3 Confidence gates on the LLM boundary
- Extraction confidence < 0.75 → agent asks ONE clarifying question instead of acting
- Still ambiguous after clarify → route to human-review queue (visible in dashboard)
- Any extraction that would trigger a MONEY action (mandate offer) at conf < 0.9 → held for human approve-click in demo ("human-in-the-loop where money moves on imperfect perception")

---

# PART 3 — DEEP ARCHITECTURE

## 3.1 System overview
```
                        ┌────────────────────────────────────────────────┐
                        │                 SIMULATOR                      │
                        │  seeded clock (45 virtual days) · personas     │
                        │  emits: invoice events · debtor replies ·      │
                        │  cart-abandon webhooks · bank/mandate          │
                        │  outcomes (approve/decline/insufficient)       │
                        └──────┬─────────────────────────────┬───────────┘
                               │ B2B events                   │ commerce events
                               ▼                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          INGESTION LAYER                                │
│   normalizes everything into one Event schema:                         │
│   {event_id, type, entity_id, payload, ts}                             │
└─────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      PERCEPTION LAYER  (the ONLY LLM zone)              │
│  ┌────────────────────┐  ┌───────────────────────┐  ┌────────────────┐ │
│  │ Root-cause triage  │  │ Promise extractor      │  │ Cart-cause     │ │
│  │ (B2B: why unpaid)  │  │ (L1–L5 + terms +       │  │ inference      │ │
│  │                    │  │  confidence)           │  │ (Scene 2)      │ │
│  └──────────┬──────────┘  └───────────┬────────────┘  └───────┬────────┘ │
│             └────────────── JSON contracts only ───────────────┘        │
└─────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    JUDGMENT LAYER  (ZERO AI — pure code)                │
│  ┌───────────────────┐  ┌────────────────────┐  ┌──────────────────────┐│
│  │ PROMISE LEDGER    │  │ TRUST MODEL        │  │ ESCALATION STATE     ││
│  │ pending/kept/     │──│ Beta(α,β) per       │──│ MACHINE              ││
│  │ broken/at-risk/   │  │ debtor, decayed     │  │ stage = f(cause,     ││
│  │ renegotiated/     │  │                     │  │ level, trust, days,  ││
│  │ disputed          │  │                     │  │ amount) + HARD BOUNDS││
│  └───────────────────┘  └────────────────────┘  └───────────┬──────────┘│
└───────────────────────────────────────────────────────────────┼──────────┘
                                                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        ACTION LAYER                                     │
│  ┌───────────────┐ ┌───────────────┐ ┌──────────────┐ ┌───────────────┐│
│  │ Razorpay TEST │ │ Razorpay TEST │ │ Message      │ │ Evidence      ││
│  │ Payment Links │ │ OTM / mandate │ │ dispatcher   │ │ packet        ││
│  │ + Invoices    │ │ create/exec   │ │ (sim WA/     │ │ assembler     ││
│  │ APIs          │ │ /revoke       │ │ email queue) │ │ (dispute)     ││
│  └───────────────┘ └───────────────┘ └──────────────┘ └───────────────┘│
└─────────────────────────────────┬─────────────────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│   AUDIT TRAIL (append-only event log — every perception, decision,      │
│   action, outcome, with reason strings)  →  feeds DASHBOARD             │
│   Dashboard: Funnel view · Entity timelines · Trust curves ·            │
│   Human-review queue · 3-arm Metrics screen                             │
└─────────────────────────────────────────────────────────────────────────┘
```

## 3.2 B2B transaction flow (Scene 1) — full sequence
```
OVERDUE INVOICE #INV-042 (₹40,000, Acme Traders, 12 days late)
    │
    ▼
[TRIAGE] LLM + records check → cause = "behavioral delay"
    │   (payment-failed? no failed txn on record. delivery confirmed? yes.
    │    disputed? no. → behavioral path)
    ▼
[OUTREACH stage 1 — gentle] draft sent (sim WhatsApp)
    ▼
Debtor replies: "boss month end tight, will clear 40k by Friday pakka"
    ▼
[EXTRACT] → {level: L1, amount: 40000, date: Fri, cond: null, conf: 0.94}
    ▼
[LEDGER] promise P-117 created: pending, due Fri
[TRUST]  Acme posterior: Beta(5,3) → mean 0.62, medium-wide interval
    ▼
[STATE MACHINE] L1 + conf≥0.9 + amount ≤ mandate cap + debtor flagged
    eNACH-familiar → ACTION: offer scheduled one-time mandate
    ▼
[RAZORPAY TEST API] order created → mandate registration link sent
    "Approve once → ₹40,000 auto-debits Friday. Cancel anytime before."
    │
    ├── Debtor APPROVES (sim) → mandate M-88 registered
    │       ▼
    │   [Fri 9:00] pre-debit check → T-1 reminder already sent Thu
    │   [Fri] EXECUTE mandate → webhook: captured ₹40,000
    │       ▼
    │   [LEDGER] P-117 = KEPT · [TRUST] Beta(6,3) ↑ · invoice CLOSED
    │   [AUDIT] full chain logged with reasons
    │
    └── Debtor REFUSES mandate but promises anyway
            ▼
        [TRUST] refusal logged (neutral-pending: resolves + if pays
            manually on time, ↑ if breaks)
        [STATE MACHINE] fallback: payment link + T-1 confirm + due-date
            morning nudge → if broken +2d → firm notice quoting their
            own words → +5d voice note → +10d formal → HARD STOP → human
```

## 3.3 Commerce transaction flow (Scene 2) — full sequence
```
CART ABANDONED (₹2,499, dropped at PAYMENT stage, 2 failed OTP attempts)
    │  (Razorpay Magic Checkout abandoned-cart webhook = our trigger —
    │   this is REAL Razorpay infrastructure, we consume its exact schema)
    ▼
[CAUSE INFERENCE] signals: reached payment + OTP failures
    → hypothesis: payment friction (NOT price, NOT trust) conf 0.87
    ▼
[STATE MACHINE] friction path → NO discount, NO mandate.
    ACTION: instant alternate-method payment link (UPI intent)
    "Looks like the card OTP gave you trouble — here's a one-tap UPI link."
    ▼ (different cart: dropped when shipping appeared → price-shock path
       → threshold offer, still no discount-by-default)
    ▼ (different cart: reply "I get salary on 1st, will buy then")
    ▼
[EXTRACT] L1 timed intent → SCHEDULED MANDATE offer:
    "Approve now, ₹2,499 debits on the 1st, order ships same day.
     Cancel anytime before."
    → approved → executes on 1st → order auto-placed → recovered
    ▼ (different cart: dropped at payment, COD unavailable, first-time
       buyer on this store → trust path)
    ▼
DELIVERY-SECURED MANDATE offer (crown jewel):
    "Pay nothing today. Rs.2,499 debit is scheduled on delivery
     confirmation. Returned? Mandate cancelled instantly, nothing debited."
    → approve → order ships → [sim] delivery confirmed day+4 →
      mandate EXECUTED → merchant paid, zero RTO risk
    → alt branch: customer rejects item → mandate REVOKED before
      execution → nothing debited → logged as clean loss, NOT chased
      (stopping rule)
```

## 3.4 Escalation state machine (explicit)
```
STATES: NEW → TRIAGED → ENGAGED → PROMISED → {MANDATED | LINKED}
        → {KEPT ✓ | AT_RISK | BROKEN} → ESCALATE(1..4) → HUMAN_HANDOFF ✓
        + DISPUTED ← (reachable from ANY state, one-way, instant)

HARD BOUNDS (enforced in code, logged on every block):
  max_touches_per_week = 2 (per debtor/customer)
  renegotiation_cap = 2 promises, then no more mandate offers
  mandate_amount_cap = ₹1,00,000 demo config (larger → partial + link)
  mandate_must_equal_ledger_invoice_amount = TRUE (no invented numbers)
  retry_on_execution_failure = 1 (then link, then ladder, then human)
  dispute → instant stop, evidence packet, human. No exceptions.
  legal threshold → generated formal notice goes to MERCHANT for review,
     agent NEVER sends legal communication itself
  post-refusal re-offer of mandate = NEVER
```

## 3.5 Payment-error and failed-recovery handling (the "jump back" matrix)
| Failure | Detection | Recovery chain | Dead-end? |
|---|---|---|---|
| Mandate execution fails (insufficient funds) | webhook status | promise → AT_RISK (not broken) → same-day polite retry ×1 → payment link → ladder resumes at current stage | No — ends at human handoff |
| Mandate approval never happens (link ignored 48h) | timer | treat as soft refusal → fallback to link + T-1 flow | No |
| Customer approves then revokes pre-execution | webhook | neutral trust event → one gentle link, then normal ladder | No |
| Payment link fails (bank/gateway error) | txn status | auto-reissue link with alternate method ordering ×1 → then wait for next scheduled touch | No |
| LLM extraction low-confidence | conf score | ONE clarifying question → human-review queue | No |
| LLM misreads (wrong amount/date) | mandate amounts NEVER come from LLM — structural prevention | n/a — blast radius designed out | — |
| Debtor goes silent after promise | promise date passes, no txn | promise → BROKEN → trust ↓ → ladder continues from stage 3 | No |
| API outage (Razorpay test) | request exception | exponential backoff ×3 → action queued, audit notes delay → next clock tick retries | No |
| Recovery exhausts all stages | stage counter | HUMAN_HANDOFF card with full context + evidence → the system's honest "I couldn't" | By design: human, never loop |

**The rule: every path terminates in KEPT, CLEAN LOSS (logged, not chased), or HUMAN_HANDOFF. Nothing loops forever; nothing dies silently.**

## 3.6 Seamlessness (customer & vendor experience)
- **Customer sees:** a normal WhatsApp/email conversation + standard UPI approval screens they already know from IPO/autopay flows. Never a new app, never a login. Every mandate message states amount, date, and "cancel anytime" in plain words. Pre-debit reminders always sent (mirrors RBI norms).
- **Vendor/merchant sees:** a dashboard that works with zero config — sensible default bounds, an approval queue only for (a) low-confidence money actions and (b) formal-notice stage, plus a per-invoice timeline they can read in 10 seconds. Merchant can pause any thread with one click (kill-switch — mention in video).

---

# PART 4 — WORKING PROTOTYPE: HOW TO ACTUALLY BUILD IT

## 4.1 Repo structure
```
promise-keeper/
├── README.md              # problem → demo gif → metrics → run in 2 cmds
├── BUILD_LOG.md           # honest log of what broke + fixes (judging gold)
├── docker-compose.yml     # optional; plain `make run` also fine
├── .env.example           # RAZORPAY_TEST_KEY, ANTHROPIC_API_KEY
├── data/
│   ├── invoices.json          # 60 invoices, 12 debtors
│   ├── carts.json             # 12 abandoned carts (Scene 2)
│   ├── conversations/         # ~95 messages, threaded
│   └── ground_truth.json      # hand labels: cause + level per message
├── sim/
│   ├── clock.py               # virtual 45-day clock, seeded
│   └── personas.py            # scripted probabilistic behaviors
├── engine/
│   ├── schemas.py             # pydantic models = the JSON contracts
│   ├── perception/
│   │   ├── triage.py          # LLM call 1
│   │   ├── extractor.py       # LLM call 2 (+ few-shots)
│   │   └── cart_cause.py      # LLM call 3
│   ├── judgment/
│   │   ├── ledger.py
│   │   ├── trust.py           # Beta posterior + decay (~40 lines)
│   │   └── state_machine.py   # transitions + BOUNDS (the heart)
│   └── action/
│       ├── razorpay_client.py # test-mode links/invoices/mandates
│       ├── messenger.py       # sim WA/email queue
│       └── evidence.py        # dispute packet builder
├── eval/
│   ├── run_arms.py            # A/B/C, same seed → metrics.json
│   └── extraction_eval.py     # vs ground_truth → accuracy tables
├── api/main.py                # FastAPI: events in, state out
└── dashboard/                 # React (Vite) single page
```

## 4.2 The demo trick that makes it land: the TIME-WARP button
The dashboard has a **"Advance 1 Day ▶"** button driving the simulator clock. In the live demo/video you press it and the whole world moves: nudges fire, promises come due, mandates execute, trust curves update, ₹-recovered ticks up — all visibly, all logged. 45 days of recovery compressed into 90 seconds of watching money come back. This single UI decision is what makes "show measured money recovered across a batch" *visceral* instead of a table. Also add "Run to Day 45 ⏩" for the metrics reveal.

## 4.3 Clean output design (what judges actually look at)
1. **Funnel screen (home):** ₹ at risk → in recovery → recovered, split by cause bucket. Big honest numbers, Tier1/Tier2 labels visible.
2. **Entity timeline:** click any invoice/cart → vertical audit trail: every perception (with confidence), decision (with reason string), action (with API ref), outcome. This screen IS the audit-trail requirement.
3. **Trust view:** Beta curves per debtor, before/after events.
4. **Human-review queue:** held actions awaiting approve-click.
5. **Metrics screen:** 3-arm table + extraction accuracy table + cost-per-₹ + false-escalation rate. Screenshot this for the README.

## 4.4 Build order (unchanged days, sharpened)
Day 1–2 dataset+ground truth+simulator · Day 3 triage+eval · Day 4 extractor+eval (GATE ≥85%) · Day 5 ledger+trust+state machine · Day 6 Razorpay wiring+evidence packet · Day 7 dashboard+time-warp+TTS sample → FREEZE · Day 8 run arms, lock metrics, BUILD_LOG polish · Day 9 video+README · Day 10 buffer+submit.

## 4.5 What's real vs simulated (state this table verbatim in README + video)
| Real | Simulated |
|---|---|
| LLM perception on every message | Debtor/customer reply generation (persona scripts) |
| All judgment code, bounds, trust math | Bank approval/decline outcomes |
| Razorpay TEST-mode API calls (links, invoices, mandate objects) | Webhook firing (simulator emits Razorpay-schema events) |
| Extraction accuracy vs hand labels | The 45-day clock |
| Cost metering | Delivery confirmations |

---

# PART 5 — THE VIDEO (final, 4:45)
0:00 Problem taste: the two numbers (₹8.1L cr receivables, 70% carts) + "Razorpay named both directions; here they are solved by one engine" · 0:40 Scene 1 happy path with time-warp (extract→mandate→self-executing promise) · 1:30 Serial promiser + refusal-as-signal + trust curve + voice note · 2:15 Graceful failure: dispute → evidence packet → human · 2:45 Scene 2: friction cart gets a UPI link (no AI theatrics — right tool), trust cart gets DELIVERY-SECURED MANDATE with the revoke branch shown · 3:30 Metrics screen: Tier-1 measured tables first, then 3-arm with the honesty sentence · 4:05 Architecture 30s: "LLM sees and speaks, never spends" + bounds list + what broke (one BUILD_LOG item, fixed) · 4:30 Why Razorpay: OTM rail they shipped, use cases they haven't; every recovered rupee on their rails. Repo link.

---

# PART 6 — ONE-PAGE UNDERSTANDING CHECK (for you + your build AI)
Before writing code, you should be able to answer these from memory:
1. Why does the LLM never touch money? (blast radius: worst case = awkward message, never wrong debit)
2. What are the exact 3 terminal states of every recovery path? (KEPT / CLEAN LOSS / HUMAN_HANDOFF)
3. Why is mandate refusal a trust SIGNAL and not a failure? (reveals promise strength no message could)
4. Why delivery-secured mandate kills two problems at once? (customer trust + merchant RTO)
5. Why 3 arms and not 2? (isolates agent value vs both silence AND the status-quo generic reminder)
6. What's measured vs simulated, and which number leads the pitch? (extraction accuracy leads; recovery ₹ follows with honest framing)
7. What happens on insufficient funds at mandate execution? (AT_RISK → 1 retry → link → ladder → human; never instant "broken")
8. Why one LLM with four prompts instead of an agent swarm? (judgment boundaries are the hard part; fewer failure modes)

---

# PART 7 — THE RELIABILITY MESH (multi-agent, done right)

## 7.1 Five agents, one shared memory, zero model-to-model chat
Agents = bounded specialists with typed contracts, coordinating through STATE
(ledger + audit log), never through free-form conversation. Pitch line:
"Agents coordinate through state, not chat — because state is auditable and chat is not."

```
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│ PERCEPTION    │   │ JUDGMENT      │   │ ACTION        │
│ AGENT (LLM)   │──▶│ AGENT (code)  │──▶│ AGENT (APIs)  │
└──────┬────────┘   └──────┬────────┘   └──────┬────────┘
       │    typed JSON events / ledger          │
       ▼                  ▼                     ▼
┌─────────────────────────────────────────────────────────┐
│              SHARED STATE (ledger + audit log)          │
└──────────┬────────────────────────────────┬─────────────┘
           │ watches                        │ watches
┌──────────▼──────────┐            ┌────────▼────────────┐
│ SENTINEL             │            │ AUDITOR             │
│ reliability           │            │ accuracy             │
│ (zero LLM)            │            │ (2nd-pass LLM        │
│                       │            │  verifier)           │
└───────────────────────┘            └──────────────────────┘
```

## 7.2 SENTINEL — the reliability agent (zero LLM)
Watches every action for silent failure:
- Link delivery/open tracking: link sent but never opened in 48h → state machine notified (soft-refusal path), never assumed delivered
- Mandate execution watch: expected debit webhook missing at T+2h → flag, query API, re-drive
- API health: heartbeat ping; failures → exponential backoff ×3
- DEAD-LETTER QUEUE: any action failing 3 retries lands in a visible dashboard queue with full context — nothing ever vanishes
- CIRCUIT BREAKER: sustained API failure → pause all outbound actions, bank pending work, resume on recovery (audit-logged)
Honest claim for judges (never claim "never fails"):
**"No failure is ever silent, and every failure has a designed next step."**

## 7.3 AUDITOR — the accuracy agent (the one justified 2nd LLM use)
- Samples 10% of extractions → verification prompt ("does extraction match message?") → agreement rate
- Tracks rolling accuracy vs ground-truth set → live accuracy widget on dashboard
- QUARANTINE RULE: rolling accuracy < 85% → extractor demoted → ALL
  money-adjacent actions route to human-review queue until accuracy recovers
  (self-monitoring AI that benches itself when underperforming — say this in the video)
- Drift log: every quarantine/restore event in the audit trail

## 7.4 Build cost & placement
~0.5 day combined (retry logic exists in action layer; eval script exists from Day 4 → Auditor = eval made continuous + widget; Sentinel = timers + queue + panel).
Dashboard gets a SYSTEM HEALTH panel: API status, dead-letter count, rolling
extraction accuracy, quarantine state. Schedule: fold into Day 7 (dashboard day).
Judging value: Sentinel = "Failure recovery" criterion made visible;
Auditor = "how accurate is the system" answered live, not statically.

---

# PART 8 — UNIVERSALITY: FROM GOODS TO ZOMATO/OLA (pitch material, NOT build scope)

## 8.1 The limitation, named honestly
Scheduled + delivery-secured mandates fit DEFERRED consumption (clothing, goods).
Instant consumption (food, rides) has no "later" and no meaningful delivery-escrow
for a 25-minute ride. The engine still generalizes — because verticals differ in
CONFIG, not architecture.

## 8.2 Instrument positioning per vertical (state this clearly in pitch + README)
**Standard e-com / goods (clothing, electronics, D2C):**
  - CORE instrument = E-MANDATES (scheduled + delivery-secured) — deferred
    consumption fits commitment-on-a-date perfectly
  - ADDITIONAL feature = Reserve Pay failover (8.6) — repeat customers opt in,
    failed payments silently heal from the reserve (Tier-0)
**Quick commerce & food & rides (Zepto, Blinkit, Zomato, Swiggy, Ola):**
  - E-mandates DON'T fit (no "later" in instant consumption) — say this honestly
  - PRIMARY instrument = RESERVE PAY: these apps run on repeat orders from the
    same users daily/weekly — the perfect reserve customer. Weekly block
    ("₹1,500 for this week's orders") → every order completes with ZERO payment
    step → payment-stage drop-off structurally impossible for reserved users
  - SECONDARY = price/surge-triggered mandate: "approve at normal fare → ride
    books itself when surge clears" / "order fires when rain fee ends" —
    identical to the e-com sale-waiter, faster clock
ONE-LINE SUMMARY: goods get commitments (mandates) with reserve as safety net;
instant commerce gets reserves as the main rail with surge-triggers on top.
Same engine, same tiers — only the instrument menu flips per vertical.

## 8.3 The universality claim (say it precisely)
The engine is parameterized by three per-vertical settings:
1. **Intent half-life** — days (goods) vs minutes (food/rides) — drives timing engine
2. **Instrument menu** — scheduled + delivery-secured (goods) vs reserve + surge-triggered (instant)
3. **Trust dimension** — product quality (goods) vs price fairness (instant)
One engine, one config profile per vertical. An adapter layer, not new code.

## 8.4 Deliverable
ONE roadmap slide in the video + ONE config table in the README (goods profile
vs instant profile side by side). No Zomato simulation gets built — judges need
to BELIEVE the generalization, not watch it. If asked in the panel: walk the
surge-triggered ride example verbally; it maps 1:1 to the sale-waiter they saw demoed.

## 8.5 The WhatsApp commitment bridge (Scene 2 channel USP)
FACTS: Razorpay Payments on WhatsApp is LIVE (native in-chat checkout, 100+
methods, marketed for abandoned-cart recovery) — so raw "pay on WhatsApp" is
NOT novel. But their own FAQ states the WhatsApp integration supports
ONE-TIME PAYMENTS ONLY — no mandate setup, no commitments, in chat.
THE GAP WE FILL: today's WhatsApp recovery = template blast + pay-now link;
if the customer isn't ready NOW, the conversation dies. Our agent makes
WhatsApp the conversation layer and chooses WHICH payment object to drop in:
- Ready now → native WhatsApp payment (their existing rail)
- "Salary Friday" → mandate registration link (bridges to UPI app, confirmation
  returns to chat)
- Trust-hesitant → delivery-secured mandate link
PITCH LINE: "Two Razorpay products that have never met — Payments on WhatsApp
and One-Time Mandates — joined by an agent that knows which one the moment calls for."
SCOPE: our simulated WA thread in Scene 2 simply labels which rail each sent
object uses. Zero extra build. NOTE: this USP belongs to D2C/SME commerce
(where WhatsApp IS the sales channel) — NOT to Zomato/Ola, who own their app
surfaces; their answer stays Reserve Pay + surge-triggered (8.2).

## 8.6 RESERVE FAILOVER — Tier-0 recovery (small build beat + big roadmap)
CONCEPT: a pre-authorized Reserve Pay block as universal fallback funding.
Primary payment fails (card decline, OTP timeout, bank glitch) → check active
reserve → debit reserve → transaction completes. Recovery with ZERO touches.

THE RECOVERY HIERARCHY (this ordering IS the system's philosophy — put it in the video):
  Tier 0 — silent failover: reserve exists → auto-debit → recovered, 0 touches
  Tier 1 — instant conversational: right-method link within minutes
  Tier 2 — commitment instruments: mandates for deferred intent
  Tier 3 — escalation ladder: staged pressure for broken commitments
  Terminal — human handoff / clean loss
Pitch line: "The best recovery conversation is the one that never needs to happen."

HONEST CAVEATS (state proactively):
- Same-bank failure: bank's UPI stack down → reserve at same bank fails through
  the same dead pipe → agent recommends reserve on a DIFFERENT bank account at setup
- Insufficient funds is STRUCTURALLY SOLVED for reserves: blocked funds are
  ring-fenced at authorization → the #1 failure reason cannot occur (defensible claim)
- Universal cross-merchant reserve = ROADMAP: requires Razorpay-as-aggregator
  holding the block (SBMD was designed for multi-debit network commerce; funds
  never leave the customer's account, so no wallet/PPI issue). Per-merchant
  reserve is live rail today; universal is the endgame framing.
- Retention instrument only: helps opted-in repeat customers, not first-timers. Say so.

BUILD SCOPE: one Scene 2 demo beat (~few hours): payment-failed event for a
reserve-holding customer → state machine reserve pre-check → simulated debit →
funnel ticks "recovered, 0 touches" → one-line audit entry. Metrics screen gets
a "touches per recovery" row where Tier-0 = 0. Universal-reserve vision = one
roadmap slide beside Zomato/Ola.
