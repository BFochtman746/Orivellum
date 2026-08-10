# Backup & Restore

Owner-confirmed backup medium: **`C:\Back_Up`** on the production PC ("Nimo").
Recommendation: pair it with one offsite copy (cloud drive or an external disk
stored elsewhere) — a folder on the same machine does not survive disk failure.

## What to back up

Everything that cannot be regenerated lives under `data/` plus two config files:

| Item | Why |
|---|---|
| `data/orivellum.db` (+ `-wal`, `-shm` if present) | The knowledge base — documents metadata, knowledge items, conversations, settings, audit log |
| `data/atelier.db`, `data/press.db` | Studio / Pressworks databases |
| `data/library/` | The original ingested document files |
| `data/premium-voices/` | Cloned/premium voice assets (if any) |
| `config.yaml` | Serving configuration (model endpoints, tuning) |
| The `SESSION_SECRET` value (and `ORIVELLUM_LOGIN_KEY` if set) | Session signing + login; store in a password manager, **never** in the backup folder in plain text |

Not needed: `kokoro-v0_19.onnx`, `voices.bin` (re-download with
`scripts/fetch_tts_model.ps1`), `.pythonlibs`, `node_modules`, `data/outputs/`,
`data/nightshift/` (regenerated nightly).

## How to back up (Windows)

Run from the repo root (schedule weekly, or after any large import):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup.ps1
```

The script:
1. Uses SQLite's online-backup (`VACUUM INTO`) so the copy is consistent even
   while the server is running — never raw-copy a live `.db` file alone, the
   `-wal` sidecar may hold unflushed writes.
2. Copies `data/library/`, the other DBs, and `config.yaml`.
3. Writes everything to a timestamped folder `C:\Back_Up\orivellum-YYYYMMDD-HHmmss\`.
4. Keeps the 8 most recent snapshots and deletes older ones.

## How to restore

Tested procedure (executed against a clean environment on 2026-08-10):

1. Set up a fresh clone: `git clone <repo>`, then the normal setup
   (`scripts\setup-windows.ps1`, `scripts\fetch_tts_model.ps1`).
2. Copy the snapshot's `data\` contents into the fresh clone's `data\`
   directory (create it if missing). Copy `config.yaml` to the repo root.
3. Set `SESSION_SECRET` (same value as before if you want existing session
   cookies to survive; a new value just forces re-login).
4. Start the server. On boot it runs schema migrations forward automatically —
   restoring an older snapshot into newer code is supported; the reverse
   (newer DB into older code) is not.
5. Verify: log in, open Library (documents present), open a Work, run one chat
   query that retrieves knowledge.

### Restore verification notes (what we checked)

- `PRAGMA integrity_check` on the restored DB returns `ok`.
- Document, knowledge, and conversation counts match the source.
- The API boots against the restored data directory and serves `/api/health`.

## Schedule

| Frequency | Action |
|---|---|
| Weekly (or after big imports) | `scripts\backup.ps1` → `C:\Back_Up` |
| Monthly | Copy the latest snapshot offsite (cloud drive / external disk) |
| Quarterly | Test-restore the latest snapshot into a scratch folder (steps above) |
