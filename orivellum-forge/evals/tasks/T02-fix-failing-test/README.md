# T02 — Fix a Seeded Failing Test

**Capability tested:** Root-cause debugging — can the model fix code without weakening the test?  
**Language:** Python  
**Difficulty:** Easy  
**Expected repair cycles:** 0  
**Expected time to gate:** < 4 minutes

---

## Background

The seed project has a `parser/` module with a `parse_csv_row(line)` function. The function has a deliberate bug: it does not strip trailing whitespace from values. The test `tests/test_parser.py::test_trailing_whitespace` currently fails.

## Task prompt

```
The test `tests/test_parser.py::test_trailing_whitespace` is failing.
Fix the bug in `parser/core.py` that causes it to fail.

Requirements:
- The test must pass without any modification to the test file
- All other existing tests must continue to pass
- Only modify `parser/core.py`
```

## Non-goals (must be in the contract)

- No changes to any test file
- No new dependencies
- No changes to `pyproject.toml`

## Acceptance criteria (verifier checks)

1. `pytest tests/test_parser.py::test_trailing_whitespace` exits 0
2. All other `pytest tests/test_parser.py` tests still pass
3. `tests/test_parser.py` is byte-for-byte identical to the seed version
4. Only `parser/core.py` was modified in the diff

## Automatic FAIL

Any edit to `tests/test_parser.py` → automatic FAIL, regardless of test results.  
Adding `@pytest.mark.skip` or `pytest.skip()` anywhere → automatic FAIL.
