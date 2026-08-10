"""Spatial audio treatment for audiobook renders.

Applied at render time only — the dry TTS segment cache is never modified.

Stages (all ffmpeg-based, all best-effort with non-spatial fallback):
  1. Per-part constant-power panning at concat time: each chapter's cast
     voice gets a stable, distinct stereo position; the narrator stays
     center.  Positions are derived deterministically from the voice ID so
     re-renders place every character in the same spot.
  2. Optional post-mastering polish for headphone listeners ("wide" mode):
     a conservative Haas-style stereo widen with a true-peak limiter.
  3. Optional ambience/music bed looped under the narration, ducked below
     speech via sidechain compression (duck spec from the owner's research
     doc: ~-20 dB under speech, fast attack / slow release) and mixed at low
     level so the QA gate's silence/clipping checks still hold.
"""

from __future__ import annotations

import hashlib
import logging
import math
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

SPATIAL_MODES = ("subtle", "wide")

# Maximum pan offset for cast voices (−1 = hard left, +1 = hard right).
# Kept conservative so dialogue never feels detached from the narration.
_MAX_PAN = 0.35

# Fixed slots keep cast voices clearly separated; a small hash-derived jitter
# makes two voices that land in the same slot still distinguishable.
_PAN_SLOTS = (-0.35, 0.35, -0.22, 0.22, -0.30, 0.30, -0.14, 0.14)

# Ambience bed level before ducking (linear gain ≈ −10.5 dB) and the limiter
# ceiling matching the mastering true-peak target (−3 dBTP ≈ 0.708 linear).
_AMBIENCE_GAIN = 0.30
_LIMIT_LINEAR = 0.708

_FFMPEG_TIMEOUT = 600


def voice_pan(voice_id: str | None, narrator_voice: str) -> float:
    """Deterministic stereo position in [−_MAX_PAN, +_MAX_PAN] for a voice.

    The narrator (and silence parts, passed as None) always sits dead center.
    Cast voices hash to a stable slot plus a tiny jitter so distinct voices
    get distinct positions across renders.
    """
    if not voice_id or voice_id == narrator_voice:
        return 0.0
    digest = hashlib.sha256(voice_id.encode()).digest()
    slot = _PAN_SLOTS[digest[0] % len(_PAN_SLOTS)]
    jitter = ((digest[1] / 255.0) - 0.5) * 0.06  # ±0.03
    return max(-_MAX_PAN, min(_MAX_PAN, slot + jitter))


def pan_filter(pan: float) -> str:
    """Constant-power pan filter producing stereo from a mono input."""
    theta = (pan + 1.0) * math.pi / 4.0
    left = math.cos(theta)
    right = math.sin(theta)
    return f"pan=stereo|c0={left:.4f}*c0|c1={right:.4f}*c0"


def spatialize_parts(
    parts: list[Path],
    part_voices: list[str | None],
    narrator_voice: str,
    tmp_dir: Path,
) -> list[Path] | None:
    """Create stereo panned copies of every segment for the concat stage.

    Returns the new part list, or None when ANY conversion fails — mixing
    mono and stereo inputs breaks the concat demuxer, so spatialization is
    all-or-nothing and the caller falls back to the dry mono parts.
    """
    if len(parts) != len(part_voices):
        logger.warning(
            "spatialize_parts: %d parts but %d voice entries — skipping spatial",
            len(parts),
            len(part_voices),
        )
        return None
    out: list[Path] = []
    for idx, (src, voice) in enumerate(zip(parts, part_voices)):
        dst = tmp_dir / f"sp_{idx:06d}.wav"
        filt = pan_filter(voice_pan(voice, narrator_voice))
        try:
            r = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    str(src),
                    "-af",
                    filt,
                    "-ar",
                    "22050",
                    str(dst),
                ],
                capture_output=True,
                timeout=120,
            )
        except Exception as exc:
            logger.warning("Spatial pan failed on part %d: %s", idx, exc)
            return None
        if r.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            logger.warning(
                "Spatial pan failed on part %d: %s",
                idx,
                r.stderr.decode(errors="replace")[:200],
            )
            return None
        out.append(dst)
    return out


def needs_finish_pass(mode: str, ambience_path: Path | None) -> bool:
    """True when a post-mastering ffmpeg pass is required.

    Subtle mode without an ambience bed is complete after panning + mastering.
    """
    return mode == "wide" or ambience_path is not None


def finish_spatial(
    mastered_path: str,
    output_path: str,
    mode: str,
    ambience_path: Path | None = None,
) -> bool:
    """Post-mastering pass: optional headphone widen + ambience bed.

    Runs AFTER loudness mastering; a true-peak limiter at the mastering
    ceiling (−3 dBTP) guards against widen/mix overshoot.  Returns False on
    any failure — the caller keeps the mastered non-spatial-polished file.
    """
    widen = "stereowiden=delay=14:feedback=0.2:crossfeed=0.3:drymix=0.75," if mode == "wide" else ""
    limiter = f"alimiter=limit={_LIMIT_LINEAR}:level=false"

    cmd: list[str] = ["ffmpeg", "-y", "-v", "error", "-i", mastered_path]
    if ambience_path is not None:
        # Loop the bed for the full narration, hold it low, duck it further
        # under speech (sidechain keyed by the narration), then mix.
        cmd += ["-stream_loop", "-1", "-i", str(ambience_path)]
        filter_complex = (
            f"[0:a]{widen}asplit=2[speech][key];"
            f"[1:a]volume={_AMBIENCE_GAIN},aformat=channel_layouts=stereo[amb];"
            "[amb][key]sidechaincompress="
            "threshold=0.015:ratio=20:attack=25:release=800:level_sc=2[duck];"
            "[speech][duck]amix=inputs=2:duration=first:"
            f"dropout_transition=0:normalize=0,{limiter}[out]"
        )
        cmd += ["-filter_complex", filter_complex, "-map", "[out]"]
    else:
        cmd += ["-af", f"{widen}{limiter}"]
    cmd += ["-codec:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-ac", "2", output_path]

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT)
    except Exception as exc:
        logger.warning("Spatial finish pass failed: %s", exc)
        return False
    if r.returncode != 0:
        logger.warning(
            "Spatial finish pass failed: %s",
            r.stderr.decode(errors="replace")[:300],
        )
        return False
    out = Path(output_path)
    return out.exists() and out.stat().st_size > 0


def ambience_path_for_doc(db, cfg, doc_id: str) -> Path | None:
    """Resolve a library document to its stored audio file, if readable."""
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
    except Exception as exc:
        logger.warning("Ambience lookup failed for %s: %s", doc_id, exc)
        return None
    if not row or not row["content_path"]:
        return None
    p = Path(cfg.data_dir) / "library" / row["content_path"]
    return p if p.exists() and p.is_file() else None
