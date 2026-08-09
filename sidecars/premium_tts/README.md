# Premium TTS Sidecar (Chatterbox on Nimo's GPU)

Fills Orivellum's empty premium TTS tier with a **local, free, loopback-only**
neural engine: [Chatterbox](https://github.com/resemble-ai/chatterbox) (MIT),
which has a proven recipe on AMD Strix Halo via ROCm-enabled PyTorch.

## What it does

- `POST /v1/tts` — the exact contract `routes/studio.py` already speaks for the
  premium slot, so Read Aloud, Studio TTS, and both audiobook pipelines get the
  upgrade with **zero rewiring**.
- `GET /health` — engine + voice-store status (the main API's `/studio/status`
  probes this and surfaces the engine badge in Voice Studio).
- Voice cloning from a short reference clip (5–30 s of clean speech), gated
  behind an explicit consent acknowledgement. A voice's SHA-256 and consent
  record are stored before it can ever be used; unacknowledged voices return
  403 from synthesis.

Binds **127.0.0.1 only**. Reference clips live in `data/premium-voices/`.

## Setup on Windows (Nimo)

1. `scripts\start-voice-sidecar.ps1 -Setup` — creates `.venv-tts`, installs
   PyTorch + chatterbox-tts. For GPU speed pass the ROCm wheel index, e.g.:
   `scripts\start-voice-sidecar.ps1 -Setup -TorchIndexUrl https://download.pytorch.org/whl/rocm6.4`
   (CPU-only torch works but renders slowly — fine for testing.)
2. `scripts\start-voice-sidecar.ps1` — starts the sidecar on 127.0.0.1:9883.
3. In `config.yaml` set:
   ```yaml
   serving:
     tts_premium_url: "http://127.0.0.1:9883"
     tts_premium_ack_license: true   # Chatterbox is MIT — ack is a config gate
   ```
4. Restart Orivellum. Voice Studio's Audiobook tab shows the premium badge when
   the sidecar is live.

First synthesis downloads the model weights (~2 GB) to the HF cache.

## Draft vs final quality

Interactive listening (Read Aloud parts) intentionally requests `draft`
quality, which the main API serves from Kokoro for instant starts. Final
renders (Studio TTS clips, audiobook and document builds) prefer this sidecar.
If the sidecar is down, a circuit breaker in the main API skips it quickly and
the cascade falls through to Kokoro/espeak — nothing breaks.
