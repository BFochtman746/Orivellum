"""Premium TTS sidecar — loopback-only neural narration engine.

Runs Chatterbox (MIT) on the local GPU and exposes the premium TTS
contract that Orivellum's synthesis cascade already speaks
(``POST /v1/tts`` + ``GET /health``), plus consent-gated voice cloning.

Never binds to anything but 127.0.0.1 — reference voice audio and cloned
speech never leave the machine.
"""
