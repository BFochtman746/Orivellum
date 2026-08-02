---
name: Read Aloud chunked TTS pattern
description: Client-side chunked document TTS playback pattern used on the web library detail page.
---

# Read Aloud chunked TTS

The rule: client-side chunked TTS playback must be session-guarded — a monotonic session id is bumped on close, new read, doc navigation, and unmount, and every synthesis result is discarded (before any blob URL is created) if the session changed while the fetch was in flight.

**Why:** blob-URL caches + async fetches otherwise resurrect stale audio (wrong document after navigation, player reopening after close) and leak object URLs. An architect review flagged exactly these races in the first version.

**How to apply:**
- Split text client-side at paragraph/sentence boundaries into ~4,500-char parts (backend `/api/studio/tts` caps at 10,000 chars); cap total parts (~40).
- Single-flight per part via a `Map<index, Promise<string>>` — never a Set + poll-wait loop (double-fetch after timeout, overwritten URLs never revoked).
- Prefetch part i+1 while part i plays; auto-advance on `ended` via an autoplay ref + effect on the audio URL (never autoplay the FIRST part — iOS Safari blocks audio started from async code).
- Voice/speed changes must invalidate the part cache (cache key is part index only).
