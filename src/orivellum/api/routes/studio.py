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
from fastapi.responses import FileResponse, StreamingResponse
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


# ── Audio duration cache ───────────────────────────────────────────────────────
# Keyed by (absolute_path_str, mtime_ns) so stale entries auto-invalidate when
# a file changes.  Process-lifetime cache — small enough that eviction isn't
# needed (at most _MAX_OUTPUTS entries).
_OUTPUT_DURATION_CACHE: dict[tuple[str, int], "float | None"] = {}


def _probe_duration(path: Path) -> "float | None":
    """Return audio/video duration in seconds via ffprobe.

    Non-fatal — returns None when ffprobe is absent or the file is unreadable.
    Results are cached by (path, mtime_ns) so repeated list calls are free.
    """
    try:
        key = (str(path), path.stat().st_mtime_ns)
    except OSError:
        return None
    if key in _OUTPUT_DURATION_CACHE:
        return _OUTPUT_DURATION_CACHE[key]
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_entries", "format=duration",
                str(path),
            ],
            capture_output=True,
            timeout=3,
        )
        dur: "float | None" = None
        if r.returncode == 0:
            import json as _jmod
            data = _jmod.loads(r.stdout)
            raw = data.get("format", {}).get("duration")
            if raw is not None:
                try:
                    dur = float(raw)
                except (TypeError, ValueError):
                    dur = None
    except Exception:
        dur = None
    _OUTPUT_DURATION_CACHE[key] = dur
    return dur


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


def _is_kokoro_loaded() -> bool:
    """Return True only when the Kokoro ONNX model is actually loaded in memory.

    Distinguishes "package installed but model files absent/failed to load"
    (returns False) from "fully operational" (returns True).

    The status endpoint uses this instead of importlib.util.find_spec so that
    it accurately reflects whether neural voice synthesis is live — not just
    whether the Python wheel is present.
    """
    return _kokoro_instance is not None


# ── Voices ────────────────────────────────────────────────────────────────────

# espeak-ng fallback map (robotic but always available)
_ESPEAK_VOICE_MAP: dict[str, str] = {
    "af_heart": "en+f4", "af_bella": "en+f1", "af_nova": "en+f3",
    "af_alloy": "en+f5", "af_sarah": "en+f4", "af_sky": "en+f2",
    "af_jessica": "en+f3", "af_kore": "en+f4", "af_nicole": "en+f1",
    "af_aoede": "en+f3", "af_river": "en+f5",
    "am_adam": "en+m1", "am_echo": "en+m4", "am_eric": "en+m3",
    "am_fenrir": "en+m5", "am_liam": "en+m2", "am_michael": "en+m1",
    "am_onyx": "en+m5", "am_puck": "en+m2", "am_santa": "en+m3",
    "bf_emma": "en+f2", "bf_alice": "en+f3",
    "bf_isabella": "en+f1", "bf_lily": "en+f2",
    "bm_george": "en+m3", "bm_daniel": "en+m2", "bm_fable": "en+m4",
    "bm_lewis": "en+m5",
}

# OpenAI-compatible voice map — groups all 28 catalog IDs by tonal similarity
# to the six OpenAI voice names.  Used when routing to an /audio/speech endpoint.
_OPENAI_VOICE_MAP: dict[str, str] = {
    # American Female
    "af_heart": "nova",    "af_bella": "nova",    "af_nova": "nova",
    "af_alloy": "alloy",   "af_sarah": "nova",    "af_sky": "shimmer",
    "af_jessica": "alloy", "af_kore": "shimmer",  "af_nicole": "nova",
    "af_aoede": "shimmer", "af_river": "alloy",
    # American Male
    "am_adam": "onyx",   "am_echo": "echo",   "am_eric": "echo",
    "am_fenrir": "onyx", "am_liam": "fable",  "am_michael": "echo",
    "am_onyx": "onyx",   "am_puck": "fable",  "am_santa": "echo",
    # British Female
    "bf_emma": "shimmer", "bf_alice": "shimmer",
    "bf_isabella": "nova", "bf_lily": "shimmer",
    # British Male
    "bm_george": "fable", "bm_daniel": "fable",
    "bm_fable": "fable",  "bm_lewis": "fable",
}

# Standard sample sentence — tests prosody, pacing, and emotional register
_SAMPLE_SENTENCE = (
    "In the beginning was the word — and the word carried the weight of all "
    "things yet to come. We remember not what was written, but how it was spoken."
)

