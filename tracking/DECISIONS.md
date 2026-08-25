# DECISIONS.md — Deviations from the master doc or BUILD.md

Any deviation: what changed, why, what it costs us.

---

## Pre-agreed scope-cut order (from CLAUDE.md §4, recorded here verbatim for reference)
If time runs short, cut in this order — later items only cut after earlier ones are already gone:
1. Reserve failover beat (master doc §8.6 Tier-0 demo)
2. Scene 2 entirely (checkout drop-off recovery)
3. Auditor (master doc §7.3)
4. TTS voice note

Scene 1 alone (B2B receivables) still covers two named track directions and can win — this is the floor, not a target.

---

## Log

### 2026-08-26 — LLM wiring: model id, temperature removed, structured outputs
**What:** CLAUDE.md/BUILD.md specify model `claude-sonnet-4-6` and "temp 0, JSON schema enforced" for every perception call. Both are stale against the `anthropic` Python SDK actually installed (1.0.0): `claude-sonnet-4-6` is a retired id (current Sonnet is `claude-sonnet-5`), and `temperature`/`top_p`/`top_k` have been removed from the Messages API entirely on Sonnet 5 / Opus 5 / Fable 5 — passing any of them is a 400. In their place, the SDK ships **structured outputs**: `client.messages.parse(..., output_format=<a pydantic model>)` returns `response.parsed_output` already validated against that model's schema server-side.
**Why this isn't a downgrade:** structured outputs is a *stronger* determinism/reliability guarantee than temp-0 sampling ever was — it's schema-validated by the API itself, not just low-entropy token sampling that could still drift off-schema. It's also a closer match to what BUILD.md was actually asking for ("JSON schema enforced") than manually parsing prose+JSON out of a temp-0 completion would have been.
**Fix:** `engine/perception/client.py` uses `MODEL = "claude-sonnet-5"` and `call_structured()` wraps `messages.parse()` with `output_format`. CLAUDE.md §6 updated to match. Confirmed via the `claude-api` skill and by introspecting the installed SDK's `message_create_params`/`json_output_format_param` source directly (no `temperature` field exists anywhere in the installed SDK's types).
**Cost:** none — this only affects the mechanism, not any prompt content or accuracy target. Still fully blocked on `ANTHROPIC_API_KEY` for an actual live call (Phase B).

