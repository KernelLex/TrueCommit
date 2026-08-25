"""Cart-abandonment cause inference — Scene 2 (BUILD.md §5).

Backend is resolved by `engine.perception.providers.get_provider` (argument →
`PK_PERCEPTION_PROVIDER` → `heuristic`); the Claude path lives in
`providers/anthropic_provider.py`.
"""

from pydantic import BaseModel

from engine.perception.providers import get_provider
from engine.schemas import Cart, CartCause, CartCauseType


class _CartCauseOut(BaseModel):
    """LLM-constrained output shape — carries no cart_id (see extractor)."""

    cause: CartCauseType
    confidence: float
    evidence: list[str]


def infer_cart_cause(cart: Cart, *, provider: str | None = None) -> CartCause:
    return get_provider(provider).cart_cause(cart)
