"""Music/SFX generation engines — lazy singletons, GPU-aware, WAV output.

Two model backends, each with its own license terms (the MAIN API enforces
per-model license acknowledgement before requests ever reach this sidecar;
this module only cares about loading and generating):

  - ``stable_audio_open`` — Stability AI's Stable Audio Open 1.0 via
    diffusers ``StableAudioPipeline``.  Weights are gated on Hugging Face
    (accept the Stability AI Community License and set HF_TOKEN).
    Generates up to ~47 s of 44.1 kHz stereo audio.  Good at both musical
    beds and sound effects / foley.

  - ``musicgen`` — Meta's MusicGen via transformers
    ``MusicgenForConditionalGeneration``.  Code is MIT but the WEIGHTS are
    CC-BY-NC 4.0 (non-commercial only).  32 kHz mono, best under ~60 s.
    Music only — it is not trained for sound effects.

Only ONE model is kept in memory at a time: loading the other backend
unloads the current one first so VRAM stays bounded on shared GPUs.

Output contract: 16-bit PCM WAV bytes.  The main API stores WAV directly —
its outputs gallery and ffprobe tooling handle WAV natively.
"""
from __future__ import annotations

import io
import logging
import os
import threading

logger = logging.getLogger("music_gen.engine")

_lock = threading.Lock()          # guards load/unload
_synth_lock = threading.Lock()    # one generation at a time — saturates GPU anyway

_loaded_id: str | None = None     # which backend currently holds the weights
_backend = None                   # the pipeline/model object
_device: str | None = None
_load_errors: dict[str, str] = {}  # model_id -> last load error

MODELS = ("stable_audio_open", "musicgen")

# Per-model generation caps (seconds).  Stable Audio Open's architecture
# tops out at ~47 s; MusicGen quality degrades well before 60 s.
MAX_DURATION_S = {"stable_audio_open": 47, "musicgen": 60}

_SAO_REPO = os.environ.get("STABLE_AUDIO_REPO", "stabilityai/stable-audio-open-1.0")
_MUSICGEN_REPO = os.environ.get("MUSICGEN_REPO", "facebook/musicgen-small")


def _pick_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():  # covers ROCm builds too
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _importable(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


def _unload_current() -> None:
    """Drop the currently-loaded backend and free GPU memory (best-effort)."""
    global _backend, _loaded_id
    if _backend is None:
        return
    logger.info("Unloading %s to make room…", _loaded_id)
    _backend = None
    _loaded_id = None
    try:
        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _load(model_id: str):
    """Load *model_id*, unloading any other backend first.  Returns backend or None."""
    global _backend, _loaded_id, _device, _load_errors
    if _loaded_id == model_id and _backend is not None:
        return _backend
    with _lock:
        if _loaded_id == model_id and _backend is not None:
            return _backend
        _unload_current()
        _device = _pick_device()
        try:
            if model_id == "stable_audio_open":
                import torch
                from diffusers import StableAudioPipeline  # type: ignore[import]
                logger.info("Loading Stable Audio Open (%s) on %s…", _SAO_REPO, _device)
                dtype = torch.float16 if _device == "cuda" else torch.float32
                pipe = StableAudioPipeline.from_pretrained(_SAO_REPO, torch_dtype=dtype)
                pipe = pipe.to(_device)
                _backend = pipe
            elif model_id == "musicgen":
                from transformers import (  # type: ignore[import]
                    AutoProcessor, MusicgenForConditionalGeneration,
                )
                logger.info("Loading MusicGen (%s) on %s…", _MUSICGEN_REPO, _device)
                processor = AutoProcessor.from_pretrained(_MUSICGEN_REPO)
                model = MusicgenForConditionalGeneration.from_pretrained(_MUSICGEN_REPO)
                model = model.to(_device)
                _backend = (processor, model)
            else:
                raise ValueError(f"unknown model {model_id!r}")
            _loaded_id = model_id
            _load_errors.pop(model_id, None)
            logger.info("%s ready on %s.", model_id, _device)
        except Exception as exc:  # ImportError, gated-repo 403, OOM, …
            _load_errors[model_id] = f"{type(exc).__name__}: {exc}"
            logger.warning("%s unavailable: %s", model_id, _load_errors[model_id])
            _backend = None
            _loaded_id = None
    return _backend


def status() -> dict:
    """Availability report for /health — never triggers a model download."""
    return {
        "device": _device or _pick_device(),
        "loaded_model": _loaded_id,
        "models": {
            "stable_audio_open": {
                "installed": _importable("diffusers"),
                "loaded": _loaded_id == "stable_audio_open",
                "max_duration_s": MAX_DURATION_S["stable_audio_open"],
                "load_error": _load_errors.get("stable_audio_open"),
                "repo": _SAO_REPO,
            },
            "musicgen": {
                "installed": _importable("transformers"),
                "loaded": _loaded_id == "musicgen",
                "max_duration_s": MAX_DURATION_S["musicgen"],
                "load_error": _load_errors.get("musicgen"),
                "repo": _MUSICGEN_REPO,
            },
        },
    }


def _to_wav_bytes(audio, sample_rate: int) -> bytes:
    """Encode a float waveform (numpy [channels, samples] or [samples]) → 16-bit WAV."""
    import numpy as np
    import soundfile as sf

    arr = np.asarray(audio, dtype="float32")
    if arr.ndim == 2:            # [channels, samples] → [samples, channels]
        arr = arr.T
    arr = np.clip(arr, -1.0, 1.0)
    buf = io.BytesIO()
    sf.write(buf, arr, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def generate_wav(model_id: str, prompt: str, duration_s: float,
                 negative_prompt: str = "") -> tuple[bytes, int]:
    """Generate audio for *prompt* → (wav_bytes, sample_rate).

    Raises ValueError on bad input and RuntimeError when the model cannot
    load or generation fails — the HTTP layer maps those to 400/503.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    if model_id not in MODELS:
        raise ValueError(f"unknown model {model_id!r} (choose from {', '.join(MODELS)})")
    duration_s = float(duration_s)
    cap = MAX_DURATION_S[model_id]
    if not (0.5 <= duration_s <= cap):
        raise ValueError(f"duration_s must be between 0.5 and {cap} for {model_id}")

    # Load AND generate under the same lock: with only one model resident at
    # a time, a concurrent request for the other backend could otherwise
    # unload this one between load and inference (invariant break / OOM).
    with _synth_lock:
        backend = _load(model_id)
        if backend is None:
            raise RuntimeError(_load_errors.get(model_id, f"{model_id} not loaded"))

        if model_id == "stable_audio_open":
            import torch
            pipe = backend
            generator = torch.Generator(device=_device or "cpu")
            result = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                audio_end_in_s=duration_s,
                num_inference_steps=100,
                num_waveforms_per_prompt=1,
                generator=generator,
            )
            audio = result.audios[0].to(torch.float32).cpu().numpy()
            sr = int(pipe.vae.sampling_rate)
            return _to_wav_bytes(audio, sr), sr

        # musicgen
        processor, model = backend
        inputs = processor(text=[prompt], padding=True, return_tensors="pt").to(model.device)
        # MusicGen's audio codec runs at ~50 tokens/second of audio.
        max_new_tokens = max(64, int(duration_s * 50))
        audio_values = model.generate(**inputs, do_sample=True, guidance_scale=3.0,
                                      max_new_tokens=max_new_tokens)
        sr = int(model.config.audio_encoder.sampling_rate)
        audio = audio_values[0].to("cpu").float().numpy()  # [channels, samples]
        return _to_wav_bytes(audio, sr), sr
