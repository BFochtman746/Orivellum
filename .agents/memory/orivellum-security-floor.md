---
name: Security test floor & settings durability
description: CI floor/zero-caller rules for security paths, and the settings-commit trap
---

# Security test floor (tests/test_security_floor.py)

Two mechanical CI rules, enforced as pytest tests (CI runs `pytest tests/`):

1. **Floor rule** — every module imported (incl. function-local imports) by a
   security/permission/unattended root (nightshift, custodian, autonomy, the
   mail package, shield, auth_keys, api/_deps) must be referenced by at least
   one file in tests/. New imports on those paths fail CI until a test names them.
2. **Zero-caller rule** — (a) every capabilities module must be imported by
   production code outside itself; (b) every public entry point must be
   referenced outside its own module by production code or tests.

Both have dated allowlists inside the test file. **They must only shrink** —
72 names grandfathered 2026-08-12.

**Why:** websearch/training_plan/rerank_candidates all shipped built-but-unwired;
security modules (token_vault, oauth, action_policy) shipped with zero tests.

# Settings durability trap

`db._set_setting()` does NOT commit — `governed_write` is normally the only
committer. A bare `_set_setting` call leaves the write in an open transaction,
invisible to the read connection (`get_setting` uses a separate read conn) and
lost on restart unless a later commit piggybacks it. This silently broke mail
token persistence.

**How to apply:** for secret/plumbing settings that must not hit the audit log,
use `db.set_setting_unaudited(key, value)`. It commits via `_maybe_commit`, so
inside `atomic()` it correctly defers to the outer transaction (a later
exception rolls it back). Never call `_set_setting` directly outside
`governed_write`, and never call `self._conn.commit()` from a mutation method
— `_maybe_commit` is the only correct committer.

# Other pinned behaviors (see the mail test files)

- `ACTION_DELETE` requires explicit user approval even when `delete_enabled`
  (was missing from the approval-required tuple — a real policy hole).
- MailStore nonces are FK-bound to a real mail record; tests must seed one via
  `upsert_mail_record` before `issue_nonce`.
- Rules must be AST-based (imports/identifiers), never text greps —
  docstring/string mentions masked ~15 genuinely dead or untested names.
