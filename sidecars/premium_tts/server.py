"""Premium TTS sidecar HTTP server.

Loopback-only FastAPI app implementing Orivellum's premium TTS contract:

    GET  /health                     — engine + voice-store status
    POST /v1/tts                     — {text, voice, speed, format} → audio/mpeg
    GET  /v1/voices                  — list cloned voices (consent state)
    POST /v1/voices                  — multipart upload of a reference clip
    POST /v1/voices/{vid}/consent    — acknowledge the consent statement
    DELETE /v1/voices/{vid}          — remove a cloned voice + its clip

Voice selection in /v1/tts:
    "clone:<id>"  → consented cloned voice (403 until consent acknowledged)
    anything else → Chatterbox's default narrator voice

Run:  python -m sidecars.premium_tts.server   (binds 127.0.0.1:9883)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import engine
from .voices import CONSENT_STATEMENT, MAX_REF_BYTES, VoiceStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("premium_tts")

HOST = "127.0.0.1"  # loopback ONLY — never expose reference/cloned audio
PORT = int(os.environ.get("PREMIUM_TTS_PORT", "9883"))

_REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PREMIUM_TTS_DATA", _REPO_ROOT / "data" / "premium-voices"))

app = FastAPI(title="Orivellum Premium TTS Sidecar", docs_url=None, redoc_url=None)
store = VoiceStore(DATA_DIR)


class TTSBody(BaseModel):
    text: str
    voice: str = "default"
    speed: float = 1.0
    format: str = "mp3"      # accepted for contract compatibility; always mp3
    # Extra fields sent by the main API's premium caller — accepted, unused.
    chunk_length: int | None = None
    normalize: bool | None = None
    latency: str | None = None


@app.get("/")
def root():
    """Generic liveness (some probes hit the root URL)."""
    return {"ok": True, "service": "premium-tts"}


@app.get("/health")
def health():
    st = engine.status()
    return {
        "ok": True,
        "service": "premium-tts",
        **st,
        "consent_statement": CONSENT_STATEMENT,
        "voices": len(store.list()),
    }


@app.post("/v1/tts")
def tts(body: TTSBody):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "text must not be empty")

    ref = None
    if body.voice.startswith("clone:"):
        vid = body.voice.split(":", 1)[1]
        voice = store.get(vid)
        if voice is None:
            raise HTTPException(404, f"cloned voice '{vid}' not found")
        if not voice.usable:
            # The consent gate: uploaded but unacknowledged voices never speak.
            raise HTTPException(403, f"voice '{voice.name}' requires consent acknowledgement before use")
        ref = store.ref_path(voice)
        if not ref.is_file():
            raise HTTPException(410, f"reference clip for '{voice.name}' is missing on disk")

    try:
        audio = engine.synthesize_mp3(text, ref_audio=ref, speed=body.speed)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        # Model not loaded / synth failure → 503 so the caller's cascade
        # falls through to Kokoro instead of surfacing a hard error.
        raise HTTPException(503, str(exc))
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"X-TTS-Engine": "chatterbox"})


@app.get("/v1/voices")
def list_voices():
    return {"voices": store.list(), "consent_statement": CONSENT_STATEMENT}


@app.post("/v1/voices")
async def create_voice(
    file: UploadFile = File(...),
    name: str = Form(...),
    consent_ack: bool = Form(False),
    consent_statement: str = Form(""),
):
    # Bounded read — anything beyond the cap is rejected without buffering more.
    audio = await file.read(MAX_REF_BYTES + 1)
    try:
        voice = store.create(name, audio, consent_ack=consent_ack,
                             consent_statement=consent_statement)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    logger.info("Cloned voice registered: %s (%s, consent=%s)",
                voice.name, voice.sha256[:12], voice.consent.acknowledged)
    return voice.public()


@app.post("/v1/voices/{vid}/consent")
def acknowledge(vid: str):
    try:
        voice = store.acknowledge_consent(vid)
    except KeyError:
        raise HTTPException(404, f"cloned voice '{vid}' not found")
    return voice.public()


@app.delete("/v1/voices/{vid}")
def delete_voice(vid: str):
    if not store.delete(vid):
        raise HTTPException(404, f"cloned voice '{vid}' not found")
    return {"ok": True}


def main() -> None:
    import uvicorn
    logger.info("Premium TTS sidecar starting on http://%s:%d (loopback only)", HOST, PORT)
    logger.info("Voice store: %s", DATA_DIR)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
