---
name: No-robot-voice policy
description: Owner policy — espeak removed from all audible TTS paths; 503/pause-and-retry instead of robotic fallback.
---

**Rule:** Audible speech comes ONLY from neural engines (premium sidecar → AI server → Kokoro ONNX). When none is ready, endpoints return 503 (`_NEURAL_TTS_UNAVAILABLE_MSG`) or fail the render — never synthesize with espeak-ng.

**Why:** Owner directive (Aug 2026): "when audio isn't optimal, wait — never the robotic voice." Also recorded in replit.md user preferences.

**How to apply:**
- Never re-add espeak (or any low-quality engine) to a synthesis cascade, engines list, or status "strategies" list.
- Segment renders (audiobook sync/async, streaming SSE) must FAIL LOUDLY when no engine works — silently skipping segments produces a "successful" silent MP3 (this bug existed; segment failure now raises).
- Cached audio provenance matters: legacy espeak-generated samples/segments must never be served. Unlabeled pre-DB sample files are deleted and regenerated, never relabeled as kokoro. Segment cache keys include the engines list, so removing an engine invalidates its cached audio.
- Client (Read Aloud `synthesizePart`) treats TTS 503 as "pause and wait": bounded retry (4 attempts, 3.5 s apart), session-guarded via TTS_STALE, then a friendly "voice engine isn't ready" error.
- espeak-ng may still exist on the system (diagnostics report it as informational/unused only).
