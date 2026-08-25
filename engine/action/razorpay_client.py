"""Razorpay TEST-mode client — interface only. Every function raises until
RAZORPAY_TEST_KEY_ID/RAZORPAY_TEST_KEY_SECRET are set (Phase C).

CLAUDE.md Day-1 priority #1: the FIRST thing to do once those keys land is
verify the sandbox actually supports the One-Time Mandate / UPI Autopay
lifecycle below (create, register, execute, revoke) end to end — this is the
crown-jewel risk for the whole build, not a formality. Whatever the sandbox
can/can't do goes into tracking/PROBLEM_TASTE.md and tracking/TRACK_BAR.md's
real-vs-simulated table BEFORE any more code is written against it.

Every caller in engine/judgment/ constructs Action objects with amounts taken
only from the ledger (never from these functions' return values, never from
an LLM) — see engine/judgment/state_machine.py's check_bounds(). These
functions execute a decision already made; they never make one.
"""

from typing import Any, NoReturn


def _not_configured() -> NoReturn:
    raise NotImplementedError(
        "Razorpay TEST-mode client not wired yet - set RAZORPAY_TEST_KEY_ID / "
        "RAZORPAY_TEST_KEY_SECRET and implement against the verified sandbox "
        "capabilities (CLAUDE.md Day-1 priority #1) before calling this."
    )


def create_payment_link(amount_inr: int, description: str, customer_contact: dict[str, str]) -> dict[str, Any]:
    """POST /payment_links — test-mode. Returns the created link's id + short_url."""
    _not_configured()


def create_invoice(amount_inr: int, description: str, customer_contact: dict[str, str], due_date: str) -> dict[str, Any]:
    """POST /invoices — test-mode."""
    _not_configured()


def create_mandate_order(amount_inr: int, debit_date: str, customer_contact: dict[str, str], kind: str) -> dict[str, Any]:
    """Creates the order + One-Time Mandate registration object. `kind` is
    "scheduled" or "delivery_secured" (master doc §1.3) - the two instruments
    this build demos; funds-blocked-until-delivery semantics for
    delivery_secured need explicit sandbox verification, not assumed."""
    _not_configured()


def execute_mandate(mandate_id: str) -> dict[str, Any]:
    """Triggers/confirms the debit for an approved mandate on its due date."""
    _not_configured()


def revoke_mandate(mandate_id: str) -> dict[str, Any]:
    """Cancels an approved-but-not-yet-executed mandate (e.g. a
    delivery-secured mandate on item rejection -> CLEAN_LOSS, master doc §3.3)."""
    _not_configured()
