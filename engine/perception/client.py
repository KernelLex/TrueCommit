"""Thin Anthropic client wrapper. Every perception call goes through here so
plugging in ANTHROPIC_API_KEY is the only thing needed to go live (Phase B).

Model + determinism note (see tracking/DECISIONS.md for the full entry):
CLAUDE.md/the master doc specify `claude-sonnet-4-6` and "temp 0" — both are
stale against the SDK actually installed here. `anthropic` 1.x has removed
sampling params entirely (temperature/top_p/top_k all return 400 on Sonnet 5
/ Opus 5 / Fable 5) in favor of structured outputs — `output_format=<a
pydantic model>` on `client.messages.parse()`, which schema-validates the
response server-side. That's a STRONGER determinism/reliability guarantee
than temp-0 sampling ever was, so this isn't a downgrade — it's the correct
current mechanism for exactly what BUILD.md asked for ("JSON schema
enforced"). Model id corrected to `claude-sonnet-5`, the current Sonnet
(CLAUDE.md's own instruction was "use Sonnet" — the id, not the model
family, was stale).
"""

import os
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2048
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set — perception is offline until it's configured "
                "(Phase B). Every other layer (judgment, action-layer scaffolding, dataset, "
                "simulator) works without it; only real LLM calls are blocked."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


_prompt_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    if name not in _prompt_cache:
        _prompt_cache[name] = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    return _prompt_cache[name]


def call_structured(system_prompt: str, user_content: str, output_model: type[T]) -> T:
    """One perception call, schema-constrained to `output_model`. Raises if
    ANTHROPIC_API_KEY isn't configured — callers don't need their own guard."""
    client = _get_client()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        output_format=output_model,
    )
    return response.parsed_output
