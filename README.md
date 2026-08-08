# Orivellum

A sovereign, local-first knowledge management and AI assistant platform. All data and AI inference stay on your machine.

---

## Quick Start (self-hosted)

### 0. System prerequisites

Install these once with your OS package manager. They are needed for document processing, OCR, and audio; all Python dependencies are handled by `uv sync`.

**macOS (Homebrew)**
```bash
brew install tesseract poppler ffmpeg espeak-ng
```

**Ubuntu / Debian**
```bash
sudo apt-get install -y tesseract-ocr poppler-utils ffmpeg espeak-ng
```

**Windows**

Run the automated setup script — it checks for and installs every prerequisite
(Tesseract, Poppler, FFmpeg, espeak-ng, uv, pnpm):

```powershell
# One-time setup (run once; skip on subsequent starts)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # allow local scripts
.\scripts\setup-windows.ps1
```

If you prefer manual installation:
- Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
- Poppler: https://github.com/oschwartz10612/poppler-windows/releases (add `Library\bin\` to PATH)
- FFmpeg: https://ffmpeg.org/download.html (add `bin\` to PATH)
- espeak-ng: https://github.com/espeak-ng/espeak-ng/releases

> **Runtime requirement:** Python ≥ 3.12, Node.js ≥ 20, pnpm ≥ 9, uv ≥ 0.4

### 1. Install dependencies

**macOS / Linux**
```bash
uv sync && pnpm install
# or: make install
```

**Windows (PowerShell)**
```powershell
# setup-windows.ps1 already runs this, but you can re-run it any time:
uv sync; pnpm install
```

### 2. Start everything

**macOS / Linux**
```bash
./start.sh
# or: make dev
```

**Windows (PowerShell)**
```powershell
.\scripts\start.ps1
```

That's it. The script starts the API server, waits until it passes its health check, then launches the Vite frontend. Press **Ctrl+C** to stop both processes cleanly.

Open **http://localhost:5173** once you see the `Ready ✓` line.

#### With Expo mobile

**macOS / Linux**
```bash
./start.sh --mobile
# or: make dev-mobile
```

**Windows**
```powershell
.\scripts\start.ps1 -Mobile
```

#### Port overrides

**macOS / Linux**
```bash
API_PORT=9000 WEB_PORT=4000 ./start.sh
```

**Windows**
```powershell
.\scripts\start.ps1 -ApiPort 9000 -WebPort 4000
```

---

### Manual startup (advanced)

If you prefer to run each process separately:

**API server** (reads `PORT`, default `8080`)
```bash
uv run python -m orivellum.api.main
```

**Frontend** (set `ORIVELLUM_API_URL` to forward `/api/*` to the running server)
```bash
PORT=5173 BASE_PATH=/ ORIVELLUM_API_URL=http://127.0.0.1:8080 \
  pnpm --filter @workspace/orivellum-ui run dev
```

---

## Local AI Setup

Orivellum connects to a local AI server via an OpenAI-compatible `/chat/completions` API. No model data or conversation content is sent to external services.

### Option A — Lemonade (recommended)

