# Orivellum — Comprehensive Systems Audit Report

**Audit Date:** 2026-08-05  
**Auditor Role:** Lead Systems Architect, Security Auditor, Home AI Server Expert  
**Methodology:** Static analysis, forensic code review, parallel multi-domain investigation, comparative gap analysis  
**Scope:** Full monorepo — Python/FastAPI backend, React web frontend, Expo mobile app, SQLite database layer  

---

## 1. Executive Summary & System Overview

Orivellum is a personal home AI server and knowledge-management platform. Its core premise: ingest any document or media, extract structured knowledge from it using a local or remote LLM, organize that knowledge into "Works" (book-scale projects), and serve it back through AI chat, brainstorming, TTS, image generation, and a learning/quiz loop.

The system is architecturally sophisticated for a solo/small-team project. It has a real governance layer (PKLOS, MONARCH), a 10-pass nightly maintenance daemon, semantic search with an embeddings circuit breaker, a multi-strategy TTS pipeline, and a formal state machine for the book pipeline. These are not common in home AI projects.

**Overall Completeness Rating: 6.5 / 10**

The core document→knowledge→chat loop works well. The platform is held back by: blocking I/O on async routes, no hardware telemetry, no persistent job queuing, under-secured API exposure surface, and several silent-failure paths in the LLM pipeline that a user cannot debug without reading logs.

---

## 2. Component Inventory & Data Flow Map

### 2.1 Services

| Service | Runtime | Entry Point | Port |
|---------|---------|-------------|------|
| API Server | Python 3.12 / FastAPI / Uvicorn | `src/orivellum/api/main.py` | `$PORT` |
| Web Frontend | React 18 / Vite / TailwindCSS | `artifacts/orivellum-ui/` | `$PORT` |
| Mobile App | React Native / Expo Router | `artifacts/mobile/` | Expo dev |
| Component Sandbox | Vite (mockup preview) | `artifacts/mockup-sandbox/` | `$PORT` |

### 2.2 API Route Inventory

| Router File | Endpoints | Purpose |
|-------------|-----------|---------|
| `auth.py` | POST /login, /logout; GET /me | Session cookie auth |
| `library.py` | GET/POST/PATCH/DELETE /library; POST /library/upload; POST /library/reprocess-all; GET /library/duplicates | Document CRUD + upload |
| `works.py` | CRUD /works; /works/{id}/stats, /brainstorm, /pipeline, /documents, /knowledge, /tasks, /gaps/top, /suggestions | Work management + book pipeline |
| `conversations.py` | CRUD /conversations; POST /messages (SSE streaming); PATCH /rename, /archive | Chat with SSE streaming |
| `knowledge.py` | GET /knowledge; GET /knowledge/search; PATCH /knowledge/{id}/review | Knowledge management |
| `intake.py` | POST /intake; POST /intake/research | Document intake pipeline |
| `studio.py` | POST /studio/tts, /studio/ocr, /studio/image, /studio/document; GET /studio/outputs | TTS / OCR / Image gen |
| `generate.py` | POST /generate/report, /excel, /slides, /bundle | Export / generation |
| `system.py` | GET /system/briefing, /settings, /diagnostics, /embeddings/status, /embeddings/probe | System health |
| `nightshift.py` (routes) | GET /api/nightshift/status, POST /trigger | Maintenance daemon control |
| `review.py` | GET /api/review; POST /api/review/{id}/resolve | Governance review queue |
| `pklos.py` | PKLOS authority/verifier/resolver/enforcer routes | Knowledge provenance layer |
| `projects.py` | CRUD /projects; GET /projects/{id}/concepts | Project + learning |
| `learning.py` | GET /learning/concepts; POST /learning/quiz; /evidence | Learning loop |
| `mcos.py` | MCOS calibration/benchmark routes | LLM quality governance |
| `memory.py` | GET /api/memory | Persistent memory recall |
| `actions.py` | POST /api/actions/{id}/run | Action registry runner |

### 2.3 Background Workers

