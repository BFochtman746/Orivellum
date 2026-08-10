---
name: Repo hygiene & release decisions
description: Cleanup-packet decisions (Aug 2026) — login-key policy, backup medium, CI gates, what stays out of git
---

## Auth login credential (do not regress)
- Rule: every credential check goes through `orivellum.api.auth_keys` — `resolve_login_key()` + `key_matches()` (constant-time). Three call sites: login route, middleware bearer path, `_deps.require_auth`.
- Resolution order: `ORIVELLUM_LOGIN_KEY` env > `login_key` DB setting > `SESSION_SECRET` (deprecated fallback, warns once) > `api_key` DB setting.
- **Why:** SESSION_SECRET signs cookies and must not double as the login key; fallback kept so the user's existing login flow keeps working.
- **How to apply:** never add a new `token == expected` comparison or a second resolution path; import from auth_keys.

## Backups
- Owner's backup medium is `C:\Back_Up` (docs/BACKUP_AND_RESTORE.md, scripts/backup.ps1, weekly).
- All SQLite snapshots must use online backup (`VACUUM INTO` / `Connection.backup`), never raw copies of live DB files — sidecar DBs (atelier/press) included. POST /api/backups archive = orivellum.db + sidecars + config.yaml + library/.
- Restore was exercised once (integrity ok, counts match, app layer opens snapshot).

## CI & coverage
- .github/workflows/ci.yml: ruff `--select F,E9` (style not gated), pytest with `--cov-fail-under=54` (baseline 55% on 2026-08-10 — ratchet up only, never down), UI tsc, pnpm audit.
- Full suite fits on GitHub runners in one process; only this Replit container needs ~10-file chunks.

## Out of git (fetch instead)
- kokoro-v0_19.onnx + voices.bin → scripts/fetch_tts_model.sh/.ps1; data/library, data/nightshift, pytest-of-runner untracked. Renovate handles JS majors (Vite 8 etc. deliberately not bumped by hand).

## Subprojects
- writing_architect_pkg + orivellum-forge live under extras/ with STATUS.md (archived reference / design contracts); nothing imports them.

## Accessibility conventions
- AppFrame content host is a `<main>` landmark; icon-only buttons and SelectTriggers need `aria-label`. axe baseline (Aug 2026): 0 critical; remaining are color-contrast (muted-foreground text) + one scrollable-region-focusable on /system.

## Flaky-test lesson
- Never seed "recent" test timestamps with now-minus-hours when a "this week" filter is involved — it crosses the Monday 00:00 boundary; seed at `now`.
