---
name: Read Aloud chunked TTS
description: Durable race/lifecycle lessons for client-side chunked TTS playback.
---

# Read Aloud chunked TTS — lessons

- Session-guard every async TTS result: bump a monotonic session id on close, new read, doc navigation, and unmount, and discard stale results **before** creating a blob URL. **Why:** without it, in-flight fetches resurrect stale audio (wrong doc after navigation, player reopening after close) and leak object URLs — an architect review caught exactly these races.
- Single-flight via a promise map, never a Set + poll-wait (double-fetches after timeout; overwritten URLs never revoked).
- Never autoplay the FIRST part — iOS Safari blocks audio started from async code; auto-advance of later parts is fine.
- Read the WHOLE document lazily (synthesize current + prefetch next, evict far-behind blob URLs) — a fixed part cap was rejected in completion review as not "full-document".
