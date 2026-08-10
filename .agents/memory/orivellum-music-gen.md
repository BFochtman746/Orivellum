---
name: Music & SFX generation
description: Local music/SFX sidecar for trailers — license-gate-in-DB pattern, single-resident model, WAV contract, per-model duration caps.
---

# Music & SFX generation (trailer soundtracks)

- Sidecar `sidecars/music_gen/` mirrors the premium TTS sidecar pattern: loopback-only FastAPI on 127.0.0.1:9884 (`MUSIC_GEN_PORT`), `GET /health` with per-model status, `POST /v1/music` → WAV bytes (400 bad input / 503 model unavailable). Started by `scripts/start-music-sidecar.ps1` (.venv-music, `-Setup`, `-TorchIndexUrl` for ROCm).
- Two backends, **one resident at a time** (VRAM bound): `stable_audio_open` (diffusers, gated HF repo, max 47 s, music+SFX) and `musicgen` (transformers, max 60 s, music only, ~50 tokens/s → max_new_tokens = duration×50).
  **Rule:** model load AND inference must sit under the SAME lock — loading outside the synth lock lets a concurrent request for the other model unload the first between load and inference (regression test exists).
- **License-gate-in-DB pattern** (differs from tts_premium's config flag): per-model DB settings `music_license_ack_<model_id>` = "true", set via `POST /api/studio/music/licenses/{id}/ack` from a UI dialog. Generate returns 403 until acked; gates ordered 503 unconfigured → 404 unknown model → 422 validation → 403 unlicensed (input errors surface before the license dialog).
  **Why:** MusicGen weights are CC-BY-NC (non-commercial ONLY — its MIT license covers only code); Stable Audio Open is Stability Community License (commercial OK under $1M revenue). Informed consent per model, asked exactly once.
- Generation is a background job (`submit_bg`, in-memory job dict + lock, poll `GET /studio/music/jobs/{id}`); httpx timeout 900 s read / 10 s connect. Output `music_`/`sfx_` prefixed WAV in the outputs dir (labels wired in studio list_outputs), `_link_output_sync` before `_rotate_outputs`, then **strict** `register_and_index` — registration failure yields `state:"done", registered:false, warning:...` (partial success, never silent).
- SFX duration capped at 15 s regardless of model; only models whose `good_for` includes "sfx" accept kind=sfx.
- UI: `MusicGenControls` in trailer-tab renders nothing when `music_gen_url` unset (clean degradation), disables with a hint when configured-but-unreachable. Poll intervals must be *owned*: clear any stale interval before starting, and closures clear only the interval they created.
- **Test quirk:** the app lifespan re-runs `_deps.init` with the real config when `TestClient` enters — tests must re-call `_deps.init(db=db, cfg=cfg)` INSIDE the client context (see `_client` helper in tests/test_music_generation.py) or routes silently use the dev DB/config.
