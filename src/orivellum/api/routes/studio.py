"""Creative Studio routes — /api/studio/*"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_MAX_OUTPUTS = 50  # keep the newest N files; delete the rest


# ── Amendment-1 registration helpers ─────────────────────────────────────────

def _link_output_sync(file_path: Path) -> str:
    """Create a durable library hard-link SYNCHRONOUSLY before rotation.

    Must be called BEFORE _rotate_outputs so the library inode is preserved
    even if rotation deletes the original source path.  Returns the
    lib-root-relative path to be passed as ``prelinked_rel`` to the background
    registration thread so it does not re-link an already-durable file.

    Non-fatal: returns an empty string on failure (background thread will fall
    back to the standard _ensure_lib_symlink path, which may fail if the source
    was already rotated away — but the link already exists from the sync call).
    """
    try:
        from orivellum.capabilities.persist import _ensure_lib_symlink
        cfg = get_config()
        lib_root = Path(cfg.data_dir) / "library"
        return _ensure_lib_symlink(file_path, lib_root)
    except Exception as exc:
        logger.debug("_link_output_sync failed (non-fatal): %s", exc)
        return ""


def _register_output_bg(
    file_path: Path,
    text_content: str,
    kind: str,
    title: str,
    *,
    prelinked_rel: str | None = None,
    work_id: str | None = None,
    origin_id: str | None = None,
) -> None:
    """Register a Studio output as a searchable library document (background).

    Called in a daemon thread so the HTTP response returns immediately.
    ``prelinked_rel`` should be the value returned by ``_link_output_sync``,
    which was called synchronously before ``_rotate_outputs`` ran.  This
    ensures the library hard-link is always durable regardless of rotation.
    """
    try:
        from orivellum.capabilities.persist import register_and_index
        cfg = get_config()
        db  = get_db()
        register_and_index(
            doc_path=file_path,
            text_content=text_content,
            kind=kind,
            db=db,
            cfg=cfg,
            title=title,
            work_id=work_id,
            provenance_source="studio",
            origin_id=origin_id,
            _prelinked_rel=prelinked_rel or None,
        )
    except Exception as exc:
        logger.debug("Studio registration failed (non-fatal): %s", exc)


def _rotate_outputs(out_dir: Path) -> None:
    """Delete oldest files in *out_dir* beyond _MAX_OUTPUTS."""
    try:
        files = sorted(
            (f for f in out_dir.iterdir() if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for old in files[_MAX_OUTPUTS:]:
            old.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning("Output rotation failed: %s", exc)


# ── Kokoro ONNX — lazy singleton ─────────────────────────────────────────────
# Loaded once on first TTS call; models (~500 MB) auto-download to HF cache.
_kokoro_lock = threading.Lock()
_kokoro_instance = None  # type: ignore[assignment]


def _get_kokoro():
    """Return a cached Kokoro instance, loading it on first call."""
    global _kokoro_instance
    if _kokoro_instance is not None:
        return _kokoro_instance
    with _kokoro_lock:
        if _kokoro_instance is not None:
            return _kokoro_instance
        try:
            from kokoro_onnx import Kokoro  # type: ignore[import]
            logger.info("Loading Kokoro ONNX model (first-run download may take a moment)…")
            _kokoro_instance = Kokoro("kokoro-v0_19.onnx", "voices.bin")
            logger.info("Kokoro ONNX ready.")
        except Exception as exc:
            logger.warning("Kokoro ONNX unavailable: %s", exc)
            _kokoro_instance = None
    return _kokoro_instance


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
                _tts_rel = _link_output_sync(Path(tmp.name))
                await asyncio.to_thread(_rotate_outputs, out_dir)
                from orivellum.api.executor import get_executor as _gex
                _gex().submit(
                    _register_output_bg, Path(tmp.name), body.text, "mp3",
                    f"TTS clip: {body.text[:60]}", prelinked_rel=_tts_rel,
                )
                return FileResponse(tmp.name, media_type="audio/mpeg",
                                    filename="speech.mp3")
    except Exception as exc:
        logger.info("AI server TTS unavailable (%s) — trying Kokoro ONNX", exc)

    # --- Strategy 2: Kokoro ONNX (local, human-quality, CPU-only) ---
    try:
        kokoro = _get_kokoro()
        if kokoro is not None:
            import numpy as np
            import soundfile as sf

            # Kokoro voice IDs match our builtin voice IDs directly
            kokoro_voice = body.voice if body.voice in {
                "af_heart", "af_bella", "am_adam", "bf_emma", "bm_george",
            } else "af_heart"

            samples, sample_rate = await asyncio.to_thread(
                kokoro.create,
                body.text,
                voice=kokoro_voice,
                speed=body.speed,
                lang="en-us",
            )

            out_dir = Path(cfg.data_dir) / "outputs"
            out_dir.mkdir(parents=True, exist_ok=True)

            wav_tmp = tempfile.NamedTemporaryFile(
                delete=False, dir=out_dir, suffix=".wav"
            )
            await asyncio.to_thread(sf.write, wav_tmp.name, samples, sample_rate)
            wav_tmp.close()

            mp3_tmp = tempfile.NamedTemporaryFile(
                delete=False, dir=out_dir, suffix=".mp3"
            )
            mp3_path = mp3_tmp.name
            mp3_tmp.close()

            ff = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-i", wav_tmp.name,
                 "-codec:a", "libmp3lame", "-q:a", "2", mp3_path],
                capture_output=True, timeout=60,
            )
            Path(wav_tmp.name).unlink(missing_ok=True)

            if ff.returncode == 0:
                _kok_rel = _link_output_sync(Path(mp3_path))
                await asyncio.to_thread(_rotate_outputs, out_dir)
                from orivellum.api.executor import get_executor as _gex
                _gex().submit(
                    _register_output_bg, Path(mp3_path), body.text, "mp3",
                    f"TTS clip: {body.text[:60]}", prelinked_rel=_kok_rel,
                )
                return FileResponse(mp3_path, media_type="audio/mpeg",
                                    filename="speech.mp3")
    except Exception as exc:
        logger.warning("Kokoro ONNX TTS failed (%s) — falling back to espeak-ng", exc)

    # --- Strategy 3: espeak-ng (always available offline, robotic fallback) ---
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

        result = await asyncio.to_thread(
            subprocess.run,
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

        ff = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-y", "-i", wav_path, "-codec:a", "libmp3lame", "-q:a", "4",
             mp3_path],
            capture_output=True, timeout=30,
        )
        Path(wav_path).unlink(missing_ok=True)

        from orivellum.api.executor import get_executor as _gex
        if ff.returncode == 0:
            _esp_mp3_rel = _link_output_sync(Path(mp3_path))
            await asyncio.to_thread(_rotate_outputs, out_dir)
            _gex().submit(
                _register_output_bg, Path(mp3_path), body.text, "mp3",
                f"TTS clip: {body.text[:60]}", prelinked_rel=_esp_mp3_rel,
            )
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
            await asyncio.to_thread(
                subprocess.run,
                ["espeak-ng", "-v", espeak_voice, "-s", str(wpm), "-w", wav_path2,
                 body.text],
                capture_output=True, timeout=30,
            )
            # Hard-link the WAV before rotation so it survives the rolling window.
            _esp_wav_rel = _link_output_sync(Path(wav_path2))
            await asyncio.to_thread(_rotate_outputs, out_dir)
            _gex().submit(
                _register_output_bg, Path(wav_path2), body.text, "wav",
                f"TTS clip: {body.text[:60]}", prelinked_rel=_esp_wav_rel,
            )
            return FileResponse(wav_path2, media_type="audio/wav",
                                filename="speech.wav")

    except FileNotFoundError:
        import sys as _sys
        if _sys.platform == "win32":
            install_hint = (
                "espeak-ng is not installed. "
                "Run scripts\\setup-windows.ps1 to install it automatically, "
                "or download manually from https://github.com/espeak-ng/espeak-ng/releases"
            )
        else:
            install_hint = "espeak-ng is not installed. Run: nix-env -iA nixpkgs.espeak-ng"
        raise HTTPException(503, {
            "detail": "All TTS backends unavailable",
            "service": "tts",
            "strategies_tried": ["ai_server", "kokoro_onnx", "espeak-ng"],
            "failed_strategy": "espeak-ng",
            "reason": install_hint,
        })
    except Exception as exc:
        logger.error("TTS espeak-ng failed: %s", exc)
        raise HTTPException(503, {
            "detail": "All TTS backends failed",
            "service": "tts",
            "strategies_tried": ["ai_server", "kokoro_onnx", "espeak-ng"],
            "failed_strategy": "espeak-ng",
            "reason": str(exc),
        })


# ── Text segmentation helper ──────────────────────────────────────────────────

def _split_text_into_segments(text: str, max_chars: int = 1500) -> list[str]:
    """Split text at paragraph/sentence boundaries, targeting max_chars per segment."""
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    segments: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            # Split long paragraph at sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                if current and len(current) + len(sent) + 1 > max_chars:
                    segments.append(current.strip())
                    current = ""
                current += (" " if current else "") + sent
        else:
            if current and len(current) + len(para) + 2 > max_chars:
                segments.append(current.strip())
                current = ""
            current += ("\n\n" if current else "") + para
    if current.strip():
        segments.append(current.strip())
    return [s for s in segments if s]


# ── Document-to-Audiobook ─────────────────────────────────────────────────────

class DocumentTTSRequest(BaseModel):
    doc_id: str
    voice: str = "af_heart"
    speed: float = 1.0
    max_segments: int = 60  # cap at ~90 000 chars / ~1 hour of reading
    return_url: bool = False  # when True, return JSON {ok, path} instead of FileResponse (for mobile)


@router.post("/studio/tts/document")
def synthesize_document(body: DocumentTTSRequest):
    """Convert an entire library document to an audiobook MP3.

    Fetches all extracted text chunks for *doc_id*, joins them, splits at
    paragraph/sentence boundaries, synthesises each segment with espeak-ng (or
    the configured AI TTS endpoint), then concatenates everything into a single
    MP3 via ffmpeg and saves it to the outputs directory.
    """
    db  = get_db()
    cfg = get_config()

    # ── Validate document ──────────────────────────────────────────────────────
    doc = db.get_document(body.doc_id)
    if not doc:
        raise HTTPException(404, f"Document {body.doc_id!r} not found")
    if doc.get("readiness") not in ("ready", "error"):
        raise HTTPException(422, "Document has not been fully processed yet. "
                                  "Wait until it shows as 'ready' in the Library.")

    # ── Fetch full text from chunks ───────────────────────────────────────────
    with db._lock:
        rows = db._conn.execute(
            "SELECT text FROM chunks WHERE doc_id=? ORDER BY page, rowid",
            (body.doc_id,),
        ).fetchall()

    if not rows:
        raise HTTPException(422, "No extracted text found for this document. "
                                  "The document may not have been processed yet.")

    full_text = "\n\n".join(r["text"] for r in rows)
    segments  = _split_text_into_segments(full_text)[:body.max_segments]

    if not segments:
        raise HTTPException(422, "Could not extract readable text from this document.")

    out_dir = Path(cfg.data_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    espeak_voice = _ESPEAK_VOICE_MAP.get(body.voice, "en+f4")
    wpm          = max(80, min(400, int(175 * body.speed)))
    kokoro_voice = body.voice if body.voice in {
        "af_heart", "af_bella", "am_adam", "bf_emma", "bm_george",
    } else "af_heart"

    wav_paths: list[Path] = []
    tmp_dir   = Path(tempfile.mkdtemp())

    # ── Determine best available TTS engine once ──────────────────────────────
    # Priority: 1) AI server  2) Kokoro ONNX  3) espeak-ng (robotic fallback)
    ai_ok = False
    try:
        import httpx
        probe = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
        ai_ok = probe.status_code == 200
    except Exception:
        ai_ok = False

    kokoro_engine = None if ai_ok else _get_kokoro()   # skip Kokoro if AI server is up

    try:
        import soundfile as _sf
        import numpy as _np
    except ImportError:
        _sf = None  # type: ignore[assignment]

    try:
        for idx, seg in enumerate(segments):
            wav_path = tmp_dir / f"seg_{idx:04d}.wav"
            synthesised = False

            # Strategy 1: AI server TTS
            if ai_ok:
                try:
                    import httpx as _hx
                    r = _hx.post(
                        f"{cfg.serving.base_url}/audio/speech",
                        json={"model": "tts-1", "input": seg,
                              "voice": body.voice, "response_format": "wav",
                              "speed": body.speed},
                        timeout=60,
                    )
                    if r.status_code == 200:
                        wav_path.write_bytes(r.content)
                        synthesised = True
                except Exception:
                    pass

            # Strategy 2: Kokoro ONNX (human-quality, local)
            if not synthesised and kokoro_engine is not None and _sf is not None:
                try:
                    samples, sample_rate = kokoro_engine.create(
                        seg, voice=kokoro_voice, speed=body.speed, lang="en-us",
                    )
                    _sf.write(str(wav_path), samples, sample_rate)
                    synthesised = True
                except Exception as ke:
                    logger.warning("Kokoro failed on segment %d: %s", idx, ke)

            # Strategy 3: espeak-ng (always-available robotic fallback)
            if not synthesised:
                res = subprocess.run(
                    ["espeak-ng", "-v", espeak_voice, "-s", str(wpm),
                     "-w", str(wav_path), seg],
                    capture_output=True, text=True, timeout=60,
                )
                if res.returncode != 0:
                    raise RuntimeError(f"espeak-ng failed on segment {idx}: {res.stderr}")

            wav_paths.append(wav_path)

        # ── Concatenate all WAVs → single high-quality MP3 ────────────────────
        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in wav_paths), encoding="utf-8"
        )

        safe_title = re.sub(r'[^\w\-]', '_', (doc.get("title") or "audiobook"))[:60]
        mp3_name   = f"{safe_title}_{uuid.uuid4().hex[:6]}.mp3"
        mp3_path   = out_dir / mp3_name

        ff = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list),
             "-codec:a", "libmp3lame", "-q:a", "2",   # q:a 2 = ~190 kbps, near-transparent
             str(mp3_path)],
            capture_output=True, timeout=300,
        )
        if ff.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {ff.stderr.decode()[:300]}")

        # Hard-link the audiobook into the library BEFORE rotation so the file
        # survives the rolling 50-output window regardless of when the background
        # thread starts.  This is the core of the durable Save invariant.
        _ab_rel = _link_output_sync(mp3_path)
        _rotate_outputs(out_dir)

        # Amendment-1: register the audiobook as a searchable library document.
        # Use the source document's text as content so "find the audiobook for X"
        # resolves via the audio file's own library entry.
        doc_title = doc.get("title") or "audiobook"
        from orivellum.api.executor import get_executor as _gex
        _gex().submit(
            _register_output_bg, mp3_path, full_text[:8000], "mp3",
            f"Audiobook: {doc_title}", prelinked_rel=_ab_rel,
            origin_id=body.doc_id,
        )

        if body.return_url:
            # Mobile clients can't play a streaming FileResponse directly;
            # return the serve path so they can create an authenticated player.
            rel = str(mp3_path.relative_to(out_dir))
            return {"ok": True, "path": rel, "filename": mp3_name}
        return FileResponse(str(mp3_path), media_type="audio/mpeg",
                            filename=mp3_name)

    except FileNotFoundError:
        import sys as _sys
        hint = (
            "espeak-ng is not installed. Run scripts\\setup-windows.ps1 to install it."
            if _sys.platform == "win32"
            else "espeak-ng is not installed. Run: nix-env -iA nixpkgs.espeak-ng"
        )
        raise HTTPException(503, hint)
    except Exception as exc:
        logger.error("Document TTS failed: %s", exc)
        raise HTTPException(500, f"Audiobook generation failed: {exc}")
    finally:
        # Clean up temp WAVs
        for p in wav_paths:
            p.unlink(missing_ok=True)
        try:
            (tmp_dir / "concat.txt").unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass


# ── Outputs ───────────────────────────────────────────────────────────────────

@router.get("/studio/outputs")
def list_outputs():
    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    if not out_dir.exists():
        return {"outputs": [], "count": 0}
    files = sorted(out_dir.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:200]:
        if not f.is_file():
            continue
        sz = f.stat().st_size
        if sz == 0:  # skip empty temp files
            continue
        suffix = f.suffix.lower()
        if suffix in {".wav", ".mp3", ".m4a", ".m4b", ".ogg"}:
            kind = "audio"
        elif suffix in {".mp4", ".webm", ".mov"}:
            kind = "video"
        elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            kind = "image"
        else:
            kind = "file"
        rel = str(f.relative_to(out_dir))
        result.append({
            "name": f.name,
            "path": rel,
            "size_bytes": sz,
            "kind": kind,
            "mtime": f.stat().st_mtime,
        })
        if len(result) >= 100:
            break
    return {"outputs": result, "count": len(result)}


@router.get("/studio/outputs/serve")
def serve_output(path: str):
    """Stream an output file for playback or download.  `path` is relative to the outputs dir."""
    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    # Sanitise — prevent path traversal
    try:
        target = (out_dir / path).resolve()
        target.relative_to(out_dir.resolve())
    except (ValueError, Exception):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    suffix = target.suffix.lower()
    mime_map = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
        ".m4a": "audio/mp4", ".m4b": "audio/mp4",
        ".mp4": "video/mp4", ".webm": "video/webm",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }
    media_type = mime_map.get(suffix, "application/octet-stream")
    return FileResponse(str(target), media_type=media_type,
                        filename=target.name,
                        headers={"Content-Disposition": f'attachment; filename="{target.name}"'})


@router.delete("/studio/outputs/archive")
def archive_output(path: str):
    """Delete (archive) an output file."""
    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    try:
        target = (out_dir / path).resolve()
        target.relative_to(out_dir.resolve())
    except (ValueError, Exception):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="Output not found")
    target.unlink()
    # Remove empty parent dirs up to out_dir
    try:
        parent = target.parent
        while parent != out_dir and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except Exception:
        pass
    return {"ok": True}


# ── Image generation ──────────────────────────────────────────────────────────
# Tries backends in order:
#   1. User-configured URL (DB setting: image_gen_url)
#   2. Automatic1111 / SD WebUI   http://localhost:7860
#   3. ComfyUI                    http://localhost:8188
#   4. OpenAI-compatible endpoint (same base_url as chat, /images/generations)
# Each strategy is tried; first 200 wins. Errors are logged at DEBUG level.

class ImageGenRequest(BaseModel):
    prompt: str
    width: int = 512
    height: int = 512
    negative_prompt: str = ""
    steps: int = 20


async def _try_openai_compat(client, base_url: str, body: ImageGenRequest) -> dict | None:
    """OpenAI /images/generations — returns b64_json or url."""
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/images/generations",
            json={"prompt": body.prompt, "n": 1,
                  "size": f"{body.width}x{body.height}",
                  "response_format": "b64_json"},
            timeout=90,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        logger.debug("openai-compat image gen failed (%s): %s", base_url, exc)
    return None


async def _try_a1111(client, body: ImageGenRequest) -> dict | None:
    """Automatic1111 / SD WebUI txt2img API."""
    try:
        r = await client.post(
            "http://localhost:7860/sdapi/v1/txt2img",
            json={"prompt": body.prompt,
                  "negative_prompt": body.negative_prompt or "",
                  "width": body.width, "height": body.height,
                  "steps": body.steps, "sampler_name": "Euler a"},
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            images = data.get("images", [])
            if images:
                return {"data": [{"b64_json": images[0]}]}
    except Exception as exc:
        logger.debug("A1111 image gen failed: %s", exc)
    return None


def _is_comfyui_url(url: str) -> bool:
    """Return True when a URL looks like a ComfyUI endpoint.

    Heuristics: port 8188, or the string 'comfyui' in the URL.
    This lets users paste http://172.20.205.199:8188 into the custom URL field
    and have it automatically routed to the ComfyUI API rather than the
    OpenAI-compat /images/generations endpoint (which ComfyUI does not support).
    """
    low = url.lower()
    return ":8188" in low or "comfyui" in low


async def _try_comfyui(client, body: ImageGenRequest,
                       base_url: str = "http://localhost:8188") -> dict | None:
    """ComfyUI — txt2img via the /prompt API.

    Works with any ComfyUI instance; ``base_url`` defaults to localhost but
    accepts any http://host:port (e.g. http://172.20.205.199:8188 for WSL
    rootless-podman setups where localhost-forwarding is not available).

    Loads the checkpoint named in the DB setting ``comfyui_checkpoint``
    (default: v1-5-pruned-emaonly.ckpt) so users can switch models without
    editing code.
    """
    base = base_url.rstrip("/")
    try:
        import uuid as _uuid
        import asyncio

        # Resolve checkpoint from DB setting (best-effort; never blocks gen)
        checkpoint = "v1-5-pruned-emaonly.ckpt"
        try:
            from orivellum.api._deps import get_db as _get_db
            _db = _get_db()
            ckpt_setting = _db.get_setting("comfyui_checkpoint", "")
            if ckpt_setting:
                checkpoint = ckpt_setting
            else:
                # Auto-detect: ask ComfyUI which checkpoints are installed
                obj_resp = await client.get(f"{base}/object_info/CheckpointLoaderSimple",
                                            timeout=3)
                if obj_resp.status_code == 200:
                    info = obj_resp.json()
                    avail = (info.get("CheckpointLoaderSimple", {})
                             .get("input", {}).get("required", {})
                             .get("ckpt_name", [[]])[0])
                    if avail:
                        checkpoint = avail[0]
        except Exception:
            pass

        client_id = str(_uuid.uuid4())
        workflow = {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": 0, "steps": body.steps, "cfg": 7,
                "sampler_name": "euler", "scheduler": "normal",
                "denoise": 1, "model": ["4", 0],
                "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": checkpoint}},
            "5": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": body.width, "height": body.height, "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": body.prompt, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": body.negative_prompt or "blurry, low quality",
                             "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage",
                  "inputs": {"filename_prefix": "orivellum", "images": ["8", 0]}},
        }
        r = await client.post(
            f"{base}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=10,
        )
        if r.status_code != 200:
            logger.debug("ComfyUI /prompt rejected (%s): %s", r.status_code, r.text[:200])
            return None
        prompt_id = r.json().get("prompt_id")
        if not prompt_id:
            return None
        # Poll for result (max 120s — large models can be slow)
        for _ in range(40):
            await asyncio.sleep(3)
            hr = await client.get(f"{base}/history/{prompt_id}", timeout=5)
            if hr.status_code == 200:
                hist = hr.json().get(prompt_id, {})
                outputs = hist.get("outputs", {})
                for node_out in outputs.values():
                    for img in node_out.get("images", []):
                        ir = await client.get(
                            f"{base}/view?filename={img['filename']}"
                            f"&subfolder={img.get('subfolder','')}&type={img.get('type','output')}",
                            timeout=15)
                        if ir.status_code == 200:
                            import base64 as _b64
                            b64 = _b64.b64encode(ir.content).decode()
                            return {"data": [{"b64_json": b64}]}
    except Exception as exc:
        logger.debug("ComfyUI image gen failed (%s): %s", base_url, exc)
    return None


def _persist_generated_image(result: dict, cfg, prompt: str = "") -> dict:
    """Save a generated image (b64_json) into the outputs dir so it appears in
    Recent Outputs.  Also registers it as a searchable library document with
    the generation prompt as the searchable text (Amendment-1 invariant).
    Best-effort — the response is returned unchanged on failure."""
    try:
        item = (result.get("data") or [{}])[0]
        b64 = item.get("b64_json")
        if not b64:
            return result
        import base64 as _b64
        out_dir = Path(cfg.data_dir) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = f"image_{uuid.uuid4().hex[:8]}.png"
        img_path = out_dir / name
        img_path.write_bytes(_b64.b64decode(b64))
        # Hard-link into library BEFORE rotation: durable Save invariant.
        _img_rel = _link_output_sync(img_path)
        _rotate_outputs(out_dir)
        item["output_path"] = name

        # Amendment-1: register image with prompt as searchable caption so
        # "find the image I made of X" resolves via semantic / keyword search.
        caption = prompt or item.get("revised_prompt") or "generated image"
        from orivellum.api.executor import get_executor as _gex
        _gex().submit(
            _register_output_bg, img_path, caption, "png",
            f"Image: {caption[:60]}", prelinked_rel=_img_rel,
        )
    except Exception as exc:
        logger.warning("Could not persist generated image to outputs: %s", exc)
    return result


def _is_ssrf_url(url: str) -> bool:
    """Return True when the URL points to a private/loopback address (SSRF risk).

    Blocks: loopback (127.x.x.x, ::1), RFC-1918 private ranges, link-local,
    and the metadata service (169.254.169.254).  Hostnames that resolve to
    blocked IPs are NOT probed here — add DNS resolution if needed.
    """
    import urllib.parse as _up
    import ipaddress as _ip
    try:
        parsed = _up.urlparse(url)
        host = parsed.hostname or ""
        # Resolve numeric IP addresses
        addr = _ip.ip_address(host)
        return (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_multicast
            or str(addr) == "169.254.169.254"  # AWS/GCP metadata
        )
    except ValueError:
        # Not an IP — allow hostnames (localhost is a special case)
        return host.lower() in ("localhost", "metadata.google.internal")
    except Exception:
        return False


@router.post("/studio/image")
async def generate_image(body: ImageGenRequest):
    db = get_db()
    cfg = get_config()
    import httpx

    async with httpx.AsyncClient() as client:
        # 1. User-configured URL — detect backend type automatically.
        #    ComfyUI URLs (port 8188 or "comfyui" in URL) are routed to
        #    _try_comfyui so WSL/remote instances work without OpenAI compat.
        custom_url = db.get_setting("image_gen_url", "").strip()
        if custom_url:
            if _is_ssrf_url(custom_url):
                raise HTTPException(
                    400,
                    "Image generation URL points to a private/loopback address. "
                    "Enter a publicly-reachable URL (e.g. http://192.168.1.x:8188 for LAN use)."
                )
            if _is_comfyui_url(custom_url):
                result = await _try_comfyui(client, body, base_url=custom_url)
            else:
                result = await _try_openai_compat(client, custom_url, body)
                if not result:
                    # Could be A1111 with its own API format
                    result = await _try_a1111(client, body)
            if result:
                return await asyncio.to_thread(_persist_generated_image, result, cfg, prompt=body.prompt)

        # 2. Automatic1111 (SD WebUI) — localhost:7860
        result = await _try_a1111(client, body)
        if result:
            return await asyncio.to_thread(_persist_generated_image, result, cfg, prompt=body.prompt)

        # 3. ComfyUI — localhost:8188
        result = await _try_comfyui(client, body, base_url="http://localhost:8188")
        if result:
            return await asyncio.to_thread(_persist_generated_image, result, cfg, prompt=body.prompt)

        # 4. OpenAI-compatible endpoint on the chat AI server
        result = await _try_openai_compat(client, cfg.serving.base_url, body)
        if result:
            return await asyncio.to_thread(_persist_generated_image, result, cfg, prompt=body.prompt)

    raise HTTPException(
        503,
        "Image generation unavailable. Set your ComfyUI address "
        "(e.g. http://172.20.205.199:8188) in System Settings → Image Generation, "
        "or install Automatic1111 at http://localhost:7860.",
    )


_STATUS_PROBE_TIMEOUT = 2.0   # per-URL connect timeout (seconds)
_STATUS_GLOBAL_TIMEOUT = 5.0  # hard wall-clock deadline for the entire status check


def _url_probe(url: str) -> tuple[bool, int | None]:
    """Probe a single URL; return (reachable, latency_ms). Never raises."""
    import time
    import urllib.request as _ur
    t0 = time.monotonic()
    try:
        _ur.urlopen(url, timeout=_STATUS_PROBE_TIMEOUT).close()
        return True, round((time.monotonic() - t0) * 1000)
    except Exception:
        return False, None


@router.get("/studio/status")
def studio_status():
    """Unified probe of all Studio services (TTS, image gen, OCR).

    All network probes run concurrently in a thread pool so the total wall-clock
    time is bounded by _STATUS_GLOBAL_TIMEOUT (5 s) rather than the sum of
    individual timeouts.  Each probe uses _STATUS_PROBE_TIMEOUT (2 s) for its
    own connect timeout.  The global deadline is enforced via Future.result()
    timeouts that shrink as the clock ticks.
    """
    import concurrent.futures as _cf
    import importlib.util
    import shutil
    import time
    from datetime import datetime, timezone

    cfg = get_config()
    db = get_db()

    # Resolve probe URLs up-front (no I/O)
    ai_tts_url = cfg.serving.base_url.rstrip("/") + "/models"
    ai_img_url = cfg.serving.base_url.replace("/api/v1", "").rstrip("/")
    custom_url = db.get_setting("image_gen_url", "").strip()
    custom_stats_url = (custom_url.rstrip("/") + "/system_stats") if custom_url else ""

    # Futures dict — all probes fired in parallel
    deadline = time.monotonic() + _STATUS_GLOBAL_TIMEOUT
    results: dict[str, object] = {}

    pool = _cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="studio-probe")
    try:
        futs: dict[str, _cf.Future] = {
            "ai_tts":    pool.submit(_url_probe, ai_tts_url),
            "ai_img":    pool.submit(_url_probe, ai_img_url),
            "a1111":     pool.submit(_url_probe, "http://localhost:7860"),
            "comfy":     pool.submit(_url_probe, "http://localhost:8188"),
            "tesseract": pool.submit(_probe_tesseract_ok),
        }
        if custom_url:
            futs["custom"] = pool.submit(_url_probe, custom_url)
            if _is_comfyui_url(custom_url):
                futs["custom_stats"] = pool.submit(_url_probe, custom_stats_url)

        for key, fut in futs.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[key] = fut.result(timeout=remaining)
            except Exception:
                results[key] = (False, None) if key != "tesseract" else False
    finally:
        # Do NOT wait for threads still blocked on their TCP connect timeout.
        # Threads will finish within _STATUS_PROBE_TIMEOUT (2 s) on their own.
        pool.shutdown(wait=False)

    def _get(key: str, default=None):
        return results.get(key, default)

    # ── TTS strategies ────────────────────────────────────────────────────────
    ai_tts_ok, ai_ms = _get("ai_tts", (False, None))
    kokoro_ok = importlib.util.find_spec("kokoro_onnx") is not None
    espeak_ok = bool(shutil.which("espeak-ng"))

    tts_strategies = [
        {"name": "AI Server",   "key": "ai_server",   "available": ai_tts_ok, "latency_ms": ai_ms},
        {"name": "Kokoro ONNX", "key": "kokoro_onnx",  "available": kokoro_ok, "latency_ms": None},
        {"name": "espeak-ng",   "key": "espeak_ng",    "available": espeak_ok, "latency_ms": None},
    ]
    best_tts = next((s["name"] for s in tts_strategies if s["available"]), None)

    # ── Image gen backends ────────────────────────────────────────────────────
    img_backends: list[dict] = []
    if custom_url:
        if _is_comfyui_url(custom_url):
            custom_ok = bool(_get("custom_stats", (False, None))[0] or  # type: ignore[index]
                              _get("custom", (False, None))[0])            # type: ignore[index]
            img_backends.append({"name": "ComfyUI (custom)", "url": custom_url, "online": custom_ok})
        else:
            img_backends.append({"name": "Custom", "url": custom_url,
                                  "online": bool(_get("custom", (False, None))[0])})  # type: ignore[index]

    a1111_ok, _ = _get("a1111", (False, None))
    comfy_ok, _ = _get("comfy", (False, None))
    ai_img_ok, _ = _get("ai_img", (False, None))

    if a1111_ok:
        img_backends.append({"name": "Automatic1111", "url": "http://localhost:7860", "online": True})
    if comfy_ok:
        img_backends.append({"name": "ComfyUI", "url": "http://localhost:8188", "online": True})
    img_backends.append({"name": "AI Server", "url": cfg.serving.base_url, "online": bool(ai_img_ok)})
    img_any = any(b["online"] for b in img_backends)

    # ── OCR ───────────────────────────────────────────────────────────────────
    tess_ok = bool(_get("tesseract", False))
    pillow_ok = importlib.util.find_spec("PIL") is not None
    pytesseract_ok = importlib.util.find_spec("pytesseract") is not None
    ocr_missing = ([] if tess_ok else ["tesseract binary"]) + \
                  ([] if pillow_ok else ["Pillow"]) + \
                  ([] if pytesseract_ok else ["pytesseract"])

    return {
        "tts": {
            "available": best_tts is not None,
            "best_strategy": best_tts,
            "strategies": tts_strategies,
        },
        "image_gen": {
            "available": img_any,
            "backends": img_backends,
        },
        "ocr": {
            "available": tess_ok and pillow_ok and pytesseract_ok,
            "engine": "tesseract" if tess_ok else None,
            "missing": ocr_missing,
        },
        "last_checked": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/studio/image-status")
def image_gen_status():
    """Quick probe to tell the UI which image backend (if any) is reachable."""
    import urllib.request as _ur
    db = get_db()
    cfg = get_config()

    def _probe(url: str) -> bool:
        try:
            _ur.urlopen(url, timeout=2).close()
            return True
        except Exception:
            return False

    custom = db.get_setting("image_gen_url", "").strip()
    backends = []
    if custom:
        # Probe ComfyUI via its /system_stats endpoint (more reliable than root)
        if _is_comfyui_url(custom):
            probe_url = custom.rstrip("/") + "/system_stats"
            online = _probe(probe_url) or _probe(custom)
            backends.append({"name": "ComfyUI (custom)", "url": custom, "online": online})
        else:
            backends.append({"name": "Custom", "url": custom, "online": _probe(custom)})
    if _probe("http://localhost:7860"):
        backends.append({"name": "Automatic1111", "url": "http://localhost:7860", "online": True})
    if _probe("http://localhost:8188"):
        backends.append({"name": "ComfyUI", "url": "http://localhost:8188", "online": True})
    backends.append({
        "name": "AI Server", "url": cfg.serving.base_url,
        "online": _probe(cfg.serving.base_url.replace("/api/v1", "")),
    })
    return {"backends": backends, "any_online": any(b["online"] for b in backends)}


# ── OCR helpers ───────────────────────────────────────────────────────────────

_NIX_STORE_SCAN_MAX = 200  # max directories to walk in /nix/store


def _probe_tesseract_ok() -> bool:
    """Return True if the tesseract binary is reachable in this environment.

    Extracted as a module-level function so tests can mock it cleanly without
    having to stub the entire nix/store filesystem walk.

    Scan order: PATH → bash login shell → /nix/store (bounded to
    _NIX_STORE_SCAN_MAX entries to avoid unbounded iteration on large stores).
    """
    import shutil as _sh
    import sys as _sys

    if _sh.which("tesseract"):
        return True
    if _sys.platform == "win32":
        import pathlib as _pl
        win_default = _pl.Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        return win_default.is_file()
    # Unix/NixOS: ask the login shell first (cheap)
    try:
        r = __import__("subprocess").run(
            ["bash", "-lc", "which tesseract"],
            capture_output=True, text=True, timeout=3,
        )
        if r.stdout.strip():
            return True
    except Exception:
        pass
    # Bounded /nix/store scan — stop after _NIX_STORE_SCAN_MAX entries
    import pathlib as _pl
    nix = _pl.Path("/nix/store")
    if nix.exists():
        for _i, d in enumerate(nix.iterdir()):
            if _i >= _NIX_STORE_SCAN_MAX:
                break
            if "tesseract" in d.name and (d / "bin" / "tesseract").is_file():
                return True
    return False


class OCRRequest(BaseModel):
    content_b64: str
    filename: str = "image.png"


def _probe_tesseract_cmd() -> None:
    """Ensure pytesseract can find the tesseract binary (NixOS/Replit path fix)."""
    import shutil, subprocess as _sp, pytesseract as _pt
    from pathlib import Path as _P

    if shutil.which("tesseract"):
        return

    import sys as _sys
    if _sys.platform != "win32":
        # On Unix/NixOS ask the login shell — it has a broader PATH than the API process
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
    else:
        # Common Windows install location from the UB-Mannheim installer
        win_default = _P(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if win_default.is_file():
            _pt.pytesseract.tesseract_cmd = str(win_default)
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
