"""File-based perception cache (CLAUDE.md §6: "Cache perception results keyed
by message_id (arms reuse them; re-runs must be instant and free)").

Layout::

    .cache/perception/<provider>/<kind>/<entity_id>.json

`kind` is one of `extract` / `triage` / `cart_cause`; `entity_id` is the
message_id / invoice_id / cart_id, exactly as CLAUDE.md specifies.

Beyond the id, every entry stores a `fingerprint` — a hash of the provider's
identity (its name AND, for parameterised providers, its tuned parameters)
plus the serialised inputs. A fingerprint mismatch is treated as a MISS, so
editing a message, re-labelling the dataset, or tuning `HeuristicParams` can
never silently serve a stale answer. That makes the cache safe to leave on by
default, which is the point: arms B/C reuse Arm A's perception for free.

Environment:
  PK_PERCEPTION_CACHE=0        disable entirely (tests, forced re-runs)
  PK_PERCEPTION_CACHE_DIR=...  relocate the cache root

TUNABLE (packet P6, config/agents.yaml `perception.cache_enabled`)
--------------------------------------------------------------------------
`enabled()` below checks `PK_PERCEPTION_CACHE` first (unchanged — every
existing test that sets it keeps working exactly as before); only when
that env var is NOT explicitly set does it fall back to
`config/agents.yaml`'s `perception.cache_enabled` (via `engine.config`,
imported lazily inside the function to avoid a circular import —
`engine.config` itself imports `engine.perception.providers`, which
imports this module). The shipped yaml sets `cache_enabled: true`, which
is what this function always returned before that file existed, so an
untouched checkout is byte-identical.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = ROOT / ".cache" / "perception"

# Counters, so tests and the System Health screen can prove the cache is
# actually being hit rather than silently recomputing.
HITS = 0
MISSES = 0
WRITES = 0

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def reset_stats() -> None:
    global HITS, MISSES, WRITES
    HITS = MISSES = WRITES = 0


def stats() -> dict[str, int]:
    return {"hits": HITS, "misses": MISSES, "writes": WRITES}


def enabled() -> bool:
    raw = os.environ.get("PK_PERCEPTION_CACHE")
    if raw is not None and raw.strip():
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    try:
        from engine.config import load_config  # lazy: see module docstring

        yaml_value = load_config().perception.cache_enabled
        if yaml_value is not None:
            return yaml_value
    except Exception:
        pass  # the config surface must never be able to break perception
    return True


def cache_dir() -> Path:
    override = os.environ.get("PK_PERCEPTION_CACHE_DIR")
    return Path(override) if override else DEFAULT_CACHE_DIR


def fingerprint(identity: str, payload: object) -> str:
    """Stable hash of (who computed it, what it was computed from)."""
    blob = json.dumps({"identity": identity, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _path(provider: str, kind: str, entity_id: str) -> Path:
    safe = _SAFE_ID.sub("_", entity_id) or "_"
    return cache_dir() / _SAFE_ID.sub("_", provider) / _SAFE_ID.sub("_", kind) / f"{safe}.json"


def load(provider: str, kind: str, entity_id: str, fp: str, model: type[T]) -> T | None:
    global HITS, MISSES
    if not enabled():
        return None
    path = _path(provider, kind, entity_id)
    if not path.exists():
        MISSES += 1
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("fingerprint") != fp:
            MISSES += 1
            return None
        result = model.model_validate(raw["result"])
    except (OSError, ValueError, KeyError, ValidationError):
        # A corrupt/half-written entry is a miss, never a crash — perception
        # must degrade to "recompute", not "fail the run".
        MISSES += 1
        return None
    HITS += 1
    return result


def store(provider: str, kind: str, entity_id: str, fp: str, result: BaseModel) -> None:
    global WRITES
    if not enabled():
        return
    path = _path(provider, kind, entity_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fingerprint": fp, "result": json.loads(result.model_dump_json())}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return  # a read-only filesystem must not break perception
    WRITES += 1


def clear(provider: str | None = None) -> None:
    """Drop cached entries (all, or one provider's)."""
    target = cache_dir() / provider if provider else cache_dir()
    if not target.exists():
        return
    for p in sorted(target.rglob("*.json"), reverse=True):
        p.unlink(missing_ok=True)
