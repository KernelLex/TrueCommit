"""Config loader for `config/agents.yaml` — the tunable-parameters surface
for the five mesh agents (master doc §7, packet P6). Read CLAUDE.md law 4
before touching this file: **bounds are hard constants in state_machine.py
by design and are never made tunable.** `JudgmentSection` below enforces
that architecturally (a yaml key under `judgment:` fails to load), not just
by convention or a comment.

WHAT IS ACTUALLY WIRED (precise, not hopeful — CLAUDE.md's honesty rule
applies to code claims, not only to metrics)
------------------------------------------------------------------------
- `sentinel.*` — fully wired. `build_sentinel()` / `sentinel_kwargs()` read
  the yaml (falling back to `engine.action.sentinel`'s own module constants,
  unchanged, wherever a key is absent) and produce a real, working
  `Sentinel`. Change `sentinel.max_retries` in a yaml file and a `Sentinel`
  built through this loader retries that many times before dead-lettering —
  there is a test (`tests/test_config.py`).
- `perception.cache_enabled` — fully wired. `engine/perception/cache.py`
  (NOT a `providers/` internal) now checks `PK_PERCEPTION_CACHE` (if
  explicitly set) → this yaml value → `True`, in that order.
- `perception.provider` / `ollama_model` / `ollama_base_url` — the
  precedence is implemented and tested here (`effective_perception_provider`
  etc.) and reported live via `GET /config`, but this packet deliberately
  does NOT rewire the running `WorldRunner`'s provider selection
  (`engine.perception.providers.get_provider()`, called from
  `engine/integration/runner.py`) to consult it. Two hard constraints of
  this packet make that out of scope, not an oversight:
    1. `engine/perception/providers/**` internals are frozen for this
       packet — only its public accessors (`get_provider`,
       `resolve_provider_name`, the module constants) may be imported.
    2. `engine/integration/**` may be touched by a parallel packet.
  Net effect: today, to actually change which perception backend the
  running app uses, set `PK_PERCEPTION_PROVIDER` / `PK_OLLAMA_MODEL` /
  `OLLAMA_BASE_URL` — env already won over the old hardcoded default before
  this file existed, so nothing regresses; the yaml default for these three
  fields is reported+honest but not yet load-bearing. See
  `tracking/DECISIONS.md`.
- `auditor.*` — NOT wired. Placeholders only, honestly labelled, until the
  Auditor packet (Day 7, master doc §7.3).
- `judgment` — has no tunables, ever (law 4). Present in the yaml purely as
  a documented, read-only mirror of the constants in `state_machine.py`;
  `bounds_snapshot()` below reads those constants directly (never
  duplicates their values by hand) so this module can never quietly drift
  from the real bounds.

PRECEDENCE (perception.* and sentinel.*)
-----------------------------------------
    explicit function argument   (programmatic callers / tests only)
        > environment variable, IF EXPLICITLY SET
            > config/agents.yaml, IF THE KEY IS PRESENT (not null)
                > the builtin default (the constant this project shipped
                  with before this file existed)

The shipped `config/agents.yaml` sets every value to exactly today's
builtin default, so a fresh checkout with the file untouched — or even
deleted — produces byte-identical behaviour to before this packet existed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.action.sentinel import (
    BACKOFF_MINUTES as _SENTINEL_BACKOFF_MINUTES,
    CIRCUIT_BREAKER_THRESHOLD as _SENTINEL_CIRCUIT_BREAKER_THRESHOLD,
    LINK_OPEN_TIMEOUT_HOURS as _SENTINEL_LINK_OPEN_TIMEOUT_HOURS,
    MAX_RETRIES as _SENTINEL_MAX_RETRIES,
    Sentinel,
)
from engine.judgment.state_machine import (
    HARD_STEP_CAP,
    MANDATE_AMOUNT_CAP,
    MAX_ESCALATE_STAGE,
    MAX_TOUCHES_PER_WEEK,
    RENEGOTIATION_CAP,
    RETRY_ON_EXECUTION_FAILURE,
    TOUCH_WINDOW_DAYS,
)
from engine.perception.providers import DEFAULT_PROVIDER
from engine.perception.providers import ENV_VAR as _PERCEPTION_ENV_VAR
from engine.perception.providers.ollama import DEFAULT_BASE_URL as _OLLAMA_DEFAULT_BASE_URL
from engine.perception.providers.ollama import DEFAULT_MODEL as _OLLAMA_DEFAULT_MODEL
from engine.perception.providers.ollama import ENV_BASE_URL as _OLLAMA_ENV_BASE_URL
from engine.perception.providers.ollama import ENV_MODEL as _OLLAMA_ENV_MODEL

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "agents.yaml"

# engine/perception/cache.py's own env var name — duplicated here (as a
# plain string, not an import) only so `effective_cache_enabled()` can
# describe the same precedence without importing cache.py, which would
# create an import cycle (cache.py imports this module lazily; see there).
ENV_CACHE = "PK_PERCEPTION_CACHE"

_ALLOWED_PERCEPTION_PROVIDERS = {"heuristic", "ollama", "anthropic"}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class JudgmentSection(BaseModel):
    """Deliberately empty, and stays that way. CLAUDE.md law 4: bounds live
    as constants in state_machine.py and are NEVER configurable. A yaml file
    that carries a key here fails to load rather than being silently
    ignored — silently ignoring a would-be bound override is scarier than
    crashing loudly at startup."""

    model_config = ConfigDict(extra="forbid")


class PerceptionSection(BaseModel):
    provider: str | None = None
    ollama_model: str | None = None
    ollama_base_url: str | None = None
    cache_enabled: bool | None = None

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        vv = v.strip().lower()
        if vv not in _ALLOWED_PERCEPTION_PROVIDERS:
            raise ValueError(
                f"perception.provider must be one of {sorted(_ALLOWED_PERCEPTION_PROVIDERS)}, got {v!r}"
            )
        return vv


class SentinelSection(BaseModel):
    max_retries: int | None = Field(default=None, ge=0, le=20)
    backoff_minutes: list[int] | None = None
    link_open_timeout_hours: float | None = Field(default=None, gt=0)
    circuit_breaker_threshold: int | None = Field(default=None, ge=1)

    @field_validator("backoff_minutes")
    @classmethod
    def _validate_backoff(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v or any((not isinstance(x, int)) or isinstance(x, bool) or x <= 0 for x in v):
            raise ValueError("sentinel.backoff_minutes must be a non-empty list of positive integers")
        return v


class AuditorSection(BaseModel):
    """Not wired until the Auditor packet (Day 7, master doc §7.3). Present
    so the yaml surface for all five mesh agents exists up front; reading
    these values today has no runtime effect anywhere."""

    sample_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    quarantine_threshold: float = Field(default=0.85, ge=0.0, le=1.0)


class AgentsConfig(BaseModel):
    perception: PerceptionSection = Field(default_factory=PerceptionSection)
    sentinel: SentinelSection = Field(default_factory=SentinelSection)
    auditor: AuditorSection = Field(default_factory=AuditorSection)
    judgment: JudgmentSection = Field(default_factory=JudgmentSection)


# ---------------------------------------------------------------------------
# Loading (memoised per resolved path)
# ---------------------------------------------------------------------------

_cached: AgentsConfig | None = None
_cached_path: Path | None = None


def load_config(path: Path | str | None = None, *, force_reload: bool = False) -> AgentsConfig:
    """Load + validate `config/agents.yaml`. A missing file resolves to an
    all-default `AgentsConfig` (every field unset/builtin), so a fresh
    checkout that deletes the file never crashes — it just behaves as if
    every value were left at its builtin default."""
    global _cached, _cached_path
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not force_reload and _cached is not None and _cached_path == target:
        return _cached
    if target.exists():
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{target}: expected a YAML mapping at the top level, got {type(raw).__name__}")
    else:
        raw = {}
    config = AgentsConfig.model_validate(raw)
    _cached, _cached_path = config, target
    return config


def reset_cache() -> None:
    """Test hook: drop the memoised config so the next `load_config()` call
    re-reads from disk (or picks up a different `path` argument)."""
    global _cached, _cached_path
    _cached = _cached_path = None


# ---------------------------------------------------------------------------
# Perception precedence — resolved + reported, not (yet) rewired into the
# live get_provider() call sites. See module docstring.
# ---------------------------------------------------------------------------


def effective_perception_provider(cfg: AgentsConfig | None = None) -> tuple[str, str]:
    """(value, source) where source is one of "env" / "yaml" / "builtin"."""
    env = os.environ.get(_PERCEPTION_ENV_VAR, "")
    if env.strip():
        return env.strip().lower(), "env"
    cfg = cfg or load_config()
    if cfg.perception.provider:
        return cfg.perception.provider, "yaml"
    return DEFAULT_PROVIDER, "builtin"


def effective_ollama_model(cfg: AgentsConfig | None = None) -> tuple[str, str]:
    env = os.environ.get(_OLLAMA_ENV_MODEL, "")
    if env.strip():
        return env, "env"
    cfg = cfg or load_config()
    if cfg.perception.ollama_model:
        return cfg.perception.ollama_model, "yaml"
    return _OLLAMA_DEFAULT_MODEL, "builtin"


def effective_ollama_base_url(cfg: AgentsConfig | None = None) -> tuple[str, str]:
    env = os.environ.get(_OLLAMA_ENV_BASE_URL, "")
    if env.strip():
        return env, "env"
    cfg = cfg or load_config()
    if cfg.perception.ollama_base_url:
        return cfg.perception.ollama_base_url, "yaml"
    return _OLLAMA_DEFAULT_BASE_URL, "builtin"


def effective_cache_enabled(cfg: AgentsConfig | None = None) -> tuple[bool, str]:
    """Mirrors the precedence `engine/perception/cache.py`'s own
    `enabled()` implements (that module consults this one lazily) — kept
    here too so `GET /config` can report the resolved value without
    re-deriving the rule inline."""
    env = os.environ.get(ENV_CACHE)
    if env is not None and env.strip():
        return env.strip().lower() not in {"0", "false", "no", "off"}, "env"
    cfg = cfg or load_config()
    if cfg.perception.cache_enabled is not None:
        return cfg.perception.cache_enabled, "yaml"
    return True, "builtin"


# ---------------------------------------------------------------------------
# Sentinel — actually wired (this factory is the wiring).
# ---------------------------------------------------------------------------


def sentinel_kwargs(cfg: AgentsConfig | None = None) -> dict[str, Any]:
    """yaml value if present, else Sentinel's own module constant — so a
    Sentinel built from an all-default config behaves exactly like a bare
    `Sentinel()` does today (zero behaviour change when the yaml is
    untouched)."""
    cfg = cfg or load_config()
    s = cfg.sentinel
    return {
        "max_retries": s.max_retries if s.max_retries is not None else _SENTINEL_MAX_RETRIES,
        "backoff_minutes": (
            list(s.backoff_minutes) if s.backoff_minutes is not None else list(_SENTINEL_BACKOFF_MINUTES)
        ),
        "link_open_timeout_hours": (
            s.link_open_timeout_hours
            if s.link_open_timeout_hours is not None
            else _SENTINEL_LINK_OPEN_TIMEOUT_HOURS
        ),
        "circuit_breaker_threshold": (
            s.circuit_breaker_threshold
            if s.circuit_breaker_threshold is not None
            else _SENTINEL_CIRCUIT_BREAKER_THRESHOLD
        ),
    }


def build_sentinel(cfg: AgentsConfig | None = None) -> Sentinel:
    """The actual wiring: a real `Sentinel` built from `config/agents.yaml`
    (or the shipped, default-matching values if a key is absent)."""
    return Sentinel(**sentinel_kwargs(cfg))


# ---------------------------------------------------------------------------
# Bounds — read-only display material (CLAUDE.md law 4). Values are read
# LIVE from state_machine.py's own constants, never hand-copied, so this
# can never drift from what check_bounds() actually enforces.
# ---------------------------------------------------------------------------


def bounds_snapshot() -> dict[str, list[dict[str, Any]]]:
    return {
        "numeric": [
            {
                "name": "MAX_TOUCHES_PER_WEEK",
                "value": MAX_TOUCHES_PER_WEEK,
                "detail": "per debtor, rolling 7-day window",
            },
            {
                "name": "TOUCH_WINDOW_DAYS",
                "value": TOUCH_WINDOW_DAYS,
                "detail": "the rolling window MAX_TOUCHES_PER_WEEK counts against",
            },
            {
                "name": "RENEGOTIATION_CAP",
                "value": RENEGOTIATION_CAP,
                "detail": "mandate re-offers after a broken promise, then no more",
            },
            {
                "name": "MANDATE_AMOUNT_CAP",
                "value": MANDATE_AMOUNT_CAP,
                "detail": "above this, falls back to partial + payment link",
            },
            {
                "name": "RETRY_ON_EXECUTION_FAILURE",
                "value": RETRY_ON_EXECUTION_FAILURE,
                "detail": "one retry on mandate execution failure, then link/ladder/human",
            },
            {
                "name": "MAX_ESCALATE_STAGE",
                "value": MAX_ESCALATE_STAGE,
                "detail": "escalation ladder caps at stage 4, next failure forces HUMAN_HANDOFF",
            },
            {
                "name": "HARD_STEP_CAP",
                "value": HARD_STEP_CAP,
                "detail": "termination backstop -- forces HUMAN_HANDOFF regardless of event content",
            },
        ],
        "policy": [
            {
                "name": "dispute = instant stop",
                "detail": (
                    "a dispute_raised event moves any non-terminal entity straight to DISPUTED, "
                    "no further outbound actions"
                ),
            },
            {
                "name": "no mandate re-offer after refusal",
                "detail": "once mandate_refused, that entity is never offered a mandate again",
            },
            {
                "name": "legal-stage notices -> merchant review",
                "detail": (
                    "the agent never sends legal communication itself; ESCALATE_3 legal-stage "
                    "touches are blocked and routed to a human"
                ),
            },
        ],
    }


__all__ = [
    "AgentsConfig",
    "AuditorSection",
    "DEFAULT_CONFIG_PATH",
    "JudgmentSection",
    "PerceptionSection",
    "SentinelSection",
    "bounds_snapshot",
    "build_sentinel",
    "effective_cache_enabled",
    "effective_ollama_base_url",
    "effective_ollama_model",
    "effective_perception_provider",
    "load_config",
    "reset_cache",
    "sentinel_kwargs",
]
