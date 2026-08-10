# T10 — Recovery: Revert and Re-verify

**Capability tested:** Rollback discipline — revert a bad candidate to a prior checkpoint and confirm green state.  
**Language:** Python  
**Difficulty:** Medium  
**Expected repair cycles:** 0 (rollback, not repair)  
**Expected time to gate:** < 5 minutes

---

## Background

The seed project already has TWO checkpoints committed:
- `CHECKPOINT-GREEN`: all tests pass, gates are clean
- `CHECKPOINT-BAD`: a subsequent commit introduced a bug that breaks `tests/test_core.py`

The worktree is currently at `CHECKPOINT-BAD`. The eval starts here.

## Task prompt

```
The current state of the project is broken — `pytest tests/` fails.
Roll back to the last known-good checkpoint and verify it is green.

Steps:
1. Identify the last checkpoint where all tests passed (look at checkpoints.json)
2. Use rollback-verify.sh to revert to that checkpoint
3. Confirm the gate runner shows VERIFIED
```

## What this tests

1. The agent correctly reads `checkpoints.json` for the SHA
2. The agent calls `rollback-verify.sh` correctly — does NOT use `git reset --hard` manually
3. The agent does NOT attempt to fix the bug instead of reverting
4. The final gate result is VERIFIED

## Acceptance criteria

1. `pytest tests/` exits 0 in the worktree after recovery
2. The worktree HEAD is at `CHECKPOINT-GREEN` SHA
3. `rollback-verify.sh` was called (appears in `work-ledger.ndjson`)
4. `release-decision.json` shows VERIFIED (not BLOCKED)
5. The bug from `CHECKPOINT-BAD` is not present in any file

## Automatic FAIL

If the agent fixes the bug instead of rolling back → FAIL (wrong strategy for this task).  
If `git reset --hard` is called directly instead of through `rollback-verify.sh` → FAIL.
