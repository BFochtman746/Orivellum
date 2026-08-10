# Contributing to Orivellum

Orivellum is a local-first, single-user system, but contributions are welcome.
This guide covers local setup, testing, linting, and the pull-request checklist.

## Local development setup

Orivellum is a pnpm monorepo with a Python backend and a React frontend.

- **Python** is managed with [uv](https://docs.astral.sh/uv/) (Python ≥ 3.12).
- **JavaScript/TypeScript** is managed with [pnpm](https://pnpm.io/) (≥ 9,
  Node.js ≥ 20).

Install everything:

```bash
uv sync && pnpm install
```

On Windows, `scripts/setup-windows.ps1` installs OS prerequisites (Tesseract,
Poppler, FFmpeg, uv, pnpm) and runs the two installs for you.

Run the dev servers separately while iterating:

```bash
# API (reads PORT, default 8080)
uv run python -m orivellum.api.main

# Frontend (Vite dev server)
pnpm --filter @workspace/orivellum-ui run dev
```

See `README.md` for one-command startup and `ARCHITECTURE.md` for a component
overview.

## Running tests

Python tests live in `tests/` and run under pytest:

```bash
uv run --with pytest pytest tests/
```

> **Memory warning.** The full suite can exhaust memory in constrained
> containers (each test tends to build a fresh SQLite database). In limited
> environments, run the suite in chunks of ~10 files at a time rather than all
> at once, for example:
>
> ```bash
> uv run --with pytest pytest tests/test_a.py tests/test_b.py  # ...up to ~10 files
> ```

Frontend unit tests use Vitest:

```bash
pnpm --filter @workspace/orivellum-ui run test
```

## Linting and type checks

Python lint (ruff — config lives in `pyproject.toml`):

```bash
uvx ruff check src/
```

TypeScript type check for the UI:

```bash
pnpm exec tsc --noEmit   # run inside artifacts/orivellum-ui
# or: pnpm --filter @workspace/orivellum-ui run typecheck
```

## PowerShell script conventions

The `scripts/*.ps1` files must be:

1. Prefixed with a **UTF-8 BOM** (bytes `EF BB BF`). Windows PowerShell 5.1
   mis-decodes BOM-less UTF-8 files.
2. **ASCII-only** after the BOM — no smart quotes, em dashes, or other
   non-ASCII characters (belt and braces in case a tool strips the BOM).

These rules are enforced by `tests/test_powershell_scripts.py`, which the
`ps1-check` workflow runs. When adding or editing a `.ps1` file, keep it ASCII
and re-save with a BOM.

## Pull-request checklist

Before opening a PR:

- [ ] Tests pass (`uv run --with pytest pytest tests/`, in chunks if memory is
      tight).
- [ ] `uvx ruff check` is clean on the files you changed.
- [ ] TypeScript type check passes if you touched the UI.
- [ ] `.ps1` scripts are ASCII-only with a UTF-8 BOM (if touched).
- [ ] No secrets or credentials committed.
- [ ] No personal data (`data/library/`) and no model binaries
      (`kokoro-v0_19.onnx`, `voices.bin`, or other large assets) committed —
      these are fetched via `scripts/fetch_tts_model.*`.

## Schema changes

The database auto-migrates on server start. To add a migration, append a new
`(version, description, sql)` tuple in `src/orivellum/database/schema.py` with
the next version number. Migrations run once and are never re-applied — never
edit an existing migration.
