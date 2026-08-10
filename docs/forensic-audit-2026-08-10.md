# Orivellum — Forensic Audit: Functional Integrity & Forensic Readiness

**Date:** 2026-08-10 · **Scope:** Section 1 (Functional & Business-Logic Integrity) and Section 6 (Code & Architecture Quality — Forensic Readiness) per the supplied audit brief.
**Method:** Static forensic review (4 parallel investigators over `src/orivellum/`, `artifacts/`), dependency audit, SAST scan, privacy-dataflow scan, targeted manual verification of every High/Critical claim.
**Context weighting:** Orivellum is a single-user, self-hosted system behind access-key auth. Severities are calibrated to that deployment model — several findings would be Critical in a multi-tenant SaaS.

Prior audit: `AUDIT_REPORT.md` (2026-08-05). Items already remediated there (F-1…F-9) are not re-reported.

---

## Findings

### FA-01 · 1B · **High** — Library upload has no size ceiling
**Location:** `src/orivellum/api/routes/library.py:850-893`, exemption at `src/orivellum/api/app.py:510-529`
The streaming multipart upload is (correctly) exempt from the in-RAM body-size middleware, but unlike the transcribe route it never enforces its own byte cap — it writes 1 MiB chunks to disk indefinitely. `POST /library/import` (base64 JSON) decodes the whole body with only the global cap as a limit.
**Repro:** Authenticated multipart upload of an arbitrarily large file with a valid magic signature; the server writes until the disk fills.
**Fix:** Mirror the transcribe pattern (`studio.py:3289-3299`): count streamed bytes, delete the partial file and return 413 past a configured ceiling.

### FA-02 · 1F/6G · **High** — `/api/download/{path}` path guard is bypassable and over-broad
**Location:** `src/orivellum/api/routes/files.py:196-204`
The traversal guard is `str(target).startswith(str(data_dir))` — a sibling directory sharing the prefix (e.g. `data_backup/`) passes. Independent of the bypass, the endpoint serves **any** file under the entire data dir: SQLite DBs, backups, the mail token vault, `api_key.txt`.
**Repro:** Authenticated `GET /api/download/../<data-dir-prefix-sibling>/file`; or `GET /api/download/orivellum.db`.
**Fix:** Use `target.is_relative_to(data_dir.resolve())` and restrict to an allowlist of servable subtrees (`library/`, `outputs/`). Explicitly deny DB files, vault, and key material.

### FA-03 · 1G/6A · **High** — Raw exception text returned to clients across many routes
**Location (representative):** `system.py:422,449,640,657`, `intake.py:214,342`, `works.py:329,352,1268`, `studio.py:1838,3306,3602,4781`, `pklos.py:77`, `mail.py:108,282,317-387`, `mcos.py:242`, `actions.py:173,331`
`HTTPException(500, str(exc))` patterns leak SQLite errors, absolute filesystem paths, and provider URLs to the client, and violate the "generic message to user, detail to logs" discipline. `files.py:182-193` even returns `ok: true` with `[Extraction error: {exc}]` embedded in the payload.
**Fix:** One helper: log with `logger.exception` + request context, return a generic message with an error reference ID. Reserve verbatim detail for deliberate 4xx validation messages.

### FA-04 · 1C · **High** — State transitions trust caller-supplied `from_state` (no compare-and-set)
**Location:** `src/orivellum/capabilities/state_machine.py:136-209`, `src/orivellum/database/db.py:1636-1691`
`apply_transition` validates the *claimed* `from_state` against the transition graph, then executes `UPDATE … WHERE id=?` without `AND state=?` or a rowcount check. Any caller can authorize a different graph edge by forging `from_state`; two concurrent callers can both transition from the same snapshot. `finalize_message` (`db.py:1666-1691`) bypasses the state machine entirely and writes arbitrary caller-provided state.
**Repro:** Call `apply_transition(..., from_state="queued", to_state="running")` on an already-running row — the update succeeds.
**Fix:** `UPDATE … SET state=? WHERE id=? AND state=?`; treat `rowcount == 0` as a conflict (409). Route `finalize_message` through the same gate or constrain it to terminal states.

