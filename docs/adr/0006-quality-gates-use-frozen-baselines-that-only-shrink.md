# 6. Quality gates use frozen baselines that only shrink

Date: 2026-08-10 | Status: Accepted

## Context
Adopting strict linting, architecture rules, and size budgets on a mature codebase either blocks all work (fix everything first) or means gates lie (blanket ignores).

## Decision
Each gate records the violations that existed at adoption in a checked-in baseline (ruff.toml per-file-ignores, import-linter ignore_imports, file_budget_baseline.json, .gitleaksignore). Baselines may only lose entries. New code complies from day one.

## Consequences
CI stays green on day one, debt is visible and enumerable, and every fix permanently ratchets the standard. Adding to a baseline is a review-blocking defect.
