# AI_JUDGMENT.md — "right tool in the right place, and where you chose not to use one"

Two lists, kept current per CLAUDE.md §4. Update the same session either list changes.

---

## (a) Every place an LLM is used

One model everywhere: **Claude Sonnet (`claude-sonnet-4-6`) via the Anthropic API.** No fine-tuning, no RAG, no agents-calling-agents (master doc §2.1).

| # | Prompt / call | File (planned) | Why an LLM here | Settings | Confidence gate |
|---|---|---|---|---|---|
| 1 | Root-cause triage (B2B: why is this invoice unpaid) | `engine/perception/triage.py`, prompt `engine/perception/prompts/triage.md` | Classifying unstructured payment/delivery/dispute context into 5 causes needs judgment a lookup table can't do | temp 0, JSON schema enforced, 6 few-shots | Gate: triage accuracy ≥90% vs ground truth (BUILD.md Day 3) or fix the prompt |
| 2 | Promise extraction (L1–L5 + amount/date/condition) | `engine/perception/extractor.py`, prompt `engine/perception/prompts/extract.md` | Nuanced/Hinglish/ambiguous natural language → structured commitment level; the load-bearing wall of the whole system | temp 0, JSON schema enforced, 8 few-shots incl. Hinglish + contradiction | conf <0.75 → one clarifying question; still ambiguous → human-review queue; conf <0.9 on anything that would trigger a money action → held for human approve-click (master doc §2.3). Accuracy gate ≥85% (BUILD.md Day 4) |
| 3 | Cart-abandonment cause inference (Scene 2) | `engine/perception/cart_cause.py`, prompt `engine/perception/prompts/cart_cause.md` | Same perception job as triage, different domain (drop-stage signals → cause) | temp 0, JSON out | Same confidence-gate pattern as triage |
| 4 | Message drafting (nudges, notices, mandate offers) | drafting module (Phase B/C), prompt `engine/perception/prompts/draft.md` | Tone control per escalation stage | temp 0.3, template-constrained — model fills slots, **never invents amounts/dates**, those interpolate from the ledger | n/a — output is language only, never a state transition |
| 5 | Dispute summary (evidence packet one-liner) | `engine/action/evidence.py`, prompt `engine/perception/prompts/verify.md` (shared verify/summary prompt file per BUILD.md §5) | Trivial summarization for the human-handoff card | temp 0 | n/a |
| 6 | Auditor 2nd-pass verification ("does extraction match message?") | Auditor module, Day 7 scope | The one justified 2nd LLM use — checks the extractor's work, not a new capability | temp 0, samples 10% of extractions | Quarantine rule: rolling accuracy <85% → extractor demoted, all money-adjacent actions route to human-review until it recovers |

**Status as of this entry (Phase A, updated 2026-08-26):** rows 1–3 are fully built and tested-as-far-as-possible without a key — `engine/perception/{triage,extractor,cart_cause}.py` + `engine/perception/client.py` (thin wrapper around `client.messages.parse(..., output_format=<pydantic model>)`, model `claude-sonnet-5`) + all 5 prompt files + `eval/{triage_eval,extraction_eval}.py`. Every module imports cleanly and fails with a clear `RuntimeError` (not a crash) when called without `ANTHROPIC_API_KEY` — see `tests/test_perception.py`. Rows 4–6 (drafting, dispute summary, Auditor) are not yet built. Nothing here has made a real API call yet; `ANTHROPIC_API_KEY` still not provided.

**Update 2026-08-26 (packet P1 — rows 1–3 are now provider-pluggable):** perception calls no longer hard-wire the LLM. `engine/perception/providers/` resolves a backend per call (argument → `PK_PERCEPTION_PROVIDER` → default `heuristic`), and rows 1–3 route through it with unchanged public signatures. The Claude path above is intact, moved verbatim into `providers/anthropic_provider.py` and selected by `--provider anthropic` / the env var. Two consequences for this file's question ("right tool in the right place"):
- **The default is now NOT an LLM.** Perception's default backend is the rules provider — see (b) row 7. An LLM is opt-in, per call, and is the only thing in the system that costs money to run.
- **Every accuracy claim is now per provider and has a baseline.** `metrics/extraction_accuracy_{provider}.json` / `metrics/triage_accuracy_{provider}.json`. "Extraction is X% accurate" is a weak claim on its own; "X% vs a rules baseline of 97.7% in-sample" says whether the LLM is earning its cost. Both evals refuse `--provider oracle` (ground-truth replay) so no circular number can enter `metrics/`.

---

## (b) Every place we deliberately do NOT use AI

Per master doc §2.2 — say this list out loud in the video:

1. **Escalation stage transitions** — deterministic state machine (`engine/judgment/state_machine.py`) — ✅ built + tested 2026-08-26 (48 passing tests)
2. **Trust score** — Beta(α,β) posterior math, closed-form, auditable (`engine/judgment/trust.py`) — ✅ built + tested 2026-08-26
3. **Mandate creation/amounts** — copied exactly from ledger records, never LLM-generated — ✅ enforced in `state_machine.check_bounds()` + `ledger.py` (proof: `tests/test_ledger.py::test_mandate_amount_always_equals_ledger_invoice_amount_never_llm_number` — feeds a wrong "extracted" amount and confirms the resulting mandate action still uses the ledger's number)
4. **Bounds enforcement** (caps, cooldowns, stop rules) — hard-coded constants, cannot be prompted around — ✅ built + tested 2026-08-26, all 8 bounds from master doc §3.4
5. **Money movement** — Razorpay APIs only, triggered by state machine only — ⬜ `engine/action/razorpay_client.py` not yet built (Phase C, needs Razorpay TEST keys)
6. **Metrics computation** — plain Python, reproducible (`eval/run_arms.py`) — ⬜ not yet built (Phase E)
7. **Perception's DEFAULT backend** — `engine/perception/providers/heuristic.py`, pure-Python rules, zero deps, zero cost, deterministic — ✅ built + tested 2026-08-26. Not an anti-AI position: it is the measured baseline an LLM has to beat on the same hand labels, and it means the whole system runs offline on a stranger's machine with no key. Measured in-sample (rules authored with the labels visible, no held-out split): extraction 97.7% (n=44, gate 85% → PASS), triage 71.7% (n=60, gate 90% → FAIL; 91.7% on the 24 invoices that have a thread, ~ceiling on the 36 that don't — see BUILD_LOG 2026-08-26). Where an LLM is genuinely worth its cost on this dataset is the open question those two numbers exist to answer honestly.

**The design law: the LLM can SEE and SPEAK, never SPEND.** Worst-case LLM hallucination = an awkward message, never a wrong debit. That's the blast-radius answer if asked.

**One-line defense of the whole stack, if asked "why not more agents?":** "the hard part of this problem is judgment boundaries, not model capability — a bigger stack adds failure modes, not accuracy" (master doc §2.1).
