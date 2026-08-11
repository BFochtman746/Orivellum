---
name: Chunked client TTS playback
description: Durable lifecycle principles for chunked text-to-speech playback in the browser.
---

# Chunked client TTS playback

**Rule:** every async step of a TTS playback session (text fetch, synthesis, blob creation) must be guarded by a monotonic session id captured *before the first await* and re-checked after *every* await; stale results are discarded before any blob URL is created.

**Why:** without it, in-flight requests resurrect stale audio (wrong document after navigation, player reopening after close) and leak object URLs.

**How to apply:**
- Bump the session id on close, new read, document navigation, and unmount.
- Deduplicate concurrent synthesis with a promise map (a flag/Set + poll-wait double-fetches and leaks overwritten URLs).
- Never autoplay the first part — iOS Safari blocks audio started from async code.
- Cover the entire document lazily (synthesize on demand, evict far-behind blobs); a fixed part cap is not "full-document" playback.

# Cross-device resume badges (server-synced positions)
- Positions sync via a single batch endpoint (`/library/read-positions`), never per-doc N+1; the literal route must precede `/library/{doc_id}`.
- Merge rule: freshest client `savedAt` wins per doc, BUT a successful batch is authoritative for absence — a local copy the server doesn't know after a 5-min grace window means "deleted on another device", so drop it (a failed fetch must never delete anything).
- Clock-skew guard: far-future `savedAt` (broken clock) is distrusted in compares and clamped server-side on PUT; without this one skewed device wins every conflict forever.
- Fire-and-forget PUT/DELETE races the batch read — the fetcher must queue exactly one follow-up fetch when a request arrives mid-flight, or a stale in-flight response resurrects a deleted position.

# Persistent dock player
- A player that survives navigation must live in a global provider rendered once in the app shell, not in a page component.
- Uncontrolled `<audio>` src swaps need a desired-src ref gate: only assign src when it matches the ref captured before the async chain, or a late synthesis result overwrites the track the user just picked.
- The docked bar must reserve layout space via a shell-level CSS variable so page content is never hidden behind it, and sit below modal/sheet z-layers so dialogs stay usable while listening.
