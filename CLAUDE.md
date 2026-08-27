# CLAUDE.md — Promise Keeper: Instructions for Claude Code
### Read this first, every session. This file tells you what we're building, the laws you must never break, and the tracking files you must keep updated as we work.

---

## 1. WHAT THIS PROJECT IS

**Promise Keeper** — my submission for the Razorpay AI Buildathon 2026 (Track 3: AI Revenue Recovery). Deadline: Sep 5, 2026. It's a hiring competition — the repo will be read by Razorpay engineers deciding whether to interview me. Code quality and honesty matter as much as features.

**The thesis:** recovery tools send messages; messages ask the customer to decide again later at a colder moment. Promise Keeper converts stated intent into self-executing payment instruments (one-time e-mandates on Razorpay rails) and only falls back to messages when there's no commitment to capture.

**Two scenes, one engine:**
- Scene 1 (deep build): B2B overdue-invoice recovery — triage → promise extraction (L1–L5) → trust-weighted escalation → promise-to-mandate conversion
- Scene 2 (breadth proof): checkout drop-off recovery — cause triage → matching instrument (scheduled mandate / delivery-secured mandate / payment link)

## 2. FILE AUTHORITY ORDER (conflicts resolve upward)
1. `promise-keeper-v3-final-master-doc.md` — the spec. WHAT and WHY. Never contradict it silently; if code needs to deviate, log the deviation in `tracking/DECISIONS.md` and tell me.
2. `BUILD.md` — the task order, day-by-day, with acceptance criteria. HOW.
3. This file — working rules and tracking duties.

## 3. DESIGN LAWS (never break these, never "temporarily" bypass them)
1. **The LLM can SEE and SPEAK, never SPEND.** No LLM output ever directly becomes an amount, a debit date, or a state transition. LLM → JSON → pydantic validation → deterministic state machine decides.
2. **Mandate amounts are copied from ledger records only.** Never generated, never interpolated by a model.
3. **Every action writes to the audit log BEFORE it executes.** No exceptions, including retries and failures.
4. **All bounds live as constants at the top of `state_machine.py`** and every action passes `check_bounds()`. Bounds: max 2 touches/week per debtor, renegotiation cap 2, mandate cap ₹1,00,000, 1 retry on execution failure, dispute = instant stop from any state, no mandate re-offer after refusal, legal-stage notices go to merchant for review — the agent never sends legal communication.
5. **Every recovery path terminates** in exactly one of: KEPT / CLEAN_LOSS / HUMAN_HANDOFF. No loops, no silent deaths. There are tests for this — keep them passing.
6. **Fixed seed (SEED=42) everywhere.** Simulator, personas, arm runs. Two identical runs must produce identical output.
7. **Persona behavior policies are FROZEN before the agent is built** (anti-circularity). Personas react to message PROPERTIES (timing, tone stage, instrument offered) — never to which arm sent the message. The dataset includes adversarial never-pay personas. Do not tune personas to make the agent look good — if I ask you to, refuse and cite this line.
8. **Honesty split in all metrics:** Tier 1 = genuinely measured (extraction accuracy vs hand labels, triage accuracy, false-escalation rate, cost per ₹). Tier 2 = simulated (3-arm recovery numbers), always labeled as simulation. Never present Tier 2 as real-world proof.

## 4. TRACKING FILES YOU MUST MAINTAIN (this is a core duty, not optional)
Create a `tracking/` folder. Update these files AS WE WORK — at minimum at the end of every working session, and immediately when the trigger event happens. If a session ends and these are stale, that session failed.

### `tracking/BUILD_LOG.md` — Failure Recovery evidence (judges explicitly ask "what broke and what you did about it")
Every real bug, dead end, or surprise: date · what broke · root cause · fix · what changed in the design because of it. REAL entries only — never invent or dramatize. This file is judging gold precisely because it's honest.

### `tracking/PROBLEM_TASTE.md` — "did you pick something that actually matters"
Running list of every claim we make about why this problem matters, each with its source: the ₹8.1 lakh crore MSME receivables figure, ~70% cart abandonment, the two named track directions we cover, and the actual gap (corrected 2026-08-27 — see BUILD_LOG: Razorpay has shipped delivery-secured/reserve-block mandate mechanics, e.g. the Feb 19 2025 OTM hotel-mandate blog post and UPI Reserve Pay/SBMD, live since the Feb 20 2026 NPCI+Claude agentic-payments announcement — the instrument is not the gap): **Razorpay ships the instrument. Nobody ships the judgment about which instrument to deploy, to whom, when — read from unstructured intent.** Separately, their WhatsApp Payments integration specifically supports one-time payments only, no in-chat mandate setup (still needs its own primary source before README/video use — see PROBLEM_TASTE.md). When a claim enters the README or video script, it must exist here first with a source.

