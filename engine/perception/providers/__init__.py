"""Perception providers — the pluggable "eyes" of Promise Keeper.

WHY THIS EXISTS
---------------
Perception is the only layer in this system that is allowed to be fuzzy. Every
other layer (ledger, trust, state machine, bounds, money movement) is
deterministic by design law. Making perception pluggable therefore costs the
architecture nothing and buys three things:

1. **It runs for free, offline, today.** The `heuristic` provider is pure
   Python with zero external dependencies — no API key, no network, no cost.
2. **It measures what an LLM actually adds.** The same hand labels are scored
   against every provider, so `metrics/extraction_accuracy_heuristic.json` is
   the rules baseline that `..._anthropic.json` (or a local model) has to beat.
   A number with no baseline is a claim; a number with a baseline is evidence.
3. **It keeps the design laws intact.** A provider may only produce
   `Extraction` / `InvoiceCause` / `CartCause` — read-only perception objects.
   No provider can emit an amount to debit, a date to debit it on, or a state
   transition; those come exclusively from the ledger and the state machine
   (CLAUDE.md §3 law 1 and law 2). Swapping providers can make the agent's
   next *message* wrong. It can never make a *debit* wrong.

RESOLUTION ORDER
----------------
`get_provider(name)` resolves: explicit argument → `PK_PERCEPTION_PROVIDER`
environment variable → `"heuristic"`.

ADDING A PROVIDER
-----------------
Either call `register("myname", factory)` at import time, or drop a module at
`engine/perception/providers/myname.py` (or `myname_provider.py`) exposing a
`build()` factory — `get_provider("myname")` finds it by convention without
this file changing.
"""

import importlib
import os
from abc import ABC, abstractmethod
from collections.abc import Callable

from engine.perception import cache
from engine.schemas import Cart, CartCause, Extraction, Invoice, InvoiceCause, Message

DEFAULT_PROVIDER = "heuristic"
ENV_VAR = "PK_PERCEPTION_PROVIDER"

BUILTIN_PROVIDERS = ("heuristic", "anthropic", "oracle")


class PerceptionProvider(ABC):
    """The three-method contract every provider implements.

    Subclasses implement the underscore-prefixed methods; the public methods
    are template methods that add caching. That way caching is a property of
    *being a provider*, not something each backend has to remember to do.
    """

    name: str = "unnamed"

    uses_cache: bool = True
    """Set False for providers where caching is meaningless or harmful — the
    oracle replays a static file, so caching it would only add a stale copy."""

    # -- identity -----------------------------------------------------------

    def identity(self) -> str:
        """Everything about this provider that could change its answers.

        Included in every cache fingerprint, so tuning a provider's parameters
        invalidates its cached results automatically instead of serving
        answers computed under the old settings.
        """
        return self.name

    # -- public API (cached) ------------------------------------------------

    def extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        return self._cached(
            "extract", message.id,
            payload=[[m.direction, m.text, m.ts.isoformat()] for m in thread_messages] + [message.id],
            model=Extraction,
            compute=lambda: self._extract(message, thread_messages),
        )

    def triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        return self._cached(
            "triage", invoice.id,
            payload={
                "invoice": {
                    "id": invoice.id, "amount_inr": invoice.amount_inr, "status": invoice.status,
                    "issued": invoice.issued.isoformat(), "due": invoice.due.isoformat(),
                    "delivery_confirmed": invoice.delivery_confirmed,
                    "payment_failed_attempt": invoice.payment_failed_attempt,
                },
                "thread": [[m.direction, m.text] for m in thread_messages],
            },
            model=InvoiceCause,
            compute=lambda: self._triage(invoice, thread_messages),
        )

    def cart_cause(self, cart: Cart) -> CartCause:
        return self._cached(
            "cart_cause", cart.id,
            payload={"drop_stage": cart.drop_stage, "drop_signals": sorted(cart.drop_signals),
                     "amount_inr": cart.amount_inr, "reserve_active": cart.reserve_active},
            model=CartCause,
            compute=lambda: self._cart_cause(cart),
        )

    # -- backend hooks ------------------------------------------------------

    @abstractmethod
    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        """`thread_messages` is the thread up to and including `message`, in order."""

    @abstractmethod
    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause: ...

    @abstractmethod
    def _cart_cause(self, cart: Cart) -> CartCause: ...

    # -- internals ----------------------------------------------------------

    def _cached(self, kind: str, entity_id: str, payload: object, model, compute):
        if not self.uses_cache:
            return compute()
        fp = cache.fingerprint(self.identity(), payload)
        hit = cache.load(self.name, kind, entity_id, fp, model)
        if hit is not None:
            return hit
        result = compute()
        cache.store(self.name, kind, entity_id, fp, result)
        return result


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_FACTORIES: dict[str, Callable[[], PerceptionProvider]] = {}
_INSTANCES: dict[str, PerceptionProvider] = {}


