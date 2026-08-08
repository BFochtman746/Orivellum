---
name: Forensic eval fixes — Aug 2026
description: Changes made in response to the Claude forensic evaluation (F-1 through F-10) and the Search/Email/UI addendum.
---

# Forensic eval fixes applied

**Why:** Two Claude reviews identified config errors causing silent AI failures, hidden navigation for key features, absent email ingestion, and blind hardware telemetry. These are the changes made and what remains.

## Done

### F-1 CRITICAL — config.yaml base_url corrected
- `base_url: "http://127.0.0.1:8000/v1"` → `"http://127.0.0.1:8000/api/v1"`
- This was causing every AI call to 404 silently (llm_call never raises).

### F-6 MEDIUM — TTS/ASR model names corrected
- `tts_model: "tts-1-hd"` → `"kokoro-v1"` (Lemonade-served Kokoro)
- `asr_model: "whisper-1"` → `"Whisper-Base"` (Lemonade model name)

### F-4 HIGH — Intake + Graph added to all nav surfaces
- `layout.tsx` PHASES: Intake in Import phase, Graph in Understand phase
- RAIL_ITEMS (tablet rail): Intake + Graph replace Studio + Write (less frequent)
- NAV_ITEMS (mobile sheet): Intake + Graph added after Library

### Email ingestion — Option A implemented (no IMAP, no OAuth)
- `library.py _KIND_MAP`: `.eml` → "email", `.msg` → "email"
- `extraction.py _DISPATCH`: added `_extract_email()` handler
  - .eml: stdlib `email` module (zero deps) — parses headers + body + attachments
  - .msg: tries `extract_msg` package, falls back to raw byte read
  - ZIP handler `_EXT_KIND` also updated
- Drop `.eml` / `.msg` files into Library or the watched folder and the full pipeline runs.

### Websearch fail-loud
- `fetch_web_context`: `logger.debug` → `logger.warning` with explicit message
- Missing key now warns clearly; network failures are named in the log
- Silent ungrounded answers will now appear in the log so you know grounding failed.

### System page — Lemonade Engine card
- New `GET /api/system/lemonade` endpoint in `system.py`
  - Proxies Lemonade `/api/v1/health` + `/api/v1/system-info` + `/api/v1/stats`
  - Falls back gracefully if Lemonade unreachable
- `LemonadeEngineCard` in `system/index.tsx` — shows loaded models, device, ctx size, tok/s
- This is the truthful hardware panel for Strix Halo (nvidia-smi / rocm-smi are absent on Windows)

### System page — MCP Server card
- `McpCard` in `system/index.tsx` — surfaces the MCP connect URL with a copy button
- Shows available tools; previously invisible despite the server being live

## Not yet done (deferred or needs task agent)

### F-2 CRITICAL — workhorse still llama3.3-70b
- config.yaml has recommendations in comments but the active value is unchanged
- Operator action needed: `lemonade pull Qwen3.5-35B-A3B` then update config.yaml workhorse_model
- **This is an operator task on A-01, not a code task.**

### F-3 HIGH — thread pool not adopted
- `api/executor.py` exists but only `folder_watch.py` uses it
- 8 raw `threading.Thread(daemon=True)` still spawn in system.py, studio.py, persist.py, pipeline.py, push.py, nightshift.py
- Large refactor; needs a task agent pass

### F-9 LOW — model picker (write-back)
- `/system/models` is read-only; no PATCH to change active models from the UI
- Needs a new endpoint + small UI form in system/index.tsx

### Search provider refactor
- `websearch.py` is still Tavily-only with no fallback chain
- Review recommends: SearchProvider protocol + providers/searxng.py, providers/brave.py, providers/tavily.py + ExtractProvider + audit_log egress logging
- Large refactor; best as a task agent

### Command palette (Cmd+K)
- No cmdk in the codebase; would surface all 28 pages + knowledge search + conv search
- Medium task

### Vellum polish pass + iPad tier
- Specified in VELLUM_design_system.md and orivellum_app.html
- Still shadcn defaults on many pages
- One-shot bounded pass; best as a task agent

**How to apply:** Check `lemonade list` on A-01 to confirm model names, then update `config.yaml` workhorse_model, reasoner_model, coder_model to the exact strings Lemonade shows.
