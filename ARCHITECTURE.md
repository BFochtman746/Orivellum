# Orivellum Architecture

Orivellum is a single-user, local-first AI knowledge system. Everything — data
storage, document processing, search, and voice — runs on one machine, and the
default LLM inference target is a local OpenAI-compatible server (Lemonade on
the owner's AMD Strix Halo PC). Nothing is required to leave the box.

The repository is a pnpm monorepo:

- **Backend** — Python 3.12+, FastAPI, SQLite, under `src/orivellum/`.
- **Frontend** — a React + Vite PWA under `artifacts/orivellum-ui/`.

The API auto-generates its OpenAPI schema (served at `/openapi.json`), so the
contract is always derived from the running code.

---

## Components

### FastAPI API server (`src/orivellum/api/`)

`app.py` builds the FastAPI application via `create_app()` and drives startup
and shutdown through a lifespan handler. `main.py` is the runnable entry point
(`python -m orivellum.api.main`), reading `PORT` (default `8080`).

Route modules live in `src/orivellum/api/routes/` — one module per surface
(`auth`, `works`, `conversations`, `library`, `knowledge`, `studio`, `system`,
`mail`, `forge`, `genesis`, `mcos`, `pklos`, and more). `create_app()` collects
these modules into a list and calls `app.include_router(module.router)` for
each. Every router is also mounted a second time under the `/orivellum-ui`
prefix (`include_in_schema=False`) so the installed PWA, which prefixes API
calls with its base path, resolves to the same handlers.

**Auth** is single-user session-cookie based (`routes/auth.py`). The user
enters an API key once at `POST /api/auth/login`; the server sets an HttpOnly
signed session cookie via Starlette's `SessionMiddleware`. Clients that cannot
use cookies (mobile/API) may instead send a bearer token. The signing secret is
resolved at `create_app()` time.

### SQLite database layer (`src/orivellum/database/`)

`db.py` wraps the SQLite connection and exposes the data-access API. Writes that
must be audited go through `governed_write(...)`, an audit/governance layer that
records the change alongside the mutation (an audit chain is verified nightly).

`schema.py` holds the migration ladder: an ordered list of
`(version, description, sql)` entries applied in sequence, with the current
version stored in the `settings` table (`key=schema_version`). Migrations run
once on server start and are never re-applied. The ladder is well past version
100 (current entries reach v111), so any new migration appends a higher-numbered
tuple rather than editing existing ones.

### Shared thread-pool executor (`src/orivellum/api/executor.py`)

A single bounded `ThreadPoolExecutor` (default 8 workers, tunable via
`ORIVELLUM_WORKERS`) backs all fire-and-forget background work — document
processing, embeddings, TTS/image registration. It is initialized by the
FastAPI lifespan and shut down on exit. It also keeps a bounded in-memory job
registry (recent jobs, capped) that the dashboard reads, with retry support so
a failed job can be re-submitted. This replaces the previous pattern of
spawning unbounded daemon threads, which could exhaust OS thread limits.

### Nightshift maintenance daemon (`src/orivellum/capabilities/nightshift.py`)

A background daemon runs a battery of nightly maintenance passes (14+ discrete
`_pass_*` functions). Passes include database optimise/VACUUM, type-aware vector
orphan cleanup, recovery of stuck/no-text documents, sparse-harvest and gap
analysis, evidence and embeddings backfill, work-stats refresh, MCOS
aggregation, audit-chain verification, version-suggestion cross-checks,
clustering, semantic dedup, cold-item detection, and memory dedup/promotion.
Passes take the `OrivellumDB` and/or config and append human-readable lines to a
shared report.

### Document ingestion pipeline (`src/orivellum/capabilities/`)

`pipeline.py` orchestrates a document from upload to searchable knowledge:
**extract → chunk → harvest → update readiness**. `process_document(doc_id,
file_path, kind, ...)` resolves the file, calls `extraction.extract(...)`,
stores extractor metadata/warnings, then runs rule-based `harvest` and
(gated) `llm_harvest` from `knowledge_harvest.py`. ZIP archives are exploded
into child documents, each processed and de-duplicated in turn.

`extraction.py` is the format dispatcher. An extension→kind map routes each file
to a specialized extractor. PDFs use a layered fallback: pdfplumber (native
text) → pypdf → VLM OCR (a configured vision model transcribes scanned/image
pages) → markitdown; raster images and scanned pages also go through Tesseract
OCR (`pytesseract`). Audio is transcribed via the AI server's Whisper endpoint
when available, otherwise locally with faster-whisper. YouTube transcripts are
fetched via `youtube-transcript-api` (see `websearch.py`).

### Hybrid search

Full-text search is SQLite **FTS5** (declared in `schema.py`, queried with
`MATCH` in `db.py`). On top of that, an optional embeddings layer
(`embeddings.py`) provides semantic ranking. Embeddings sit behind a **circuit
breaker**: when the embedding endpoint is unconfigured or unreachable, the
breaker opens so search stays fast and keyword-only instead of blocking on a
dead model. The breaker state is surfaced by the System page.

### TTS / voice

Neural text-to-speech uses **Kokoro** via ONNX (`kokoro_onnx.Kokoro` loaded
lazily in `routes/studio.py` from `kokoro-v0_19.onnx` + `voices.bin`, both
fetched by the one-time TTS model script). An optional **premium sidecar**
(configured via `serving.tts_premium_url`, gated behind a license
acknowledgement flag) can be tried first for studio-quality output. By owner
policy there is **no espeak fallback**: audible paths never emit robotic
synthesis — they use Kokoro (or the premium engine) or fail closed with a 503.

### React PWA (`artifacts/orivellum-ui/`)

A Vite-built React single-page app installable as a PWA. In development it runs
the Vite dev server and proxies `/api/*` to the backend; in production `start.ps1`
builds the bundle and FastAPI serves it under `/orivellum-ui/` from the same
process. The frontend consumes the auto-generated OpenAPI schema.

---

## Why SQLite

Orivellum is deliberately single-user and local-first, so SQLite is the right
fit: an embedded, zero-operations database with no server to run, no network
surface, and a single portable file to back up. It keeps the whole system
installable on one PC with nothing to administer.

**Known ceiling.** SQLite is single-writer (a write lock serializes writers)
and lives on one machine. That is acceptable for a single human operator, but it
means Orivellum does not scale to concurrent multi-user writes or a distributed
deployment without replacing this layer. Background writers are funneled through
the shared executor and the `governed_write` path precisely to keep write
contention predictable.

---

## Extension points

### Adding a new document type

1. Write an extractor in `src/orivellum/capabilities/extraction.py` that returns
   an `ExtractionResult`.
2. Register the file extension in the extension→kind map (and the ZIP member map
   if the type should also be extracted from inside archives).
3. Route the new `kind` through the dispatcher so `extract(path, kind)` reaches
   your function. From there, `pipeline.process_document` chunking, harvesting,
   and readiness updates apply automatically — no pipeline changes needed for a
   plain text-bearing format.

### Adding a new capability

1. Create a module under `src/orivellum/capabilities/` (or a package directory
   for a larger feature, e.g. `forge/`, `genesis/`, `mail/`). Keep all LLM calls
   going through the central gateway (`capabilities/llm.py`).
2. Persist any new tables by appending a migration tuple in
   `database/schema.py` with the next version number, and use `governed_write`
   for auditable mutations.
3. Expose it over HTTP by adding a route module in
   `src/orivellum/api/routes/` and registering it in the `_route_modules` list
   in `api/app.py::create_app()`. It is then reachable both directly and under
   `/orivellum-ui`, and appears in `/openapi.json` automatically.
4. For long-running work, submit to the shared executor
   (`from orivellum.api.executor import get_executor`) rather than spawning
   threads, and add a `_pass_*` to `nightshift.py` if it needs periodic
   maintenance.
