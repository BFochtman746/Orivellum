# 7. No source file grows past 1,500 lines

Date: 2026-08-10 | Status: Accepted

## Context
Multi-thousand-line files (db.py at ~7,500 lines) are where defects hide and merges hurt.

## Decision
scripts/check_file_budget.py gates CI: new files max 1,500 lines; the 18 existing giants are frozen at adoption size +10% and must shrink when touched. Ceilings are never raised.

## Consequences
Growth pressure forces extraction of modules instead of accretion. Splitting db.py is expected to happen gradually, table-group by table-group.
