# BUILD_LOG.md — Failure Recovery Evidence

Real bugs, dead ends, and surprises hit during the build. Every entry: date · what broke · root cause · fix · what changed in the design because of it. REAL entries only — never invented or dramatized (per CLAUDE.md §4). This file is judging material for the "failure recovery" criterion precisely because it's honest — an empty section below just means nothing has broken yet, not that this file was skipped.

---

## Entries

### 2026-08-26 — pydantic field named `date` broke on Python 3.14
**What broke:** `engine/schemas.py` raised `TypeError: unsupported operand type(s) for |: 'NoneType' and 'NoneType'` on import, from `Extraction.date: date | None = None`.
**Root cause:** Python 3.14 ships PEP 649 (lazy annotation evaluation) as default — pydantic resolves each field's string annotation against the class's own namespace. Since the field is named `date` (matching BUILD.md §2's `Extraction {..., date: date|None, ...}` literally), by the time pydantic evaluates the annotation, the class-namespace name `date` is already the field's own default (`None`), not the imported `datetime.date` type — so `date | None` becomes `None | None`. This wasn't a `from __future__ import annotations` issue (removing it didn't fix it) — 3.14 does this regardless.
**Fix:** switched `engine/schemas.py` from `from datetime import date, datetime` to `import datetime as dt`, referencing `dt.date` / `dt.datetime` everywhere. A module-qualified reference can never collide with a same-named field.
**Design change:** none to the contracts themselves (field is still named `date` per spec) — just the import style in this one file. Worth remembering for any other pydantic model with a field name that shadows a stdlib type (e.g. a future `id`, `type`, `date`, `list` field).

### 2026-08-26 — sim/run.py emitted events that didn't match the Event schema
**What broke:** nothing crashed, but `sim/run.py`'s `EventLog.emit()` originally wrote a `day: int` field instead of the `ts: datetime` field BUILD.md §2's `Event` contract actually specifies — the simulator would have silently drifted from its own schema if I hadn't cross-checked by explicitly validating the output against `engine.schemas.Event`.
**Root cause:** wrote the simulator's internal day-counter straight into the output dict without mapping it back to the shared `Event` contract — easy to do since the simulator's own logic only ever needs `day`, not a timestamp.
**Fix:** `EventLog.emit()` now constructs a real `engine.schemas.Event` (computing `ts` from a `SIM_EPOCH` + day offset) and validates it before appending; `day` is kept alongside `ts` in the output dict purely as a dashboard-timeline convenience, not part of the contract.
**Design change:** confirms the value of validating generator/simulator output against the shared pydantic contracts as a standard last step, not just trusting that "it ran without an exception."
