# 9. No synthesized robot voice in any audible path

Date: 2026-08-10 | Status: Accepted

## Context
espeak output damaged trust in the audio product once; 'temporary' fallbacks became permanent.

## Decision
espeak is banned from every user-audible path. When real TTS is unavailable the API returns 503 and clients pause and retry. Legacy espeak audio is never relabeled or served.

## Consequences
Users hear silence-with-retry instead of a jarring fallback. Requires honest capacity signalling from the TTS layer.