### FA-05 · 1D · **High** — GENESIS gate/seal are check-then-write without a lock
**Location:** `src/orivellum/api/routes/genesis.py:256-360`
Gate recording checks prior stages and artifact content from a snapshot, then appends ledger + upserts status + updates book state with no `db._lock`/CAS. Two concurrent gate (or seal) requests both pass on the same snapshot and produce duplicate/conflicting ledger entries. Seal completeness is delegated to `compute_seal` without route-level enforcement.
**Fix:** Wrap the read-check-write in one lock/governed transaction; make ledger append idempotent per (book, stage, verdict) or use a conditional upsert as the claim.

### FA-06 · 6C · **High** — Background jobs are restart-fragile with no retry policy
**Location:** `src/orivellum/api/executor.py:33-37,110-132,152-188`, `intake.py:25-59`, `studio.py:3200-3341`, shutdown at `app.py:312-317`
All job registries (executor dashboard, transcription, research, music, forge) are in-memory: a restart orphans running work and clients polling old IDs get 404. There is no automatic retry, backoff, attempt cap, dead-letter record, or alerting — only manual retry, which itself has a check-then-submit race (two rapid retries of a `failed` entry can both submit) and no idempotency guard. On submit failure, `submit_bg` falls back to an *untracked, unbounded* daemon thread, defeating both the bounded pool and dashboard visibility. Shutdown uses `wait=False`, abandoning in-flight work and temp files.
**Fix (incremental):** persist a minimal durable job row (id, kind, state, attempts) for restart reconciliation; make retry an atomic conditional state claim; cap attempts; remove or bound the daemon-thread fallback.

### FA-07 · 6B · **High** — Multi-step pipeline resets span separate commits
**Location:** `src/orivellum/api/routes/studio.py:3403-3417` (re-transcription; acknowledged in comments), `db.py:3247-3259` (background enrichment)
Clear-warnings → delete-derived-knowledge → reset-document → reprocess run as independent commits. A crash mid-sequence leaves a document with old knowledge deleted and nothing rebuilt, with no compensating recovery beyond the generic nightshift stuck-doc pass.
**Fix:** Record a durable "reset in progress" marker (document meta or job row) that startup/nightshift recovery uses to re-drive or roll forward the sequence.

### FA-08 · 1B · **Medium** — PKLOS ingestion mass-assigns arbitrary extra fields
**Location:** `src/orivellum/api/routes/pklos.py:34-50,69-71`
`payload.update(body.model_extra or {})` deliberately persists any client-supplied key, defeating the request model's field allowlist.
**Fix:** `model_config = ConfigDict(extra="forbid")`, or copy only named optional fields.

### FA-09 · 1B · **Medium** — Schema-less `dict` request bodies
**Location:** `bench.py:47-73,110-165`, `mcp.py:153-171`, `works.py:1311-1318`, `library.py:480-492,195-200`
Several endpoints accept `payload: dict = Body(...)` or raw `request.json()` with manual, inconsistent field handling; string lengths are mostly unbounded (the global body cap is the only limit). Low direct risk (single user, most consumed fields are picked explicitly) but inconsistent with the Pydantic discipline elsewhere and easy to regress.
**Fix:** Define request models with `max_length` constraints; forbid extras.

### FA-10 · 1F · **Medium** — Authorization relies solely on global middleware (no per-route dependency)
**Location:** `app.py:354-434,578-590`; bare routers in `system.py`, `files.py`, `forge.py`, `mcp.py`, `actions.py`, …
Every privileged router (settings writes, file downloads, MCP tool dispatch, forge builds) is protected only by the path-prefix middleware. The middleware fails closed and is well-built, but a mounting/path-normalization regression exposes everything at once; there is no second layer.
**Fix:** Add a shared `Depends(require_auth)` on the router constructors — one line each, defense in depth.

