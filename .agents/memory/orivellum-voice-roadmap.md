---
name: Voice platform roadmap
description: Decisions from the Aug 2026 voice-platform evaluation — what exists, what was chosen, and design rules for the neural TTS upgrade.
---
Evaluation (Aug 2026) of user's "Voice Forge" Java package + research doc vs the existing stack.

**Existing (don't rebuild):** 4-tier TTS cascade in routes/studio.py — premium HTTP slot (serving.tts_premium_url, POST /v1/tts, EMPTY) → Lemonade /audio/speech → Kokoro ONNX v0.19 → espeak-ng; 28-voice catalog + Browse/Recommend/Design; audiobook chunk/stream/concat pipelines; faster-whisper "base" local STT; DeepFilterNet3 opt-in input enhancement. No cloning, no music/SFX generation (trailer planner emits prompts only).

**Chosen direction:** fill the empty premium tier with a loopback-only Python sidecar on Nimo's GPU (ROCm) — Chatterbox (MIT, proven Strix Halo recipe) first choice, Qwen3-TTS/CosyVoice3 alternates. Do NOT port the Java Voice Forge app; steal its designs: 127.0.0.1-only sidecars with /health, consent+SHA-256 gate before any cloned reference voice is usable, deterministic segment cache keys (text/engine/voice/speed), ffprobe QA gate before concat, -23 LUFS loudnorm mastering.

**Why:** user wants one system (Python/FastAPI), free/local only; the premium slot means zero rewiring for a quality leap.

**How to apply:** queued as tasks (mastering+casting; whisper large-v3-turbo). Deferred, not rejected: MusicGen/Stable Audio Open for trailers (license gates needed), real-time voice chat. Draft renders = Kokoro, final = sidecar.

**Sidecar built (Aug 2026):** premium tier is now a loopback-only Chatterbox sidecar. Durable rules:
- Premium breaker is single-flight until first success; only transport failures open the cooldown — any HTTP response closes it.
- Draft-quality requests skip the premium tier so Read Aloud stays instant on Kokoro.
- Cloned voices (`clone:<id>`) must FAIL CLOSED in EVERY synthesis path — one-off, streaming, document jobs, AND both Work audiobook endpoints. The Kokoro resolver maps unknown ids to a default narrator, so any fallback silently renders the wrong voice. **Why:** identity/consent — a book must never ship in an unrelated narrator.
- Sidecar is loopback-only; the UI manages clones via proxy routes on the main API; consent is enforced sidecar-side.
