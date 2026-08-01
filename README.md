# Orivellum

A sovereign, local-first knowledge management and AI assistant platform. All data and AI inference stay on your machine.

---

## Quick Start (self-hosted)

Orivellum is two processes: a Python API server and a React/Vite frontend. The frontend sends all `/api/*` requests to the API server via a proxy — you need to wire them together with the env vars below.

### 1. Install dependencies

```bash
# Python dependencies
uv sync

# Frontend dependencies
pnpm install
```

### 2. Start the API server

The API server reads `PORT` for its listening port (default `8080`).

```bash
# Default port 8080
uv run python -m orivellum.api.main

# Custom port
PORT=9000 uv run python -m orivellum.api.main
```

### 3. Start the frontend

The frontend requires two env vars:

| Variable | Description |
|----------|-------------|
| `PORT` | Port for the Vite dev server (e.g. `5173`) |
| `BASE_PATH` | URL base path (use `/` for root) |
| `ORIVELLUM_API_URL` | Base URL of the running API server — activates the `/api` proxy |

```bash
PORT=5173 BASE_PATH=/ ORIVELLUM_API_URL=http://127.0.0.1:8080 \
  pnpm --filter @workspace/orivellum-ui run dev
```

Open `http://localhost:5173`.

> **Why `ORIVELLUM_API_URL`?** The frontend makes relative `/api/...` requests. When you run Vite separately from the API server, those requests would hit Vite (and 404). Setting this env var activates a Vite proxy that forwards `/api/*` to the Python process.

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