[Lemonade](https://github.com/amd/lemonade) is a local model server tuned for Orivellum. It defaults to port **13305**.

```bash
# Install (one-time)
pip install lemonade-server

# Start
lemonade-server --port 13305
```

Orivellum's default `ORIVELLUM_AI_URL` points to `http://127.0.0.1:13305/api/v1`, so no extra configuration is needed when using the default port.

### Option B — Ollama

[Ollama](https://ollama.com) runs many open-source models locally and exposes an OpenAI-compatible API on port **11434**.

```bash
# Install from https://ollama.com/download, then:
ollama serve

# Pull a model (one-time)
ollama pull llama3.2          # 3 B, fast
ollama pull mistral           # 7 B, balanced
ollama pull qwen2.5:14b       # 14 B, high quality

# Point Orivellum at Ollama's API (add to your shell profile or .env)
export ORIVELLUM_AI_URL=http://127.0.0.1:11434/v1
```

Then set the model name in `config.yaml`:

```yaml
serving:
  workhorse_model: llama3.2   # must match the pulled model name
```

### Option C — Any OpenAI-compatible server

Any server that implements `POST /chat/completions` (LM Studio, vLLM, text-generation-inference, etc.) works:

```bash
export ORIVELLUM_AI_URL=http://127.0.0.1:<PORT>/v1
```

---

## Configuration

Configuration is resolved from three sources in priority order:

| Priority | Source |
|----------|--------|
| Highest | Environment variables (`ORIVELLUM_*`) |
| Middle | `config.yaml` in the project root |
| Lowest | Built-in defaults |

### Key environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | API server listening port |
| `ORIVELLUM_AI_URL` | `http://127.0.0.1:13305/api/v1` | Base URL of the local AI server |
| `ORIVELLUM_DATA_DIR` | `./data` | Directory for the SQLite database and uploaded files |
| `ORIVELLUM_DB_PATH` | `$DATA_DIR/orivellum.db` | Override DB path directly |
| `ORIVELLUM_HOST` | `0.0.0.0` | API server bind address |
| `ORIVELLUM_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ORIVELLUM_API_KEY` | *(none)* | Optional bearer token to protect the API |
| `TAVILY_API_KEY` | *(none)* | Enables web search capability (Tavily provider) |

### `config.yaml` example

```yaml
serving:
  base_url: http://127.0.0.1:13305/api/v1
  workhorse_model: Qwen3-30B-A3B-Instruct-2507
  timeout_sec: 120

server:
  host: 0.0.0.0
```

---

## Features

### Chat & Streaming

The Chat page streams AI replies in real time using Server-Sent Events (SSE). Conversations can be linked to a Work — when linked, the system automatically injects the Work's title and top knowledge items into the prompt context so the AI stays grounded in your material.

**On clean completion**: both the user message and the full assistant reply are persisted.

**On mid-stream failure**: the user message is already saved. The conversation will show it on reload; re-sending it will get a fresh reply. When the AI server is unreachable, a clear error is shown in place of a reply — input is never silently dropped.

### Document Library

Import PDFs, DOCX, XLSX, images, audio, and ZIP archives. Each document goes through a processing pipeline:

1. **Text extraction** — 3-tier PDF fallback (native text → OCR → image analysis); DOCX tables; XLSX capped at 5000 rows.
2. **Rule-based harvest** — facts, entities, and structure extracted deterministically.
3. **LLM harvest** — AI extracts deeper knowledge items (gated by the `ai_extraction_enabled` setting). LLM items land in a review queue with approve/reject controls.
4. **Near-duplicate detection** — MinHash similarity flags potential duplicates for human resolution.

Documents can be linked to Works, which scopes all their extracted knowledge to that Work.

### Works

A Work is a curated collection of documents with shared knowledge, tasks, conversations, and learning material. Works are the primary unit of organisation.

The Work detail page includes:
- **Documents tab** — linked docs, with lifecycle (draft / canonical / archived) management
- **Knowledge tab** — extracted facts with approve/reject review; AI items get a Sparkles badge
- **Tasks tab** — per-Work task queue
- **Conversations tab** — Work-linked chats
- **Gaps tab** — detected knowledge gaps, live-polled while a pipeline is active
- **Book tab** — GENESIS origination pipeline stepper (PLAN → DESIGN → BUILD → VERIFY)
- **Intelligence tab** — MONARCH chapter analysis, completeness scores, dedup, graph

### Book Intelligence (MONARCH)

Navigate to `/works/:id/intelligence` for deep structural analysis of a Work:

- Chapter-level knowledge extraction with scene counts
- Completeness scoring per chapter
- Gap detection across seven categories
- Near-duplicate flagging within the Work
- Knowledge graph topology

### Learning Loop

The `/learn` page drives spaced-repetition study from any Work's knowledge base. Orivellum generates Socratic questions, accepts free-text answers, grades them, and tracks mastery per concept. Session limits and cooldowns prevent over-drilling.

### Forge Website Factory

Navigate to `/forge` to generate complete static websites from a plain-language brief.

1. **Create a project** — optionally link it to a Work (the Work's knowledge graph is injected into the planning prompt).
2. **Review the plan** — the AI generates a structured site plan; approve or reject it.
3. **Pick a visual direction** — three design concepts with palettes and typography are generated; choose one.
4. **Build** — a tool-calling agent writes all HTML/CSS/JS files, runs quality gates (structure, tokens, HTML validity, JS syntax, links, static-only scope), and auto-repairs on failures.
5. **Preview** — an inline iframe shows the finished site; all files are available for download.

All LLM calls go through the MCOS gateway and appear in governance logs.

### Writing Desk

The `/write` page is an AI-assisted document workshop. Create structured documents, iterate with AI assistance, and export as plain text.

### Web Search

If `TAVILY_API_KEY` is set, Orivellum can search the web during chat. The search pipeline uses multi-query Reciprocal Rank Fusion (RRF) and BM25 passage ranking to surface relevant results.

### Nightshift

A background daemon runs 14 maintenance passes nightly:

- Vector orphan cleanup (type-aware)
- Database VACUUM
- Sequential document recovery (retries stuck imports)
- Version suggestion cross-checking (flags possible duplicate documents)
- Gap cache refresh
- And more

### System Diagnostics

Run a full health check from the command line:

```bash
uv run python scripts/run_diagnostics.py
# With database defragmentation:
uv run python scripts/run_diagnostics.py --vacuum
```

Or navigate to **System** in the sidebar to see:

- Live AI endpoint reachability and configured URL
- Database connection status and schema version
- Embeddings service status and circuit-breaker state
- Audio enhancement (DeepFilterNet3) status
- Step-by-step setup instructions (shown automatically when AI is offline)

---

## Governance & Calibration

### MCOS (Model Calibration & Observation System)

Navigate to `/mcos` to run LLM benchmark suites, track regression over time, and view per-prompt telemetry. Every model call in Orivellum is logged to the `llm_calls` table; MCOS aggregates this into pass/fail trends.

### Review Inbox

Navigate to `/review` for a unified inbox of items requiring human attention:
- AI-harvested knowledge awaiting approve/reject
- Near-duplicate documents awaiting resolution
- Reclassification suggestions

### PKLOS (Provenance & Knowledge Lifecycle Operating System)

Tracks the provenance of every knowledge claim: whether it was user-asserted, retrieved from a document, or derived by inference. Accessible via `/governance`.

---

## Development

```bash
# Run API server with auto-reload
uv run uvicorn orivellum.api.app:app --reload --port 8080

# Regenerate the frontend API client after OpenAPI changes
pnpm --filter @workspace/api-spec run sync-client

# Full typecheck
pnpm run typecheck

# System diagnostics
uv run python scripts/run_diagnostics.py
```

### After schema changes

The database schema auto-migrates on every server start. If you add a new migration in `src/orivellum/database/schema.py`, bump the target version constant and add a `_run_vN()` function. The migration runs once and is never re-applied.

### Capabilities

All AI capabilities live under `src/orivellum/capabilities/`:

| Module | Purpose |
|--------|---------|
| `llm.py` | Central gateway — all model calls go here |
| `harvest.py` | Rule-based knowledge extraction |
| `llm_harvest.py` | LLM-based knowledge extraction |
| `dedup.py` | MinHash near-duplicate detection |
| `embeddings.py` | Vector embedding with circuit breaker |
| `websearch.py` | Tavily web search with RRF ranking |
| `tts.py` | Text-to-speech via espeak-ng + ffmpeg |
| `ocr.py` | Tesseract OCR |
| `audio_enhance.py` | DeepFilterNet3 audio enhancement |
| `forge/` | Website Factory pipeline (plan/design/build/verify) |
| `workshop.py` | AI document workshop |
