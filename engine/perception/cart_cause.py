"""Cart-abandonment cause inference — LLM call 3 of 4, Scene 2 (BUILD.md §5)."""

from pydantic import BaseModel

from engine.perception.client import call_structured, load_prompt
from engine.schemas import Cart, CartCause, CartCauseType


class _CartCauseOut(BaseModel):
    cause: CartCauseType
    confidence: float
    evidence: list[str]


def infer_cart_cause(cart: Cart) -> CartCause:
    user_content = (
        f"Cart {cart.id}: Rs.{cart.amount_inr:,}, drop_stage={cart.drop_stage}, "
        f"drop_signals={cart.drop_signals}, reserve_active={cart.reserve_active}."
    )
    out = call_structured(load_prompt("cart_cause"), user_content, _CartCauseOut)
    return CartCause(cart_id=cart.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence)
