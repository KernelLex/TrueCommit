# AI_JUDGMENT.md — "right tool in the right place, and where you chose not to use one"

Two lists, kept current per CLAUDE.md §4. Update the same session either list changes.

---

## (a) Every place an LLM is used

One model everywhere: **Claude Sonnet (`claude-sonnet-4-6`) via the Anthropic API.** No fine-tuning, no RAG, no agents-calling-agents (master doc §2.1).

| # | Prompt / call | File (planned) | Why an LLM here | Settings | Confidence gate |
|---|---|---|---|---|---|
| 1 | Root-cause triage (B2B: why is this invoice unpaid) | `engine/perception/triage.py`, prompt `engine/perception/prompts/triage.md` | Classifying unstructured payment/delivery/dispute context into 5 causes needs judgment a lookup table can't do | temp 0, JSON schema enforced, 6 few-shots | Gate: triage accuracy ≥90% vs ground truth (BUILD.md Day 3) or fix the prompt |
| 2 | Promise extraction (L1–L5 + amount/date/condition) | `engine/perception/extractor.py`, prompt `engine/perception/prompts/extract.md` | Nuanced/Hinglish/ambiguous natural language → structured commitment level; the load-bearing wall of the whole system | temp 0, JSON schema enforced, 8 few-shots incl. Hinglish + contradiction | ✅ **WIRED 2026-08-26 (packet P9)** — conf <0.75 → one clarifying question, then the queue; conf <0.9 on anything that would trigger a money action → held for human approve-click (master doc §2.3). Implemented in `engine/judgment/ledger.py`; see the P9 update block below for thresholds, tests and measured counts. Accuracy gate ≥85% (BUILD.md Day 4) |
| 3 | Cart-abandonment cause inference (Scene 2) | `engine/perception/cart_cause.py`, prompt `engine/perception/prompts/cart_cause.md` | Same perception job as triage, different domain (drop-stage signals → cause) | temp 0, JSON out | Same confidence-gate pattern as triage |
| 4 | Message drafting (nudges, notices, mandate offers) | drafting module (Phase B/C), prompt `engine/perception/prompts/draft.md` | Tone control per escalation stage | temp 0.3, template-constrained — model fills slots, **never invents amounts/dates**, those interpolate from the ledger | n/a — output is language only, never a state transition |
| 5 | Dispute summary (evidence packet one-liner) | `engine/action/evidence.py`, prompt `engine/perception/prompts/verify.md` (shared verify/summary prompt file per BUILD.md §5) | Trivial summarization for the human-handoff card | temp 0 | n/a |
| 6 | Auditor 2nd-pass verification ("does extraction match message?") | Auditor module, Day 7 scope | The one justified 2nd LLM use — checks the extractor's work, not a new capability | temp 0, samples 10% of extractions | Quarantine rule: rolling accuracy <85% → extractor demoted, all money-adjacent actions route to human-review until it recovers |

**Status as of this entry (Phase A, updated 2026-08-26):** rows 1–3 are fully built and tested-as-far-as-possible without a key — `engine/perception/{triage,extractor,cart_cause}.py` + `engine/perception/client.py` (thin wrapper around `client.messages.parse(..., output_format=<pydantic model>)`, model `claude-sonnet-5`) + all 5 prompt files + `eval/{triage_eval,extraction_eval}.py`. Every module imports cleanly and fails with a clear `RuntimeError` (not a crash) when called without `ANTHROPIC_API_KEY` — see `tests/test_perception.py`. Rows 4–6 (drafting, dispute summary, Auditor) are not yet built. Nothing here has made a real API call yet; `ANTHROPIC_API_KEY` still not provided.

**Update 2026-08-26 (packet P9 — the confidence-gate column is now WIRED, not aspirational).** Until this packet, row 2's "Confidence gate" cell described intent: the extractor produced a `confidence` and nothing downstream read it. All three gates from master doc §2.3 now exist in `engine/judgment/ledger.py`, on the decide path, and are what actually decides whether the agent acts alone:

