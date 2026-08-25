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

# P(an approved mandate executes successfully, vs insufficient-funds failure)
MANDATE_EXECUTE_SUCCESS_PROBABILITY: dict[PersonaId, float] = {
    "reliable_promiser": 0.92,
    "serial_renegotiator": 0.60,
    "cashflow_constrained": 0.70,
    "adversarial": 0.30,
    "silent_ghost": 0.50,   # rarely reached — this persona rarely confirms a mandate at all
    "disputer": 0.50,       # rarely reached — same reason
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