| Worker | Schedule | Passes / Steps |
|--------|----------|---------------|
| **Nightshift daemon** (`nightshift.py`) | 03:00 local, daily | 14 passes: DB optimise → temp cleanup → orphan cleanup → stuck-doc retry → sparse-doc harvest → gap analysis → evidence rescore → embedding backfill → work stats refresh → MCOS benchmarks → version suggestions → outbox drain → vacuum → nightshift report |
| **Document pipeline** (`pipeline.py`) | Per upload (background thread) | extract → chunk → store → harvest → dedup → embed |
| **ZIP exploder** (`pipeline.py`) | Per ZIP upload | Explodes children → runs pipeline per child → suggests Work |
| **Intake orchestrator** (`intake.py`) | On demand | 5 stages: identify → extract → embed → research → profile |
| **Book pipeline** (`works.py`) | Per user request | 17-stage SM: outline → chapter drafts → revision → export |
| **Embedding backfill** (`embeddings.py`) | Called from nightshift + pipeline | Batch embeds all un-vectored chunks |

### 2.4 Database Table Inventory (70 schema versions)

Core tables: `objects`, `settings`, `works`, `documents`, `chunks`, `knowledge`, `conversations`, `messages`, `tasks`, `suggestions`, `entities`, `edges`, `vectors`, `doc_dupes`, `minhash_sig`, `work_gap_cache`, `object_provenance`, `memories`, `audit_log`, `outbox`, `nightshift_runs`, `book_chapters`, `learning_concepts`, `forge_learning`, `daily_stats`, `conflicts`, `topics`.

### 2.5 Data Flow Map

```
USER REQUEST
    │
    ▼
[FastAPI] ─── SessionMiddleware ─── RateLimiter ─── Auth check
    │
    ├─ Library Upload ─────────────────────────────────────────►
    │       │  multipart/form-data → SHA dedup → create_document
    │       │  → background thread: process_document()
    │       │      └─ extract text (PDF/DOCX/XLSX/OCR)
    │       │      └─ chunk (512-char FTS)
    │       │      └─ harvest knowledge (rules + LLM)
    │       │      └─ dedup (MinHash)
    │       │      └─ embed chunks (circuit-breaker-guarded)
    │       │      └─ record_provenance → object_provenance
    │       │
    ├─ Chat POST /messages ────────────────────────────────────►
    │       │  → _build_system_prompt (injects top-8 knowledge)
    │       │  → PKLOS verifier gate
    │       │  → LLM stream (httpx SSE)
    │       │  → PKLOS output validator
    │       │  → stream to client (SSE)
    │       │  → background: embed conv chunks, save memory
    │       │
    ├─ Studio TTS ─────────────────────────────────────────────►
    │       │  → Strategy 1: AI server /audio/speech
    │       │  → Strategy 2: Kokoro ONNX (local)
    │       │  → Strategy 3: espeak-ng (offline fallback)
    │       │  → _link_output_sync() [hard-link to lib_root]
    │       │  → _rotate_outputs() [keep newest 50]
    │       │  → FileResponse + background: register_and_index()
    │       │
    └─ Nightshift (03:00) ─────────────────────────────────────►
            │  → 14 sequential passes
            └─ writes: gap_cache, vectors, audit_log, nightshift_runs
```

### 2.6 Key Frameworks & Libraries

**Backend:** FastAPI, Uvicorn, SQLite (WAL mode), httpx, pypdf, python-docx, pytesseract, markitdown, kokoro-onnx, espeak-ng, ffmpeg, numpy, pydantic v2, slowapi (rate limiting)  
**Frontend:** React 18, Vite, TailwindCSS, Radix UI, TanStack Query v5, Wouter, Tiptap, Recharts  
**Mobile:** Expo SDK, Expo Router, Expo AV, Expo SecureStore, React Native  
**AI:** OpenAI-compatible API (configurable endpoint), local Kokoro ONNX, local espeak-ng  

---

## 3. Critical Bugs & Logical Defects

### HIGH Severity

