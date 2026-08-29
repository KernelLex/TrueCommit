"""6 scripted debtor personas — frozen behavior tables, seeded RNG.

CLAUDE.md law #7 (anti-circularity): personas react to message PROPERTIES
(stage, instrument offered) — never to which arm sent the message. The same
tables drive Arm A/B/C in eval/run_arms.py (Phase E); arms differ only in
which messages they send, not in how a persona reacts to a given message.
Includes one adversarial never-pay persona per the same law.

Tag `personas-frozen` is cut right after this file lands — nothing downstream
may edit these tables to make the agent look better (CLAUDE.md is explicit:
refuse and cite the law if asked).
"""

import random
from typing import Literal

from engine.schemas import DebitFailureReason

PersonaId = Literal[
    "reliable_promiser", "serial_renegotiator", "silent_ghost",
    "disputer", "cashflow_constrained", "adversarial",
]
Stage = Literal["gentle", "firm", "formal"]
ReplyMove = Literal[
    "promise_firm", "promise_vague", "promise_conditional", "silence", "dispute",
]
MandateMove = Literal["confirm_mandate", "refuse_but_promise", "ignore"]

# Reply distribution when NO instrument was offered, by escalation stage.
REPLY_TABLE: dict[PersonaId, dict[Stage, dict[ReplyMove, float]]] = {
    "reliable_promiser": {
        "gentle": {"promise_firm": 0.60, "promise_vague": 0.30, "silence": 0.10},
        "firm": {"promise_firm": 0.75, "promise_vague": 0.20, "silence": 0.05},
        "formal": {"promise_firm": 0.85, "promise_vague": 0.10, "silence": 0.05},
    },
    "serial_renegotiator": {
        "gentle": {"promise_firm": 0.30, "promise_vague": 0.30, "promise_conditional": 0.20, "silence": 0.20},
        "firm": {"promise_firm": 0.35, "promise_conditional": 0.35, "silence": 0.20, "dispute": 0.10},
        "formal": {"promise_firm": 0.40, "promise_conditional": 0.30, "silence": 0.30},
    },
    "silent_ghost": {
        "gentle": {"silence": 0.85, "promise_vague": 0.15},
        "firm": {"silence": 0.80, "promise_vague": 0.15, "dispute": 0.05},
        "formal": {"silence": 0.70, "promise_vague": 0.20, "dispute": 0.10},
    },
    "disputer": {
        "gentle": {"dispute": 0.40, "promise_vague": 0.30, "silence": 0.30},
        "firm": {"dispute": 0.55, "promise_vague": 0.20, "silence": 0.25},
        "formal": {"dispute": 0.65, "silence": 0.35},
    },
    "cashflow_constrained": {
        "gentle": {"promise_conditional": 0.35, "promise_vague": 0.35, "promise_firm": 0.20, "silence": 0.10},
        "firm": {"promise_firm": 0.40, "promise_conditional": 0.30, "promise_vague": 0.20, "silence": 0.10},
        "formal": {"promise_firm": 0.55, "promise_conditional": 0.25, "promise_vague": 0.15, "silence": 0.05},
    },
    "adversarial": {
        "gentle": {"promise_vague": 0.60, "silence": 0.30, "promise_firm": 0.10},
        "firm": {"promise_vague": 0.55, "silence": 0.25, "promise_firm": 0.15, "dispute": 0.05},
        "formal": {"promise_vague": 0.40, "silence": 0.30, "promise_firm": 0.20, "dispute": 0.10},
    },
}

# Response when a mandate is offered — a stable trait, not stage-dependent
# (approving an auto-debit is a bigger decision than a WhatsApp reply).
MANDATE_TABLE: dict[PersonaId, dict[MandateMove, float]] = {
    "reliable_promiser": {"confirm_mandate": 0.70, "refuse_but_promise": 0.25, "ignore": 0.05},
    "serial_renegotiator": {"confirm_mandate": 0.25, "refuse_but_promise": 0.55, "ignore": 0.20},
    "silent_ghost": {"confirm_mandate": 0.05, "refuse_but_promise": 0.05, "ignore": 0.90},
    "disputer": {"confirm_mandate": 0.00, "refuse_but_promise": 0.20, "ignore": 0.80},
    "cashflow_constrained": {"confirm_mandate": 0.50, "refuse_but_promise": 0.40, "ignore": 0.10},
    "adversarial": {"confirm_mandate": 0.05, "refuse_but_promise": 0.45, "ignore": 0.50},
}

# P(an accepted promise is actually kept by its due date) — this is what
# makes "adversarial" adversarial: it promises plausibly but almost never pays.
KEEP_PROBABILITY: dict[PersonaId, float] = {
    "reliable_promiser": 0.88,
    "serial_renegotiator": 0.45,
    "silent_ghost": 0.15,
    "disputer": 0.05,
    "cashflow_constrained": 0.65,
    "adversarial": 0.05,
}

