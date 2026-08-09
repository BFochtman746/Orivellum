"""Audio enhancement — DeepFilterNet3 pre-processing for transcription.

Runs *before* the audio file is sent to Whisper (via Lemonade or faster-whisper)
so that noisy recordings are cleaned first, dramatically improving transcription
accuracy on poor-quality input (phone calls, room recordings, voice memos, etc.).

DeepFilterNet3 (MIT/Apache-2.0) is the highest-quality open-source neural speech
enhancer.  It runs on CPU at ~0.19× RTF — a single modern core handles it
comfortably while Lemonade/Whisper runs in parallel on the NPU/GPU.

Required packages (install once):
    uv add torch torchaudio --extra-index-url https://download.pytorch.org/whl/cpu
    uv add deepfilternet

Without these packages the module is a safe no-op: ``enhance_audio()`` returns
the original path unchanged and ``is_available()`` returns False.

Typical integration::

    from orivellum.capabilities.enhancement import enhance_audio, is_available
    enhanced = enhance_audio(path, output_dir=tmp_dir)
    # Pass enhanced to Whisper; enhanced == path on failure so safe unconditionally.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Lazy singleton ────────────────────────────────────────────────────────────
# Same double-checked-locking pattern as faster-whisper in extraction.py.
# _df_model is:
#   None  — not yet attempted
#   False — attempted and failed (package absent or load error)
#   tuple — (model, df_state) ready to use

_df_lock:  threading.Lock = threading.Lock()
_df_model: object          = None
_last_error: str | None    = None   # why the last probe failed (for diagnostics)

_NATIVE_SR = 48_000   # DeepFilterNet3 native sample rate (full-band)

INSTALL_HINT = (
    "uv add deepfilternet torch torchaudio "
    "--extra-index-url https://download.pytorch.org/whl/cpu"
)


def _get_df_model():
    """Return (model, df_state) or None when unavailable."""
    global _df_model, _last_error
    if _df_model is not None:
        return None if _df_model is False else _df_model
    with _df_lock:
        if _df_model is not None:
            return None if _df_model is False else _df_model
        try:
            from df import init_df  # type: ignore[import]
            logger.info("Loading DeepFilterNet3 model (first call — may download ~7 MB)…")
            model, df_state, _ = init_df()
            _df_model = (model, df_state)
            _last_error = None
            logger.info("DeepFilterNet3 ready.")
        except ImportError as exc:
            _last_error = f"ImportError: {exc}"
            logger.info(
                "deepfilternet not installed — audio enhancement unavailable. "
                "Install with: %s", INSTALL_HINT,
            )
            _df_model = False
        except Exception as exc:
            _last_error = f"{exc.__class__.__name__}: {exc}"
            logger.warning("DeepFilterNet3 failed to load: %s", exc)
            _df_model = False
    return None if _df_model is False else _df_model


def is_available() -> bool:
    """Return True when DeepFilterNet3 can be loaded and is ready to use."""
    return _get_df_model() is not None


def probe(force: bool = False) -> dict:
    """Report DeepFilterNet3 availability with diagnostics.

    With ``force=True`` a previously-cached FAILED probe is discarded and the
    import is attempted fresh (a loaded model is never thrown away), so a
    package installed after server start registers WITHOUT a restart.

    Returns ``{available, error, python, install_hint}`` — ``python`` is the
    interpreter this server runs from, so an environment mismatch (package
    installed into a different Python) is immediately visible.
    """
    global _df_model
    import sys
    if force:
        with _df_lock:
            if _df_model is False:
                _df_model = None
        # Pick up packages installed after interpreter start
        import importlib
        importlib.invalidate_caches()
    available = _get_df_model() is not None
    return {
        "available": available,
        "error": None if available else _last_error,
        "python": sys.executable,
        "install_hint": None if available else INSTALL_HINT,
    }


def enhance_audio(path: Path, output_dir: Path | None = None) -> Path:
    """Enhance *path* with DeepFilterNet3; return the enhanced WAV path.

    On any failure (package missing, model error, IO error) the original *path*
    is returned unchanged so callers never fail silently — audio extraction
    degrades gracefully to unenhanced Whisper.

    The enhanced WAV is written next to *path* (or into *output_dir* when given)
    with the suffix ``_dfn3.wav``.  The caller is responsible for deleting it
    after use — pass a ``tempfile.TemporaryDirectory`` path as *output_dir*.

    Processing chain:
        Input (any SR/channels) → mono → resample to 48 kHz → DeepFilterNet3
        → 48 kHz WAV

    Note: Whisper / faster-whisper happily resamples from 48 kHz to 16 kHz
    internally, so no additional resampling step is needed after enhancement.
    """
    pair = _get_df_model()
    if pair is None:
        return path  # package absent — skip silently

    try:
        import torch          # type: ignore[import]
        import torchaudio     # type: ignore[import]
        from df import enhance  # type: ignore[import]

        model, df_state = pair

        audio, sr = torchaudio.load(str(path))

        # Convert to mono
        if audio.shape[0] > 1:
            audio = torch.mean(audio, dim=0, keepdim=True)

        # Resample to DeepFilterNet3's native rate if needed
        if sr != _NATIVE_SR:
            resampler = torchaudio.transforms.Resample(sr, _NATIVE_SR)
            audio = resampler(audio)

        enhanced = enhance(model, df_state, audio)

        out_dir  = output_dir if output_dir is not None else path.parent
        out_path = Path(out_dir) / f"{path.stem}_dfn3.wav"
        torchaudio.save(str(out_path), enhanced, _NATIVE_SR)

        logger.info(
            "DeepFilterNet3 enhanced %s → %s (%.1f s audio)",
            path.name, out_path.name, enhanced.shape[-1] / _NATIVE_SR,
        )
        return out_path

    except Exception as exc:
        logger.warning(
            "DeepFilterNet3 enhancement failed for %s: %s — using original audio",
            path.name, exc,
        )
        return path
