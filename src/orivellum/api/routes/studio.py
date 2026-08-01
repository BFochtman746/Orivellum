"""Creative Studio routes — /api/studio/*"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


# ── Voices ────────────────────────────────────────────────────────────────────

# Built-in voices: map studio voice IDs → espeak-ng voice strings
_ESPEAK_VOICE_MAP: dict[str, str] = {
    "af_heart":  "en+f4",
    "af_bella":  "en+f1",
    "am_adam":   "en+m1",
    "bf_emma":   "en+f2",
    "bm_george": "en+m3",
}

_BUILTIN_VOICES = [
    {"id": "af_heart",  "name": "Heart (AF)",   "engine": "kokoro", "builtin": True},
    {"id": "af_bella",  "name": "Bella (AF)",   "engine": "kokoro", "builtin": True},
    {"id": "am_adam",   "name": "Adam (AM)",    "engine": "kokoro", "builtin": True},
    {"id": "bf_emma",   "name": "Emma (BF)",    "engine": "kokoro", "builtin": True},
    {"id": "bm_george", "name": "George (BM)", "engine": "kokoro", "builtin": True},
]


@router.get("/studio/voices")
def list_voices():
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM voice_profiles ORDER BY is_default DESC, name"
        ).fetchall()
    profiles = [dict(r) for r in rows]
    return {"voices": profiles + _BUILTIN_VOICES, "profile_count": len(profiles)}


# ── TTS synthesis ─────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0   # 0.5 – 2.0


@router.post("/studio/tts")
async def synthesize_speech(body: TTSRequest):
    """Synthesize *text* to speech.

    Strategy:
    1. Try the local AI server's /audio/speech endpoint (OpenAI-compatible).
    2. Fall back to espeak-ng (always available, no model download needed).

    Returns audio/wav.
    """
    if not body.text.strip():
        raise HTTPException(400, "text must not be empty")
    if len(body.text) > 10_000:
        raise HTTPException(400, "text too long (max 10 000 chars)")

    cfg = get_config()

    # --- Strategy 1: AI server /audio/speech ---
    try:
        import httpx
        # Map our voice IDs to OpenAI-compatible voice names
        openai_voice = {
            "af_heart": "alloy", "af_bella": "nova", "am_adam": "onyx",
            "bf_emma": "shimmer", "bm_george": "echo",
        }.get(body.voice, "alloy")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/audio/speech",
                json={
                    "model": "tts-1",
                    "input": body.text,
                    "voice": openai_voice,
                    "response_format": "mp3",
                    "speed": body.speed,
                },
            )
            if resp.status_code == 200:
                # Save to temp file and serve
                out_dir = Path(cfg.data_dir) / "outputs"
                out_dir.mkdir(parents=True, exist_ok=True)
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, dir=out_dir, suffix=".mp3"
                )
                tmp.write(resp.content)
                tmp.close()
                return FileResponse(tmp.name, media_type="audio/mpeg",
                                    filename="speech.mp3")
    except Exception as exc:
        logger.info("AI server TTS unavailable (%s) — falling back to espeak-ng", exc)

    # --- Strategy 2: espeak-ng (always available offline) ---
    try:
        out_dir = Path(cfg.data_dir) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)

        wav_tmp = tempfile.NamedTemporaryFile(
            delete=False, dir=out_dir, suffix=".wav"
        )
        wav_path = wav_tmp.name
        wav_tmp.close()

        espeak_voice = _ESPEAK_VOICE_MAP.get(body.voice, "en+f4")
        # espeak-ng speed: words per minute, default ~175; our 0.5–2.0 → 90–350 wpm
        wpm = int(175 * body.speed)
        wpm = max(80, min(400, wpm))

        result = subprocess.run(
            ["espeak-ng", "-v", espeak_voice, "-s", str(wpm), "-w", wav_path, body.text],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"espeak-ng exited {result.returncode}: {result.stderr}")

        # Convert WAV → MP3 with ffmpeg for smaller size and broader browser support
        mp3_tmp = tempfile.NamedTemporaryFile(
            delete=False, dir=out_dir, suffix=".mp3"
        )
        mp3_path = mp3_tmp.name
        mp3_tmp.close()

        ff = subprocess.run(
            ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-q:a", "4",
             mp3_path],
            capture_output=True, timeout=30,
        )
        Path(wav_path).unlink(missing_ok=True)

        if ff.returncode == 0:
            return FileResponse(mp3_path, media_type="audio/mpeg",
                                filename="speech.mp3")
        else:
            # ffmpeg failed — serve raw WAV
            Path(mp3_path).unlink(missing_ok=True)
            # Re-generate WAV since we deleted it
            wav_tmp2 = tempfile.NamedTemporaryFile(
                delete=False, dir=out_dir, suffix=".wav"
            )
            wav_path2 = wav_tmp2.name
            wav_tmp2.close()
            subprocess.run(
                ["espeak-ng", "-v", espeak_voice, "-s", str(wpm), "-w", wav_path2,
                 body.text],
                capture_output=True, timeout=30,
            )
            return FileResponse(wav_path2, media_type="audio/wav",
                                filename="speech.wav")

    except FileNotFoundError:
        raise HTTPException(
            503,
            "espeak-ng is not installed. Run: nix-env -iA nixpkgs.espeak-ng"
        )
    except Exception as exc:
        logger.error("TTS espeak-ng failed: %s", exc)
        raise HTTPException(500, f"TTS synthesis failed: {exc}")


# ── Outputs ───────────────────────────────────────────────────────────────────

@router.get("/studio/outputs")
def list_outputs():
    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    if not out_dir.exists():
        return {"outputs": [], "count": 0}
    files = sorted(out_dir.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:100]:
        if f.is_file():
            suffix = f.suffix.lower()
            if suffix in {".wav", ".mp3", ".m4a", ".m4b", ".ogg"}:
                kind = "audio"
            elif suffix in {".mp4", ".webm", ".mov"}:
                kind = "video"
            elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                kind = "image"
            else:
                kind = "file"
            result.append({
                "name": f.name,
                "path": str(f.relative_to(out_dir)),
                "size_bytes": f.stat().st_size,
                "kind": kind,
            })
    return {"outputs": result, "count": len(result)}


# ── Image generation ──────────────────────────────────────────────────────────

class ImageGenRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512


@router.post("/studio/image")
async def generate_image(body: ImageGenRequest):
    cfg = get_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/images/generations",
                json={"prompt": body.prompt, "n": 1,
                      "size": f"{body.width}x{body.height}"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception as exc:
        raise HTTPException(503, f"Image generation unavailable: {exc}")
    raise HTTPException(503, "Image generation unavailable")


# ── OCR ───────────────────────────────────────────────────────────────────────

class OCRRequest(BaseModel):
    content_b64: str
    filename: str = "image.png"


def _probe_tesseract_cmd() -> None:
    """Ensure pytesseract can find the tesseract binary (NixOS/Replit path fix)."""
    import shutil, subprocess as _sp, pytesseract as _pt
    from pathlib import Path as _P

    if shutil.which("tesseract"):
        return

    # Ask the login shell — it has a broader PATH than the API process
    try:
        r = _sp.run(["bash", "-lc", "which tesseract"],
                    capture_output=True, text=True, timeout=5)
        c = r.stdout.strip()
        if c and _P(c).is_file():
            _pt.pytesseract.tesseract_cmd = c
            return
    except Exception:
        pass

    # Walk /nix/store at depth-1 only — fast, avoids timeout
    nix = _P("/nix/store")
    if nix.exists():
        for d in nix.iterdir():
            if "tesseract" in d.name:
                cand = d / "bin" / "tesseract"
                if cand.is_file():
                    _pt.pytesseract.tesseract_cmd = str(cand)
                    return


@router.post("/studio/ocr")
def run_ocr(body: OCRRequest):
    import base64
    import io

    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")

    try:
        from PIL import Image
        import pytesseract
        _probe_tesseract_cmd()
        img = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(img)
        return {"text": text, "ok": True}
    except ImportError:
        raise HTTPException(503, "OCR dependencies (Pillow, pytesseract) not available")
    except Exception as exc:
        raise HTTPException(500, f"OCR failed: {exc}")