| Gate | Threshold | What the code does | Test |
|---|---|---|---|
| Clarify-first | conf **< 0.75** | Emits ONE `message` action at `stage:"clarify"` instead of acting — bounds-checked and touch-counted like any outreach. A SECOND sub-0.75 read on the same entity is NOT a second question: it goes to the review queue as "still ambiguous after clarification". | `test_ledger.py::test_sub_075_gets_exactly_one_clarifying_question_then_the_queue` |
| Money gate | conf **< 0.90** | The money action (mandate offer, or the link it falls back to) is DECIDED and NOT emitted — it becomes a `HeldAction` awaiting a merchant approve-click. `check_bounds()` re-runs **at click time**, so a stale hold cannot be approved through a cap that has since been hit. | `test_ledger.py::test_a_money_action_under_the_confidence_gate_is_held_not_emitted`, `::test_approval_re_runs_check_bounds_at_click_time_not_at_hold_time` |
| Formal notice | n/a (stage, not confidence) | The legal-stage draft enters the same queue with `sendable=False` — **no approve-send path exists**, at any layer. Only "mark handled". | `test_review_queue.py::test_the_formal_notice_draft_can_never_be_sent_by_any_api_call` |

Three things worth saying out loud about where the LLM's number is and is not trusted:
- **The confidence moves who decides, never what is decided.** A gated mandate offer still carries the LEDGER's amount, not the extraction's — held or approved, law 2 is untouched (asserted on held actions specifically, `test_integration.py::test_the_45_day_run_really_holds_money_actions_for_a_human`).
- **The gates can only ever DEFER, never permit.** They are strictly weaker than the bounds: there is no confidence value that lets an action past `check_bounds()`. That is why their constants live in `ledger.py` and not next to the bounds in `state_machine.py` — see DECISIONS.md 2026-08-26 (P9), call 1.
- **An extraction with no confidence at all is not gated**, deliberately and testably: the gate compares a number, and "we were told nothing" is not evidence of low confidence. Every in-system producer supplies one.
- **Measured, 45-day heuristic run:** 3 money actions held (all at extraction confidence **0.78**, an L3 conditional read) + 1 formal-notice draft. **0 clarify messages**, because the heuristic provider's confidence table bottoms out at 0.78 for the levels the runner books as promises — a property of the rules baseline's discrete scores, not of the wiring, and stated as such rather than papered over. An LLM provider with continuous confidences would exercise it; the wire is proven meanwhile by `test_integration.py::test_a_low_confidence_extraction_reaches_the_clarify_gate_in_the_real_pipeline`.

**Update 2026-08-26 (packet P1 — rows 1–3 are now provider-pluggable):** perception calls no longer hard-wire the LLM. `engine/perception/providers/` resolves a backend per call (argument → `PK_PERCEPTION_PROVIDER` → default `heuristic`), and rows 1–3 route through it with unchanged public signatures. The Claude path above is intact, moved verbatim into `providers/anthropic_provider.py` and selected by `--provider anthropic` / the env var. Two consequences for this file's question ("right tool in the right place"):
- **The default is now NOT an LLM.** Perception's default backend is the rules provider — see (b) row 7. An LLM is opt-in, per call, and is the only thing in the system that costs money to run.
- **Every accuracy claim is now per provider and has a baseline.** `metrics/extraction_accuracy_{provider}.json` / `metrics/triage_accuracy_{provider}.json`. "Extraction is X% accurate" is a weak claim on its own; "X% vs a rules baseline of 97.7% in-sample" says whether the LLM is earning its cost. Both evals refuse `--provider oracle` (ground-truth replay) so no circular number can enter `metrics/`.

---

## (b) Every place we deliberately do NOT use AI

Per master doc §2.2 — say this list out loud in the video:

