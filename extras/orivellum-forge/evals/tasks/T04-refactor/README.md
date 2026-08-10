# T04 — Refactor While Preserving Public API

**Capability tested:** Refactoring — restructure internals without breaking the public interface or existing tests.  
**Language:** Python  
**Difficulty:** Medium  
**Expected repair cycles:** 0–1  
**Expected time to gate:** < 6 minutes

---

## Background

The seed project has a `formatter/` module with a single large function `format_report(data)` that does three things: validates the data, transforms it, and renders it to a string. It is ~80 lines long. The public API (`format_report`) must remain identical.

## Task prompt

```
Refactor `formatter/core.py` to extract three private helper functions:
  - `_validate(data)` — raises ValueError on invalid input
  - `_transform(data)` — returns the transformed data dict
  - `_render(data)` — returns the final string

The public function `format_report(data)` must keep the same signature
and return the same output for all inputs.

All existing tests in `tests/test_formatter.py` must continue to pass
without modification.
```

## Non-goals

- No changes to `tests/test_formatter.py`
- No changes to `formatter/__init__.py`
- No new dependencies
- No change to `format_report`'s signature or return type

## Acceptance criteria (verifier checks)

1. `pytest tests/test_formatter.py` exits 0
2. `_validate`, `_transform`, `_render` are present in `formatter/core.py`
3. `format_report` still exists with the same signature
4. `formatter/__init__.py` is unmodified
5. `tests/test_formatter.py` is unmodified

## Scope

Only `formatter/core.py` may be modified.
