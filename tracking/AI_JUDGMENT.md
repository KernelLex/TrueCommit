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

**Status as of this entry (Phase A):** none of the above are wired to a live API call yet — `ANTHROPIC_API_KEY` not yet provided. Prompt files are being written now (static content); the Python modules will exist with the call behind a thin wrapper so plugging in the key is a one-line change.

---

## (b) Every place we deliberately do NOT use AI

Per master doc §2.2 — say this list out loud in the video:

1. **Escalation stage transitions** — deterministic state machine (`engine/judgment/state_machine.py`)
2. **Trust score** — Beta(α,β) posterior math, closed-form, auditable (`engine/judgment/trust.py`)
3. **Mandate creation/amounts** — copied exactly from ledger records, never LLM-generated
4. **Bounds enforcement** (caps, cooldowns, stop rules) — hard-coded constants, cannot be prompted around
5. **Money movement** — Razorpay APIs only, triggered by state machine only
6. **Metrics computation** — plain Python, reproducible (`eval/run_arms.py`)

**The design law: the LLM can SEE and SPEAK, never SPEND.** Worst-case LLM hallucination = an awkward message, never a wrong debit. That's the blast-radius answer if asked.

**One-line defense of the whole stack, if asked "why not more agents?":** "the hard part of this problem is judgment boundaries, not model capability — a bigger stack adds failure modes, not accuracy" (master doc §2.1).