### FA-11 · 6G · **Medium** — `SESSION_SECRET` doubles as a login credential
**Location:** `src/orivellum/api/auth_keys.py:9-21,62-75`
When no dedicated login key exists, the cookie-*signing* secret is accepted as the bearer/login key (deprecation-warned but active). Compromise of the signing secret then also authenticates API clients. (This deployment currently relies on that fallback.)
**Fix:** Set `ORIVELLUM_LOGIN_KEY` or the `login_key` DB setting; then remove the fallback and rotate `SESSION_SECRET`.

### FA-12 · 6D · **Medium** — No correlation IDs; action trail is partial
**Location:** access-log middleware in `app.py`; `db.py:537-577` (governed writes)
Requests carry no correlation/request ID and access logs omit the actor. The hash-chained audit + transactional outbox (`governed_write`) is genuinely strong for the mutations that use it — but high-frequency writes run at trace level, several write paths commit directly (e.g. around `db.py:868,902,3259`), and background jobs have no durable IDs. Reconstructing a full user journey from logs alone (the "5-minute incident test") is not currently possible.
**Fix:** Middleware-assigned request ID logged on every line + returned in error responses; include actor in access log; route remaining mutating paths through `governed_write`.

### FA-13 · 6A · **Medium** — Inconsistent exception logging loses tracebacks
**Location:** `executor.py:85-90` (`str(exc)[:300]`), `studio.py:3240-3245,3456-3463` (`logger.warning("%s", exc)`) vs. correct `logger.exception` at `intake.py:184-192`
Worker failures frequently store/log only the message, not the stack, making postmortems depend on transient stdout.
**Fix:** Standardize on `logger.exception` in every worker/route catch that maps to 500 or job failure.

### FA-14 · 6E · **Medium** — Forge Website Factory Node service concentrates SAST risk
**Location:** `artifacts/forge-factory/src/` — 25 High findings: path construction from untrusted data (`agent-tools.mjs:68-104`, `factory-service.mjs:218-237`, `quality-gates.mjs`, `store.mjs`, `utils.mjs`), command execution from tainted input (`process.mjs:7`), plus a Medium DOM-XSS pattern (`public/app.js`).
It is an internal build tool driven by the trusted backend, so exploitability is low today — but it executes commands and writes files from LLM-influenced input, which is the classic prompt-injection-to-RCE path.
**Fix:** Root-confine every path (`path.resolve` + `startsWith` on a canonical build root — or better, `relative()` check), and pass exec args as arrays, never shell strings.

### FA-15 · 1D · **Low** — Research-job dedup is check-then-create
**Location:** `src/orivellum/api/routes/intake.py:249-260`
The pending-job check and creation are not under one lock; rapid duplicate POSTs can enqueue duplicate research. Worst case is wasted LLM calls (writes are per-doc idempotent-ish).
**Fix:** Take the registry lock across check+insert.

### FA-16 · 1A · **Low** — `except (ValueError, Exception)` maps every failure to 422
**Location:** `src/orivellum/api/routes/actions.py:283`
All action-execution errors — including genuine bugs — surface as 422 "validation" errors with raw exception text, unlike the sibling handler at `:328` which distinguishes 422 from 500.
**Fix:** Match the sibling pattern.

### FA-17 · 6E · **Low** — One High dependency vulnerability
The dependency audit reports 1 High (0 Critical) across the workspace; details in the workspace Security pane. No custom cryptography or unmaintained forks found; committed `__pycache__/*.pyc` under `src/` and `scripts/` is build-artifact litter worth a `.gitignore` entry.

