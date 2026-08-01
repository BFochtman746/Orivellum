---
name: Orivellum hardening decisions
description: Security, reliability, and UX hardening decisions made during the hardening session.
---

## Rate limiting (in-memory sliding window)

Added to `src/orivellum/api/app.py` as middleware. Keyed by `(client_ip, route_prefix)` → deque of timestamps. Limits:
- `/api/studio/tts` — 20/min
- `/api/studio/ocr` — 30/min  
- `/api/studio/image` — 10/min
- `/api/conversations` — 60/min

Returns HTTP 429 with `Retry-After` header. Confirmed working: 21st TTS request within 60s gets 429.

**Why:** TTS/OCR/image-gen can be CPU-heavy; chat is bounded by AI server anyway but prevents local abuse.

## Output rotation

`_rotate_outputs(out_dir)` in `src/orivellum/api/routes/studio.py` keeps the newest 50 files, deletes the rest. Called after each successful TTS write (both AI-server path and espeak-ng path).

**Why:** Outputs dir otherwise grows unbounded with every synthesis.

## Streaming silence timeout

In `_stream_response()` (`conversations.py`), wrapped `async for line in resp.aiter_lines()` with the intent to guard per-chunk silence. Full `asyncio.timeout` wrap added around the try/except block. `_CHUNK_TIMEOUT_SEC = 30`.

**Why:** AI server can go silent mid-stream without closing the connection; without a timeout the request hangs indefinitely.

## Error boundaries

`ErrorBoundary` class component in `src/orivellum-ui/src/components/error-boundary.tsx`. `RouteErrorFallback` for full-page errors. All routes in `App.tsx` wrapped via `RouteWithBoundary`. Catches render errors without blanking the whole app.

## Connection status indicators

- **Web:** `ServerStatus` component in `layout.tsx` — polls `/api/system/health` every 15s; shows colored dot + label + Wifi/WifiOff icon in sidebar footer.
- **Mobile:** `ServerDot` component in `_layout.tsx` — same poll, renders as 6px dot overlaid on home tab icon.

Both use `useGetSystemHealth` from the generated API client.

## AI reconnection toast

In `chat/index.tsx`, a `useEffect` watches `aiOnline` and shows `toast.success("AI is back online")` when it transitions false→true. Uses a `prevAiOnlineRef` to detect the transition. Only fires when it genuinely comes back (not on initial load).

## Model attribution per message

`db.add_message()` accepts `meta: dict | None`. All assistant messages now pass `meta={"model": model}` in `conversations.py`. Web chat reads `msg.meta?.model` first, falls back to `conv.model` for attribution badge.

## Single-command startup

`scripts/dev.sh` — starts API server + web UI (+ optional mobile with `--mobile` flag) in parallel, traps SIGINT to kill all. Root `package.json` adds `"dev"` and `"dev:mobile"` scripts.
