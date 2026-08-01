# Orivellum

A sovereign, local-first knowledge management and AI assistant platform. All data and AI inference stay on your machine.

---

## Quick Start (self-hosted)

### 0. System prerequisites

Install these once with your OS package manager. They are needed for PDF-to-image conversion and OCR; all other Python dependencies are handled by `uv sync`.

**macOS (Homebrew)**
```bash
brew install tesseract poppler
```

**Ubuntu / Debian**
```bash
sudo apt-get install -y tesseract-ocr poppler-utils
```

**Windows**
- Tesseract: download installer from https://github.com/UB-Mannheim/tesseract/wiki
- Poppler: download from https://github.com/oschwartz10612/poppler-windows/releases and add `bin/` to your `PATH`

> **Runtime requirement:** Python ≥ 3.12, Node.js ≥ 20, pnpm ≥ 9, uv ≥ 0.4

### 1. Install dependencies

```bash
uv sync && pnpm install
# or: make install
```

### 2. Start everything

```bash
./start.sh
# or: make dev
# or: pnpm dev
```

That's it. The script starts the API server, waits until it passes its health check, then launches the Vite frontend. Press **Ctrl+C** to stop both processes cleanly.

Open **http://localhost:5173** once you see the `Ready ✓` line.

#### With Expo mobile

```bash
./start.sh --mobile
# or: make dev-mobile
```

#### Port overrides

```bash
API_PORT=9000 WEB_PORT=4000 ./start.sh
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
| `PORT` | `8080` | API server listening port (read by `main.py`) |
| `ORIVELLUM_AI_URL` | `http://127.0.0.1:13305/api/v1` | Base URL of the local AI server |
| `ORIVELLUM_DATA_DIR` | `./data` | Directory for the SQLite database and uploaded files |
| `ORIVELLUM_DB_PATH` | `$DATA_DIR/orivellum.db` | Override DB path directly |
| `ORIVELLUM_HOST` | `0.0.0.0` | API server bind address |
| `ORIVELLUM_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ORIVELLUM_API_KEY` | *(none)* | Optional bearer token to protect the API |

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

## Chat & Streaming

The Chat page streams AI replies in real time using Server-Sent Events (SSE). The user message is saved to the database immediately before the AI call begins. The assistant reply is saved once the full response has been received and the SSE stream closes normally.

**On clean completion**: both the user message and the full assistant reply are persisted.

**On mid-stream failure** (network drop, server crash, or client navigating away): the user message is already saved, but the assistant reply may be partial or absent. The conversation will show the user's message on reload; re-sending it will get a fresh reply.

When the AI server is unreachable, the Chat page saves your message and returns a clear error in place of an AI reply — it never silently drops input.

---

## System Status page

Navigate to **System** in the sidebar to see:

- Live AI endpoint reachability and configured URL
- Database connection status
- Step-by-step setup instructions (shown automatically when AI is offline)
- Active capability modules

---

## Development

```bash
# Run API server with auto-reload
uv run uvicorn orivellum.api.app:app --reload --port 8080

# Regenerate the frontend API client after OpenAPI changes
pnpm --filter @workspace/orivellum-ui run codegen
```
