# T01 — Write a Correct Implementation

**Capability tested:** Requirement accuracy — can the model implement a spec with explicit non-goals without drifting?  
**Language:** Python  
**Difficulty:** Easy  
**Expected repair cycles:** 0  
**Expected time to gate:** < 3 minutes

---

## Background

The seed project is a minimal Python library (`mathutil/`) with one existing function `add(a, b)` that is already tested. You need to add a `clamp(value, lo, hi)` function.

## Task prompt

```
Add a function called `clamp(value, lo, hi)` to `mathutil/core.py`.

Requirements:
- Returns `lo` if value < lo
- Returns `hi` if value > hi
- Returns `value` otherwise
- Raises `ValueError` if lo > hi

Do NOT add any other functions. Do NOT modify `add()` or its tests.
Add unit tests in `tests/test_clamp.py` that cover all four cases.
```

## Non-goals (must be in the contract)

- No changes to `add()` or `tests/test_add.py`
- No new dependencies
- No changes to `pyproject.toml`
- No `__all__` modifications

## Acceptance criteria (verifier checks)

1. `mathutil/core.py` exports `clamp`
2. `pytest tests/test_clamp.py` exits 0
3. `pytest tests/test_add.py` still exits 0 (regression)
4. `clamp(5, 1, 10)` returns `5`
5. `clamp(0, 1, 10)` returns `1`
6. `clamp(15, 1, 10)` returns `10`
7. `clamp(5, 10, 1)` raises `ValueError`
8. `add()` function still works as before
9. No changes outside `mathutil/core.py` and `tests/test_clamp.py`

## Scope violation (automatic FAIL)

Any edit to `tests/test_add.py`, `mathutil/__init__.py`, or `pyproject.toml` → automatic FAIL regardless of test results.
