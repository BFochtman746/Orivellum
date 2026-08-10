---
name: In-browser microphone recording
description: MediaRecorder capture patterns, race guards, and how to test mic flows in this container
---

# In-browser microphone recording (Studio Transcription "Record" source)

- **Session-generation guard is mandatory.** A ref counter bumped by teardown/discard/unmount; `startRecording` captures its generation and re-checks after `await getUserMedia()` (stop tracks if stale) and inside `onstop` (skip publishing file/object URL if stale). Without it, a permission prompt resolving after navigation leaves the mic hot, and `onstop` leaks an unrevoked object URL.
  **Why:** architect review failed the first implementation on exactly these races.
  **How to apply:** any future getUserMedia/MediaRecorder feature (voice chat already has a similar session-id guard — see orivellum-voice-chat.md).
- Mime pick order: `audio/webm;codecs=opus` → `audio/webm` → `audio/mp4`; name Safari `audio/mp4` blobs `.m4a` (already in `_AUDIO_EXTS`). `.webm` was added to `_AUDIO_EXTS`; library magic-byte table already validates EBML.
- Use `mr.start(1000)` timeslice so long recordings flush chunks incrementally.
- **Testing mic flows:** this container has no audio devices and Chromium fake-media flags don't help. Stub via `addInitScript`: replace `navigator.mediaDevices.getUserMedia` with an `AudioContext` oscillator → `createMediaStreamDestination().stream`. MediaRecorder records it fine; gesture requirement is satisfied because the call happens in a click handler.