1. **Escalation stage transitions** — deterministic state machine (`engine/judgment/state_machine.py`) — ✅ built + tested 2026-08-26 (48 passing tests)
2. **Trust score** — Beta(α,β) posterior math, closed-form, auditable (`engine/judgment/trust.py`) — ✅ built + tested 2026-08-26
3. **Mandate creation/amounts** — copied exactly from ledger records, never LLM-generated — ✅ enforced in `state_machine.check_bounds()` + `ledger.py` (proof: `tests/test_ledger.py::test_mandate_amount_always_equals_ledger_invoice_amount_never_llm_number` — feeds a wrong "extracted" amount and confirms the resulting mandate action still uses the ledger's number)
4. **Bounds enforcement** (caps, cooldowns, stop rules) — hard-coded constants, cannot be prompted around — ✅ built + tested 2026-08-26, all 8 bounds from master doc §3.4. **Including the human's clicks (packet P9):** the review-queue approve path re-enters the same `check_bounds()` at click time, so a merchant cannot approve an action past a bound any more than the ladder can. The gates that *route to* a human are confidence-driven; the gate that decides whether anything actually goes out never is.
5. **Money movement** — Razorpay APIs only, triggered by state machine only — ⬜ `engine/action/razorpay_client.py` not yet built (Phase C, needs Razorpay TEST keys)
6. **Metrics computation** — plain Python, reproducible (`eval/run_arms.py`) — ⬜ not yet built (Phase E)
7. **Perception's DEFAULT backend** — `engine/perception/providers/heuristic.py`, pure-Python rules, zero deps, zero cost, deterministic — ✅ built + tested 2026-08-26. Not an anti-AI position: it is the measured baseline an LLM has to beat on the same hand labels, and it means the whole system runs offline on a stranger's machine with no key. Measured in-sample (rules authored with the labels visible, no held-out split): extraction 97.7% (n=44, gate 85% → PASS), triage 71.7% (n=60, gate 90% → FAIL; 91.7% on the 24 invoices that have a thread, ~ceiling on the 36 that don't — see BUILD_LOG 2026-08-26). Where an LLM is genuinely worth its cost on this dataset is the open question those two numbers exist to answer honestly.

8. **Whether a held action may be sent** — the merchant's own click, not a model's re-read. There is deliberately no "the extractor is more confident now, release it" path: an approval queue that can empty itself is not an approval queue. ✅ packet P9, 2026-08-26.
9. **The formal-notice decision** — no model and no click sends it. `sendable=False` is enforced in the ledger, and both `approve` and `reject` return 403; the merchant sends the notice themselves, outside the system, and marks it handled. ✅ packet P9, 2026-08-26.
10. **Closing a handoff** — `human_resolution` (recovered / written off) is a merchant's statement of fact, never inferred. It does not move the trust posterior either: an admin click is bookkeeping about our process, not evidence about the debtor. ✅ packet P9, 2026-08-26.

11. **The Day Story narration** — the judge-facing screen that shows what happened on a simulated day writes no prose of its own. Every sentence on it is an audit `summary`, a `reason` string, or a message the run genuinely sent or received; every number is a stored value (`Ledger.audit`, `Ledger.gate_log`, `WorldRunner.threads`, `WorldRunner.day_snapshots`, `DEBTOR_BY_ID`). An LLM summariser here would have been the single easiest place in the repo to launder a fabrication into something that looks like evidence — a plausible paragraph beside a real amount. Where the data does not exist the API returns `null` plus a note saying why (a Scene-2 cart customer has no stored business name; a debtor with no posterior yet has no trust mean), and the screen prints that note. ✅ packet P10, 2026-08-26 (`engine/integration/day_story.py`, `dashboard/src/screens/DayStoryScreen.jsx`, `tests/test_day_story.py`).

**The design law: the LLM can SEE and SPEAK, never SPEND.** Worst-case LLM hallucination = an awkward message, never a wrong debit. That's the blast-radius answer if asked.

**One-line defense of the whole stack, if asked "why not more agents?":** "the hard part of this problem is judgment boundaries, not model capability — a bigger stack adds failure modes, not accuracy" (master doc §2.1).
