---
name: Local transcription (faster-whisper)
description: Model-size resolution, low-memory fallback, timestamp capture rules for audio transcription
---

# Local transcription (faster-whisper)

- Default local ASR model is **large-v3-turbo (int8)**; DB setting `asr_local_model` overrides config.yaml (same precedence as chat model overrides). Remember config.yaml wins over the dataclass default — change BOTH when changing defaults.
- **Low-memory guard**: heavy sizes (medium/large*) fall back to "base" when psutil reports < 6 GB available RAM; load failure of a heavy model also retries "base".
- **Fallback must be cached per requested size** (`_fw_requested_size`): otherwise every transcription re-attempts the heavy download. Code-review bounce — don't regress this.
- **Model metadata must travel with the model**: `_get_faster_whisper_snapshot()` returns (model, loaded_size, fallback_reason) atomically; never read the globals after `transcribe()` — a concurrent settings-driven reload would misattribute the model in doc meta.
- **Timestamps**: word/segment timestamps stored in document meta (`segments` cap 2000, `words` cap 6000 + `words_truncated` flag) for future read-along highlighting. OpenAI-compatible verbose_json requires BOTH `timestamp_granularities[]` segment AND word or word timing is omitted; retry plain-json when a server rejects the verbose form.
- Settings endpoints: GET/PATCH `/api/system/settings/asr`; studio status reports the loaded (post-fallback) size when a model is in memory.

**Why:** transcription accuracy upgrade (Aug 2026); guards keep low-RAM machines working while Nimo gets large-v3-turbo.