def register(name: str, factory: Callable[[], PerceptionProvider], *, replace: bool = False) -> None:
    key = name.strip().lower()
    if key in _FACTORIES and not replace:
        raise ValueError(f"perception provider {key!r} is already registered")
    _FACTORIES[key] = factory
    _INSTANCES.pop(key, None)


def _lazy(module_suffixes: tuple[str, ...], attr: str) -> Callable[[], PerceptionProvider]:
    def factory() -> PerceptionProvider:
        last: Exception | None = None
        for suffix in module_suffixes:
            try:
                module = importlib.import_module(f"{__name__}.{suffix}")
            except ModuleNotFoundError as exc:  # pragma: no cover - defensive
                last = exc
                continue
            return getattr(module, attr)()
        raise last or ModuleNotFoundError(module_suffixes[0])
    return factory


register("heuristic", _lazy(("heuristic",), "build"))
register("anthropic", _lazy(("anthropic_provider",), "build"))
register("oracle", _lazy(("oracle",), "build"))


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def resolve_provider_name(name: str | None = None) -> str:
    """argument → PK_PERCEPTION_PROVIDER → default."""
    if name and name.strip():
        return name.strip().lower()
    env = os.environ.get(ENV_VAR, "")
    if env.strip():
        return env.strip().lower()
    return DEFAULT_PROVIDER


def get_provider(name: str | None = None) -> PerceptionProvider:
    """Resolve and instantiate a provider (memoised per name)."""
    key = resolve_provider_name(name)
    if key in _INSTANCES:
        return _INSTANCES[key]
    factory = _FACTORIES.get(key)
    if factory is None:
        factory = _discover(key)
    if factory is None:
        raise ValueError(
            f"unknown perception provider {key!r}. Known: {', '.join(available_providers())}. "
            f"Set {ENV_VAR} or pass provider=... explicitly."
        )
    provider = factory()
    if not isinstance(provider, PerceptionProvider):
        raise TypeError(f"provider {key!r} does not implement PerceptionProvider")
    _INSTANCES[key] = provider
    return provider


def _discover(key: str) -> Callable[[], PerceptionProvider] | None:
    """Convention-over-configuration: `providers/<key>.py` or
    `providers/<key>_provider.py` exposing `build()` is auto-registered."""
    for suffix in (key, f"{key}_provider"):
        try:
            module = importlib.import_module(f"{__name__}.{suffix}")
        except ModuleNotFoundError:
            continue
        builder = getattr(module, "build", None)
        if callable(builder):
            _FACTORIES[key] = builder
            return builder
    return None


def reset_instances() -> None:
    """Drop memoised instances (tests that re-tune parameters between cases)."""
    _INSTANCES.clear()


__all__ = [
    "BUILTIN_PROVIDERS", "DEFAULT_PROVIDER", "ENV_VAR", "PerceptionProvider",
    "available_providers", "get_provider", "register", "reset_instances",
    "resolve_provider_name",
]
