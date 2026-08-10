# Status: Archived reference implementation

`writing_architect_pkg` is the standalone Book Production Operating System (BPOS)
package — a stdlib-only Python implementation with its own spec, tests, and
installers. Its verified pipeline stages (B0–B13) have been **ported into the
core product** (`src/orivellum/capabilities/enums.py` and the book pipeline
state machine), which is now the load-bearing implementation.

- **Fate:** kept as archived reference material and spec source of truth.
- **Not imported** by `src/orivellum` or any artifact; nothing in the core
  product breaks if this directory is removed.
- Run its own tests from inside this directory with
  `uv run --with pytest pytest tests/`.
