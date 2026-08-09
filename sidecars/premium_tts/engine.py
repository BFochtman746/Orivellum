"""Chatterbox engine wrapper — lazy singleton, GPU-aware, MP3 output.

Chatterbox (Resemble AI, MIT license) is the first-choice model for the
premium slot: proven recipe on AMD Strix Halo via ROCm-enabled PyTorch.
The model is loaded once on first synthesis; if the package or its weights
are unavailable the sidecar stays up and reports the problem via /health
so the main API's cascade simply falls through to Kokoro.

Output contract: MP3 bytes (the main API writes them straight to ``.mp3``
files and its audiobook concat step expects real MP3 frames). Chatterbox
produces waveform tensors, so we save WAV then convert with ffmpeg —
already a hard dependency of the Orivellum stack.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("premium_tts.engine")

_lock = threading.Lock()
_model = None
_device: str | None = None
_load_error: str | None = None

# One synthesis at a time — the model saturates the GPU anyway, and
# serializing keeps VRAM bounded when the audiobook worker fans out.
_synth_lock = threading.Lock()

MAX_TEXT_CHARS = 2000  # Chatterbox degrades on very long inputs; callers chunk.


def _pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():  # covers ROCm builds too (torch.cuda API)
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def get_model():
    """Load Chatterbox once. Returns the model or None (see load_error())."""
    global _model, _device, _load_error
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        try:
            from chatterbox.tts import ChatterboxTTS  # type: ignore[import]
            _device = _pick_device()
            logger.info("Loading Chatterbox on %s (first run downloads weights)…", _device)
            _model = ChatterboxTTS.from_pretrained(device=_device)
            _load_error = None
            logger.info("Chatterbox ready on %s.", _device)
        except Exception as exc:  # ImportError, OOM, missing weights, …
            _load_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Chatterbox unavailable: %s", _load_error)
            _model = None
    return _model


def status() -> dict:
    return {
        "engine": "chatterbox",
        "model_loaded": _model is not None,
        "device": _device,
        "load_error": _load_error,
    }


def synthesize_mp3(
    text: str,
    *,
    ref_audio: Path | None = None,
    speed: float = 1.0,
) -> bytes:
    """Synthesize *text* → MP3 bytes.

    ``ref_audio`` (a consented reference clip) switches Chatterbox into
    zero-shot cloning mode.  ``speed`` outside 1.0 is applied with ffmpeg
    ``atempo`` (pitch-preserving), clamped to its supported 0.5–2.0 range.

    Raises RuntimeError when the model is unavailable — the HTTP layer maps
    that to 503 so the main cascade falls through cleanly.
    """
    model = get_model()
    if model is None:
        raise RuntimeError(_load_error or "Chatterbox model not loaded")
    text = text.strip()
    if not text:
        raise ValueError("text must not be empty")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"text too long for one synthesis call (max {MAX_TEXT_CHARS} chars)")

    import torchaudio  # ships with chatterbox's torch stack

    with _synth_lock:
        kwargs = {}
        if ref_audio is not None:
            kwargs["audio_prompt_path"] = str(ref_audio)
        wav = model.generate(text, **kwargs)

    with tempfile.TemporaryDirectory(prefix="ptts_") as td:
        wav_path = Path(td) / "out.wav"
        mp3_path = Path(td) / "out.mp3"
        torchaudio.save(str(wav_path), wav, model.sr)

        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(wav_path)]
        s = max(0.5, min(2.0, float(speed or 1.0)))
        if abs(s - 1.0) > 0.01:
            cmd += ["-filter:a", f"atempo={s:.3f}"]
        cmd += ["-codec:a", "libmp3lame", "-b:a", "128k", str(mp3_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg mp3 encode failed: {proc.stderr.decode(errors='replace')[:300]}")
        return mp3_path.read_bytes()
