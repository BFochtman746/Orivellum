---
name: Phase 2-4 hardening decisions
description: Operational visibility, security hardening, MCP server, audio, offline cache, and folder watch added in the 6.5→10/10 push.
---

# Phase 2-4 Hardening Decisions

## Schema v71
Added `access_log` table (id, ts, method, path, status, latency_ms, ip, user_agent, user_id). Indexed on ts and path. Written best-effort after every non-health-probe request via the background executor.

## Access Log Middleware
`app.py` — after the rate-limit middleware, records method/path/status/latency_ms/ip/ua. Health-probe paths excluded via `_ACCESS_LOG_EXCLUDE`. Submits `_write_access_log` to executor — never blocks response. `_write_access_log` function is defined just above the lifespan in app.py.

**Why:** Access log is essential for debugging production issues; must not add latency to request path.

## Security Headers Middleware
Added `security_headers` middleware in `app.py` — adds X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy to all `/api/` responses.

## MIME Magic Byte Validation
`library.py` — `_validate_mime_signature(file_path)` checks magic bytes against `_MIME_SIGNATURES` list. Called after the upload temp file is written, before `_ingest_file`. Raises HTTP 415 on mismatch. The check uses the `.part` temp file suffix — the extension comes from the `name` variable (original filename), but the signature check uses `file_path.suffix` which would be `.part`. This is a bug — the check should use the original filename extension, not the temp file extension.

**Fix needed:** Pass the original filename to `_validate_mime_signature` or derive the ext from `name` instead of `file_path`.

## SSRF Blocklist for Image Gen
`studio.py` — `_is_ssrf_url(url)` blocks loopback/private/link-local/metadata IPs. Called before using a user-configured custom image gen URL. Allows hostnames (except localhost).

## Executor Job Tracking
`executor.py` — `_tracked_submit(fn, *args, kind, label)` wraps submitted work in a closure that records start/finish/error to a `deque(maxlen=200)`. `get_recent_jobs(limit)` returns sorted list newest-first. `GET /api/system/jobs` exposes this; old `/system/jobs` (document queue) renamed to `/system/document-queue`.

## Chat Message Search
`db.py` — `search_messages(query, limit)` does case-insensitive `instr(lower(text), q)` match across all `done` messages, returns snippets. `conversations.py` — `GET /api/conversations/search?q=` endpoint. **Route must be declared BEFORE `GET /api/conversations/{conv_id}`** or it gets swallowed by the parameterized route — the `/search` literal route IS declared first at the top of the conversations router.

Chat sidebar — `isSearchMode = search.trim().length >= 2` switches from filteredConvs list to `msgSearchResults` from the API (debounced 400ms, AbortController per request).

## LLM Health Check
`GET /api/system/llm-health` in `system.py` — probes primary + optional fallback model with a minimal /chat/completions request (max_tokens=1). Returns `overall: "ok"|"degraded"|"down"` plus per-model latency. LlmHealthCard on the system page polls every 60s.

## Hardware Telemetry
`GET /api/system/hardware` — psutil for CPU/RAM/disk, nvidia-smi for NVIDIA GPU, rocm-smi for AMD GPU. HardwareCard on system page polls every 10s.

## MCP Server
`src/orivellum/api/routes/mcp.py` — full MCP 2024-11-05 over HTTP POST at `/mcp`. Tools: `search_knowledge`, `get_document`, `list_works`, `list_documents`. JSON-RPC 2.0, handles batch requests and notifications. Registered in app.py route modules.

## Audio Transcription
`extraction.py` — `_extract_audio(path, db)` tries POST /v1/audio/transcriptions (OpenAI-compat Whisper). Falls back to metadata-only result. Added to `_DISPATCH["audio"]`. Configured via cfg.serving.base_url.

## Folder Watch Daemon
`capabilities/folder_watch.py` — polls a configured directory every 15s. Imports new files via `db.create_document` + `process_document`. Seen paths stored in `db_settings["folder_watch_seen"]` (JSON list, capped at 5000). Started in lifespan alongside nightshift. Settings via `db_settings`: `folder_watch_enabled`, `folder_watch_path`, `folder_watch_work_id`. API: `GET/POST /api/system/folder-watch`.

## Mobile Offline Cache
`artifacts/mobile/lib/cache.ts` — AsyncStorage wrapper with `readCache/writeCache/isCacheStale/clearCache`. Works screen: on `isError`, loads from `cache:works:list`. Library screen: on `listError`, loads from `cache:library:list:{workFilter}`. Both show cached data with existing OfflineBanner component.

## Token-Aware Context Builder
`conversations.py` `_build_messages` — estimates tokens (chars//4), computes 80% budget minus system-prompt tokens minus 256 margin, drops oldest history first. Non-fatal fallback to untrimmed history.

## Known Bugs / Tech Debt
- MIME validation uses temp file path (`.part` extension) — should use original filename extension. Need to pass `name` (original filename) to `_validate_mime_signature`.
- Access log `_write_access_log` references `_deps.get_db` as a closure captured at function-definition time — works at runtime but fragile.
- Old `/system/jobs` endpoint (document processing queue) renamed to `/system/document-queue` — any clients using the old path need updating.
