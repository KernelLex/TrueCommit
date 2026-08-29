# Promise Keeper

**₹8.1 lakh crore of Indian MSME money is stuck in delayed payments** ([Economic Survey 2025-26](https://telanganatoday.com/indian-msmes-face-mounting-delayed-payments-recordent-report-reveals), tabled January 2026). **~70% of online carts are abandoned before checkout** ([Baymard Institute](https://baymard.com/lists/cart-abandonment-rate), 4,500+ checkouts studied). Both are old problems with a newer, sharper diagnosis: today's recovery tools — reminder emails, WhatsApp nudges, dunning sequences — all do the same thing. They ask the customer to decide again later, at a colder moment than the one where they actually said yes.

Promise Keeper converts stated intent into a self-executing payment instrument (a scheduled mandate on Razorpay's rails) the moment a customer commits, and only falls back to a message when there's no commitment to capture. **Razorpay already ships the instrument** — one-time mandates, UPI Reserve Pay, delivery-secured blocks. **Nobody ships the judgment about which instrument to deploy, to whom, when — read from unstructured intent.** That judgment layer is what this repo builds.

Two scenes, one engine:
- **Scene 1** (B2B overdue-invoice recovery): triage → promise extraction → trust-weighted escalation → promise-to-mandate conversion
- **Scene 2** (checkout drop-off recovery): cause triage → matching instrument (scheduled mandate / delivery-secured mandate / payment link)

Built for the Razorpay AI Buildathon 2026, Track 3 (AI Revenue Recovery).

## Quick start (2 commands)

```
# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File setup.ps1
powershell -ExecutionPolicy Bypass -File run.ps1

# Mac/Linux
./setup.sh
./run.sh
```

`setup` creates a Python venv, installs dependencies, and installs the dashboard's `node_modules`. `run` starts the API and the dashboard together. The app runs fully offline with no API keys — copy `.env.example` to `.env` and add real keys only for the live-Razorpay / real-channel demos.

Run the test suite (642 tests as of this writing, fully offline):
```
.venv/Scripts/python.exe -m pytest tests/   # Windows
.venv/bin/python -m pytest tests/           # Mac/Linux
```

## The honesty split

This project measures what it can and labels what it can't (CLAUDE.md's design law): **Tier 1** figures (extraction accuracy vs. hand labels, triage accuracy, cost per ₹) are genuinely measured against this repo's dataset. **Tier 2** figures (the 3-arm recovery comparison in `eval/run_arms.py` / `metrics.json`) are simulated against frozen, scripted personas and are always labeled as such — never presented as real-world proof. See `tracking/TRACK_BAR.md` for the full breakdown with proof links into the repo.

### Tier 1 — measured

| Metric | Result | Gate | Notes |
|---|---|---|---|
| Promise extraction accuracy (L1–L5 vs. hand labels) | **97.7%** heuristic (in-sample) · **88.6%** qwen2.5:7b | ≥85% | The heuristic number is in-sample (rules authored with the labels visible) — the honest ceiling, not a held-out estimate. The LLM number is the one that means something as a generalization claim. |
| Root-cause triage accuracy | **91.7%** on the 24 invoices with a message thread · 71.7% across all 60 | ≥90% | Gated on threaded invoices by explicit ruling (`tracking/DECISIONS.md`) — a triage call with zero messages to read is an information-ceiling problem, not a classifier failure, and every provider tested (heuristic and two Ollama model sizes) hits the same wall on those cases. |
| Reproducibility | Byte-identical across independent runs at seed 42 | diff = empty | Dataset generator, simulator, integration runner, and the 3-arm runner all re-verified — see `tracking/BUILD_QUALITY.md`. |
| Test suite | **642 tests, offline by default** | — | `pytest tests/` — no network call happens unless a `PK_REAL_*` flag is explicitly opted into, with one deliberate exception: a live smoke test against a local Ollama server (skips cleanly if Ollama isn't running). |

### Tier 2 — simulated (frozen personas, never tuned to flatter the result)

Same seed, same 60-invoice/12-debtor dataset, same frozen persona behavior tables, three arms:

| Arm | Recovered | % of active value | Touches per recovery | DSO |
|---|---|---|---|---|
| A — silence (no intervention) | ₹0 | 0.0% | — | — |
| B — generic reminder every 3 days, no judgment layer | ₹55,08,943 | 71.2% | 11.26 | 20.0 days |
| C — Promise Keeper (this system) | ₹23,24,347 | 30.0% | **4.76** | 42.0 days |

**The honest headline, not smoothed over:** Arm B recovers more nominal rupees than Arm C. It has no touch-frequency limit; Arm C is bounded by the same per-debtor contact cap (`MAX_TOUCHES_PER_WEEK`) real regulation — and this project's own stopping rules — impose. The number that tells the real story is touches per recovery: **Arm C needs less than half of Arm B's contact attempts** to recover a promise. An unbounded baseline can extract slightly more in a simulation that carries no real-world cost for contacting someone 15 times in 6 weeks; a compliant system can't do that, and shouldn't be scored as if it should. Full reasoning: `tracking/BUILD_LOG.md`, 2026-08-27.

**Mandate-acceptance sensitivity band, not a point estimate.** The whole thesis rests on what fraction of debtors accept a mandate offer instead of refusing it — a number this project has no real-world data for, so instead of picking one, every headline figure above is re-run across a 10%–60% acceptance range:

| Target acceptance | Recovered | % of active value |
|---|---|---|
| 10% | ₹24,82,090 | 32.1% |
| 20% | ₹24,82,090 | 32.1% |
| 30% | ₹28,40,212 | 36.7% |
| 40% | ₹23,48,197 | 30.3% |
| 50% | ₹23,48,197 | 30.3% |
| 60% | ₹23,48,197 | 30.3% |

(Plateaus at adjacent targets are real, not a bug — checked directly: this heavily-gated system offers so few mandates in a 45-day run that nearby target rates can land on the same side of every RNG draw that actually fires. See `tracking/BUILD_LOG.md`.)

## Architecture

```
                    ┌───────────────┐   ┌───────────────┐   ┌───────────────┐
  inbound message → │  PERCEPTION   │──▶│   JUDGMENT    │──▶│    ACTION     │ → real Razorpay /
                    │ triage/extract│   │ trust · bounds│   │ Messenger ·   │   Twilio / Telegram
                    │ (LLM, opt-in; │   │ state machine │   │ mandate/link  │
                    │  heuristic by │   │  (ZERO LLM)   │   │  dispatch     │
                    │  default)     │   └──────┬────────┘   └──────┬────────┘
                    └───────────────┘          │                   │
                                                ▼                   ▼
                                    ┌─────────────────────────────────────┐
                                    │   SHARED STATE (ledger + audit log)  │  append-only, every
                                    └──────────┬───────────────┬──────────┘  action logged BEFORE
                                               watches        watches        it executes
                                    ┌──────────▼──────┐ ┌──────▼──────────┐
                                    │    SENTINEL      │ │     AUDITOR     │
                                    │ retry · backoff · │ │ samples extract-│
                                    │ dead-letter ·     │ │ ions, rolling   │
                                    │ circuit breaker   │ │ agreement rate, │
                                    │  (ZERO LLM)       │ │ quarantine gate │
                                    └───────────────────┘ └─────────────────┘
```

Five agents, one shared append-only state, zero model-to-model chat — agents coordinate through the ledger and audit trail, never through free-form conversation between them. The one design law that matters most: **the LLM can SEE and SPEAK, never SPEND** — no LLM output ever directly becomes an amount, a debit date, or a state transition. LLM → JSON → pydantic validation → deterministic state machine decides. Full detail: `promise-keeper-v3-final-master-doc.md` §7, `tracking/AI_JUDGMENT.md` (every place an LLM is used, and every place one deliberately isn't).

## What's real vs. simulated

Verified live against Razorpay's TEST-mode sandbox, 2026-08-26 (`tracking/TRACK_BAR.md` §0):

| Capability | Status |
|---|---|
| Payment Links, Invoices, Customers, plain Orders | ✅ Real (`short_url`s genuinely issued) |
| Mandate registration links (UPI Autopay + netbanking eMandate) | ✅ Real |
| **Full mandate lifecycle** — create → authorize → execute → revoke, via the Subscriptions API | ✅ Real, human-verified end to end (netbanking eMandate authorization, real recurring token, real revoke confirmed gone) |
| UPI Autopay execution | ⬜ Account-level gated on this sandbox (not this project's limitation — Razorpay's own account enablement) |
| Automated 45-day run's own mandate execute/revoke | Simulated by default — a scheduling constraint, not a capability gap: the simulator advances 45 days in seconds, Razorpay's real billing engine does not |
| Real phone calls (IVR: press 1/2 for a live mandate or link) | ✅ Real — Twilio, gated to manual/opt-in clicks only, never the autonomous ladder |
| Real WhatsApp confirmation after an IVR selection | ⬜ Attempted, genuinely fails today — Twilio requires a pre-approved Content Template for a fresh outbound WhatsApp send outside an active session window; the system audits the failure honestly rather than claiming success (`tracking/DECISIONS.md`, 2026-08-28) |
| Real Telegram messages (text + generated voice audio) | ✅ Real, gated the same way as the phone call |
| A real network failure mid-run | ✅ Genuinely dead-lettered with retry/backoff, not left to crash the run (`tracking/BUILD_LOG.md`, 2026-08-29) |

## AFA segmentation: where the regulator drew the line we were missing

RBI's Digital Payments – E-Mandate Framework, 2026 (notified 21 April 2026) sets a ₹15,000-per-transaction threshold for Additional Factor of Authentication (AFA) on recurring e-mandate debits. This is the segmentation boundary this project's thesis was originally missing — it isn't a design choice Promise Keeper invented, it's a line the regulator already drew:

- **Under ₹15,000:** the mandate executes unassisted. No further customer action is needed once it's registered — this is the "self-executing instrument" case the whole product thesis rests on.
- **Above ₹15,000:** the mandate still schedules, still pre-commits the debtor, and still sends the same pre-debit (T-1) and post-debit notices — but execution requires an additional factor of authentication, i.e. a human-assisted step at debit time. The instrument still converts a stated promise into a scheduled commitment; it just can't complete unattended past this line.

**A genuine scope ambiguity, stated honestly rather than resolved in our favor:** sources disagree on whether this ₹15,000 AFA line extends to NACH / netbanking eMandate — the primary rail this project's own Razorpay sandbox testing verified as fully real (`tracking/TRACK_BAR.md` §0). Law-firm summaries of the framework scope its text explicitly to "cards, PPIs, and UPI" and do not name NACH:

> "RBI's Digital Payments – E-mandate Framework, 2026... appl[ies] to all payment system providers and participants handling recurring transactions through cards, UPI, and PPIs"
> — [SCC Times, "RBI notifies Digital Payments — E-mandate Framework, 2026"](https://www.scconline.com/blog/post/2026/04/24/rbi-issues-digital-payments-e-mandate-framework-2026/)

At least one consumer-facing publication, by contrast, treats card, e-NACH, and UPI AutoPay as following the same aligned thresholds:

> "All three mandate structures follow aligned guidelines: transactions below ₹15,000 can go through without an additional factor authentication (AFA)..."
> — [PayPro Global, "What is India's RBI e-Mandate?"](https://payproglobal.com/answers/what-is-indias-rbi-e-mandate/)

We could not find primary RBI text resolving this either way as of this writing. **This project designed for the stricter reading** — treating netbanking eMandate as if it were AFA-gated above ₹15,000 the same way cards/UPI/PPIs explicitly are — rather than assuming the ambiguity in our own favor. The pre-debit and post-debit notice requirements (also part of this same framework) are built for every mandate regardless of amount; see `tracking/PROBLEM_TASTE.md` (sourced claims) and `tracking/TRACK_BAR.md` §2 (compliant escalation) for the implementation.

## Limitations, stated honestly

- **WhatsApp confirmations after a real IVR call don't land today.** The real Razorpay object still gets created; only the "here's your link" message fails, with a real, audited Twilio rejection (`ContentSid Required`). Fixing it needs a template-first/free-form-after-reply architecture that was scoped but deliberately not built this pass (Track B, not selected).
- **UPI Autopay execution is gated on this Razorpay TEST account**, not a code limitation — netbanking eMandate is this project's primary, fully-verified rail instead.
- **Whether RBI's ₹15,000 AFA threshold extends to NACH/netbanking eMandate is genuinely ambiguous** in publicly available sources — see the AFA section above. This project designed for the stricter reading rather than assuming the gap in its own favor.
- **The Auditor's default 2nd-pass verification is a self-agreement check when heuristic is the active perception provider** (this project's own default) — it becomes a meaningful independent cross-check only when a real LLM provider is active. Stated in the code and in `tracking/AI_JUDGMENT.md`, not hidden.
- **Scene 2 (checkout drop-off) is the breadth proof, not the deep build** — Scene 1 (B2B receivables) is where most of the engineering and testing depth lives, by the project's own pre-agreed scope-cut order. The cause → instrument judgment layer (friction/price-shock/comparison/unknown → payment link, timing → scheduled mandate, trust → delivery-secured mandate with the revoke branch) is built and tested against the real 12-cart dataset, but carts carry no persona model the way Scene 1 debtors do — outcomes for the mandate-bearing causes are scripted deterministically per master doc §3.3 rather than drawn from a behavioral simulation, and the plain-link causes are closed out by the same idle-sweep/timeout machinery Scene 1 uses rather than a modeled conversion rate.
- **Tier 2 recovery numbers are simulated against frozen personas**, never real debtor behavior — see the honesty split above. The mandate-acceptance rate the whole thesis is most sensitive to has no real-world data behind it, which is exactly why it's reported as a band, not a point estimate.

## More detail

- `promise-keeper-v3-final-master-doc.md` — the full spec (what and why)
- `BUILD.md` — day-by-day build plan and acceptance criteria
- `CLAUDE.md` — design laws and tracking discipline this repo was built under
- `tracking/` — the honesty paper trail: what broke (`BUILD_LOG.md`), why this problem (`PROBLEM_TASTE.md`), the track's own bar (`TRACK_BAR.md`), where AI is/isn't used (`AI_JUDGMENT.md`), build health (`BUILD_QUALITY.md`), and every deviation from spec (`DECISIONS.md`)
