---
name: DeepFilterNet3 audio enhancement integration
description: How DeepFilterNet3 pre-processing slots into the audio transcription pipeline.
---

# DeepFilterNet3 Audio Enhancement

## Rule
DeepFilterNet3 runs as step 0 in `_extract_audio()` before any Whisper call.
Enabled via `db.get_setting("audio_enhance_enabled", "false")` — same pattern as `ai_extraction_enabled`.

**Why:** Lemonade (the default Whisper backend at port 13305) was already wired; DeepFilterNet3 is a pre-processing step, not a separate backend.

## How to apply
- `capabilities/enhancement.py` — lazy singleton `_get_df_model()`, `is_available()`, `enhance_audio(path, output_dir)`
- `capabilities/extraction.py` — step 0 block at line ~926; uses `transcribe_path` variable; temp dir cleaned in `finally`
- `api/routes/system.py` — `GET/PUT /system/settings/audio-enhance`
- `pages/system/index.tsx` — `AudioEnhancementCard` with installed/not-installed badge + toggle

## Key details
- Output WAV: `{stem}_dfn3.wav` in a `tempfile.mkdtemp()` dir, always cleaned up in `finally`
- Native rate: 48 kHz → Whisper resamples internally, no double resample needed
- `meta.enhanced: bool` added to AI-server transcription result for downstream display (#706)
- Toggle disabled when not installed AND not enabled (guard in UI)
- Package install: `uv add deepfilternet torch torchaudio --extra-index-url https://download.pytorch.org/whl/cpu`