### FA-18 · 6A · **Informational** — Scanner false positives (verified)
- SAST Critical ×3 in `actions.py` ("SQL injection" at 168/283/328): flagged lines pass typed inputs to the parameterized DB layer — no string-built SQL. False positive.
- Privacy scan Critical "Password sent to Logs" (`conversations.py:1724-1728`): the log line contains document/work IDs only. False positive.
- `state_machine.py` f-string table/column interpolation uses internal constants (noqa'd) — acceptable, keep constants non-user-controlled.

---

## What is demonstrably done well

- **Auth middleware fails closed** (`app.py:412-434`); generic 401s; startup auto-generates a key; config redacts `api_key` (`configuration/config.py:156-175`). No hardcoded credentials, test accounts, or auth-bypass comments found anywhere in production paths.
- **Upload hardening where it matters most:** magic-byte validation table (`library.py:146`), filename sanitization (`library.py:805-812`), transcribe route's full streaming-cap pattern, zip-slip-guarded backup restore (`app.py:130-145`).
- **Duplicate-send guard** is genuinely atomic: UNIQUE `(conversation_id, client_msg_id)` + `INSERT OR IGNORE` (`db.py:1546-1606`). Document reprocess uses reservation/CAS claims (`library.py:1058-1184`).
- **Forensic backbone exists:** hash-chained audit log + transactional outbox with commit-tamper detection (`db.py:480-820`) — rare at this project scale. FTS/object/chunk writes are single governed transactions.
- **Global 404/version/conflict handlers** are generic and deliberate; body-size middleware covers all non-streaming routes; per-IP rate limiting on auth-sensitive routes.
- **Docs & bus factor (6F):** `ARCHITECTURE.md`, `CHANGELOG.md`, runbooks (`docs/BACKUP_AND_RESTORE.md`, `REMEDIATION.md`), decision records in `replit.md` + agent memory. Best-in-class for a solo project; keep them versioned with schema bumps.

---

## Summary table

| Severity | ID | Category | Finding |
|---|---|---|---|
| High | FA-01 | 1B | Library upload: no size ceiling (disk exhaustion) |
| High | FA-02 | 1F/6G | `/api/download` prefix-bypass + over-broad file disclosure |
| High | FA-03 | 1G/6A | Raw exception text returned to clients (many routes) |
| High | FA-04 | 1C | State transitions trust caller-supplied `from_state` (no CAS) |
| High | FA-05 | 1D | GENESIS gate/seal check-then-write without a lock |
| High | FA-06 | 6C | Jobs restart-fragile; no retry policy; untracked-thread fallback |
| High | FA-07 | 6B | Pipeline resets span separate commits (partial-state window) |
| Medium | FA-08 | 1B | PKLOS ingestion mass-assigns extra fields |
| Medium | FA-09 | 1B | Schema-less `dict` request bodies |
| Medium | FA-10 | 1F | Authorization relies solely on global middleware |
| Medium | FA-11 | 6G | `SESSION_SECRET` doubles as login credential |
| Medium | FA-12 | 6D | No correlation IDs; partial action trail |
| Medium | FA-13 | 6A | Inconsistent exception logging loses tracebacks |
| Medium | FA-14 | 6E | Forge-factory Node service: path/command-injection surface |
| Low | FA-15 | 1D | Research-job dedup is check-then-create |
| Low | FA-16 | 1A | `except (ValueError, Exception)` maps all failures to 422 |
| Low | FA-17 | 6E | One High dependency vuln; committed `.pyc` litter |
| Info | FA-18 | 6A | Verified scanner false positives (SQLi, password-in-log) |

**Totals:** 7 High · 7 Medium · 3 Low · 1 Informational.

**Priority order for remediation:** FA-02 and FA-01 first (data disclosure + disk exhaustion, both small fixes), then FA-04/FA-05 (integrity CAS gaps), then FA-03/FA-13 (logging discipline, one shared helper covers both), then FA-06/FA-07 (durability — the largest effort). FA-10 and FA-11 are cheap defense-in-depth wins.