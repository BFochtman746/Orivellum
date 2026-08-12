---
name: Security test floor & settings durability
description: CI floor/zero-caller policy for security paths, and the settings-commit trap
---

# Security test floor policy

Two mechanical CI rules live as pytest tests (so plain `pytest tests/` enforces
them). They must stay **AST-based** (imports/identifiers) — text greps let
docstring/string mentions mask genuinely dead or untested code (~15 names when
converted).

1. **Floor rule** — any module imported by a permission decision, security
   path, or unattended job must be imported by at least one test file.
2. **Zero-caller rule** — a capabilities module nothing imports, or a public
   entry point nothing references outside its module, fails the build.
   **Why:** websearch/training_plan/rerank_candidates all shipped built but
   never wired.

Rules must be symbol-aware (a reference counts only when it resolves to the
owning module — same-spelled names and string patch targets never count).
Allowlists are dated AND tamper-evident: a hardcoded SHA-256 of the key set
means any addition, removal, or swap forces a loud baseline edit. Only shrink.

# Settings durability trap

`db._set_setting()` does NOT commit; the read connection never sees the write
and it is lost on restart unless a later commit piggybacks it. This silently
broke mail token persistence and the steward's disconnect flag.

**How to apply:** `db.set_setting_unaudited(key, value)` is the only correct
direct-setting writer for secret/plumbing keys (no audit row). It commits via
`_maybe_commit`, so inside `atomic()` it defers to the outer transaction and
rolls back with it. Mutation methods must never call `self._conn.commit()`
directly. When a state flag must survive restart, add a reopen-the-DB
persistence test — in-process reads pass even when the write was uncommitted.

# Mail policy pins

- `ACTION_DELETE` requires explicit user approval even when `delete_enabled`
  (was missing from the approval-required tuple — a real policy hole).
- Mail nonces are FK-bound to a real mail record; tests must seed one first.
