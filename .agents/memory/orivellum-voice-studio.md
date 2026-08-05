---
name: Voice Studio system
description: Full voice catalog, sample generation/caching, AI recommender, voice designer, ACX mastering, and audiobook builder.
---

# Voice Studio

## Architecture

### Backend — `src/orivellum/api/routes/studio.py`
- `_VOICE_CATALOG`: 28 voices (11 AF, 8 AM, 5 BF, 4 BM), each with `id/name/accent/gender/description/dimensions/tags/builtin/engine`
- `_VOICE_BY_ID`: index dict for fast lookup
- `_ESPEAK_VOICE_MAP`: fallback espeak strings for all 28 voice IDs
- `_SAMPLE_SENTENCE`: standardized 2-sentence evocative text for all previews
- Schema v78: `voice_samples` table (`voice_id TEXT PK`, `sample_path`, `engine`, timestamps)

### Voice dimension schema (1–10)
`warmth` `authority` `gravitas` `pace` `brightness` `age` — all in every catalog entry.

### Endpoints added
| Route | Method | Purpose |
|-------|--------|---------|
| `/api/studio/voices` | GET | Returns full catalog + custom profiles |
| `/api/studio/voices/{id}/sample` | GET | Cached MP3 sample; generates on first call; Kokoro→espeak fallback |
| `/api/studio/voices/recommend` | POST | LLM analyzes Work (title/desc/knowledge/chunks) → ranked 5 voices |
| `/api/studio/voices/design` | POST | NL description → LLM maps to dimensions → Euclidean match |
| `/api/studio/tts/work` | POST | Full Work audiobook: all ready docs, chapter structure, credits, ACX mastering |

### ACX mastering helper
`_apply_acx_mastering(input, output)` — ffmpeg `loudnorm=I=-20:TP=-3:LRA=7` + 192 kbps CBR 44.1 kHz 2ch.
Returns True on success; caller uses raw file on failure.

### LLM call pattern for async routes
All LLM calls use `await run_in_threadpool(llm_call, messages, base_url=cfg.serving.base_url, model=cfg.serving.workhorse_model, ...)`.
Both recommender and designer have full deterministic fallbacks for when LLM is unavailable.

## Frontend — `artifacts/orivellum-ui/src/pages/studio/VoiceStudio.tsx`
- `useGlobalAudio()`: single HTMLAudio element shared across all voice cards; singleton prevents double-play
- `VoiceCard`: grid card with 3 key dimension bars, genre tags, inline play button
- `VoiceDetailPanel`: slide-in detail with all 6 dimensions, full description, CTA
- `BrowseTab`: filter bar (search + gender + accent + tone), responsive 2–4 col grid, side detail panel
- `RecommendTab`: work picker → POST recommend → genre analysis + narrator profile + ranked recommendation cards
- `DesignTab`: NL textarea + 5 example prompts → POST design → target dimensions + 3 best matches
- `AudiobookTab`: Work or Document mode, voice picker synced from other tabs, speed, credits, ACX toggles, inline player + download

## Studio page — `artifacts/orivellum-ui/src/pages/studio/index.tsx`
- Top-level tabs: Voice Studio | Image Generation | Document Workshop | Recent Outputs
- `VoiceStudio` is the default/primary tab
- Full-height layout (calc 100vh - 4rem) so the voice grid fills the viewport

**Why:** The old 5-voice TTS dropdown was discovered to be far below what Kokoro ONNX supports — the catalog expansion gives users genuine choice and makes the AI recommender/designer features meaningful.

## Mobile status
- `artifacts/mobile/app/studio.tsx` updated: full 28-voice `FALLBACK_VOICES` + `VoiceEntry` interface + `VoiceBrowserCard` component with horizontal scroll, accent badges, gender indicators, and play button wired to `GET /api/studio/voices/{id}/sample` via `useSharedAudio.toggle()`
- `loadVoices` maps all rich fields (accent, gender, description, tags) from the API

## TTS voice routing (critical — was broken, now fixed)
- Both `POST /studio/tts` (Kokoro path) and `POST /studio/tts/document` previously restricted Kokoro to only the original 5 voice IDs; any other voice silently fell back to `af_heart`
- Both now use `_resolve_kokoro_voice(body.voice)` which accepts all 28 catalog IDs
- OpenAI-compat path uses `_OPENAI_VOICE_MAP` covering all 28 voices grouped by tonal similarity

## voice_samples DB table (schema v78)
- `_upsert_voice_sample_db()` writes voice_id, sample_path, engine, timestamps after every successful generation
- `_lookup_voice_sample_db()` reads the DB cache in `get_voice_sample` before hitting the filesystem
- `_synthesize_sample_sync` checks DB first, then file, then generates; backfills DB for pre-existing files