#### BUG-001 — Blocking I/O on Async Route Handlers
**Files:** `src/orivellum/api/routes/studio.py:164` (synthesize_speech), `studio.py:~290` (espeak-ng subprocess), `studio.py:~870` (_persist_generated_image → _rotate_outputs)  
**Description:** `subprocess.run()` for ffmpeg and espeak-ng, `kokoro.create()` (heavy CPU), and `_rotate_outputs()` (disk I/O) are all called directly inside `async def` route handlers without `asyncio.run_in_executor()`. This blocks the FastAPI event loop for the duration of TTS synthesis (can be 5-30 seconds) and image persistence, making the entire server unresponsive to other requests during that window.  
**Impact:** Service-wide latency spike during any TTS or image generation request. Under concurrent use, requests queue behind a blocked event loop.  
**Fix:** Wrap all blocking calls in `await asyncio.get_event_loop().run_in_executor(None, blocking_fn)`.

---

#### BUG-002 — Unbounded Daemon Thread Spawning Per Request
**Files:** `src/orivellum/api/routes/studio.py` (all 6 output paths), `src/orivellum/capabilities/pipeline.py:143` (per-document thread), `src/orivellum/capabilities/pipeline.py:449` (embed thread)  
**Description:** Every document upload, TTS request, image generation, and audiobook creation spawns one or more `threading.Thread(daemon=True)` threads with no pool, no queue, no backpressure. Under 50 concurrent uploads, 50+ threads compete for the single SQLite connection (db._lock serializes them but doesn't limit count). Thread creation overhead accumulates; OS thread limits can be hit.  
**Impact:** Memory growth, OS thread exhaustion under sustained load, no way to cancel or inspect in-flight background work.  
**Fix:** Replace per-request threads with a `concurrent.futures.ThreadPoolExecutor(max_workers=4)` shared at app startup. Queue work items; let the pool drain them.

---

#### BUG-003 — LLM Stream Has No Absolute Timeout
**Files:** `src/orivellum/api/routes/conversations.py:~280` (stream generator), `src/orivellum/capabilities/intake.py` (web_search_synthesize)  
**Description:** The chat SSE stream uses `httpx.AsyncClient(timeout=cfg.serving.timeout_sec)` for connection but no hard wall-clock timeout on the stream iteration. If the LLM server stalls mid-stream (sends partial tokens then hangs), the generator blocks indefinitely. `asyncio.timeout()` or an outer `asyncio.wait_for()` is absent.  
**Impact:** Hung SSE connections accumulate; no client disconnection recovery for stalled LLM sessions; server leaks connection resources.  
**Fix:** Wrap the stream iteration with `asyncio.wait_for(stream.__anext__(), timeout=30)` per chunk, or use `httpx`'s per-read timeout.

---

#### BUG-004 — `governed_write` Double-Commit Risk
**File:** `src/orivellum/database/db.py:327`  
**Description:** The `governed_write` context manager calls `self._conn.commit()` after its `yield`. Any code inside the `with governed_write(...)` block that also calls `self._conn.commit()` will commit the partial transaction early, bypassing the audit log and outbox insertions that `governed_write` adds after the yield. The docstring warns against this but there is no enforcement.  
**Impact:** Silent audit log omissions; outbox events silently dropped, breaking the MONARCH intelligence pipeline's event-driven updates.  
**Fix:** Track whether a commit happened inside the block (via a flag on the connection) and raise if the caller committed early, or use a savepoint pattern instead of raw commits.

---

#### BUG-005 — ZIP Child Documents Not Registered in `object_provenance`
**Files:** `src/orivellum/capabilities/pipeline.py:100-148` (_explode_zip_into_documents)  
**Description:** ZIP explosion calls `db.create_document()` for each child and spawns `process_document()`. Neither `_explode_zip_into_documents` nor `process_document` calls `record_provenance()`. ZIP children are the only document creation path that bypasses the Save/Process/Recall invariant established in Task #336.  
**Impact:** ZIP-extracted documents are invisible to the recall index ("find everything I've imported"). Library audit is incomplete for archive uploads.  
**Fix:** Call `record_provenance(doc_id, "upload", db, origin_id=parent_doc_id)` inside `_explode_zip_into_documents` for each child, after `db.create_document()`.

---

### MEDIUM Severity

#### BUG-006 — Chat Context Injection Can Exceed Model Context Window
**File:** `src/orivellum/api/routes/conversations.py` (_build_system_prompt)  
**Description:** The system prompt injects up to 8 knowledge items + work title + conversation history. There is no token counting; for long knowledge items or long conversation histories, the total can exceed the model's context window. The LLM API returns a 400/context-exceeded error which propagates as an unhandled exception.  
**Impact:** Silent failure: chat silently errors for users with large Works or long conversations.  
**Fix:** Add token estimation (4 chars ≈ 1 token heuristic) and truncate injected knowledge to stay under 80% of the configured context window.

---

#### BUG-007 — Nightshift VACUUM Runs Under db._lock Without Timeout
**File:** `src/orivellum/capabilities/nightshift.py` (_pass_vacuum)  
**Description:** VACUUM acquires an exclusive SQLite lock for its entire duration (can be minutes on large DBs). It runs inside `with db._lock:`, which blocks all other DB operations during nightshift execution. If the API server is handling a request concurrently, that request blocks at db._lock acquisition until VACUUM completes.  
**Impact:** Potential 30-60 second API freezes at 03:00 if the database is large.  
**Fix:** Run VACUUM in a separate connection on a copy (`VACUUM INTO 'db_compacted.sqlite'`) or skip VACUUM when the DB is actively serving requests (check active connection count).

---

#### BUG-008 — Missing `await asyncio.shield()` Around SSE Client Disconnect
**File:** `src/orivellum/api/routes/conversations.py` (StreamingResponse generator)  
**Description:** When a client disconnects mid-stream (closes browser tab), the FastAPI streaming generator raises `GeneratorExit`. The finally block in the streaming generator attempts to finalize the incomplete message in the database, but `GeneratorExit` propagates through async generators differently than regular exceptions, and the finally block may not run reliably in all cases.  
**Impact:** Incomplete messages sometimes not marked as `incomplete=True`; message state corruption in edge cases.

---

#### BUG-009 — `process_document` Ignores `work_id` for Provenance and Embeddings
**File:** `src/orivellum/capabilities/pipeline.py:~380-470`  
**Description:** `process_document()` accepts `work_id` as a parameter and passes it to `harvest()`, but the `embed_chunks_for_doc()` call and the audit entry do not use `work_id`. The embeddings backfill in nightshift doesn't filter by work, so semantic search results don't benefit from work-scoped embedding strategies.

---

#### BUG-010 — Stale `react-query` Default `staleTime=0` Causes Over-fetching
**File:** `artifacts/orivellum-ui/src/` (all useQuery hooks without explicit staleTime)  
**Description:** TanStack Query v5 defaults to `staleTime: 0`, meaning every component mount or window focus refetches all data. Library lists, work stats, and knowledge items are refetched on every navigation, adding unnecessary API load and causing visible loading flickers.  
**Fix:** Set a global default `staleTime: 30_000` in the QueryClient config.

---

### LOW Severity

#### BUG-011 — `_chunk_text` Doesn't Handle Unicode Surrogates or Very Long Words
**File:** `src/orivellum/capabilities/persist.py:124` (_chunk_text)  
A word longer than `_CHUNK_SIZE` (512 chars) is not split and becomes a solo oversized chunk. OCR output and URLs can exceed this.

#### BUG-012 — Nightshift Pass 14 (`_pass_version_suggestions`) Compares Stems Without Normalizing Case
**File:** `src/orivellum/capabilities/nightshift.py` (_pass_version_suggestions)  
Filename stem comparison is case-sensitive on Linux; `Chapter_1.docx` and `chapter_1.docx` won't be detected as likely revisions.

#### BUG-013 — `register_text_note` Uses `hashlib.sha256` but Notes Directory Not Cleaned Up
**File:** `src/orivellum/capabilities/persist.py` (register_text_note)  
Research notes accumulate in `lib_root/generated/notes/` without any rotation or cleanup. Over time with many research runs, this directory can grow unbounded.

---

## 4. Security Findings

### CRITICAL

#### SEC-001 — No Authentication on High-Impact API Endpoints When `api_key` Is Empty
**File:** `src/orivellum/api/app.py` (auth middleware)  
**Description:** When `cfg.server.api_key` is empty (the default), the auth middleware is effectively bypassed for non-session routes. The mobile bearer-token auth path checks the token only when one is configured. This means a fresh install with default config exposes all endpoints (including TTS, image gen, LLM chat, file upload) unauthenticated on whatever port the server binds.  
**Severity:** CRITICAL for internet-exposed installs; LOW for LAN-only use behind a router.  
**Fix:** Require `api_key` to be set at startup, or default to a randomly-generated key displayed once at first launch. Add a warning log if the server binds to `0.0.0.0` with no key.

---

### HIGH

#### SEC-002 — SSRF via User-Configurable Image Generation URL
**File:** `src/orivellum/api/routes/studio.py:~693` (_try_openai_compat)  
**Description:** The image generation route reads `image_gen_url` from the database (user-configurable via settings) and makes an HTTP request to it. An attacker with access to the settings endpoint can point this URL to internal services (`http://169.254.169.254/latest/meta-data/` on cloud, `http://localhost:11434/` for other local services).  
**Fix:** Validate that the URL is not a private/loopback address before making the request. Block RFC1918 and loopback ranges.

#### SEC-003 — File Upload Has No MIME Type Validation
**File:** `src/orivellum/api/routes/library.py` (upload handler)  
**Description:** File uploads are accepted based on the file extension and content-type header, both of which are user-controlled. A malicious actor can upload an executable disguised as a `.txt` and have it land in the library data directory. While it won't be executed by the server, it is a storage abuse and potential pivot point.  
**Fix:** Add magic-byte validation (e.g., `python-magic`) on the first 512 bytes of the upload.

---

### MEDIUM

#### SEC-004 — Prompt Injection Not Structurally Mitigated
**File:** `src/orivellum/api/routes/conversations.py` (_build_system_prompt, _build_messages)  
**Description:** User messages are concatenated into the LLM prompt without a structural separator that prevents the model from treating user content as system instructions. A user can send `"Ignore previous instructions and..."` patterns. The PKLOS output validator catches factual violations but not behavioral injection.  
**Fix:** Use a role-based prompt structure where user content is always in the `user` role and system instructions in the `system` role with a clear delimiter. Most modern models respect this boundary.

#### SEC-005 — No Content-Security-Policy Headers
**File:** `src/orivellum/api/app.py`  
**Description:** The API server adds CORS headers but no `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, or `Strict-Transport-Security` headers. The frontend (Vite) also does not configure CSP in its dev or build config.  
**Fix:** Add `secure-headers` middleware to FastAPI. For the Vite frontend, add CSP via `vite-plugin-csp` or server response headers.

#### SEC-006 — Session Secret Has No Entropy Enforcement
**File:** `src/orivellum/api/app.py` (SessionMiddleware), `src/orivellum/configuration/config.py`  
**Description:** `SESSION_SECRET` is read from env. If the secret is not set or is set to a weak value (e.g., `"secret"` in dev), all session cookies are cryptographically weak. There is no minimum-length check.  
**Fix:** Validate `len(SESSION_SECRET) >= 32` at startup and refuse to start with a weak or missing secret.

---

### LOW

#### SEC-007 — CORS Regex May Be Too Permissive
**File:** `src/orivellum/api/app.py`  
The CORS origin regex includes Tailscale IP ranges and `*.replit.dev`. In a home deployment, this could allow any subdomain of `replit.dev` to make credentialed requests. Consider restricting to exact known origins.

#### SEC-008 — Mobile Bearer Token Stored in Expo SecureStore (Acceptable)
SecureStore is the correct storage location on mobile. No issue here, but token rotation (logout → new token) should be tested.

---

## 5. Schema, Configuration & State Findings

#### CFG-001 — Hardcoded Model Names in Default Config
**File:** `src/orivellum/configuration/config.py` (ServingConfig defaults)  
`workhorse_model = "Qwen3-30B"`, `reasoner_model = "gpt-oss-120b"` are hardcoded defaults. These model names are deployment-specific and will silently fail if the user's LLM server doesn't serve them.  
**Fix:** Default to empty strings and display a clear startup warning if model names are not configured, instead of failing silently mid-request.

#### CFG-002 — Missing Indexes on Several Foreign Keys
**File:** `src/orivellum/database/schema.py`  
Tables `daily_stats`, `graph_layouts`, `memories`, `forge_learning` reference `work_id` but have no index on it. At scale (1000+ works), queries joining on these tables will do full scans.

#### CFG-003 — Single SQLite Connection With Reentrant Lock
**File:** `src/orivellum/database/db.py`  
The entire system uses one `sqlite3.connect` instance protected by `threading.RLock`. This correctly serializes writes but means all concurrent read queries also queue behind each other. SQLite WAL mode supports concurrent readers on separate connections; the current design abandons that benefit.  
**Recommendation:** Use a connection pool (e.g., `sqlite3.connect` per thread via threading.local()) for reads, with write operations on the shared locked connection.

#### CFG-004 — Mobile Hardcoded `localhost:8000` Fallback
**Files:** `artifacts/mobile/app/` (multiple files)  
`process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000'` will silently use port 8000 in development if the env var is missing, while the server runs on `$PORT` (varies). On production builds without the env var set, the app will be broken.

#### CFG-005 — OpenAPI Spec Partially Out of Sync
**File:** `lib/api-spec/openapi.yaml`  
Several routes added in recent tasks (Actions, MCOS, Memory recall, Book pipeline routes) are not reflected in the OpenAPI spec. This means generated client hooks from Orval may be missing or stale for these endpoints.

---

## 6. Feature Completeness Gap Analysis

### What Exists and Works Well ✅
- **Document ingestion pipeline** (PDF, DOCX, XLSX, ZIP, OCR) — multi-strategy, resilient
- **Knowledge extraction** — rule-based + LLM harvest, review queue, confidence scoring
- **Semantic search** — embeddings with circuit breaker, FTS fallback, hybrid approach
- **Chat with knowledge injection** — context-aware, work-scoped, streaming
- **TTS** — 3-strategy (AI server → Kokoro ONNX → espeak-ng), durable output registration
- **Book/Works pipeline** — 17-stage state machine, chapter extraction, gap detection
- **Learning loop** — quiz, evidence scoring, concept mastery tracking
- **Governance layer** — PKLOS, MCOS, audit log, review queue
- **Nightly maintenance** — 14-pass daemon, embedding backfill, orphan cleanup
- **Provenance tracking** — `object_provenance` table, hard-linked durable library copies
- **Multi-modal** — Images, TTS, audiobook, DOCX/XLSX generation, OCR

### What Is Missing or Incomplete ❌

| Gap | Severity | Description |
|-----|----------|-------------|
| **Hardware/GPU Telemetry** | HIGH | No VRAM usage, GPU temperature, CPU utilization, or RAM pressure monitoring. No dashboard widget for server health. Users can't tell if a long generation is due to OOM or a hanging model. |
| **Persistent Background Job Queue** | HIGH | All background work uses fire-and-forget daemon threads. There is no way to see pending jobs, cancel a stuck job, or resume a crashed pipeline. A server restart loses all in-flight work silently. |
| **Model Management UI** | HIGH | No interface to list loaded models, switch active models, see context window usage, or check model health. Model names are configured once in settings and never validated. |
| **Process/OOM Recovery** | HIGH | If the LLM server OOMs and crashes, Orivellum has no automatic detection or fallback. Requests fail silently with a connection error logged to console only. |
| **Real-time Progress Tracking** | MEDIUM | File processing progress exists for uploads (polling) but no unified job dashboard showing all active/queued/failed background operations. |
| **Structured Request Logging** | MEDIUM | No access log (request method, path, response code, latency) stored persistently. Debugging production issues requires reading stdout logs. The audit_log table tracks knowledge governance but not API access patterns. |
| **Data Export / Backup** | MEDIUM | No full-database export (works + knowledge + library). The user has no way to create a portable backup without directly copying the SQLite file. |
| **Multi-user Support** | LOW | Entire system is single-user. No user table, no per-user isolation. Not a current goal but worth noting. |
| **Plugin / Extension System** | LOW | The action registry (`capabilities/actions/`) is a good start but actions are hardcoded Python modules, not loadable plugins. |
| **Embedding Model Management** | MEDIUM | The embeddings endpoint URL is configured once. No UI to test, switch, or benchmark embedding models. Circuit breaker state is visible but not surfaceable in a user-friendly way. |
| **Mobile Offline Mode** | MEDIUM | Mobile app has no offline fallback. All screens fail with connection errors when the server is unreachable. No cached data layer. |
| **Search Result Ranking Explanation** | LOW | Semantic search results show no relevance score or explanation, making it hard to understand why a result appeared. |

### Completeness Rating Justification: **6.5 / 10**

- Core ingestion → knowledge → chat loop: **8/10** (very solid)
- Background maintenance / governance: **8/10** (sophisticated for home project)
- Operational visibility (telemetry, job dashboard, logs): **2/10** (major gap)
- Security posture: **5/10** (adequate for LAN, insufficient for internet exposure)
- Model management / resilience: **3/10** (fragile, no recovery)
- Data portability / backup: **3/10** (no user-facing export)

---

## 7. Refactoring & Optimization Recommendations

### R-001 — Introduce a ThreadPoolExecutor for Background Work
**Priority:** HIGH  
Replace all `threading.Thread(daemon=True).start()` patterns with a shared `concurrent.futures.ThreadPoolExecutor(max_workers=4)` initialized in the FastAPI lifespan. Submit work via `executor.submit(fn, *args)`. Track futures for progress visibility. This caps thread count, enables task cancellation, and allows a job dashboard.

### R-002 — Async-ify Studio Route Blocking Calls
**Priority:** HIGH  
All `subprocess.run()` calls (ffmpeg, espeak-ng) and `kokoro.create()` should become:
```python
loop = asyncio.get_event_loop()
await loop.run_in_executor(None, subprocess.run, ...)
```
Or use `asyncio.create_subprocess_exec()` for ffmpeg/espeak-ng.

### R-003 — Global `staleTime` for React Query
**Priority:** MEDIUM  
Set `defaultOptions: { queries: { staleTime: 30_000, gcTime: 300_000 } }` in the QueryClient. This reduces API calls from O(navigations) to O(time).

### R-004 — Token Counting in Chat Context Builder
**Priority:** MEDIUM  
Implement a lightweight token estimator (`len(text) // 4`) and truncate the knowledge injection to stay within `context_window * 0.7`. Expose `context_window` as a `ServingConfig` field.

### R-005 — Connection Pool for Read Queries
**Priority:** MEDIUM  
Use `threading.local()` to maintain per-thread read connections (SQLite WAL allows multiple readers). Reserve the shared locked connection for writes only. This removes the read-write contention that causes latency spikes under concurrent access.

### R-006 — Replace `localhost:8000` Mobile Fallback
**Priority:** MEDIUM  
Remove all hardcoded fallbacks. Instead, throw a clear startup error if `EXPO_PUBLIC_DOMAIN` is missing in production builds.

### R-007 — VACUUM Strategy
**Priority:** LOW  
Replace the blocking VACUUM with `PRAGMA wal_checkpoint(TRUNCATE)` for routine maintenance. Schedule full VACUUM only when `freelist_count / page_count > 0.3` and no API requests are in flight (check an atomic counter).

---

## 8. Prioritized Implementation Roadmap to Version 1.0

### 🔴 Phase 1 — Stability & Safety (Do First, Blocks Everything Else)

| # | Task | Files Affected | Effort |
|---|------|---------------|--------|
| 1.1 | **Async-ify all blocking Studio calls** (BUG-001) | `studio.py` | 2h |
| 1.2 | **Shared ThreadPoolExecutor for background work** (BUG-002) | `app.py`, `pipeline.py`, `studio.py`, `persist.py` | 4h |
| 1.3 | **LLM stream absolute timeout** (BUG-003) | `conversations.py` | 1h |
| 1.4 | **Require SESSION_SECRET ≥ 32 chars at startup** (SEC-006) | `app.py` | 30m |
| 1.5 | **Default api_key enforcement** (SEC-001) | `app.py`, `config.py` | 1h |
| 1.6 | **ZIP children → object_provenance** (BUG-005) | `pipeline.py` | 1h |

### 🟠 Phase 2 — Operational Visibility (Makes the System Debuggable)

| # | Task | Files Affected | Effort |
|---|------|---------------|--------|
| 2.1 | **Job dashboard** — list active/queued/failed background jobs | New: `jobs.py` route, `jobs` table | 6h |
| 2.2 | **Structured access log** — log every request to `audit_log` | `app.py` middleware | 2h |
| 2.3 | **Hardware telemetry endpoint** — CPU %, RAM %, disk %, GPU VRAM (if available) | New: `system.py` additions | 3h |
| 2.4 | **System health dashboard widget** — surface telemetry in web UI | `pages/system/index.tsx` | 2h |
| 2.5 | **Global staleTime fix** (BUG-010) | `orivellum-ui/src/main.tsx` | 15m |
| 2.6 | **Token counting in chat context** (BUG-006) | `conversations.py` | 2h |

### 🟡 Phase 3 — Resilience & Model Management

| # | Task | Files Affected | Effort |
|---|------|---------------|--------|
| 3.1 | **LLM health check + auto-retry** — detect OOM/crash, retry with fallback model | `conversations.py`, `config.py` | 4h |
| 3.2 | **Model management UI** — list models, switch active, show context window | `pages/system/`, `system.py` | 5h |
| 3.3 | **SSRF URL validation** (SEC-002) | `studio.py` | 1h |
| 3.4 | **MIME type validation on upload** (SEC-003) | `library.py` | 1h |
| 3.5 | **Security headers middleware** (SEC-005) | `app.py` | 30m |
| 3.6 | **WAL checkpoint instead of blocking VACUUM** (BUG-007) | `nightshift.py` | 1h |

### 🟢 Phase 4 — Polish & Portability

| # | Task | Files Affected | Effort |
|---|------|---------------|--------|
| 4.1 | **Full data export** — ZIP of SQLite + library files + config | New: `export.py` route | 4h |
| 4.2 | **OpenAPI spec sync** — regenerate spec and client hooks | `lib/api-spec/openapi.yaml`, Orval regeneration | 3h |
| 4.3 | **Mobile offline cache** — cache last-known data in Expo SQLite | `artifacts/mobile/` | 6h |
| 4.4 | **Fix missing indexes** (CFG-002) | `schema.py` (new migration) | 1h |
| 4.5 | **Mobile env fallback removal** (CFG-004) | `artifacts/mobile/app/` | 1h |
| 4.6 | **Model names from env/config only** (CFG-001) | `config.py` | 1h |

---

## Appendix A: What Is Genuinely Well-Built

This codebase has several components that are unusually well-designed for a home AI server project:

- **Circuit-breaker pattern on embeddings** (`embeddings.py`) — with state tracking, backoff, and an API to probe/reset. Most home projects just let embeddings fail silently.
- **PKLOS provenance layer** — formal knowledge authority/verifier/resolver/enforcer architecture is more rigorous than anything in open-source home AI tools.
- **MinHash deduplication** — real approximate nearest-neighbor dedup on document content, not just filename matching.
- **3-strategy TTS with graceful fallback** — AI server → Kokoro ONNX → espeak-ng, with the same API surface throughout.
- **17-stage book pipeline state machine** (`state_machine.py`) — declarative transitions, gate conditions, blocker tracking. Production-grade design.
- **Nightshift 14-pass maintenance daemon** — rivals commercial document management system maintenance routines.
- **Hard-link durable output registration** (`persist.py`) — the link-before-rotate invariant (Task #336) is a correctness property most systems get wrong.
- **Evidence scoring loop** (`learning.py`) — spaced-repetition style confidence scoring tied to actual knowledge validation events.
- **Governance review queue** (`review.py`) — unified inbox for AI suggestions, version conflicts, duplicate detections, and reclassification requests.

---

*Report generated from forensic static analysis of all source files. No application code was modified during this audit.*
