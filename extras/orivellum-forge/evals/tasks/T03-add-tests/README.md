# T03 — Add Behavior Tests for a Defect

**Capability tested:** Test creation — write tests that prove a defect exists before the fix and pass after.  
**Language:** Python  
**Difficulty:** Medium  
**Expected repair cycles:** 0–1  
**Expected time to gate:** < 5 minutes

---

## Background

The seed project has a `validator/` module with an `is_valid_email(address)` function. It has a known defect: it accepts addresses with two consecutive dots (`user..name@example.com`) as valid. No test currently covers this case.

## Task prompt

```
The function `validator/core.py::is_valid_email` incorrectly accepts
email addresses with two consecutive dots in the local part.
Example: `is_valid_email("user..name@example.com")` returns True but should return False.

Add tests in `tests/test_email_double_dot.py` that:
1. Fail BEFORE the bug is fixed (red)
2. Would pass AFTER a correct fix (green specification)

Do NOT fix the bug itself. Do NOT modify `validator/core.py`.
Only add `tests/test_email_double_dot.py`.
```

## Non-goals

- Do not fix `validator/core.py`
- Do not modify any existing test file
- Do not add any new dependencies

## Acceptance criteria (verifier checks)

1. `tests/test_email_double_dot.py` exists
2. Running the tests currently produces at least one FAILURE (the bug is active)
3. The test file imports from `validator` — not a mocked version
4. At least 3 distinct test cases are present
5. The tests would pass if `is_valid_email` were corrected to reject double dots
6. `validator/core.py` is unmodified (byte-for-byte match to seed)
