---
name: Voice chat / live TTS session
description: Hands-free voice chat design — live TTS queue rules in read-aloud, voice transcribe endpoint gates, autoplay priming constraints
---

# Voice chat (hands-free) design

## Live TTS session (read-aloud.tsx)
- `startLive({title, onDone})` / `enqueueLive(text)` / `endLive()` extend the chunked TTS engine for streamed replies.
- **startLive MUST be called synchronously inside a user gesture** — it primes the shared `<audio>` element by playing a ~100 ms silent WAV data URI (`SILENT_WAV`) so later async part playback is allowed by autoplay policies. It also sets `desiredSrcRef` to the silent WAV so reset()'s pending `mediaUrl=null` commit doesn't clear the primed source.
- **Critical race:** the silent prime's `ended` event fires while part 0 is still synthesizing. `onEnded` must IGNORE the event when `liveRef && lastSrcRef === SILENT_WAV`, otherwise the queue double-starts and skips/reorders opening sentences.
- `liveIdleRef` = pipeline drained; next `enqueueLive` must call `goToPart(i, true)` itself. Otherwise just prefetch — `onEnded` advances.
- `onDone` fires exactly once via `fireLiveDone()` (nulls the ref before calling). A synthesis failure in `goToPart` must mark idle + maybe fire onDone, or the hands-free loop hangs.
- `reset()` clears all four live refs.

## Chat integration (chat/index.tsx)
- `voiceTurnRef`/`spokenIdxRef`; token loop flushes ≥60 unspoken chars up to the LAST sentence boundary (`[.!?…][)"'\u201d\]]*\s|\n\n`) through `stripForSpeech` → `enqueueLive`. Clarify branch enqueues the question. `finally` flushes the remainder + `endLive()` BEFORE resetting the accumulator.
- `useReadAloud()` had to move above `sendText` in the component — deps array evaluation hits TDZ otherwise.
- Empty transcript → `readAloud.close()` to tear down the primed session so the dock doesn't linger.

## VoiceControls (pages/chat/voice-controls.tsx)
- Secure-context gate: over plain HTTP (Tailscale) `getUserMedia` is blocked — show muted MicOff + explanation dialog (tailscale cert / localhost), never hide silently.
- MediaRecorder mime fallback: webm;opus → webm → mp4 (Safari) → ogg. 120 s cap (auto-stop has no gesture → no priming; acceptable edge). Clips <1.5 KB discarded.
- **Contract:** every failure path (no STT, no mime, getUserMedia denied, short clip, transcribe error) must call `onTranscript("")` exactly once so the parent state machine terminates.
- Priming happens on the STOP tap (`onPrimeSpeech` before `recorder.stop()`), hands-free only.

## Backend
- `POST /studio/voice/transcribe` — SYNCHRONOUS quick transcribe for mic clips (unlike the async job at /studio/transcribe). Gates in order: ext (webm/mp4/mp3/wav/m4a/ogg/flac) 422 → 25 MB 413 → empty 422 → magic-byte 415 (reuses library's `_validate_mime_signature`) → extract via `run_in_threadpool` → 503 when `result.meta` lacks `"transcription"`. Clean transcript = `result.pages[0].text` (full_text has a header prefix).
- library.py signatures gained `.webm` (EBML `\x1a\x45\xdf\xa3` @0) and `.mp4` (added to the ftyp @4 entry).

**Why:** PWA often runs over plain HTTP via Tailscale; browser autoplay + secure-context rules drive most of this design.
**How to apply:** any feature that auto-plays async audio must prime inside a gesture and guard the prime's ended event; any new upload endpoint should reuse the magic-byte validator.
