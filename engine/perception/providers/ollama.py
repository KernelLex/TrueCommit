"""Ollama provider — a real local LLM, free, offline-capable, measured honestly.

WHAT IT IS
----------
The same three perception tasks (`extract` / `triage` / `cart_cause`) driven
by a locally-hosted model through Ollama's `/api/chat` endpoint, using the
SAME prompt files every LLM provider shares (`engine/perception/prompts/*.md`
via `client.load_prompt`) and the SAME output shapes Anthropic's provider is
constrained to (`_ExtractOut` / `_TriageOut` / `_CartCauseOut` — imported, not
re-derived, so the three providers can never quietly drift apart on what a
valid answer looks like). It differs from `anthropic_provider.py` only in
transport: a POST to a local HTTP server instead of the Anthropic SDK, using
Ollama's own JSON-schema-constrained `format` field as the structured-output
mechanism (the local analogue of `output_format=<pydantic model>`).

Design law compliance (CLAUDE.md law 1): this module returns `Extraction` /
`InvoiceCause` / `CartCause` and nothing else. If the model invents a number
not explicit in the source text, that is an ACCURACY MISS scored by
`eval/extraction_eval.py` — never a money risk, because nothing downstream
accepts an amount that doesn't already match the ledger's own record.

CONFIG (env)
------------
  OLLAMA_BASE_URL   default "http://localhost:11434"
  PK_OLLAMA_MODEL   default "qwen2.5:7b"

The resolved model name is part of `identity()` (`"ollama:<model>"`), which
feeds every cache fingerprint (`cache.fingerprint(self.identity(), payload)`
in `providers/__init__.py`'s `_cached()` template method) — qwen2.5:7b and
qwen2.5:3b therefore never share a cache entry, and re-pointing
PK_OLLAMA_MODEL at a different tag invalidates old answers automatically
instead of silently serving them.

ROBUSTNESS (each one earned by a real behaviour observed against a live
CPU-hosted qwen2.5, not speculative)
----------------------------------------------------------------------
(a) CONFIDENCE NORMALISATION. Local models sometimes answer "confidence": 85
    instead of 0.85 — the schema says "float 0-1" but nothing stops a model
    from reading the *shape* of a percentage into the number. `>1` is divided
    by 100, then the whole thing is clamped to [0, 1]. This runs BEFORE
    pydantic validation so a plain formatting slip never becomes a retry.
(b) RETRY-THEN-TYPED-ERROR. A `json.loads` failure or a pydantic validation
    failure gets exactly one retry with a "Return ONLY valid JSON matching
    the schema" nudge appended to the user turn. A second failure raises
    `OllamaProviderError` — loud, typed, never a silent wrong answer.
(c) DATES: ISO-only, never a guess. A `date` field that isn't a parseable ISO
    date string is normalised to `None` before validation (never invented) —
    same "if it's not explicit, don't invent it" law the prompts already
    teach, applied one layer earlier so a model's formatting quirk can't
    manufacture a phantom promise date.
(d) TIMEOUT: 120s. CPU inference on a 7B/3B model is slow (~3-10s typically,
    but a cold model load or a loaded machine can take much longer) — a
    short timeout would turn "slow" into "unreachable" and trigger the
    fallback below for the wrong reason.

FALLBACK (the promised demo beat: "no failure is silent, every failure has a
designed next step" — CLAUDE.md's own framing, not just this module's)
------------------------------------------------------------------------
If Ollama cannot be reached at all (connection refused, DNS failure, or a
timeout at the transport level — `httpx.TransportError` and its subclasses),
that ONE call is silently handed to a `HeuristicProvider` instance instead of
raising. The call still SUCCEEDS. The degradation is recorded on the instance
(`self.fallback_events`) and mirrored into a module-level log
(`get_fallback_events()`) so a later packet (System Health) can surface
"N calls degraded to heuristic" without this provider needing to know that
screen exists. This is deliberately narrow: an HTTP error status, a malformed
model name, or two failed JSON parses are NOT connectivity problems and are
NOT silently downgraded — they raise `OllamaProviderError` instead, because
falling back to heuristic on every kind of failure would hide a real
misconfiguration behind a quietly-passing test.

WHAT THIS MODULE DOES NOT DO
-----------------------------
Tune the prompts. The prompt files are shared with `anthropic_provider.py`
and `heuristic.py`'s own honesty note applies here in spirit: measure what
the shared prompts produce, don't rewrite them to chase a gate.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from engine.perception.cart_cause import _CartCauseOut
from engine.perception.client import load_prompt
from engine.perception.extractor import _ExtractOut
from engine.perception.providers import PerceptionProvider
from engine.perception.providers.heuristic import HeuristicProvider
from engine.perception.triage import _TriageOut
from engine.schemas import Cart, CartCause, Extraction, Invoice, InvoiceCause, Message

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
ENV_BASE_URL = "OLLAMA_BASE_URL"
ENV_MODEL = "PK_OLLAMA_MODEL"
TIMEOUT_SECONDS = 120.0
"""Generous on purpose — CPU inference, not a GPU-backed API."""

RETRY_NUDGE = "\n\nReturn ONLY valid JSON matching the schema."

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# JSON schemas for Ollama's `format` field — the local structured-output
# mechanism, mirroring what `output_format=<pydantic model>` does for the
# Anthropic path. Field names and enums match _ExtractOut / _TriageOut /
# _CartCauseOut (engine/perception/extractor.py, triage.py, cart_cause.py)
# and the taxonomies in engine/schemas.py exactly, so a valid response here
# is a valid response there.
# ---------------------------------------------------------------------------

_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "level": {"type": "string", "enum": ["L1", "L2", "L3", "L4", "L5"]},
        "amount_inr": {"type": ["integer", "null"]},
        "date": {"type": ["string", "null"]},
        "condition": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
    },
    "required": ["level", "amount_inr", "date", "condition", "confidence"],
}

_TRIAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "cause": {
            "type": "string",
            "enum": ["payment_failed", "delivery_dispute", "cashflow_delay", "dispute", "non_responsive"],
        },
        "confidence": {"type": "number"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cause", "confidence", "evidence"],
}

_CART_CAUSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "cause": {
            "type": "string",
            "enum": ["friction", "price_shock", "trust", "timing", "comparison", "unknown"],
        },
        "confidence": {"type": "number"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["cause", "confidence", "evidence"],
}


class OllamaProviderError(RuntimeError):
    """Ollama responded but never produced valid, schema-matching JSON, even
    after one retry nudge. Distinct from connectivity failure (see
    `_OllamaUnreachable`) — this is a model/response problem, not a
    reachability problem, so it is NOT silently downgraded to heuristic."""


class _OllamaUnreachable(Exception):
    """Internal signal only — never escapes this module. Raised when the
    transport itself fails (connection refused, DNS failure, timeout);
    caught by `_extract` / `_triage` / `_cart_cause` to trigger the
    heuristic fallback for that one call."""


# ---------------------------------------------------------------------------
# Module-level fallback log — the System Health accessor a later packet reads.
# ---------------------------------------------------------------------------

_FALLBACK_EVENTS: list[dict[str, str]] = []


def get_fallback_events() -> list[dict[str, str]]:
    """Every Ollama->heuristic degradation recorded by ANY OllamaProvider
    instance in this process, oldest first. Read-only copy."""
    return list(_FALLBACK_EVENTS)


def reset_fallback_events() -> None:
    """Test/ops hook — clears the module-level log (does not touch any
    instance's own `fallback_events`)."""
    _FALLBACK_EVENTS.clear()


class OllamaProvider(PerceptionProvider):
    name = "ollama"

    uses_cache = True

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get(ENV_MODEL) or DEFAULT_MODEL
        self._client = httpx.Client(base_url=self.base_url, timeout=TIMEOUT_SECONDS, transport=transport)
        self.fallback_events: list[dict[str, str]] = []
        """This instance's own degradations — a subset of `get_fallback_events()`."""
        self._heuristic = HeuristicProvider()
        """Fallback backend. Its `_extract`/`_triage`/`_cart_cause` hooks are
        called directly (not the cached public methods) — the result still
        gets cached, but under THIS provider's cache namespace/identity, via
        the PerceptionProvider template method that wraps `_extract` etc."""

    def identity(self) -> str:
        # Model id participates in the cache fingerprint: qwen2.5:7b and
        # qwen2.5:3b must never share a cache entry.
        return f"{self.name}:{self.model}"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "OllamaProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- perception tasks -----------------------------------------------

    def _extract(self, message: Message, thread_messages: list[Message]) -> Extraction:
        thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages)
        user_content = (
            f"Thread so far:\n{thread_text}\n\n"
            f'Extract the commitment from the LAST message above: "{message.text}"'
        )
        try:
            out = self._chat_json(
                "extract", load_prompt("extract"), user_content, _EXTRACT_SCHEMA, _ExtractOut, date_field="date"
            )
        except _OllamaUnreachable as exc:
            self._record_fallback("extract", message.id, exc)
            return self._heuristic._extract(message, thread_messages)
        return Extraction(
            message_id=message.id, level=out.level, amount_inr=out.amount_inr,
            date=out.date, condition=out.condition, confidence=out.confidence,
        )

    def _triage(self, invoice: Invoice, thread_messages: list[Message]) -> InvoiceCause:
        thread_text = "\n".join(f"[{m.direction}] {m.text}" for m in thread_messages) or "(no messages yet)"
        user_content = (
            f"Invoice {invoice.id}: Rs.{invoice.amount_inr:,}, status={invoice.status}, "
            f"issued {invoice.issued}, due {invoice.due}.\n"
            f"delivery_confirmed={invoice.delivery_confirmed}, "
            f"payment_failed_attempt={invoice.payment_failed_attempt}.\n\n"
            f"Thread so far:\n{thread_text}"
        )
        try:
            out = self._chat_json("triage", load_prompt("triage"), user_content, _TRIAGE_SCHEMA, _TriageOut)
        except _OllamaUnreachable as exc:
            self._record_fallback("triage", invoice.id, exc)
            return self._heuristic._triage(invoice, thread_messages)
        return InvoiceCause(invoice_id=invoice.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence)

    def _cart_cause(self, cart: Cart) -> CartCause:
        user_content = (
            f"Cart {cart.id}: Rs.{cart.amount_inr:,}, drop_stage={cart.drop_stage}, "
            f"drop_signals={cart.drop_signals}, reserve_active={cart.reserve_active}."
        )
        try:
            out = self._chat_json(
                "cart_cause", load_prompt("cart_cause"), user_content, _CART_CAUSE_SCHEMA, _CartCauseOut
            )
        except _OllamaUnreachable as exc:
            self._record_fallback("cart_cause", cart.id, exc)
            return self._heuristic._cart_cause(cart)
        return CartCause(cart_id=cart.id, cause=out.cause, confidence=out.confidence, evidence=out.evidence)

    # -- internals --------------------------------------------------------

    def _record_fallback(self, kind: str, entity_id: str, exc: Exception) -> None:
        event = {
            "provider": self.name,
            "model": self.model,
            "kind": kind,
            "entity_id": entity_id,
            "error": str(exc),
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        self.fallback_events.append(event)
        _FALLBACK_EVENTS.append(event)

    def _chat_json(
        self, kind: str, system_prompt: str, user_content: str, schema: dict, out_model: type[T],
        *, date_field: str | None = None,
    ) -> T:
        """One perception call, schema-constrained to `out_model`.

        Raises `_OllamaUnreachable` immediately on a transport failure (no
        point nudging a server that isn't there). Raises `OllamaProviderError`
        if the model never produces valid JSON matching `out_model`, after
        exactly one retry with `RETRY_NUDGE` appended.
        """
        content = user_content
        last_err: Exception | None = None
        for attempt in range(2):
            raw_text = self._post_chat(system_prompt, content, schema)
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                last_err = exc
                content = user_content + RETRY_NUDGE
                continue
            if not isinstance(data, dict):
                last_err = ValueError(f"expected a JSON object, got {type(data).__name__}: {data!r}")
                content = user_content + RETRY_NUDGE
                continue
            data = self._normalize_payload(data, date_field=date_field)
            try:
                return out_model.model_validate(data)
            except ValidationError as exc:
                last_err = exc
                content = user_content + RETRY_NUDGE
                continue
        raise OllamaProviderError(
            f"ollama provider (model={self.model!r}, task={kind!r}) never produced valid JSON "
            f"matching {out_model.__name__} after one retry: {last_err}"
        )

    @staticmethod
    def _normalize_payload(data: dict, *, date_field: str | None = None) -> dict:
        """Robustness rules (a) and (c) from the module docstring, applied
        BEFORE pydantic validation so a formatting quirk never eats a retry."""
        data = dict(data)

        conf = data.get("confidence")
        if isinstance(conf, (int, float)) and not isinstance(conf, bool):
            c = float(conf)
            if c > 1.0:
                c = c / 100.0  # observed live: a model answering "85" for 0.85
            data["confidence"] = max(0.0, min(1.0, c))

        if date_field and date_field in data:
            raw_date = data.get(date_field)
            if isinstance(raw_date, str):
                try:
                    dt.date.fromisoformat(raw_date)
                except ValueError:
                    data[date_field] = None  # unparseable -> None, never a guess
            elif raw_date is not None:
                data[date_field] = None

        return data

    def _post_chat(self, system_prompt: str, user_content: str, schema: dict) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "format": schema,
            "options": {"temperature": 0},
        }
        try:
            resp = self._client.post("/api/chat", json=payload)
        except httpx.TransportError as exc:
            raise _OllamaUnreachable(f"could not reach Ollama at {self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            # A bad status is a real, loud failure — NOT a connectivity
            # problem, so it does not trigger the heuristic fallback.
            raise OllamaProviderError(
                f"Ollama /api/chat returned HTTP {resp.status_code} for model {self.model!r}: "
                f"{resp.text[:300]}"
            )
        try:
            body = resp.json()
            return body["message"]["content"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OllamaProviderError(f"unexpected Ollama /api/chat response shape: {exc}") from exc


def build() -> OllamaProvider:
    return OllamaProvider()
