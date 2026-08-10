# Changelog

All notable changes to Orivellum are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project has not yet cut a tagged release; the version in `pyproject.toml`
is `0.1.0` and the sections below track work rather than published versions.

## [Unreleased]

### Added
- Apache 2.0 `LICENSE` file at the repository root.
- One-time TTS model fetch scripts (`scripts/fetch_tts_model.sh`,
  `scripts/fetch_tts_model.ps1`) that download the Kokoro ONNX model and
  `voices.bin` from upstream releases. The model binaries are no longer
  bundled in git.
- Renovate configuration for automated dependency-update pull requests.

### Changed
- Bumped dependency floors, including `cryptography>=50.0.0` and
  `youtube-transcript-api>=1.2.4`.
- Ported the YouTube transcript integration to the `youtube-transcript-api`
  1.x API (`YouTubeTranscriptApi().fetch(...)` returning snippet objects,
  replacing the removed static `get_transcript()`).
- Completed the migration of fire-and-forget background work to the shared
  bounded `ThreadPoolExecutor` in `src/orivellum/api/executor.py`, replacing
  ad-hoc `threading.Thread(daemon=True)` spawns.
- Removed model binaries and large TTS assets from git tracking in favour of
  the fetch scripts above.
- Stopped tracking test artifacts and personal library data (e.g. transient
  SQLite files and `data/library/` contents).

### Fixed
- Ruff cleanup across the Python source (large batch of auto-fixes) plus three
  undefined-name (`F821`) bugfixes surfaced by the lint pass.

### Security
- Removed the espeak-ng fallback from all audible TTS paths ("no robot voice"
  policy): synthesis now uses the neural Kokoro ONNX engine (or an optional
  premium sidecar) and fails closed rather than emitting robotic audio.

## [Pre-cleanup history]

History prior to August 2026 is reconstructed from `git log` highlights and is
intentionally coarse — it predates this changelog, so no versions or dates are
asserted. Major feature areas that landed before the cleanup above include:

- **Voice & Read Aloud** — hands-free voice chat (mic capture, sync
  transcription endpoint, sentence-chunked TTS replies), docked Read Aloud
  player with resume and Media Session lock-screen controls.
- **Studio production suite** — audio transcription tool, audiobook mastering
  (two-pass loudnorm, per-Work voice casting), and a consent-gated
  studio-quality voice engine sidecar.
- **Music & trailers** — local music and SFX generation for book trailers.
- **Measurement layer** — streaming TTFT / decode-rate telemetry, native
  benchmarks, and a retrieval golden-set evaluation harness (schema v109).
- **Ingestion shield & governance** — ingestion shield, quarantine flow, mail
  gates, and chat abstention (PKLOS provenance / lifecycle tracking).
- **Mail steward** — Microsoft Graph mail integration with OAuth token vault,
  threat intelligence, and policy-gated actions.
- **MONARCH book intelligence** — chapter-level analysis, completeness scoring,
  gap detection, and near-duplicate flagging within a Work.
- **GENESIS origination** — the PLAN → DESIGN → BUILD → VERIFY book-origination
  pipeline (codex, gates, seal).
- **Forge website factory** — tool-calling agent that plans, designs, builds,
  and verifies static websites from a brief.
- **Learning loop** — Socratic spaced-repetition study over a Work's knowledge
  base with mastery tracking.
- **Weather, audio enhancement, and UI rebrand** — DeepFilterNet3 audio
  cleanup, the Home Screen weather card, and the manuscript-themed Orivellum
  PWA identity that retired the legacy web UI and native app shells.
