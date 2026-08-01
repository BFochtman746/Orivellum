"""Creative Studio routes — /api/studio/*"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")


@router.get("/studio/voices")
def list_voices():
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM voice_profiles ORDER BY is_default DESC, name"
        ).fetchall()
    # Built-in Kokoro voices always available
    builtin = [
        {"id": "af_heart", "name": "Heart (AF)", "engine": "kokoro", "builtin": True},
        {"id": "af_bella", "name": "Bella (AF)", "engine": "kokoro", "builtin": True},
        {"id": "am_adam", "name": "Adam (AM)", "engine": "kokoro", "builtin": True},
        {"id": "bf_emma", "name": "Emma (BF)", "engine": "kokoro", "builtin": True},
        {"id": "bm_george", "name": "George (BM)", "engine": "kokoro", "builtin": True},
    ]
    profiles = [dict(r) for r in rows]
    return {"voices": profiles + builtin, "profile_count": len(profiles)}


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
            result.append({
                "name": f.name,
                "path": str(f.relative_to(out_dir)),
                "size_bytes": f.stat().st_size,
                "kind": "audio" if f.suffix in {".wav", ".mp3", ".m4b", ".m4a"} else "file",
            })
    return {"outputs": result, "count": len(result)}


class ImageGenRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512


class OCRRequest(BaseModel):
    content_b64: str
    filename: str = "image.png"


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


@router.post("/studio/ocr")
def run_ocr(body: OCRRequest):
    import base64
    import tempfile
    from pathlib import Path

    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")

    try:
        from PIL import Image
        import pytesseract
        import io
        img = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(img)
        return {"text": text, "ok": True}
    except ImportError:
        raise HTTPException(503, "OCR dependencies (Pillow, pytesseract) not available")
    except Exception as exc:
        raise HTTPException(500, f"OCR failed: {exc}")
