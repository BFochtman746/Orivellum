"""Music & SFX generation sidecar HTTP server.

Loopback-only FastAPI app implementing Orivellum's music generation contract:

    GET  /health     — engine + per-model availability status
    POST /v1/music   — {prompt, duration_s, model, negative_prompt} → audio/wav

Model selection in /v1/music:
    "stable_audio_open"  → Stable Audio Open 1.0 (Stability AI Community License)
    "musicgen"           → MusicGen (weights CC-BY-NC 4.0 — non-commercial)

License gating is enforced on the MAIN API — a request only arrives here
after the user acknowledged the selected model's license terms.

Run:  python -m sidecars.music_gen.server   (binds 127.0.0.1:9884)
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("music_gen")

HOST = "127.0.0.1"  # loopback ONLY
PORT = int(os.environ.get("MUSIC_GEN_PORT", "9884"))

app = FastAPI(title="Orivellum Music Generation Sidecar", docs_url=None, redoc_url=None)


class MusicBody(BaseModel):
    prompt: str
    duration_s: float = 30.0
    model: str = "stable_audio_open"
    negative_prompt: str = ""
    # Accepted for contract compatibility; output is always WAV.
    format: str = "wav"
    kind: str = "music"  # "music" | "sfx" — informational (logging) only


@app.get("/")
def root():
    """Generic liveness (some probes hit the root URL)."""
    return {"ok": True, "service": "music-gen"}


@app.get("/health")
def health():
    return {"ok": True, "service": "music-gen", **engine.status()}


@app.post("/v1/music")
def music(body: MusicBody):
    logger.info("Generate %s: model=%s dur=%.1fs prompt=%r",
                body.kind, body.model, body.duration_s, body.prompt[:120])
    try:
        wav, sr = engine.generate_wav(
            body.model, body.prompt, body.duration_s,
            negative_prompt=body.negative_prompt,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        # Model not loaded / generation failure → 503 so the main API can
        # surface a clear "backend not ready" message instead of a hard error.
        raise HTTPException(503, str(exc))
    return Response(content=wav, media_type="audio/wav",
                    headers={"X-Music-Engine": body.model,
                             "X-Sample-Rate": str(sr)})


def main() -> None:
    import uvicorn
    logger.info("Music generation sidecar starting on http://%s:%d (loopback only)", HOST, PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