### `tracking/TRACK_BAR.md` — the bar, verbatim: "Don't just identify the problem. Show measured money recovered across a batch, with compliant escalation, stopping rules, and an audit trail."
Four sections, each with status (⬜ not started / 🟡 partial / ✅ done + proof link into the repo):
- **Measured money recovered across a batch** — 3-arm run outputs, metrics.json path, screenshot path, plus the Tier1/Tier2 honesty framing
- **Compliant escalation** — state machine stages, merchant-review gate at legal stage, pre-debit reminders
- **Stopping rules** — the bounds constants + the pytest file that tries to violate each one
- **Audit trail** — the append-only log implementation + the dashboard timeline screen
Nothing ships in the video that isn't ✅ here.

### `tracking/AI_JUDGMENT.md` — "right tool in the right place, and where you chose not to use one"
Two lists, kept current: (a) every place an LLM is used (which prompt, why, confidence gate), (b) every place we deliberately did NOT use AI (state transitions, trust math = Beta posterior, bounds, money movement, metrics). When either list changes, update same session.

### `tracking/BUILD_QUALITY.md` — "does it run, is it structured, would you trust it"
Checklist kept truthful: cold-start runs in 2 commands (last verified date) · tests passing count · no secrets in repo (grep check date) · reproducibility check (two seeded runs diffed) · which acceptance criteria from BUILD.md are green.

### `tracking/DECISIONS.md`
Any deviation from the master doc or BUILD.md: what changed, why, what it costs us. Includes scope cuts — the pre-agreed cut order is: Reserve failover beat → Scene 2 entirely → Auditor → TTS voice. Scene 1 alone still covers two named track directions and can win.

## 5. DAY-1 PRIORITIES (before any other code)
1. **Verify Razorpay TEST mode actually supports the mandate objects we need** (one-time mandate / UPI Autopay token lifecycle: create, register, execute, revoke). This is the crown-jewel risk. Whatever the sandbox can and cannot do goes into `tracking/TRACK_BAR.md` and drives the honest "real vs simulated" table. If OTM/SBMD is gated in sandbox, tell me immediately — we re-frame honestly on Day 1, not Day 6.
2. Write `engine/schemas.py` (all contracts from BUILD.md §2) before any feature code.
3. Freeze personas: write `sim/personas.py` behavior tables, commit, tag `personas-frozen`. The agent gets built only after that tag exists.

## 6. WORKING CONVENTIONS
- Python 3.11+, FastAPI, SQLite, pydantic v2; React (Vite) dashboard; pytest for everything in `judgment/`
- LLM: Anthropic API, model `claude-sonnet-5` (corrected from this file's original `claude-sonnet-4-6`, which is retired — see tracking/DECISIONS.md), JSON-schema-constrained outputs via `output_format=<pydantic model>` on `client.messages.parse()` (the installed SDK removed temperature/top_p/top_k entirely — structured outputs is the current, stronger mechanism for what "temp 0 + JSON schema enforced" was asking for); prompts live in `engine/perception/prompts/*.md`, versioned in git
- Cache perception results keyed by message_id (arms reuse them; re-runs must be instant and free)
- Commit style: small commits, message = what + why. Tag freeze points (`personas-frozen`, `v1.0-freeze` end of Day 7)
- Never commit secrets. `.env` is gitignored; `.env.example` has placeholders only. Grep for `rzp_` and `sk-ant` before any push.
- When an acceptance criterion in BUILD.md passes, check it off there AND reflect it in `tracking/BUILD_QUALITY.md`
- If I ask for a new feature not in the docs: remind me of the cut order and the freeze date (feature freeze = end of Day 7, Sep 1), then implement only if I confirm.

## 7. WHAT WINNING LOOKS LIKE
A repo that runs in 2 commands on a stranger's machine · tests pass · time-warp demo visibly moves money · extraction accuracy ≥85% vs hand labels · every claim in the video traceable to a ✅ in `tracking/TRACK_BAR.md` · a BUILD_LOG full of real scars. The judges are hiring a builder, not buying a pitch — every hour hardening proof beats every hour adding concepts.