# P(an approved mandate executes successfully, vs a debit failure)
MANDATE_EXECUTE_SUCCESS_PROBABILITY: dict[PersonaId, float] = {
    "reliable_promiser": 0.92,
    "serial_renegotiator": 0.60,
    "cashflow_constrained": 0.70,
    "adversarial": 0.30,
    "silent_ghost": 0.50,   # rarely reached — this persona rarely confirms a mandate at all
    "disputer": 0.50,       # rarely reached — same reason
}

# GIVEN a mandate execution fails (the table above already decided THAT it
# fails), which NACH/eMandate return reason it fails WITH. Added 2026-08-30
# alongside the debit-failure taxonomy (engine/schemas.py's
# `DebitFailureReason`) — this is an ADDITIVE enrichment of the frozen
# tables, not a re-tuning of them: it does not touch a single success/fail
# PROBABILITY above (personas-frozen, CLAUDE.md law 7), it only answers a
# question the original tables never asked. It is also not circular under
# the same law — the reason is a property of the (already-decided) failure
# itself, drawn independently of which recovery arm or agent choice led to
# the attempt, exactly like every other persona table. See
# tracking/DECISIONS.md 2026-08-30 for the full "why this doesn't violate
# the freeze tag" reasoning.
#
# Each persona's distribution matches its OWN already-established character
# rather than a shared default, argued explicitly:
#   reliable_promiser     — almost never fails (8%); on the rare miss, it is
#                            overwhelmingly an infra/structural fluke, not
#                            unwillingness — this debtor wanted to pay.
#   cashflow_constrained  — its entire defining trait IS a funds/timing
#                            problem, so insufficient_funds dominates.
#   serial_renegotiator   — flaky about money generally: a mix of funds
#                            problems and a real minority of outright
#                            mandate revocations (they DO sometimes bail).
#   adversarial           — this is the persona that promises but never
#                            intends to pay (KEEP_PROBABILITY 0.05); its
#                            debit failures should overwhelmingly read as
#                            genuine unwillingness (mandate_revoked /
#                            account_closed_frozen), not bad luck.
#   silent_ghost/disputer — rarely reached at all (they rarely confirm a
#                            mandate in the first place); given a generic,
#                            unweighted-toward-any-story mix since the
#                            sample size in any real run is near zero.
MANDATE_FAILURE_REASON: dict[PersonaId, dict[DebitFailureReason, float]] = {
    "reliable_promiser": {
        "bank_downtime": 0.50, "amount_exceeds_limit": 0.30, "insufficient_funds": 0.20,
    },
    "cashflow_constrained": {
        "insufficient_funds": 0.70, "amount_exceeds_limit": 0.15, "bank_downtime": 0.15,
    },
    "serial_renegotiator": {
        "insufficient_funds": 0.45, "mandate_revoked": 0.25,
        "amount_exceeds_limit": 0.15, "bank_downtime": 0.15,
    },
    "adversarial": {
        "mandate_revoked": 0.55, "account_closed_frozen": 0.15,
        "insufficient_funds": 0.20, "bank_downtime": 0.10,
    },
    "silent_ghost": {
        "insufficient_funds": 0.40, "mandate_revoked": 0.30,
        "bank_downtime": 0.15, "account_closed_frozen": 0.15,
    },
    "disputer": {
        "insufficient_funds": 0.40, "mandate_revoked": 0.30,
        "bank_downtime": 0.15, "account_closed_frozen": 0.15,
    },
}


def _weighted_choice(rng: random.Random, table: dict[str, float]) -> str:
    items = list(table.items())
    total = sum(w for _, w in items)
    r = rng.uniform(0, total)
    upto = 0.0
    for move, w in items:
        upto += w
        if r <= upto:
            return move
    return items[-1][0]


def decide_reply_move(rng: random.Random, persona_id: PersonaId, stage: Stage) -> ReplyMove:
    return _weighted_choice(rng, REPLY_TABLE[persona_id][stage])  # type: ignore[return-value]


def decide_mandate_move(rng: random.Random, persona_id: PersonaId) -> MandateMove:
    return _weighted_choice(rng, MANDATE_TABLE[persona_id])  # type: ignore[return-value]


def keeps_promise(rng: random.Random, persona_id: PersonaId) -> bool:
    return rng.random() < KEEP_PROBABILITY[persona_id]


def mandate_executes(rng: random.Random, persona_id: PersonaId) -> bool:
    return rng.random() < MANDATE_EXECUTE_SUCCESS_PROBABILITY[persona_id]


def debit_failure_reason(rng: random.Random, persona_id: PersonaId) -> DebitFailureReason:
    """Only ever called immediately after `mandate_executes()` returns False
    — draws which NACH/eMandate return reason THIS failure carries, from the
    SAME shared RNG stream as every other persona draw (this is a persona-
    behavior question, not a meta/analysis feature, so it belongs in the
    ordinary narrative sequence — unlike the Auditor's or the sensitivity
    sweep's dedicated RNG streams, which exist specifically to NOT perturb
    this one)."""
    return _weighted_choice(rng, MANDATE_FAILURE_REASON[persona_id])  # type: ignore[return-value]
