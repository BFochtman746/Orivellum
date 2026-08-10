---
name: Quality gates (8-phase CI)
description: How the CI gates work, their frozen-baseline/ratchet mechanics, and the tooling quirks hit while building them.
---

# Eight-phase quality gates (adopted 2026-08-10)

CI = two "CI" runs per push (python job + security job + JS job) plus a
nightly mutation workflow. All policies documented in docs/adr/ (0006-0008,
0010) — read those before changing any gate.

## Frozen-baseline mechanics (the core pattern)
- Baselines: ruff.toml per-file-ignores (162 files), pyproject import-linter
  ignore_imports (21 edges), scripts/file_budget_baseline.json (18 giants,
  ceiling = adoption+10%), .gitleaksignore (16 fake test keys),
  MAX_SURVIVORS=41 in check_mutation_score.py. All shrink-only.
- **Per-file-ignores alone are a hole**: they permit NEW violations of a
  grandfathered code in that file. scripts/check_lint_ratchet.py closes it —
  re-lints baselined files with `--isolated` and compares per-(file,code)
  counts to lint_ratchet_counts.json; growth fails, shrink demands
  `--update` to lock in. Any new lint fix in a baselined file requires
  running the update + committing the snapshot.
- Gates that read a results file must FAIL CLOSED on empty/missing input
  (review caught `mutmut run || true` making the gate vacuous).

## Tooling quirks (hard-won)
- mutmut 3: config keys are `source_paths` + `pytest_add_cli_args_test_selection`
  (paths_to_mutate/tests_dir deprecated, tests_dir must be a LIST or it
  crashes); needs `also_copy = ["src/orivellum/", "tests/conftest.py"]`.
  NEVER run `uv run` from inside mutants/ — uv treats it as the project and
  reinstalls orivellum from the mutated copy (recover with `uv sync` at root).
- CI dummy SESSION_SECRET must be ≥32 chars — app.py enforces at import, so
  any CI step that imports the app dies with a short secret.
- OpenAPI drift check (scripts/check_openapi_drift.py) normalizes path params
  to `{}` because spec uses {workId} and FastAPI {work_id}; it is a
  route-existence check only (spec ⊆ app), schemas covered by tsc.
- gitleaks scans full history, so CI checkout needs fetch-depth: 0.
- ruff heredoc scripts: f-strings with nested quotes silently killed a
  python3 heredoc once — prefer `python3 /dev/stdin` or %-format in heredocs,
  and verify the file exists after writing.

## Property tests found a real bug
GENESIS verify_ledger hashed the stored payload JSON *string*, ledger_append
hashed the *dict* — every non-empty ledger failed verification. Fix parses
payload back before re-hashing (backward compatible with all written
ledgers); corrupt JSON now returns a tamper verdict instead of crashing.
Lesson: hash-chain writers and verifiers must share one body-construction
helper, never two hand-written copies.

## Provenance stamps
src/orivellum/version.py code_version() = ORIVELLUM_BUILD env > git describe
--always --dirty > "unknown"; stamped into generate.py output meta,
book_package manifest, workbench archive manifest, /system/health.