### 2026-08-26 — data/generate.py: the L1–L5 extraction ladder definitions
**What:** BUILD.md/master doc reference "L1–L5" levels throughout (e.g. the Acme Traders example extracts `{level: L1, amount: 40000, date: Fri, cond: null}`) but never define what distinguishes L1 from L2 from L3 etc. — that's deferred to Day-4 prompt-writing. Since the Day 1–2 dataset needs concrete per-message labels now, I fixed a 5-level ladder in `data/generate.py`:
- L1 — firm + unconditional: explicit amount AND explicit date
- L2 — firm but partially specific: only one of {amount, date} explicit
- L3 — conditional OR structured/partial: can't be captured by one amount+date pair — either contingent on a stated external condition ("once my client pays me"), or a split/partial-payment offer ("half now, half in 2 weeks"); the `condition` field carries the qualifying detail either way
- L4 — vague/soft acknowledgment: generic intent, no concrete amount/date
- L5 — no commitment: silence-equivalent, deflection, dispute, or refusal
**Why L3 covers two cases:** initially treated "conditional" and "partial-payment" as separate concerns while labeling the dataset, then caught during a self-review pass (BUILD.md's "judge mode" re-read) that a strict contingency-only L3 didn't fit the partial-payment thread (T-16) — broadened the definition rather than inventing a 6th level, since both cases share the same practical consequence: the state machine can't execute a single clean mandate from the message alone.
**Cost:** none to scope — same one-line-change-plus-relabel note as the InvoiceCauseType decision below applies here too if Day-4 prompt-writing (Phase B) wants a different split. BUILD.md's own fallback path (documented in BUILD.md Day 4) is "merge L2/L4" if accuracy is low, which is coherent under this ladder (L2 and L4 are the two levels most likely to be confused: both lack full amount+date specificity, differing mainly in how resolvable the vague part is).

### 2026-08-26 — data/generate.py: judge-mode self-review caught a real mislabel
**What:** Per BUILD.md's Day 1–2 acceptance criterion ("a second person, or your coding AI in judge mode, reads 10 random conversations and agrees the labels are fair"), re-read a sample of generated threads (T-05, T-06, T-16, T-18, T-22) critically after first generation. Found `M-06-4` ("sending 27000 today, rest by the 15th") originally labeled `L1, amount=27000, date=<the 15th>` — this wrongly attached the FIRST tranche's amount to the SECOND tranche's date, misrepresenting the message (it isn't "27000 due by the 15th", it's "27000 today + a different 27000 by the 15th"). Relabeled to `L3, amount=27000, date=null, condition="Rs.27,000 (this tranche) sent today; remaining Rs.27,000 due by the 15th"`, consistent with the L3 partial-payment pattern above.
**Also fixed:** message timestamps were originally `i × 6h` apart (a 5-message thread compressed into ~24h), which made T-22's "Following up — Friday's date passed" follow-up read as sent before Friday had even arrived. Changed to `i` days apart (± a few hours) — realistic for a B2B collections cadence and resolves the narrative-timestamp mismatch.
**Cost:** none — caught before any Phase B work (triage/extraction eval) depends on this file. This is exactly the kind of check the acceptance criterion is meant to force; recorded here rather than just silently fixing it, per CLAUDE.md's honesty-in-tracking-files rule.

### 2026-08-26 — schemas.py: fields and a taxonomy BUILD.md didn't spell out
**What:** `engine/schemas.py` adds three boolean fields to `Invoice` beyond BUILD.md §2's literal listing — `delivery_confirmed`, `payment_failed_attempt`, `enach_familiar` — and defines a 5-value `InvoiceCauseType` literal (`payment_failed` / `delivery_dispute` / `cashflow_delay` / `dispute` / `non_responsive`) plus an `InvoiceCause` model (Scene 1's analogue of the already-specified `CartCause`).
**Why:** BUILD.md Day 3 says triage inputs are "invoice + payment records + delivery flag + thread-so-far" and the master doc §3.2 walkthrough has the state machine branch on "debtor flagged eNACH-familiar" — none of that is representable with BUILD.md's literal 7-field `Invoice` contract, and BUILD.md defers the exact "5 causes" enumeration to Day-3 prompt-writing without listing them. Rather than block schema-writing on that, I picked a denormalized (flags live on Invoice, not a separate Debtor entity BUILD.md never lists a `debtors.json` for) and a defensible 5-cause taxonomy that matches the one worked example in the master doc (checks failed-txn → delivery → dispute → falls through to behavioral/cashflow delay).
**Cost:** none to scope. If Day-3 prompt-writing (Phase B, needs `ANTHROPIC_API_KEY`) surfaces a better taxonomy split, `InvoiceCauseType` is a one-line change plus a ground-truth relabel — cheap now, before any data is hand-labeled against it.

### 2026-08-26 — Build sequencing: Phase A / B / C / D split
**What:** Rather than executing BUILD.md's Day 0–10 strictly in calendar order, work is split into phases by external dependency: Phase A = everything needing zero external API (repo scaffold, schemas, tracking docs, dataset+simulator+ground truth, frozen personas, judgment layer with tests, sim messenger/Sentinel/evidence scaffolding, prompt files, FastAPI skeleton). Phase B = triage+extractor (needs `ANTHROPIC_API_KEY`). Phase C = Razorpay sandbox verification + real wiring (needs Razorpay TEST keys). Phase D+ = dashboard, metrics, video, submit — downstream of B+C.
**Why:** at kickoff (2026-08-26) the user had Razorpay TEST keys pending (not yet obtained) and hadn't yet handed over `ANTHROPIC_API_KEY`. Rather than stall waiting on either, everything unblocked by external keys is built first.
**Cost:** none to final scope — this is a resequencing, not a cut. BUILD.md's day-by-day acceptance criteria are still the target; Phase A just front-loads the parts with zero external dependency (which includes all of Day 5's judgment layer, normally scheduled after Day 3/4, since it only depends on `schemas.py`).
**Full plan reference:** see the approved plan for this pass — repo-relative concept, not persisted as a separate file, but summarized in `tracking/BUILD_QUALITY.md` progress log and reflected in this file's dated entries going forward.
