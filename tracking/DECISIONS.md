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

### 2026-08-26 — Build sequencing: Phase A / B / C / D split
**What:** Rather than executing BUILD.md's Day 0–10 strictly in calendar order, work is split into phases by external dependency: Phase A = everything needing zero external API (repo scaffold, schemas, tracking docs, dataset+simulator+ground truth, frozen personas, judgment layer with tests, sim messenger/Sentinel/evidence scaffolding, prompt files, FastAPI skeleton). Phase B = triage+extractor (needs `ANTHROPIC_API_KEY`). Phase C = Razorpay sandbox verification + real wiring (needs Razorpay TEST keys). Phase D+ = dashboard, metrics, video, submit — downstream of B+C.
**Why:** at kickoff (2026-08-26) the user had Razorpay TEST keys pending (not yet obtained) and hadn't yet handed over `ANTHROPIC_API_KEY`. Rather than stall waiting on either, everything unblocked by external keys is built first.
**Cost:** none to final scope — this is a resequencing, not a cut. BUILD.md's day-by-day acceptance criteria are still the target; Phase A just front-loads the parts with zero external dependency (which includes all of Day 5's judgment layer, normally scheduled after Day 3/4, since it only depends on `schemas.py`).
**Full plan reference:** see the approved plan for this pass — repo-relative concept, not persisted as a separate file, but summarized in `tracking/BUILD_QUALITY.md` progress log and reflected in this file's dated entries going forward.