# ── Full voice catalog with perceptual dimensions ─────────────────────────────
# Dimensions (1–10 scale):
#   warmth    — cold/clinical → warm/intimate
#   authority — soft/gentle → commanding/authoritative
#   gravitas  — light/bright → heavy/solemn
#   pace      — fast/urgent → slow/measured (higher = slower)
#   brightness — dark/rich → bright/clear
#   age       — youthful → elder
# Genre tags indicate best-fit content categories.
_VOICE_CATALOG: list[dict] = [
    # ── American Female ───────────────────────────────────────────────────────
    {
        "id": "af_heart", "name": "Heart", "accent": "american", "gender": "feminine",
        "description": (
            "Warm and intimate — feels like a close friend telling a personal story. "
            "Natural pauses and conversational rhythm that draws listeners in."
        ),
        "dimensions": {"warmth": 9, "authority": 5, "gravitas": 4, "pace": 5, "brightness": 7, "age": 5},
        "tags": ["literary fiction", "memoir", "spiritual", "romance"],
        "builtin": True, "engine": "kokoro",
    },
    {
        "id": "af_bella", "name": "Bella", "accent": "american", "gender": "feminine",
        "description": (
            "Bright and engaging with clear diction. Suits energetic prose "
            "and stories with forward momentum and optimistic energy."
        ),
        "dimensions": {"warmth": 7, "authority": 6, "gravitas": 3, "pace": 7, "brightness": 9, "age": 4},
        "tags": ["thriller", "young adult", "adventure", "commercial"],
        "builtin": True, "engine": "kokoro",
    },
    {
        "id": "af_nova", "name": "Nova", "accent": "american", "gender": "feminine",
        "description": (
            "Smooth and professional with natural warmth. The go-to for non-fiction, "
            "documentary narration, and authoritative storytelling."
        ),
        "dimensions": {"warmth": 6, "authority": 8, "gravitas": 6, "pace": 5, "brightness": 6, "age": 6},
        "tags": ["non-fiction", "documentary", "academic", "thriller"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_alloy", "name": "Alloy", "accent": "american", "gender": "feminine",
        "description": (
            "Neutral, clean, and precise. Excellent for texts requiring clarity "
            "above all else — instructional, academic, or technical content."
        ),
        "dimensions": {"warmth": 5, "authority": 7, "gravitas": 5, "pace": 6, "brightness": 6, "age": 5},
        "tags": ["academic", "news", "instructional", "documentary"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_sarah", "name": "Sarah", "accent": "american", "gender": "feminine",
        "description": (
            "Natural and unhurried storytelling voice with genuine warmth. "
            "Sounds like a gifted author reading their own work aloud."
        ),
        "dimensions": {"warmth": 8, "authority": 5, "gravitas": 5, "pace": 4, "brightness": 6, "age": 6},
        "tags": ["literary fiction", "memoir", "spiritual", "romance"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_sky", "name": "Sky", "accent": "american", "gender": "feminine",
        "description": (
            "Light and youthful with crystalline clarity. Perfect for whimsical prose "
            "and stories with an optimistic or magical tone."
        ),
        "dimensions": {"warmth": 7, "authority": 3, "gravitas": 2, "pace": 6, "brightness": 10, "age": 2},
        "tags": ["children", "young adult", "fantasy", "romance"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_jessica", "name": "Jessica", "accent": "american", "gender": "feminine",
        "description": (
            "Confident and measured — projects quiet authority without sounding remote. "
            "Ideal for mystery, suspense, and literary fiction with dark themes."
        ),
        "dimensions": {"warmth": 6, "authority": 8, "gravitas": 7, "pace": 4, "brightness": 5, "age": 7},
        "tags": ["mystery", "literary fiction", "thriller", "historical"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_kore", "name": "Kore", "accent": "american", "gender": "feminine",
        "description": (
            "Rich and theatrical with expressive emotional range. "
            "Handles dramatic peaks, mythological weight, and tense scenes with natural intensity."
        ),
        "dimensions": {"warmth": 7, "authority": 7, "gravitas": 7, "pace": 4, "brightness": 6, "age": 6},
        "tags": ["epic", "literary fiction", "mythology", "drama"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_nicole", "name": "Nicole", "accent": "american", "gender": "feminine",
        "description": (
            "Warm and engaging with a natural conversational quality. "
            "Listeners feel spoken to, not read at — excellent for personal narratives."
        ),
        "dimensions": {"warmth": 9, "authority": 4, "gravitas": 4, "pace": 5, "brightness": 7, "age": 5},
        "tags": ["memoir", "self-help", "romance", "literary fiction"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_aoede", "name": "Aoede", "accent": "american", "gender": "feminine",
        "description": (
            "Poetic and expressive with natural musicality — named after the muse of song. "
            "Suited for language-forward, lyrical, or spiritual prose."
        ),
        "dimensions": {"warmth": 8, "authority": 5, "gravitas": 6, "pace": 3, "brightness": 7, "age": 5},
        "tags": ["literary fiction", "poetry", "spiritual", "mythology"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "af_river", "name": "River", "accent": "american", "gender": "feminine",
        "description": (
            "Calm and unhurried — flows steadily through long passages without losing "
            "the listener's attention. Perfect for contemplative or meditative content."
        ),
        "dimensions": {"warmth": 7, "authority": 5, "gravitas": 6, "pace": 3, "brightness": 5, "age": 6},
        "tags": ["meditation", "spiritual", "literary fiction", "nature"],
        "builtin": False, "engine": "kokoro",
    },
    # ── American Male ─────────────────────────────────────────────────────────
    {
        "id": "am_adam", "name": "Adam", "accent": "american", "gender": "masculine",
        "description": (
            "Deep and authoritative with natural gravitas. The voice of a historian, "
            "a prophet, or a general — serious, commanding, and completely trustworthy."
        ),
        "dimensions": {"warmth": 5, "authority": 9, "gravitas": 8, "pace": 4, "brightness": 3, "age": 7},
        "tags": ["epic", "historical", "thriller", "non-fiction", "spiritual"],
        "builtin": True, "engine": "kokoro",
    },
    {
        "id": "am_echo", "name": "Echo", "accent": "american", "gender": "masculine",
        "description": (
            "Broadcast-quality clarity with neutral authority. Clean, dependable, "
            "and never intrusive — the professional narrator."
        ),
        "dimensions": {"warmth": 5, "authority": 8, "gravitas": 6, "pace": 5, "brightness": 5, "age": 6},
        "tags": ["non-fiction", "documentary", "news", "academic"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_eric", "name": "Eric", "accent": "american", "gender": "masculine",
        "description": (
            "Warm and conversational with a natural storytelling cadence. "
            "Approachable authority — thinks out loud in a way that sounds genuine."
        ),
        "dimensions": {"warmth": 8, "authority": 6, "gravitas": 5, "pace": 5, "brightness": 5, "age": 5},
        "tags": ["memoir", "literary fiction", "thriller", "self-help"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_fenrir", "name": "Fenrir", "accent": "american", "gender": "masculine",
        "description": (
            "Deeply resonant with dramatic gravitas — named after the great wolf. "
            "Powerful, ancient, and absolutely commanding. Best for mythological or epic material."
        ),
        "dimensions": {"warmth": 3, "authority": 10, "gravitas": 10, "pace": 3, "brightness": 1, "age": 9},
        "tags": ["epic", "mythology", "thriller", "horror", "spiritual"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_liam", "name": "Liam", "accent": "american", "gender": "masculine",
        "description": (
            "Youthful and energetic — narrates with forward momentum and genuine "
            "enthusiasm for the story. Ideal for adventure and action-driven prose."
        ),
        "dimensions": {"warmth": 7, "authority": 4, "gravitas": 2, "pace": 8, "brightness": 7, "age": 2},
        "tags": ["young adult", "adventure", "thriller", "science fiction"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_michael", "name": "Michael", "accent": "american", "gender": "masculine",
        "description": (
            "Authoritative and neutral — sounds like a seasoned professional. "
            "Clear pronunciation, consistent pacing, never draws attention to itself."
        ),
        "dimensions": {"warmth": 5, "authority": 8, "gravitas": 7, "pace": 5, "brightness": 4, "age": 7},
        "tags": ["non-fiction", "historical", "documentary", "academic"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_onyx", "name": "Onyx", "accent": "american", "gender": "masculine",
        "description": (
            "Deep, rich, and powerful — the richest bass register in the catalog. "
            "Commands attention the moment it speaks. Built for gravitas."
        ),
        "dimensions": {"warmth": 4, "authority": 10, "gravitas": 10, "pace": 3, "brightness": 1, "age": 8},
        "tags": ["epic", "thriller", "historical", "mystery", "spiritual"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_puck", "name": "Puck", "accent": "american", "gender": "masculine",
        "description": (
            "Energetic and playful with surprising depth — moves between comedy "
            "and earnestness naturally. Perfect for young adult, adventure, and wit-driven stories."
        ),
        "dimensions": {"warmth": 8, "authority": 4, "gravitas": 3, "pace": 7, "brightness": 8, "age": 3},
        "tags": ["young adult", "adventure", "comedy", "fantasy"],
        "builtin": False, "engine": "kokoro",
    },
    # ── British Female ────────────────────────────────────────────────────────
    {
        "id": "bf_emma", "name": "Emma", "accent": "british", "gender": "feminine",
        "description": (
            "Refined, authoritative, and precise — the literary narrator par excellence. "
            "Crisp vowels and measured delivery give every sentence weight."
        ),
        "dimensions": {"warmth": 6, "authority": 8, "gravitas": 7, "pace": 4, "brightness": 5, "age": 6},
        "tags": ["literary fiction", "historical", "mystery", "non-fiction"],
        "builtin": True, "engine": "kokoro",
    },
    {
        "id": "bf_alice", "name": "Alice", "accent": "british", "gender": "feminine",
        "description": (
            "Clear, crisp, and professional — cuts through complex text with "
            "effortless legibility. Trusted, dependable, never theatrical."
        ),
        "dimensions": {"warmth": 5, "authority": 8, "gravitas": 6, "pace": 5, "brightness": 7, "age": 5},
        "tags": ["academic", "documentary", "historical", "mystery"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "am_santa", "name": "Santa", "accent": "american", "gender": "masculine",
        "description": (
            "Jovial and rich with natural warmth — commanding without sternness. "
            "Suited for celebratory, family, and feel-good storytelling."
        ),
        "dimensions": {"warmth": 10, "authority": 6, "gravitas": 4, "pace": 4, "brightness": 6, "age": 9},
        "tags": ["children", "family", "holiday", "feel-good"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "bf_isabella", "name": "Isabella", "accent": "british", "gender": "feminine",
        "description": (
            "Warm and sophisticated — warmth contained within elegance. "
            "Brings aristocratic grace to lyrical prose without coldness."
        ),
        "dimensions": {"warmth": 8, "authority": 6, "gravitas": 6, "pace": 4, "brightness": 6, "age": 6},
        "tags": ["literary fiction", "romance", "historical", "memoir"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "bf_lily", "name": "Lily", "accent": "british", "gender": "feminine",
        "description": (
            "Bright and charming with crystal-clear diction. Brings warmth and light "
            "to stories without losing credibility — ideal for uplifting content."
        ),
        "dimensions": {"warmth": 8, "authority": 4, "gravitas": 3, "pace": 6, "brightness": 9, "age": 3},
        "tags": ["children", "young adult", "romance", "comedy"],
        "builtin": False, "engine": "kokoro",
    },
    # ── British Male ──────────────────────────────────────────────────────────
    {
        "id": "bm_george", "name": "George", "accent": "british", "gender": "masculine",
        "description": (
            "Deep, distinguished, and authoritative. The voice of a scholar who has "
            "lived every page — measured, resonant, completely trustworthy."
        ),
        "dimensions": {"warmth": 6, "authority": 9, "gravitas": 9, "pace": 3, "brightness": 3, "age": 8},
        "tags": ["historical", "literary fiction", "epic", "spiritual", "non-fiction"],
        "builtin": True, "engine": "kokoro",
    },
    {
        "id": "bm_daniel", "name": "Daniel", "accent": "british", "gender": "masculine",
        "description": (
            "Warm and storytelling-focused with natural, unhurried quality. "
            "Sounds like someone who genuinely loves the story they are telling."
        ),
        "dimensions": {"warmth": 8, "authority": 6, "gravitas": 7, "pace": 4, "brightness": 4, "age": 6},
        "tags": ["literary fiction", "memoir", "mystery", "historical"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "bm_fable", "name": "Fable", "accent": "british", "gender": "masculine",
        "description": (
            "Theatrical and expressive — built for dramatic stories. Handles character "
            "voices, emotional peaks, and mythological tension with natural skill."
        ),
        "dimensions": {"warmth": 6, "authority": 7, "gravitas": 8, "pace": 4, "brightness": 5, "age": 7},
        "tags": ["epic", "mythology", "literary fiction", "fantasy", "drama"],
        "builtin": False, "engine": "kokoro",
    },
    {
        "id": "bm_lewis", "name": "Lewis", "accent": "british", "gender": "masculine",
        "description": (
            "Clear, professional, and confident. Brings intellectual authority "
            "to dense text without sounding stiff — ideal for non-fiction."
        ),
        "dimensions": {"warmth": 5, "authority": 8, "gravitas": 7, "pace": 5, "brightness": 5, "age": 6},
        "tags": ["non-fiction", "academic", "historical", "documentary"],
        "builtin": False, "engine": "kokoro",
    },
]

# Index by ID for fast lookup
_VOICE_BY_ID: dict[str, dict] = {v["id"]: v for v in _VOICE_CATALOG}

# All known Kokoro voice IDs (for synthesis routing)
_ALL_KOKORO_IDS: set[str] = {v["id"] for v in _VOICE_CATALOG}

# Guaranteed-working builtin IDs (tested in CI)
_BUILTIN_IDS: set[str] = {v["id"] for v in _VOICE_CATALOG if v.get("builtin")}


def _resolve_kokoro_voice(voice_id: str) -> str:
    """Return the best Kokoro voice ID — falls back to af_heart if unknown."""
    if voice_id in _ALL_KOKORO_IDS:
        return voice_id
    return "af_heart"


# ── ACX audio mastering ───────────────────────────────────────────────────────

def _apply_acx_mastering(input_path: str, output_path: str) -> bool:
    """Apply ACX-compliant loudness normalization via ffmpeg loudnorm.

    Targets: -20 LUFS integrated loudness (within ACX window -18 to -23 dBRMS),
    true peak ceiling -3 dBTP, LRA 7 LU.  Outputs 192 kbps joint-stereo MP3
    at 44.1 kHz — meeting ACX technical requirements.

    Returns True on success, False on failure (caller should use raw file instead).
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-af", "loudnorm=I=-20:TP=-3:LRA=7:print_format=none",
                "-codec:a", "libmp3lame", "-b:a", "192k",
                "-ar", "44100", "-ac", "2",
                output_path,
            ],
            capture_output=True, timeout=300,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.warning("ACX mastering failed (%s)", exc)
        return False


@router.get("/studio/voices")
def list_voices():
    """Return the full voice catalog plus any custom voice profiles.

    Each voice entry includes a ``sample_engine`` field (``"kokoro"``,
    ``"espeak"``, or ``null``) sourced from the voice_samples DB table.
    A non-null value means a sample has already been generated; ``"espeak"``
    means the robotic fallback was used and the UI should warn the user.
    """
    db = get_db()
    with db._lock:
        profile_rows = db._conn.execute(
            "SELECT * FROM voice_profiles ORDER BY is_default DESC, name"
        ).fetchall()
        # Batch-fetch sample engine for every known voice in one query
        sample_rows = db._conn.execute(
            "SELECT voice_id, engine FROM voice_samples"
        ).fetchall()

    engine_map: dict[str, str] = {r["voice_id"]: r["engine"] for r in sample_rows}

    profiles = [dict(r) for r in profile_rows]
    # Mark custom profiles and add missing catalog fields
    for p in profiles:
        p.setdefault("accent", "custom")
        p.setdefault("gender", "unknown")
        p.setdefault("description", p.get("name", "Custom voice"))
        p.setdefault("dimensions", {})
        p.setdefault("tags", [])
        p["builtin"] = False
        p["custom"] = True
        p["sample_engine"] = engine_map.get(p.get("id", ""))

    # Return catalog copies annotated with sample_engine (avoids mutating the
    # module-level _VOICE_CATALOG list)
    catalog_with_engine = [
        {**v, "sample_engine": engine_map.get(v["id"])} for v in _VOICE_CATALOG
    ]

    return {
        "voices": catalog_with_engine + profiles,
        "catalog_count": len(_VOICE_CATALOG),
        "profile_count": len(profiles),
    }


# ── Voice sample generation and caching ───────────────────────────────────────

def _get_sample_cache_path(cfg, voice_id: str) -> Path:
    p = Path(cfg.data_dir) / "voice_samples"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{voice_id}.mp3"


def _upsert_voice_sample_db(db, voice_id: str, sample_path: str, engine: str) -> None:
    """Upsert a voice_samples row — records which file backs this voice's sample."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db._lock:
        db._conn.execute(
            """INSERT INTO voice_samples (voice_id, sample_path, engine, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(voice_id) DO UPDATE SET
                   sample_path=excluded.sample_path,
                   engine=excluded.engine,
                   updated_at=excluded.updated_at""",
            (voice_id, sample_path, engine, now, now),
        )
        db._conn.commit()


def _lookup_voice_sample_db(db, voice_id: str) -> str | None:
    """Return the cached sample_path for *voice_id*, or None if not recorded."""
    with db._lock:
        row = db._conn.execute(
            "SELECT sample_path FROM voice_samples WHERE voice_id=?",
            (voice_id,),
        ).fetchone()
    return row["sample_path"] if row else None


def _lookup_voice_sample_engine(db, voice_id: str) -> str | None:
    """Return the synthesis engine recorded for *voice_id*'s cached sample.

    Returns ``"kokoro"`` when neural synthesis was used, ``"espeak"`` when the
    robotic fallback was used, or ``None`` when no sample exists yet.
    """
    with db._lock:
        row = db._conn.execute(
            "SELECT engine FROM voice_samples WHERE voice_id=?",
            (voice_id,),
        ).fetchone()
    return row["engine"] if row else None


def _synthesize_sample_sync(voice_id: str) -> Path | None:
    """Generate a sample MP3 for *voice_id* using the best available engine.

    Checks the voice_samples DB table first, then the file cache.
    On successful generation writes the result to both the file system
    and the voice_samples table so subsequent calls skip synthesis.

    Returns the file Path on success, None on failure.  Always synchronous —
    call inside a thread when needed from async routes.
    """
    from orivellum.api._deps import get_config as _cfg
    cfg = _cfg()
    db  = get_db()

    # ── DB-backed cache check ────────────────────────────────────────────────
    cached_path = _lookup_voice_sample_db(db, voice_id)
    if cached_path:
        p = Path(cached_path)
        if p.exists() and p.stat().st_size > 1000:
            return p
        # Stale DB row — file was rotated away; fall through to re-generate

    # ── File-based fallback check (pre-DB entries) ────────────────────────────
    out_path = _get_sample_cache_path(cfg, voice_id)
    if out_path.exists() and out_path.stat().st_size > 1000:
        # Backfill the DB row for this already-generated file
        _upsert_voice_sample_db(db, voice_id, str(out_path), "kokoro")
        return out_path

    # ── Generate ──────────────────────────────────────────────────────────────
    tmp_wav = out_path.with_suffix(".tmp.wav")
    engine_used = "espeak"
    try:
        # Strategy 1: Kokoro ONNX
        kokoro = _get_kokoro()
        if kokoro is not None:
            try:
                import soundfile as sf
                samples, sr = kokoro.create(
                    _SAMPLE_SENTENCE,
                    voice=_resolve_kokoro_voice(voice_id),
                    speed=1.0, lang="en-us",
                )
                sf.write(str(tmp_wav), samples, sr)
                result = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(tmp_wav),
                     "-af", "loudnorm=I=-20:TP=-3:LRA=7:print_format=none",
                     "-codec:a", "libmp3lame", "-b:a", "128k",
                     str(out_path)],
                    capture_output=True, timeout=60,
                )
                if result.returncode == 0:
                    engine_used = "kokoro"
                    _upsert_voice_sample_db(db, voice_id, str(out_path), engine_used)
                    return out_path
            except Exception as exc:
                logger.debug("Kokoro sample gen failed for %s: %s", voice_id, exc)

        # Strategy 2: espeak-ng
        espeak_v = _ESPEAK_VOICE_MAP.get(voice_id, "en+m3")
        r = subprocess.run(
            ["espeak-ng", "-v", espeak_v, "-w", str(tmp_wav), _SAMPLE_SENTENCE],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            ff = subprocess.run(
                ["ffmpeg", "-y", "-i", str(tmp_wav),
                 "-codec:a", "libmp3lame", "-b:a", "128k", str(out_path)],
                capture_output=True, timeout=30,
            )
            if ff.returncode == 0:
                _upsert_voice_sample_db(db, voice_id, str(out_path), engine_used)
                return out_path
    except Exception as exc:
        logger.warning("Sample generation failed for %s: %s", voice_id, exc)
    finally:
        tmp_wav.unlink(missing_ok=True)

    return None


@router.get("/studio/voices/{voice_id}/sample")
async def get_voice_sample(voice_id: str):
    """Return a cached MP3 sample for *voice_id*, generating it on first call.

    Cache hierarchy:
      1. voice_samples DB row → file path (fast lookup, survives restarts)
      2. Deterministic file path in data/voice_samples/ (pre-DB back-compat)
      3. Generate on-demand via Kokoro ONNX → espeak-ng fallback

    Every response includes an ``X-TTS-Engine`` header — ``"kokoro"`` when
    neural synthesis was used, ``"espeak"`` when the robotic fallback ran.
    The UI reads this header to surface a "basic synthesis" warning so users
    are never silently served degraded audio.
    """
    if voice_id not in _VOICE_BY_ID:
        raise HTTPException(404, f"Unknown voice: {voice_id!r}")

    db = get_db()

    # Quick DB lookup before spawning a thread
    cached_path = await asyncio.to_thread(_lookup_voice_sample_db, db, voice_id)
    if cached_path:
        p = Path(cached_path)
        if p.exists() and p.stat().st_size > 1000:
            engine = await asyncio.to_thread(_lookup_voice_sample_engine, db, voice_id) or "kokoro"
            return FileResponse(str(p), media_type="audio/mpeg",
                                filename=f"sample_{voice_id}.mp3",
                                headers={
                                    "X-TTS-Engine": engine,
                                    "Cache-Control": "public, max-age=86400",
                                })

    # Generate (also writes to DB on success)
    result = await asyncio.to_thread(_synthesize_sample_sync, voice_id)
    if result is None:
        raise HTTPException(503, "Could not generate voice sample — TTS backend unavailable")

    engine = await asyncio.to_thread(_lookup_voice_sample_engine, db, voice_id) or "espeak"
    return FileResponse(str(result), media_type="audio/mpeg",
                        filename=f"sample_{voice_id}.mp3",
                        headers={
                            "X-TTS-Engine": engine,
                            "Cache-Control": "public, max-age=86400",
                        })


# ── AI Narrator Recommender ────────────────────────────────────────────────────

class VoiceRecommendRequest(BaseModel):
    work_id: str
    top_n: int = 5  # number of recommendations to return


def _build_voice_catalog_summary() -> str:
    lines = []
    for v in _VOICE_CATALOG:
        d = v["dimensions"]
        lines.append(
            f"  {v['id']} | {v['name']} ({v['accent']} {v['gender']}) | "
            f"warmth={d['warmth']} authority={d['authority']} gravitas={d['gravitas']} "
            f"pace={d['pace']} brightness={d['brightness']} age={d['age']} | "
            f"tags={','.join(v['tags'][:3])}"
        )
    return "\n".join(lines)


@router.post("/studio/voices/recommend")
async def recommend_voices(body: VoiceRecommendRequest):
    """Analyze a Work and recommend the best narrator voices using the LLM."""
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call

    db  = get_db()
    cfg = get_config()

    # ── Fetch work context ─────────────────────────────────────────────────────
    with db._lock:
        work_row = db._conn.execute(
            "SELECT id, title, work_type, description FROM works WHERE id=?",
            (body.work_id,),
        ).fetchone()
    if not work_row:
        raise HTTPException(404, f"Work {body.work_id!r} not found")

    work = dict(work_row)
    work_title = work.get("title") or "Untitled"
    work_desc  = work.get("description") or ""

    # Fetch top knowledge items for richer context
    with db._lock:
        ki_rows = db._conn.execute(
            """SELECT subject, text FROM knowledge
               WHERE work_id=? AND review_status != 'rejected'
               ORDER BY created_at DESC LIMIT 12""",
            (body.work_id,),
        ).fetchall()
        # Fetch a sample of text chunks from work documents
        chunk_rows = db._conn.execute(
            """SELECT c.text FROM chunks c
               JOIN documents d ON d.id = c.doc_id
               WHERE d.work_id=? ORDER BY d.rowid, c.page LIMIT 8""",
            (body.work_id,),
        ).fetchall()
        # Count all documents linked to this work (including uploaded/processing
        # ones that haven't produced chunks yet) so we don't misclassify a work
        # whose documents are still being extracted as having no content.
        doc_count: int = db._conn.execute(
            "SELECT COUNT(*) FROM documents WHERE work_id=?",
            (body.work_id,),
        ).fetchone()[0]

    # ── Sparse-content early exit ──────────────────────────────────────────────
    # Skip the LLM only when the work truly has nothing to analyse: no documents
    # (not even in-flight ones), no knowledge items, and no description text.
    # Works that have uploaded/processing documents are left to the LLM path so
    # the user is not told to add documents they have already added.
    has_content = bool(doc_count or ki_rows or work_desc.strip())
    if not has_content:
        return _fallback_recommendation(work_title, body.top_n, no_content=True)

    knowledge_text = "\n".join(
        f"- {r['subject']}: {(r['text'] or '')[:200]}" for r in ki_rows
    ) or "(no knowledge items yet)"

    sample_text = " ".join(r["text"][:300] for r in chunk_rows)[:800] or "(no document text)"

    voice_table = _build_voice_catalog_summary()

    system_prompt = (
        "You are an expert audiobook casting director with 20 years of experience. "
        "Your job is to analyze a written work and recommend the best narrator voices "
        "based on the genre, tone, emotional register, narrative distance, and intended audience. "
        "You always return valid JSON and nothing else."
    )

    user_prompt = f"""Analyze this work and recommend the {body.top_n} best narrator voices from the catalog below.

## Work
Title: {work_title}
Type: {work.get('work_type', 'unknown')}
Description: {work_desc or '(no description)'}

## Sample Knowledge Items
{knowledge_text}

## Sample Text From the Book
{sample_text}

## Available Voice Catalog
Format: voice_id | name (accent gender) | warmth=N authority=N gravitas=N pace=N brightness=N age=N | tags
(All dimensions 1–10: warmth=intimate warmth; authority=command; gravitas=weight/solemnity; pace=slowness; brightness=clarity; age=perceived elder quality)
{voice_table}

## Your Task
Return a JSON object with this exact structure. No other text, just JSON:
{{
  "genre_analysis": "2-3 sentences on the work's genre, tone, and narrative style",
  "narrator_profile": "Describe the ideal narrator in 2-3 sentences (warmth/authority/gravitas/pace needed)",
  "recommendations": [
    {{
      "voice_id": "the_voice_id",
      "score": 92,
      "headline": "One punchy sentence on why this voice suits the work",
      "rationale": "2-3 sentences explaining the match in detail",
      "dimension_match": "Which specific dimensions align perfectly"
    }}
  ]
}}

Return exactly {body.top_n} recommendations, ranked best first."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    result = await run_in_threadpool(
        llm_call, messages,
        base_url=cfg.serving.base_url,
        model=cfg.serving.workhorse_model,
        timeout=45.0,
        purpose="studio.voice_recommend",
        db=db,
        temperature=0.3,
        max_tokens=1200,
    )

    if not result.ok or not result.text:
        # Deterministic fallback — score by genre tag overlap
        return _fallback_recommendation(work_title, body.top_n, no_content=False)

    # Parse JSON response
    try:
        import json as _json
        raw = result.text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
        data = _json.loads(raw)
        recs = data.get("recommendations", [])
        # Validate and enrich each recommendation
        enriched = []
        for r in recs[:body.top_n]:
            vid = r.get("voice_id", "")
            if vid not in _VOICE_BY_ID:
                continue
            voice = _VOICE_BY_ID[vid]
            enriched.append({
                **r,
                "voice": voice,
            })
        return {
            "work_id": body.work_id,
            "work_title": work_title,
            "genre_analysis": data.get("genre_analysis", ""),
            "narrator_profile": data.get("narrator_profile", ""),
            "recommendations": enriched,
            "no_content": False,
        }
    except Exception as exc:
        logger.warning("Voice recommend JSON parse failed: %s", exc)
        return _fallback_recommendation(work_title, body.top_n, no_content=False)


def _fallback_recommendation(
    work_title: str, top_n: int, *, no_content: bool = False
) -> dict:
    """Return sensible defaults when the LLM is unavailable or content is sparse.

    ``no_content=True`` signals to the client that the Work has no analysable
    content yet, so it can display a helpful prompt rather than presenting the
    curated defaults as personalised AI recommendations.
    """
    defaults = ["bm_george", "am_puck", "af_sarah", "bf_emma", "am_adam"]
    recs = []
    for vid in defaults[:top_n]:
        if vid in _VOICE_BY_ID:
            v = _VOICE_BY_ID[vid]
            recs.append({
                "voice_id": vid,
                "score": 80,
                "headline": f"{v['name']} suits a wide range of narrative content",
                "rationale": v["description"],
                "dimension_match": "Well-rounded dimensions suitable for most audiobook narration",
                "voice": v,
            })
    genre_analysis = (
        "No document content yet — add documents to this Work for a personalised analysis."
        if no_content
        else "AI analysis unavailable — showing curated defaults"
    )
    return {
        "work_id": "",
        "work_title": work_title,
        "genre_analysis": genre_analysis,
        "narrator_profile": "Well-rounded narrator voices that suit most content",
        "recommendations": recs,
        "no_content": no_content,
    }


# ── Voice Designer ─────────────────────────────────────────────────────────────

class VoiceDesignRequest(BaseModel):
    description: str  # e.g. "deep, ancient male voice with gravitas and reverence"


@router.post("/studio/voices/design")
async def design_voice(body: VoiceDesignRequest):
    """Map a natural-language narrator description to the closest catalog voices."""
    import math
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call

    if not body.description.strip():
        raise HTTPException(400, "description must not be empty")
    if len(body.description) > 500:
        raise HTTPException(400, "description too long (max 500 chars)")

    cfg = get_config()
    db  = get_db()

    voice_table = _build_voice_catalog_summary()

    system_prompt = (
        "You are a voice casting assistant. Map the user's narrator description to "
        "dimension scores, then identify the best matching voice from the catalog. "
        "Always respond with valid JSON only — no other text."
    )
    user_prompt = f"""The user wants this narrator voice: "{body.description}"

Available voices:
{voice_table}

Step 1 — Score what the user is describing (1–10 each):
  warmth, authority, gravitas, pace, brightness, age

Step 2 — Find the 3 best matching voice IDs (closest to those scores).

Return this JSON structure exactly:
{{
  "target_dimensions": {{"warmth": N, "authority": N, "gravitas": N, "pace": N, "brightness": N, "age": N}},
  "interpretation": "1-2 sentences on how you interpreted the description",
  "matches": [
    {{
      "voice_id": "voice_id_here",
      "match_score": 94,
      "why": "1-2 sentences on the dimensional alignment"
    }}
  ]
}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    result = await run_in_threadpool(
        llm_call, messages,
        base_url=cfg.serving.base_url,
        model=cfg.serving.workhorse_model,
        timeout=30.0,
        purpose="studio.voice_design",
        db=db,
        temperature=0.2,
        max_tokens=500,
    )

    if result.ok and result.text:
        try:
            import json as _json
            raw = result.text.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("` \n")
            data = _json.loads(raw)
            matches = []
            for m in (data.get("matches") or [])[:3]:
                vid = m.get("voice_id", "")
                if vid in _VOICE_BY_ID:
                    matches.append({**m, "voice": _VOICE_BY_ID[vid]})
            if matches:
                return {
                    "description": body.description,
                    "target_dimensions": data.get("target_dimensions", {}),
                    "interpretation": data.get("interpretation", ""),
                    "matches": matches,
                }
            # LLM returned valid JSON but every voice_id was unknown — fall through
            # to keyword scoring so the UI always gets 3 usable match cards.
            logger.warning(
                "Voice design: LLM returned %d match(es) but none had a valid "
                "catalog voice_id — falling back to keyword scoring",
                len((data.get("matches") or [])),
            )
        except Exception as exc:
            logger.warning("Voice design parse failed: %s", exc)

    # Fallback: simple keyword scoring without LLM
    desc_lower = body.description.lower()

    def _keyword_score(voice: dict) -> float:
        d = voice["dimensions"]
        score = 0.0
        # Warmth keywords
        if any(w in desc_lower for w in ("warm", "intimate", "personal", "friendly")):
            score += d["warmth"]
        # Authority keywords
        if any(w in desc_lower for w in ("authority", "command", "authoritative", "strong", "powerful")):
            score += d["authority"]
        # Gravitas keywords
        if any(w in desc_lower for w in ("gravitas", "solemn", "serious", "weight", "deep", "ancient", "prophet", "biblical")):
            score += d["gravitas"]
        # Age/wisdom keywords
        if any(w in desc_lower for w in ("old", "elder", "wise", "ancient", "mature", "seasoned")):
            score += d["age"]
        # Gender keywords
        if any(w in desc_lower for w in ("male", "man", "masculine")) and voice["gender"] == "masculine":
            score += 5
        if any(w in desc_lower for w in ("female", "woman", "feminine")) and voice["gender"] == "feminine":
            score += 5
        # Accent keywords
        if any(w in desc_lower for w in ("british", "english", "uk")) and voice["accent"] == "british":
            score += 4
        if any(w in desc_lower for w in ("american", "us")) and voice["accent"] == "american":
            score += 4
        return score

    ranked = sorted(_VOICE_CATALOG, key=_keyword_score, reverse=True)[:3]
    matches = [
        {
            "voice_id": v["id"],
            "match_score": 75,
            "why": v["description"],
            "voice": v,
        }
        for v in ranked
    ]
    return {
        "description": body.description,
        "target_dimensions": {},
        "interpretation": "Matched using keyword analysis (AI service unavailable)",
        "matches": matches,
    }


# ── Work-level audiobook generation ──────────────────────────────────────────

class WorkAudiobookRequest(BaseModel):
    work_id: str
    voice: str = "bm_george"
    speed: float = 1.0
    include_credits: bool = True   # opening + closing ACX-style credits
    acx_mastering: bool = True     # apply loudnorm ACX mastering
    return_url: bool = False       # for mobile: return JSON path instead of FileResponse


@router.post("/studio/tts/work")
def synthesize_work_audiobook(body: WorkAudiobookRequest):
    """Generate a full audiobook MP3 from all documents in a Work.

    Produces a single concatenated MP3 with optional opening/closing credits
    and ACX-compliant loudness mastering.
    """
    db  = get_db()
    cfg = get_config()

    # ── Validate work ──────────────────────────────────────────────────────────
    with db._lock:
        work_row = db._conn.execute(
            "SELECT id, title FROM works WHERE id=?", (body.work_id,)
        ).fetchone()
    if not work_row:
        raise HTTPException(404, f"Work {body.work_id!r} not found")

    work_title = work_row["title"] or "Untitled Work"
    voice_meta = _VOICE_BY_ID.get(body.voice, {})
    voice_name = voice_meta.get("name", body.voice)

    # ── Fetch all ready documents in work order ────────────────────────────────
    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.id, d.title, d.source
               FROM documents d JOIN objects o ON o.id = d.id
               WHERE d.work_id=? AND d.readiness='ready'
               ORDER BY o.created_at""",
            (body.work_id,),
        ).fetchall()

    if not doc_rows:
        raise HTTPException(422, "No ready documents found in this Work. "
                                 "Process documents in the Library first.")

    # ── Fetch text chunks per document ─────────────────────────────────────────
    doc_texts: list[tuple[str, str]] = []  # (title, full_text)
    with db._lock:
        for doc in doc_rows:
            chunks = db._conn.execute(
                "SELECT text FROM chunks WHERE doc_id=? ORDER BY page, rowid",
                (doc["id"],),
            ).fetchall()
            if chunks:
                text = "\n\n".join(r["text"] for r in chunks)
                doc_title = doc["title"] or doc["source"].split("/")[-1] if doc["source"] else "Chapter"
                doc_texts.append((doc_title, text))

    if not doc_texts:
        raise HTTPException(422, "No extracted text found in any document of this Work.")

    out_dir = Path(cfg.data_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp())

    voice_id    = _resolve_kokoro_voice(body.voice)
    espeak_v    = _ESPEAK_VOICE_MAP.get(body.voice, "en+m3")
    wpm         = max(80, min(400, int(175 * body.speed)))
    kokoro_eng  = _get_kokoro()

    try:
        import soundfile as _sf
    except ImportError:
        _sf = None  # type: ignore[assignment]

    wav_parts: list[Path] = []

    def _synth_segment(text: str, idx: int) -> Path | None:
        """Synthesise one text segment to WAV, returning the path or None."""
        wav = tmp_dir / f"seg_{idx:06d}.wav"
        # Strategy 1: Kokoro
        if kokoro_eng is not None and _sf is not None:
            try:
                samples, sr = kokoro_eng.create(text, voice=voice_id, speed=body.speed, lang="en-us")
                _sf.write(str(wav), samples, sr)
                return wav
            except Exception as ke:
                logger.debug("Kokoro seg %d: %s", idx, ke)
        # Strategy 2: espeak-ng
        try:
            r = subprocess.run(
                ["espeak-ng", "-v", espeak_v, "-s", str(wpm), "-w", str(wav), text],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0:
                return wav
        except Exception as ee:
            logger.debug("espeak seg %d: %s", idx, ee)
        return None

    try:
        seg_idx = 0

        # ── Opening credits ────────────────────────────────────────────────────
        if body.include_credits:
            credits_text = (
                f"{work_title}. Narrated by {voice_name}. "
                "This is an AI-generated audiobook produced with Orivellum."
            )
            p = _synth_segment(credits_text, seg_idx)
            if p:
                wav_parts.append(p)
                seg_idx += 1
            # 1-second silence between credits and content
            sil = tmp_dir / f"seg_{seg_idx:06d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                 "-t", "1", str(sil)],
                capture_output=True, timeout=10,
            )
            if sil.exists():
                wav_parts.append(sil)
                seg_idx += 1

        # ── Document chapters ──────────────────────────────────────────────────
        for doc_title, doc_text in doc_texts:
            # Chapter header announcement
            chapter_intro = _synth_segment(doc_title + ".", seg_idx)
            if chapter_intro:
                wav_parts.append(chapter_intro)
                seg_idx += 1

            # Segment the document text
            segments = _split_text_into_segments(doc_text)[:60]
            for seg_text in segments:
                p = _synth_segment(seg_text, seg_idx)
                if p:
                    wav_parts.append(p)
                    seg_idx += 1

            # Short silence between chapters
            sil = tmp_dir / f"seg_{seg_idx:06d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                 "-t", "1.5", str(sil)],
                capture_output=True, timeout=10,
            )
            if sil.exists():
                wav_parts.append(sil)
                seg_idx += 1

        # ── Closing credits ────────────────────────────────────────────────────
        if body.include_credits:
            closing = (
                f"You have been listening to {work_title}. "
                f"Narrated by {voice_name}. The end."
            )
            p = _synth_segment(closing, seg_idx)
            if p:
                wav_parts.append(p)

        if not wav_parts:
            raise RuntimeError("No audio segments were generated")

        # ── Concatenate all WAVs ───────────────────────────────────────────────
        safe_title = re.sub(r"[^\w\-]", "_", work_title)[:50]
        raw_mp3    = out_dir / f"{safe_title}_{uuid.uuid4().hex[:6]}_raw.mp3"
        final_mp3  = out_dir / f"{safe_title}_{uuid.uuid4().hex[:6]}.mp3"

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in wav_parts), encoding="utf-8"
        )

        ff = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list),
             "-codec:a", "libmp3lame", "-q:a", "2",
             str(raw_mp3)],
            capture_output=True, timeout=600,
        )
        if ff.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {ff.stderr.decode()[:400]}")

        # ── ACX mastering ──────────────────────────────────────────────────────
        if body.acx_mastering and _apply_acx_mastering(str(raw_mp3), str(final_mp3)):
            raw_mp3.unlink(missing_ok=True)
            mp3_path = final_mp3
        else:
            raw_mp3.rename(final_mp3)
            final_mp3.unlink(missing_ok=True) if not final_mp3.exists() else None
            mp3_path = raw_mp3 if raw_mp3.exists() else final_mp3

        # Hard-link into library before rotation
        _ab_rel = _link_output_sync(mp3_path)
        _rotate_outputs(out_dir)

        all_text = "\n\n".join(t for _, t in doc_texts)
        from orivellum.api.executor import get_executor as _gex
        _gex().submit(
            _register_output_bg, mp3_path, all_text[:8000], "mp3",
            f"Audiobook: {work_title}", prelinked_rel=_ab_rel,
        )

        # Push notification — work audiobook ready (best-effort, executor)
        try:
            from orivellum.capabilities.push import notify_push_best_effort as _push_ab
            from orivellum.api.executor import submit_bg as _submit_bg_push_ab
            _push_label = work_title[:50] if work_title else "Your Work"
            _submit_bg_push_ab(
                _push_ab,
                get_db(),
                "🎙️ Audiobook ready",
                f'"{_push_label}" audiobook is ready to play',
                {"screen": "studio"},
                kind="background", label="push:audiobook_ready",
            )
        except Exception:
            pass

        if body.return_url:
            rel = str(mp3_path.relative_to(out_dir))
            return {"ok": True, "path": rel, "filename": mp3_path.name, "work_title": work_title}

        return FileResponse(str(mp3_path), media_type="audio/mpeg", filename=mp3_path.name)

    except Exception as exc:
        logger.error("Work audiobook failed: %s", exc)
        raise HTTPException(500, f"Audiobook generation failed: {exc}")
    finally:
        for p in wav_parts:
            p.unlink(missing_ok=True)
        for f in tmp_dir.iterdir():
            f.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


# ── Work-Audiobook async job model ───────────────────────────────────────────
# Per-job registry keys: state, chapter_idx, total_chapters, chapter_title,
#                        work_title, cancel_requested, result?, error?
_work_tts_jobs: dict[str, dict] = {}
_work_tts_jobs_lock = threading.Lock()


def _run_work_tts_job(
    job_id: str,
    voice: str,
    speed: float,
    include_credits: bool,
    acx_mastering: bool,
    work_title: str,
    doc_texts: list[tuple[str, str]],
    out_dir: Path,
    cfg,
) -> None:
    """Background worker: synthesise a full work audiobook chapter by chapter."""
    kokoro_eng = _get_kokoro()
    try:
        import soundfile as _sf2
    except ImportError:
        _sf2 = None  # type: ignore[assignment]

    voice_id   = _resolve_kokoro_voice(voice)
    espeak_v   = _ESPEAK_VOICE_MAP.get(voice, "en+m3")
    wpm        = max(80, min(400, int(175 * speed)))
    voice_meta = _VOICE_BY_ID.get(voice, {})
    voice_name = voice_meta.get("name", voice)

    tmp_dir = Path(tempfile.mkdtemp())
    wav_parts: list[Path] = []
    seg_idx = 0

    def _synth(text: str) -> "Path | None":
        nonlocal seg_idx
        wav = tmp_dir / f"seg_{seg_idx:06d}.wav"
        seg_idx += 1
        if kokoro_eng is not None and _sf2 is not None:
            try:
                samples, sr = kokoro_eng.create(text, voice=voice_id, speed=speed, lang="en-us")
                _sf2.write(str(wav), samples, sr)
                return wav
            except Exception as ke:
                logger.debug("Kokoro work-job %s seg: %s", job_id, ke)
        try:
            r = subprocess.run(
                ["espeak-ng", "-v", espeak_v, "-s", str(wpm), "-w", str(wav), text],
                capture_output=True, timeout=120,
            )
            if r.returncode == 0:
                return wav
        except Exception as ee:
            logger.debug("espeak work-job %s seg: %s", job_id, ee)
        return None

    def _silence(dur: float) -> None:
        nonlocal seg_idx
        sil = tmp_dir / f"seg_{seg_idx:06d}.wav"
        seg_idx += 1
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
             "-t", str(dur), str(sil)],
            capture_output=True, timeout=10,
        )
        if sil.exists():
            wav_parts.append(sil)

    def _cancelled() -> bool:
        with _work_tts_jobs_lock:
            return bool(_work_tts_jobs.get(job_id, {}).get("cancel_requested"))

    try:
        with _work_tts_jobs_lock:
            _work_tts_jobs[job_id]["state"] = "running"

        # Opening credits
        if include_credits:
            credits_text = (
                f"{work_title}. Narrated by {voice_name}. "
                "This is an AI-generated audiobook produced with Orivellum."
            )
            p = _synth(credits_text)
            if p:
                wav_parts.append(p)
            _silence(1.0)

        # Chapter-by-chapter synthesis
        for idx, (doc_title, doc_text) in enumerate(doc_texts):
            if _cancelled():
                with _work_tts_jobs_lock:
                    _work_tts_jobs[job_id]["state"] = "cancelled"
                return

            with _work_tts_jobs_lock:
                _work_tts_jobs[job_id].update({
                    "chapter_idx": idx,
                    "chapter_title": doc_title,
                })

            intro = _synth(doc_title + ".")
            if intro:
                wav_parts.append(intro)

            for seg_text in _split_text_into_segments(doc_text)[:60]:
                if _cancelled():
                    with _work_tts_jobs_lock:
                        _work_tts_jobs[job_id]["state"] = "cancelled"
                    return
                p = _synth(seg_text)
                if p:
                    wav_parts.append(p)

            _silence(1.5)

        # Closing credits — skip entirely if cancelled after the last chapter
        if include_credits and not _cancelled():
            closing = (
                f"You have been listening to {work_title}. "
                f"Narrated by {voice_name}. The end."
            )
            p = _synth(closing)
            if p:
                wav_parts.append(p)

        if not wav_parts:
            raise RuntimeError("No audio segments were generated")

        # Final cancellation gate: if the user cancelled during or after the
        # last chapter, stop here before the expensive concat / mastering /
        # registration steps.
        if _cancelled():
            with _work_tts_jobs_lock:
                _work_tts_jobs[job_id]["state"] = "cancelled"
            return

        # Concatenate all WAV parts to MP3
        safe_title = re.sub(r"[^\w\-]", "_", work_title)[:50]
        raw_mp3    = out_dir / f"{safe_title}_{uuid.uuid4().hex[:6]}_raw.mp3"
        final_mp3  = out_dir / f"{safe_title}_{uuid.uuid4().hex[:6]}.mp3"

        concat_list = tmp_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p}'" for p in wav_parts), encoding="utf-8"
        )
        ff = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list),
             "-codec:a", "libmp3lame", "-q:a", "2", str(raw_mp3)],
            capture_output=True, timeout=600,
        )
        if ff.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {ff.stderr.decode()[:400]}")

        # ACX mastering
        if acx_mastering and _apply_acx_mastering(str(raw_mp3), str(final_mp3)):
            raw_mp3.unlink(missing_ok=True)
            mp3_path = final_mp3
        else:
            raw_mp3.rename(final_mp3)
            mp3_path = final_mp3 if final_mp3.exists() else raw_mp3

        # Cancellation gate: checked BEFORE any durable publication so a cancel
        # that arrived during mastering cannot leave a registered artifact behind.
        if _cancelled():
            try:
                mp3_path.unlink(missing_ok=True)
            except Exception:
                pass
            with _work_tts_jobs_lock:
                _work_tts_jobs[job_id]["state"] = "cancelled"
            return

        _ab_rel = _link_output_sync(mp3_path)
        _rotate_outputs(out_dir)

        all_text = "\n\n".join(t for _, t in doc_texts)
        from orivellum.api.executor import get_executor as _gex_wj
        _gex_wj().submit(
            _register_output_bg, mp3_path, all_text[:8000], "mp3",
            f"Audiobook: {work_title}", prelinked_rel=_ab_rel,
        )

        # Push notification (best-effort)
        try:
            from orivellum.capabilities.push import notify_push_best_effort as _push_wj
            threading.Thread(
                target=_push_wj,
                args=(
                    get_db(),
                    "🎙️ Audiobook ready",
                    f'"{work_title[:50]}" audiobook is ready to play',
                    {"screen": "studio"},
                ),
                daemon=True,
            ).start()
        except Exception:
            pass

        rel = str(mp3_path.relative_to(out_dir))
        with _work_tts_jobs_lock:
            _work_tts_jobs[job_id].update({
                "state": "done",
                "chapter_idx": len(doc_texts),
                "chapter_title": "",
                "result": {
                    "path": rel,
                    "filename": mp3_path.name,
                    "work_title": work_title,
                },
            })

    except Exception as exc:
        logger.error("Work TTS job %s failed: %s", job_id, exc)
        with _work_tts_jobs_lock:
            _work_tts_jobs[job_id].update({"state": "failed", "error": str(exc)[:400]})
    finally:
        for p in wav_parts:
            p.unlink(missing_ok=True)
        for f in tmp_dir.iterdir():
            f.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except Exception:
            pass


class WorkAudiobookStartRequest(BaseModel):
    work_id: str
    voice: str = "bm_george"
    speed: float = 1.0
    include_credits: bool = True
    acx_mastering: bool = True


@router.post("/studio/tts/work/start")
def start_work_audiobook_async(body: WorkAudiobookStartRequest):
    """Start an async audiobook generation job; returns {job_id, total_chapters} immediately.

    Poll GET /studio/tts/work/{job_id}/status for chapter-level progress.
    Send DELETE /studio/tts/work/{job_id} to cancel.
    """
    db  = get_db()
    cfg = get_config()

    with db._lock:
        work_row = db._conn.execute(
            "SELECT id, title FROM works WHERE id=?", (body.work_id,)
        ).fetchone()
    if not work_row:
        raise HTTPException(404, f"Work {body.work_id!r} not found")

    work_title = work_row["title"] or "Untitled Work"

    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.id, d.title, d.source
               FROM documents d JOIN objects o ON o.id = d.id
               WHERE d.work_id=? AND d.readiness='ready'
               ORDER BY o.created_at""",
            (body.work_id,),
        ).fetchall()

    if not doc_rows:
        raise HTTPException(422, "No ready documents found in this Work. "
                                 "Process documents in the Library first.")

    doc_texts: list[tuple[str, str]] = []
    with db._lock:
        for doc in doc_rows:
            chunks = db._conn.execute(
                "SELECT text FROM chunks WHERE doc_id=? ORDER BY page, rowid",
                (doc["id"],),
            ).fetchall()
            if chunks:
                text = "\n\n".join(r["text"] for r in chunks)
                doc_title = doc["title"] or (
                    doc["source"].split("/")[-1] if doc["source"] else "Chapter"
                )
                doc_texts.append((doc_title, text))

    if not doc_texts:
        raise HTTPException(422, "No extracted text found in any document of this Work.")

    job_id  = str(uuid.uuid4())
    out_dir = Path(cfg.data_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    with _work_tts_jobs_lock:
        _work_tts_jobs[job_id] = {
            "state": "starting",
            "chapter_idx": 0,
            "total_chapters": len(doc_texts),
            "chapter_title": "",
            "work_title": work_title,
            "cancel_requested": False,
        }

    from orivellum.api.executor import submit_bg as _submit_bg_tts
    _submit_bg_tts(
        _run_work_tts_job,
        job_id, body.voice, body.speed, body.include_credits,
        body.acx_mastering, work_title, doc_texts, out_dir, cfg,
        kind="studio", label=f"work_tts:{job_id[:8]}",
    )

    return {"job_id": job_id, "total_chapters": len(doc_texts)}


@router.get("/studio/tts/work/{job_id}/status")
def get_work_tts_status(job_id: str):
    """Return current chapter-level progress for a work audiobook job."""
    with _work_tts_jobs_lock:
        raw = _work_tts_jobs.get(job_id)
    if raw is None:
        raise HTTPException(404, f"Work TTS job {job_id!r} not found")
    # Strip the internal cancel flag before returning
    out = {k: v for k, v in raw.items() if k != "cancel_requested"}
    return {"job_id": job_id, **out}


@router.delete("/studio/tts/work/{job_id}")
def cancel_work_tts(job_id: str):
    """Request cancellation of an in-progress work audiobook job."""
    with _work_tts_jobs_lock:
        job = _work_tts_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"Work TTS job {job_id!r} not found")
    with _work_tts_jobs_lock:
        _work_tts_jobs[job_id]["cancel_requested"] = True
    return {"ok": True, "job_id": job_id}


# ── TTS synthesis ─────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    voice: str = "af_heart"
    speed: float = 1.0   # 0.5 – 2.0
    stream: bool = False  # True → SSE per-segment streaming; False → full-file (legacy)
    return_url: bool = False  # mobile: return JSON {ok,path,filename} instead of FileResponse


@router.post("/studio/tts")
async def synthesize_speech(body: TTSRequest):
    """Synthesize *text* to speech.

    When ``body.stream`` is ``True`` the response is ``text/event-stream`` (SSE).
    The text is split into ~150-word segments; each segment is synthesised in
    sequence and an event is emitted as soon as its MP3 is ready so the client
    can start playing before all synthesis is done.  Event shapes::

        {"type":"segment","idx":0,"total":3,"uri":"/api/studio/outputs/serve?path=…"}
        {"type":"segment_error","idx":1,"total":3,"message":"…"}
        {"type":"done","total":3}

    When ``body.stream`` is ``False`` (default) the full MP3 is returned as
    ``audio/mpeg`` — the original behaviour.

    Strategies tried in order for both paths:
    1. Local AI server /audio/speech (OpenAI-compatible)
    2. Kokoro ONNX (local neural TTS, CPU-only)
    3. espeak-ng (always-available robotic fallback)
    """
    if not body.text.strip():
        raise HTTPException(400, "text must not be empty")
    if len(body.text) > 10_000:
        raise HTTPException(400, "text too long (max 10 000 chars)")

    # ── Streaming path ────────────────────────────────────────────────────────
    if body.stream:
        return StreamingResponse(
            _stream_tts_events(body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Non-streaming path (original — returns full MP3 file) ─────────────────
    cfg = get_config()

    # --- Strategy 0: Premium TTS engine (Fish Audio S2 / Hume TADA / etc.) ---
    try:
        premium_audio = await _call_premium_tts(body.text, body.voice, body.speed, cfg)
        if premium_audio is not None:
            out_dir = Path(cfg.data_dir) / "outputs"
            out_dir.mkdir(parents=True, exist_ok=True)
            tmp = tempfile.NamedTemporaryFile(delete=False, dir=out_dir, suffix=".mp3")
            tmp.write(premium_audio)
            tmp.close()
            _prem_rel = _link_output_sync(Path(tmp.name))
            await asyncio.to_thread(_rotate_outputs, out_dir)
            from orivellum.api.executor import get_executor as _gex
            _gex().submit(
                _register_output_bg, Path(tmp.name), body.text, "mp3",
                f"TTS clip: {body.text[:60]}", prelinked_rel=_prem_rel,
            )
            if body.return_url:
                return {"ok": True, "path": str(_prem_rel), "filename": "speech.mp3"}
            return FileResponse(tmp.name, media_type="audio/mpeg",
                                filename="speech.mp3",
                                headers={"X-TTS-Engine": "premium"})
    except Exception as exc:
        logger.info("Premium TTS failed (%s) — trying AI server", exc)

    # --- Strategy 1: AI server /audio/speech ---
    try:
        import httpx
        openai_voice = _OPENAI_VOICE_MAP.get(body.voice, "alloy")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/audio/speech",
                json={
                    "model": cfg.serving.tts_model,   # configurable: tts-1-hd / tts-1 / etc.
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
                if body.return_url:
                    return {"ok": True, "path": str(_tts_rel), "filename": "speech.mp3"}
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

            # All 28 catalog IDs are valid Kokoro voice IDs; resolve via the
            # catalog index so unknown/custom IDs fall back to af_heart.
            kokoro_voice = _resolve_kokoro_voice(body.voice)

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
                if body.return_url:
                    return {"ok": True, "path": str(_kok_rel), "filename": "speech.mp3"}
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
            if body.return_url:
                return {"ok": True, "path": str(_esp_mp3_rel), "filename": "speech.mp3"}
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
            if body.return_url:
                return {"ok": True, "path": str(_esp_wav_rel), "filename": "speech.wav"}
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

def _hard_split_at_words(text: str, max_chars: int) -> list[str]:
    """Force-split *text* at word boundaries — and at character boundaries for
    individual tokens that exceed *max_chars* — when no sentence break is
    available.

    All returned chunks are guaranteed to be at most *max_chars* characters.
    Used as a last resort so the streaming TTS latency cap holds regardless of
    punctuation density or token length.
    """
    def _chop(token: str) -> list[str]:
        """Split a single token that is longer than max_chars at char boundaries."""
        return [token[i : i + max_chars] for i in range(0, len(token), max_chars)]

    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            # Flush the current accumulator first, then chop the oversized word
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_chop(word))
            continue
        candidate = (current + " " + word) if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks or ([text[:max_chars]] if text else [])


def _split_text_into_segments(text: str, max_chars: int = 1500) -> list[str]:
    """Split text at paragraph/sentence/word boundaries, capping at *max_chars*.

    Three-tier strategy:
    1. Paragraph boundaries (``\\n\\n``) — prefer keeping paragraphs together.
    2. Sentence boundaries (``[.!?]`` followed by whitespace) — used when a
       paragraph alone exceeds *max_chars*.
    3. Word boundaries — used when a single sentence exceeds *max_chars*
       (e.g. unpunctuated or very long input) so the latency cap is always met.
    """
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    segments: list[str] = []
    current = ""

    def _flush() -> None:
        nonlocal current
        if current.strip():
            segments.append(current.strip())
        current = ""

    for para in paragraphs:
        if len(para) > max_chars:
            # Tier 2: split at sentence boundaries
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                if len(sent) > max_chars:
                    # Tier 3: force-split at word boundaries
                    if current:
                        _flush()
                    for chunk in _hard_split_at_words(sent, max_chars):
                        if chunk:
                            segments.append(chunk)
                    continue
                if current and len(current) + len(sent) + 1 > max_chars:
                    _flush()
                current += (" " if current else "") + sent
        else:
            if current and len(current) + len(para) + 2 > max_chars:
                _flush()
            current += ("\n\n" if current else "") + para

    _flush()
    return [s for s in segments if s]


# ── Per-segment synthesis helper (streaming TTS) ─────────────────────────────

async def _synthesize_text_to_mp3(
    text: str,
    voice: str,
    speed: float,
    out_dir: Path,
    cfg: object,
) -> "Path | None":
    """Synthesize *text* → MP3 using the same 3-strategy cascade as
    ``synthesize_speech``.  Returns the saved ``Path`` on success or ``None``
    when all backends fail.  Does **not** call ``_link_output_sync``,
    ``_rotate_outputs``, or ``_register_output_bg`` — the streaming caller
    handles those after all segments are done.
    """
    kokoro_voice = _resolve_kokoro_voice(voice)
    espeak_v     = _ESPEAK_VOICE_MAP.get(voice, "en+f4")
    wpm          = max(80, min(400, int(175 * speed)))

    # Strategy 0: Premium TTS engine ---------------------------------------
    try:
        premium_audio = await _call_premium_tts(text, voice, speed, cfg)
        if premium_audio is not None:
            tmp = tempfile.NamedTemporaryFile(delete=False, dir=out_dir, suffix=".mp3")
            tmp.write(premium_audio)
            tmp.close()
            return Path(tmp.name)
    except Exception:
        pass

    # Strategy 1: AI-server /audio/speech ---------------------------------
    try:
        import httpx
        openai_voice = _OPENAI_VOICE_MAP.get(voice, "alloy")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/audio/speech",  # type: ignore[union-attr]
                json={
                    "model": cfg.serving.tts_model,  # type: ignore[union-attr]
                    "input": text,
                    "voice": openai_voice,
                    "response_format": "mp3",
                    "speed": speed,
                },
            )
            if resp.status_code == 200:
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, dir=out_dir, suffix=".mp3"
                )
                tmp.write(resp.content)
                tmp.close()
                return Path(tmp.name)
    except Exception:
        pass

    # Strategy 2: Kokoro ONNX -------------------------------------------------
    try:
        kokoro = _get_kokoro()
        if kokoro is not None:
            import soundfile as sf  # type: ignore[import]
            samples, sample_rate = await asyncio.to_thread(
                kokoro.create, text,
                voice=kokoro_voice, speed=speed, lang="en-us",
            )
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
                return Path(mp3_path)
            Path(mp3_path).unlink(missing_ok=True)
    except Exception:
        pass

    # Strategy 3: espeak-ng -----------------------------------------------
    try:
        wav_tmp = tempfile.NamedTemporaryFile(
            delete=False, dir=out_dir, suffix=".wav"
        )
        wav_path = wav_tmp.name
        wav_tmp.close()
        result = await asyncio.to_thread(
            subprocess.run,
            ["espeak-ng", "-v", espeak_v, "-s", str(wpm), "-w", wav_path, text],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            mp3_tmp = tempfile.NamedTemporaryFile(
                delete=False, dir=out_dir, suffix=".mp3"
            )
            mp3_path = mp3_tmp.name
            mp3_tmp.close()
            ff = await asyncio.to_thread(
                subprocess.run,
                ["ffmpeg", "-y", "-i", wav_path,
                 "-codec:a", "libmp3lame", "-q:a", "4", mp3_path],
                capture_output=True, timeout=30,
            )
            Path(wav_path).unlink(missing_ok=True)
            if ff.returncode == 0:
                return Path(mp3_path)
            Path(mp3_path).unlink(missing_ok=True)
        else:
            Path(wav_path).unlink(missing_ok=True)
    except Exception:
        pass

    return None


async def _stream_tts_events(body: "TTSRequest"):
    """Async generator: synthesise ``body.text`` in ~150-word segments and
    yield SSE lines for each completed segment.

    **Output lifecycle** — each successful segment follows the same durable
    path as non-streaming TTS: ``_link_output_sync`` is called synchronously
    *before* the event is yielded (so the hard-link survives any subsequent
    rotation), and ``_register_output_bg`` is submitted to the background
    executor so the clip appears in the library.  ``_rotate_outputs`` runs
    once after the final segment.

    Yields ``data: <json>\\n\\n`` lines in three event shapes::

        {"type":"segment","idx":N,"total":T,"path":"/abs/path/to/file.mp3","ok":true}
        {"type":"segment_error","idx":N,"total":T,"message":"…","ok":false}
        {"type":"done","total":T,"ok_count":N,"error_count":M}

    The ``path`` field in ``segment`` events is the raw output filesystem path.
    Clients MUST percent-encode it before appending to the
    ``/api/studio/outputs/serve?path=`` query — use ``serveUrl(evt.path)``
    on the mobile client or ``encodeURIComponent`` elsewhere.
    """
    import json as _json
    from orivellum.api.executor import get_executor as _gex

    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ~150-word segments keep synthesis latency below ~2 s for the first chunk
    segments = _split_text_into_segments(body.text, max_chars=900)
    total     = len(segments)
    ok_count  = 0
    err_count = 0
    # Accumulate successful segment paths for the post-loop concat step
    ok_paths: list[Path] = []

    for idx, seg_text in enumerate(segments):
        try:
            mp3_path = await _synthesize_text_to_mp3(
                seg_text, body.voice, body.speed, out_dir, cfg
            )
            if mp3_path:
                # Hard-link BEFORE rotation so the library inode is durable
                seg_rel = _link_output_sync(mp3_path)
                # Register as a searchable Studio clip (best-effort background)
                seg_title = f"TTS clip ({idx + 1}/{total}): {body.text[:50]}"
                _gex().submit(
                    _register_output_bg, mp3_path, seg_text, "mp3",
                    seg_title, prelinked_rel=seg_rel,
                )
                # Emit the output-relative path (e.g. "tmpXXXX.mp3") so the
                # /studio/outputs/serve endpoint can safely resolve it within
                # out_dir — the same format as list_outputs uses.
                rel_path = mp3_path.relative_to(out_dir)
                event: dict = {
                    "type": "segment", "idx": idx, "total": total,
                    "path": str(rel_path), "ok": True,
                }
                ok_count += 1
                ok_paths.append(mp3_path)
            else:
                event = {
                    "type": "segment_error", "idx": idx, "total": total,
                    "message": "All TTS backends failed for this segment",
                    "ok": False,
                }
                err_count += 1
        except Exception as exc:
            event = {
                "type": "segment_error", "idx": idx, "total": total,
                "message": str(exc)[:200], "ok": False,
            }
            err_count += 1
        yield f"data: {_json.dumps(event)}\n\n"

    # ── Post-loop: concatenate all segments into one shareable MP3 ────────────
    # When there is only one successful segment the segment path IS the full
    # audio; when there are two or more, ffmpeg concat merges them seamlessly.
    # The concat file is registered and linked the same way individual segments
    # are.  Failure is non-fatal — the client falls back to the last segment.
    concat_rel: str = ""
    if ok_paths:
        if len(ok_paths) == 1:
            # Single segment — reuse it directly; no ffmpeg needed
            concat_rel = str(ok_paths[0].relative_to(out_dir))
        else:
            try:
                import tempfile as _tmpmod
                concat_mp3 = out_dir / f"tts_full_{uuid.uuid4().hex[:8]}.mp3"
                list_file  = _tmpmod.NamedTemporaryFile(
                    mode="w", suffix=".txt", delete=False
                )
                list_file.write(
                    "\n".join(f"file '{p}'" for p in ok_paths)
                )
                list_file.close()
                ff = await asyncio.to_thread(
                    subprocess.run,
                    ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                     "-i", list_file.name,
                     "-codec:a", "libmp3lame", "-q:a", "2", str(concat_mp3)],
                    capture_output=True, timeout=180,
                )
                Path(list_file.name).unlink(missing_ok=True)
                if ff.returncode == 0:
                    # Verify concat file has content before advertising it
                    try:
                        concat_size = concat_mp3.stat().st_size
                    except OSError:
                        concat_size = 0
                    if concat_size > 0:
                        # Hard-link before rotation
                        concat_lib_rel = _link_output_sync(concat_mp3)
                        full_title = f"TTS narration: {body.text[:60]}"
                        _gex().submit(
                            _register_output_bg, concat_mp3,
                            body.text[:4000], "mp3", full_title,
                            prelinked_rel=concat_lib_rel,
                        )
                        concat_rel = str(concat_mp3.relative_to(out_dir))
                    else:
                        logger.warning("TTS concat produced an empty file — skipping")
                else:
                    logger.warning(
                        "TTS concat ffmpeg failed: %s",
                        ff.stderr.decode()[:300],
                    )
            except Exception as exc:
                logger.warning("TTS concat failed (non-fatal): %s", exc)

    # ── Emit dedicated concat event so clients get the merged URI directly ──
    # Emitted BEFORE rotation and done so clients can set up the share button
    # without waiting for done or building the URL themselves.
    # Shape: {"type":"concat","path":"<out_dir-relative>","uri":"<serve URL>","ok":true}
    # Non-fatal: if concat failed, no concat event is emitted; clients fall
    # back to the last-segment URI tracked via lastSegPath.
    if concat_rel:
        from urllib.parse import quote as _quote
        concat_uri = f"/api/studio/outputs/serve?path={_quote(concat_rel, safe='')}"
        yield f"data: {_json.dumps({'type': 'concat', 'path': concat_rel, 'uri': concat_uri, 'ok': True})}\n\n"

    # Rotate after all links are written
    await asyncio.to_thread(_rotate_outputs, out_dir)
    done_evt: dict = {
        "type": "done", "total": total,
        "ok_count": ok_count, "error_count": err_count,
    }
    if concat_rel:
        done_evt["concat_path"] = concat_rel   # kept for backward compat
    yield f"data: {_json.dumps(done_evt)}\n\n"


# ── Document-to-Audiobook ─────────────────────────────────────────────────────
# Per-job registry: {state, segments_done, total_segments, cancel (threading.Event),
#                    mp3_path, filename, error}
_doc_tts_jobs: dict[str, dict] = {}
_doc_tts_jobs_lock = threading.Lock()


def _run_doc_tts_job(
    job_id: str,
    body: "DocumentTTSRequest",
    segments: list[str],
    full_text: str,
    doc: dict,
    db,
    cfg,
) -> None:
    """Background worker: synthesise all segments and update _doc_tts_jobs."""
    with _doc_tts_jobs_lock:
        job = _doc_tts_jobs.get(job_id)
    if job is None:
        return

    cancel_event: threading.Event = job["cancel"]
    out_dir = Path(cfg.data_dir) / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)

    espeak_voice = _ESPEAK_VOICE_MAP.get(body.voice, "en+f4")
    wpm          = max(80, min(400, int(175 * body.speed)))
    kokoro_voice = _resolve_kokoro_voice(body.voice)

    # Determine best available TTS engine once (Premium > AI > Kokoro > espeak-ng).
    premium_ok = _is_premium_tts_enabled(cfg)

    ai_ok = False
    if not premium_ok:
        try:
            import httpx
            probe = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
            ai_ok = probe.status_code == 200
        except Exception:
            ai_ok = False

    kokoro_engine = None if (premium_ok or ai_ok) else _get_kokoro()

    try:
        import soundfile as _sf
    except ImportError:
        _sf = None  # type: ignore[assignment]

    wav_paths: list[Path] = []
    tmp_dir = Path(tempfile.mkdtemp())

    try:
        for idx, seg in enumerate(segments):
            # Honour cancellation between segments.
            if cancel_event.is_set():
                with _doc_tts_jobs_lock:
                    _doc_tts_jobs[job_id]["state"] = "cancelled"
                return

            wav_path    = tmp_dir / f"seg_{idx:04d}.wav"
            synthesised = False

            # Strategy 0: Premium TTS engine
            if premium_ok:
                try:
                    audio_bytes = _call_premium_tts_sync(seg, body.voice, body.speed, cfg)
                    if audio_bytes:
                        # Premium engine returns MP3 — write directly, skip WAV step
                        mp3_path = tmp_dir / f"seg_{idx:04d}.mp3"
                        mp3_path.write_bytes(audio_bytes)
                        wav_paths.append(mp3_path)
                        synthesised = True
                except Exception as pe:
                    logger.warning("Premium TTS failed on segment %d: %s", idx, pe)

            # Strategy 1: AI server TTS
            if ai_ok and not synthesised:
                try:
                    import httpx as _hx
                    r = _hx.post(
                        f"{cfg.serving.base_url}/audio/speech",
                        json={"model": cfg.serving.tts_model,
                              "input": seg, "voice": body.voice,
                              "response_format": "wav", "speed": body.speed},
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
                    raise RuntimeError(
                        f"espeak-ng failed on segment {idx}: {res.stderr}"
                    )

            wav_paths.append(wav_path)
            with _doc_tts_jobs_lock:
                _doc_tts_jobs[job_id]["segments_done"] = idx + 1

        # Check cancellation before the expensive ffmpeg step.
        if cancel_event.is_set():
            with _doc_tts_jobs_lock:
                _doc_tts_jobs[job_id]["state"] = "cancelled"
            return

        # ── Concatenate all WAVs → single high-quality MP3 ───────────────────
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
             "-codec:a", "libmp3lame", "-q:a", "2",
             str(mp3_path)],
            capture_output=True, timeout=300,
        )
        if ff.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {ff.stderr.decode()[:300]}")

        # Hard-link into the library BEFORE rotation so the file survives the
        # rolling 50-output window regardless of rotation timing.
        _ab_rel = _link_output_sync(mp3_path)
        _rotate_outputs(out_dir)

        # Amendment-1: register as searchable library document in background.
        doc_title = doc.get("title") or "audiobook"
        from orivellum.api.executor import get_executor as _gex
        _gex().submit(
            _register_output_bg, mp3_path, full_text[:8000], "mp3",
            f"Audiobook: {doc_title}", prelinked_rel=_ab_rel,
            origin_id=body.doc_id,
        )

        # Push notification — best-effort via executor.
        try:
            from orivellum.capabilities.push import notify_push_best_effort as _push
            from orivellum.api.executor import submit_bg as _submit_bg_push_doc
            _doc_label = (doc.get("title") or body.doc_id)[:50]
            _submit_bg_push_doc(
                _push,
                db, "🎙️ Audiobook ready",
                f'"{_doc_label}" audiobook is ready to play',
                {"screen": f"library/{body.doc_id}"},
                kind="background", label="push:doc_tts_ready",
            )
        except Exception:
            pass

        # ── Mark job done ─────────────────────────────────────────────────────
        rel_path = str(mp3_path.relative_to(out_dir))
        with _doc_tts_jobs_lock:
            _doc_tts_jobs[job_id].update({
                "state":    "done",
                "mp3_path": rel_path,
                "filename": mp3_name,
            })

    except FileNotFoundError:
        import sys as _sys
        hint = (
            "espeak-ng is not installed. Run scripts\\setup-windows.ps1 to install it."
            if _sys.platform == "win32"
            else "espeak-ng is not installed. Run: nix-env -iA nixpkgs.espeak-ng"
        )
        logger.error("Document TTS job %s: %s", job_id, hint)
        with _doc_tts_jobs_lock:
            _doc_tts_jobs[job_id].update({"state": "failed", "error": hint})

    except Exception as exc:
        logger.error("Document TTS job %s failed: %s", job_id, exc)
        with _doc_tts_jobs_lock:
            _doc_tts_jobs[job_id].update({
                "state": "failed",
                "error": str(exc)[:300],
            })

    finally:
        # Clean up temp WAVs regardless of outcome.
        for p in wav_paths:
            p.unlink(missing_ok=True)
        try:
            (tmp_dir / "concat.txt").unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass


class DocumentTTSRequest(BaseModel):
    doc_id: str
    voice: str = "af_heart"
    speed: float = 1.0
    max_segments: int = 60  # cap at ~90 000 chars / ~1 hour of reading
    return_url: bool = False  # kept for backward-compat; ignored in async flow


@router.post("/studio/tts/document")
def synthesize_document(body: DocumentTTSRequest):
    """Kick off async audiobook generation; returns {job_id, total_segments} immediately.

    The heavy TTS + ffmpeg work runs in the shared background executor so the
    HTTP response returns in milliseconds.  Poll
    GET  /studio/tts/document/{job_id}/status  for progress.
    Send DELETE /studio/tts/document/{job_id}  to cancel.
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

    # ── Create job entry ──────────────────────────────────────────────────────
    job_id       = str(uuid.uuid4())
    cancel_event = threading.Event()

    with _doc_tts_jobs_lock:
        _doc_tts_jobs[job_id] = {
            "state":          "running",
            "segments_done":  0,
            "total_segments": len(segments),
            "cancel":         cancel_event,
            "mp3_path":       None,
            "filename":       None,
            "error":          None,
        }

    from orivellum.api.executor import _tracked_submit
    _tracked_submit(
        _run_doc_tts_job,
        job_id, body, segments, full_text, doc, db, cfg,
        kind="tts",
        label=f"audiobook:{(doc.get('title') or body.doc_id)[:30]}",
    )

    return {"job_id": job_id, "total_segments": len(segments)}


@router.get("/studio/tts/document/{job_id}/status")
def get_doc_tts_status(job_id: str):
    """Return current progress for a document TTS job."""
    with _doc_tts_jobs_lock:
        raw = _doc_tts_jobs.get(job_id)
    if raw is None:
        raise HTTPException(404, f"TTS job {job_id!r} not found")
    # Never serialise the threading.Event.
    job = {k: v for k, v in raw.items() if k != "cancel"}
    return {"job_id": job_id, **job}


@router.delete("/studio/tts/document/{job_id}")
def cancel_doc_tts(job_id: str):
    """Signal cancellation for a running document TTS job."""
    with _doc_tts_jobs_lock:
        job = _doc_tts_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"TTS job {job_id!r} not found")
    if job["state"] != "running":
        return {"ok": True, "state": job["state"]}
    job["cancel"].set()
    with _doc_tts_jobs_lock:
        _doc_tts_jobs[job_id]["state"] = "cancelling"
    return {"ok": True, "state": "cancelling"}


# ── Outputs ───────────────────────────────────────────────────────────────────

@router.get("/studio/outputs")
def list_outputs():
    cfg = get_config()
    out_dir = Path(cfg.data_dir) / "outputs"
    if not out_dir.exists():
        return {"outputs": [], "count": 0}
    files = sorted(out_dir.rglob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    # Limit ffprobe calls per request; the in-process cache makes subsequent
    # calls free after the first probe of each file.
    probe_budget = 10
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

        # ── Human-readable label ──────────────────────────────────────────
        # Distinguish merged narrations (tts_full_*.mp3), audiobook TTS files
        # (named after the document title), and raw synthesis clips (tmp*.mp3).
        name = f.name
        if kind == "audio":
            if name.startswith("tts_full_"):
                label: "str | None" = "Full narration"
            elif name.startswith("tmp") and name.endswith(".mp3"):
                label = "Clip"
            else:
                label = "Audiobook"
        else:
            label = None

        # ── Duration (best-effort) ────────────────────────────────────────
        duration_sec: "float | None" = None
        if kind == "audio" and probe_budget > 0:
            duration_sec = _probe_duration(f)
            if duration_sec is not None or probe_budget > 0:
                probe_budget -= 1

        rel = str(f.relative_to(out_dir))
        result.append({
            "name": f.name,
            "path": rel,
            "size_bytes": sz,
            "kind": kind,
            "label": label,
            "duration_sec": duration_sec,
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


def _probe_vision_model_listed(base_url: str, model_name: str) -> bool:
    """Return True when *model_name* appears in the AI server's /models list.

    This is stronger than a bare server-up check: it validates that the
    configured vision model is actually loaded and served, not just that
    the endpoint is reachable.  Returns False on any error or timeout.
    """
    import json as _json
    import urllib.request as _ur

    if not model_name:
        return False
    try:
        models_url = base_url.rstrip("/") + "/models"
        with _ur.urlopen(models_url, timeout=_STATUS_PROBE_TIMEOUT) as resp:
            body = _json.loads(resp.read().decode("utf-8"))
        # OpenAI-compatible response: {"data": [{"id": "..."}]}
        # Lemonade may also return {"models": [...]} or a flat list of strings.
        if isinstance(body, list):
            entries: list = body
        elif isinstance(body, dict):
            entries = body.get("data") or body.get("models") or []
        else:
            entries = []
        loaded_ids: set[str] = set()
        for e in entries:
            if isinstance(e, dict):
                loaded_ids.add(e.get("id") or e.get("name") or "")
            elif isinstance(e, str) and e:
                loaded_ids.add(e)
        return model_name in loaded_ids
    except Exception:
        return False


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


# ── Premium TTS helpers ───────────────────────────────────────────────────────

def _is_premium_tts_enabled(cfg) -> bool:
    """Return True when the premium TTS path is configured AND licensed."""
    url = getattr(cfg.serving, "tts_premium_url", "").strip()
    ack = getattr(cfg.serving, "tts_premium_ack_license", False)
    return bool(url and ack)


async def _call_premium_tts(text: str, voice: str, speed: float, cfg) -> bytes | None:
    """POST to the premium TTS engine and return raw audio bytes on success.

    Supported engines (all expose ``POST /v1/tts``):
      - Fish Audio S2  (http://127.0.0.1:9880)
      - Hume TADA      (http://127.0.0.1:9881)
      - IndexTTS-2     (http://127.0.0.1:9882)
      - Chatterbox     (http://127.0.0.1:9883)

    Guards: ``tts_premium_url`` must be set AND ``tts_premium_ack_license`` must
    be ``True``.  Returns ``None`` (silently) on any failure so the caller falls
    through to the next strategy.
    """
    if not _is_premium_tts_enabled(cfg):
        return None
    premium_url = cfg.serving.tts_premium_url.rstrip("/")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{premium_url}/v1/tts",
                json={
                    "text": text,
                    "voice": voice,
                    "speed": speed,
                    "format": "mp3",
                    "chunk_length": 200,
                    "normalize": True,
                    "latency": "normal",
                },
                headers={"Accept": "audio/mpeg, audio/mp3, audio/*, */*"},
            )
            if resp.status_code == 200 and resp.content:
                ct = resp.headers.get("content-type", "")
                # Accept if content-type is audio, or if the body looks like audio
                # bytes (> 1 KB) — some engines return without a proper MIME type.
                if "audio" in ct or len(resp.content) > 1024:
                    return resp.content
    except Exception as exc:
        logger.debug("Premium TTS unavailable (%s) — falling through", exc)
    return None


def _call_premium_tts_sync(text: str, voice: str, speed: float, cfg) -> bytes | None:
    """Synchronous version of ``_call_premium_tts`` for background worker threads."""
    if not _is_premium_tts_enabled(cfg):
        return None
    premium_url = cfg.serving.tts_premium_url.rstrip("/")
    try:
        import httpx
        resp = httpx.post(
            f"{premium_url}/v1/tts",
            json={
                "text": text,
                "voice": voice,
                "speed": speed,
                "format": "mp3",
                "chunk_length": 200,
                "normalize": True,
                "latency": "normal",
            },
            headers={"Accept": "audio/mpeg, audio/mp3, audio/*, */*"},
            timeout=60,
        )
        if resp.status_code == 200 and resp.content:
            ct = resp.headers.get("content-type", "")
            if "audio" in ct or len(resp.content) > 1024:
                return resp.content
    except Exception as exc:
        logger.debug("Premium TTS (sync) unavailable (%s) — falling through", exc)
    return None


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

    # Resolve vision model for OCR probe (done before pool so the lambda captures it)
    _vision_model_for_probe = (db.get_setting("vision_model", "").strip()
                               or cfg.serving.vision_model)

    pool = _cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="studio-probe")
    try:
        # Resolve premium TTS URL for the probe (empty string = feature off)
        _premium_tts_url = getattr(cfg.serving, "tts_premium_url", "").strip()
        _premium_ack     = getattr(cfg.serving, "tts_premium_ack_license", False)

        futs: dict[str, _cf.Future] = {
            "ai_tts":    pool.submit(_url_probe, ai_tts_url),
            "ai_img":    pool.submit(_url_probe, ai_img_url),
            "a1111":     pool.submit(_url_probe, "http://localhost:7860"),
            "comfy":     pool.submit(_url_probe, "http://localhost:8188"),
            "tesseract": pool.submit(_probe_tesseract_ok),
            # Vision model probe: checks /models list, not just server reachability.
            # Returns False when vision_model is unset (no inference call made).
            "vision_model_listed": pool.submit(
                _probe_vision_model_listed,
                cfg.serving.base_url,
                _vision_model_for_probe,
            ),
        }
        # Premium TTS probe — only fire if URL is configured
        if _premium_tts_url:
            futs["premium_tts"] = pool.submit(_url_probe, _premium_tts_url)
        if custom_url:
            futs["custom"] = pool.submit(_url_probe, custom_url)
            if _is_comfyui_url(custom_url):
                futs["custom_stats"] = pool.submit(_url_probe, custom_stats_url)

        for key, fut in futs.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results[key] = fut.result(timeout=remaining)
            except Exception:
                # Scalar probes (tesseract, vision_model_listed) default to False;
                # URL probes default to (False, None).
                results[key] = False if key in ("tesseract", "vision_model_listed") else (False, None)
    finally:
        # Do NOT wait for threads still blocked on their TCP connect timeout.
        # Threads will finish within _STATUS_PROBE_TIMEOUT (2 s) on their own.
        pool.shutdown(wait=False)

    def _get(key: str, default=None):
        return results.get(key, default)

    # ── TTS strategies ────────────────────────────────────────────────────────
    ai_tts_ok, ai_ms = _get("ai_tts", (False, None))
    # Distinguish "package installed" from "model actually loaded in memory".
    # find_spec only tells us whether the wheel is present; _is_kokoro_loaded()
    # tells us whether the ONNX model was successfully opened and is ready to use.
    kokoro_pkg_ok = importlib.util.find_spec("kokoro_onnx") is not None
    kokoro_ok     = _is_kokoro_loaded()   # True only when neural synthesis is live
    espeak_ok     = bool(shutil.which("espeak-ng"))

    # Premium TTS probe result
    _prem_probe   = _get("premium_tts", (False, None)) if _premium_tts_url else (False, None)
    premium_tts_reachable, prem_ms = (_prem_probe if isinstance(_prem_probe, tuple)
                                      else (bool(_prem_probe), None))
    premium_tts_active = bool(_premium_tts_url and _premium_ack and premium_tts_reachable)

    tts_strategies = [
        {
            "name": "Premium TTS", "key": "premium_tts",
            "available": premium_tts_active, "latency_ms": prem_ms,
            "url": _premium_tts_url or None,
            "license_ack": _premium_ack,
        },
        {"name": "AI Server",   "key": "ai_server",   "available": ai_tts_ok, "latency_ms": ai_ms},
        {"name": "Kokoro ONNX", "key": "kokoro_onnx",  "available": kokoro_ok, "latency_ms": None},
        {"name": "espeak-ng",   "key": "espeak_ng",    "available": espeak_ok, "latency_ms": None},
    ]
    best_tts = next((s["name"] for s in tts_strategies if s["available"]), None)

    # Voice-sample synthesis uses ONLY local engines (Kokoro ONNX or espeak-ng).
    # The AI Server is NOT a valid fallback for GET /studio/voices/{id}/sample —
    # that route calls _synthesize_sample_sync() which only has two paths:
    # Kokoro and espeak. Report this separately so the UI doesn't conflate
    # "AI Server TTS is up" with "voice samples can be generated locally".
    best_sample_engine = (
        "kokoro_onnx" if kokoro_ok else
        "espeak_ng"   if espeak_ok else
        None
    )

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

    # VLM-based OCR — active when vision_model is set AND the /models probe confirmed
    # the model is loaded.  _vision_model_for_probe was resolved before the thread pool
    # so both the probe and the response use the same value.
    _vision_model_cfg = _vision_model_for_probe  # alias for response clarity
    _vlm_reachable = bool(results.get("vision_model_listed", False))
    _vlm_ocr_active = bool(_vision_model_cfg) and _vlm_reachable

    # Active OCR engine: VLM beats Tesseract when both are available.
    if _vlm_ocr_active:
        _active_ocr_engine: str | None = "vlm"
    elif tess_ok and pillow_ok and pytesseract_ok:
        _active_ocr_engine = "tesseract"
    else:
        _active_ocr_engine = None

    # ── ASR ───────────────────────────────────────────────────────────────────
    # The AI server's /audio/transcriptions endpoint lives on the same host as
    # /models — if the AI server is reachable (ai_tts_ok probed /models), we
    # assume it may expose a Whisper endpoint as well.  We report this separately
    # because users may have Whisper loaded or not, even with the server up.
    from orivellum.capabilities.extraction import (
        _is_faster_whisper_loaded as _fw_loaded_check,
    )
    ai_asr_server_ok = bool(ai_tts_ok)   # same server; proxy from TTS probe
    fw_installed = importlib.util.find_spec("faster_whisper") is not None
    fw_loaded = _fw_loaded_check()
    asr_local_model_sz = getattr(cfg.serving, "asr_local_model", "base")

    # Active ASR engine: AI server first, then faster-whisper, then none.
    if ai_asr_server_ok:
        _active_asr_engine: str | None = f"AI server ({cfg.serving.asr_model})"
    elif fw_loaded:
        _active_asr_engine = f"faster-whisper ({asr_local_model_sz})"
    elif fw_installed:
        # Package installed but model not yet loaded — will load on first call.
        _active_asr_engine = f"faster-whisper ({asr_local_model_sz}, pending)"
    else:
        _active_asr_engine = None

    asr_available = ai_asr_server_ok or fw_installed

    return {
        "tts": {
            "available": best_tts is not None,
            "best_strategy": best_tts,
            # kokoro_loaded: True only if the ONNX model is actually in memory and
            # ready to synthesize.  False means the pkg may be installed but the
            # model failed to load — synthesis is currently routed through espeak.
            "kokoro_loaded": kokoro_ok,
            "kokoro_pkg_installed": kokoro_pkg_ok,
            # Premium TTS fields — all False/None when feature is off (zero regression).
            # premium_tts_active: True means the engine is configured, licensed, AND reachable.
            "premium_tts_configured": bool(_premium_tts_url),
            "premium_tts_license_ack": _premium_ack,
            "premium_tts_reachable": premium_tts_reachable,
            "premium_tts_active": premium_tts_active,
            "premium_tts_url": _premium_tts_url or None,
            "strategies": tts_strategies,
            # Voice-sample quality — distinct from general TTS availability.
            # Samples use ONLY local engines; AI Server is NOT a sample fallback.
            # "kokoro_onnx" → neural quality | "espeak_ng" → basic | null → 503
            "sample_engine": best_sample_engine,
            "sample_available": best_sample_engine is not None,
        },
        "image_gen": {
            "available": img_any,
            "backends": img_backends,
        },
        "ocr": {
            # available = at least one OCR path is operational
            "available": _vlm_ocr_active or (tess_ok and pillow_ok and pytesseract_ok),
            # engine: "vlm" (VLM-primary, ~96% DocVQA) |
            #         "tesseract" (fallback, ~72% DocVQA) |
            #         null (no OCR available)
            # "active_engine" is an alias — same value — for new callers.
            "engine": _active_ocr_engine,
            "active_engine": _active_ocr_engine,
            # VLM details
            "vlm_model": _vision_model_cfg if _vision_model_cfg else None,
            "vlm_active": _vlm_ocr_active,
            # Tesseract details
            "tesseract_available": tess_ok and pillow_ok and pytesseract_ok,
            "missing": ocr_missing if not _vlm_ocr_active else [],
        },
        "asr": {
            # available = at least one transcription path is operational
            # (AI server reachable OR faster-whisper installed locally)
            "available": asr_available,
            # active_engine: which engine will handle the NEXT transcription call.
            #   "AI server (whisper-1)"          — AI server is reachable
            #   "faster-whisper (base)"          — model loaded in memory, AI offline
            #   "faster-whisper (base, pending)" — installed, loads on first use
            #   null                             — no ASR available
            "active_engine": _active_asr_engine,
            # AI server ASR details
            "ai_server_available": ai_asr_server_ok,
            "ai_server_model": cfg.serving.asr_model,
            # Local faster-whisper details
            "faster_whisper_installed": fw_installed,
            "faster_whisper_loaded": fw_loaded,
            "faster_whisper_model_size": asr_local_model_sz,
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


_OCR_TIMEOUT = 60  # seconds — runaway Tesseract jobs return 504 after this


@router.post("/studio/ocr")
async def run_ocr(body: OCRRequest):
    """Run Tesseract OCR off the event-loop thread so concurrent requests are never blocked.

    ``pytesseract.image_to_string`` is CPU-bound and can take 5–30 s for
    high-resolution images.  Running it in the thread pool via
    ``asyncio.to_thread`` keeps the event loop free for other requests.
    A 60-second timeout cancels runaway scans and returns HTTP 504.
    """
    import base64
    import io

    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")

    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        raise HTTPException(503, "OCR dependencies (Pillow, pytesseract) not available")

    try:
        _probe_tesseract_cmd()
        img = Image.open(io.BytesIO(data))

        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(pytesseract.image_to_string, img),
                timeout=_OCR_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("OCR timed out after %ds", _OCR_TIMEOUT)
            raise HTTPException(504, f"OCR timed out after {_OCR_TIMEOUT} s — "
                                     "try a smaller or lower-resolution image")

        return {"text": text, "ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"OCR failed: {exc}")
