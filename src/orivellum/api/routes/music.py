"""Music & SFX generation routes — /api/studio/music/*

Bridges the trailer planner's music/SFX prompts to the local generation
sidecar (sidecars/music_gen, loopback-only on Nimo's GPU).

License gating: each model's license terms must be acknowledged BEFORE it
can generate.  Acknowledgements are DB settings (music_license_ack_<id>),
mirroring the consent-gate pattern from voice cloning — per-model, explicit,
and persisted so the user is asked exactly once.

Generation runs as a background job (sidecar renders can take minutes on
GPU); finished audio lands in the Studio outputs directory and is
registered as a searchable library document, exactly like TTS clips.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

# ── Model registry ────────────────────────────────────────────────────────────
# License facts are surfaced verbatim in the UI acknowledgement dialog.
# Keep them accurate — the whole point of the gate is informed consent.
MUSIC_MODELS: dict[str, dict] = {
    "stable_audio_open": {
        "id": "stable_audio_open",
        "name": "Stable Audio Open 1.0",
        "vendor": "Stability AI",
        "license": "Stability AI Community License",
        "license_url": "https://huggingface.co/stabilityai/stable-audio-open-1.0",
        "license_summary": (
            "Free for research, non-commercial use, and commercial use by "
            "individuals or organizations with under $1M in annual revenue. "
            "Larger organizations need a Stability AI Enterprise license. "
            "Verify the current terms at the license URL before shipping work "
            "that includes generated audio. Weights are gated on Hugging Face — "
            "accept the license there and log in (huggingface-cli) on the "
            "machine running the sidecar."
        ),
        "commercial_use": "conditional",
        "max_duration_s": 47,
        "good_for": ["music", "sfx"],
    },
    "musicgen": {
        "id": "musicgen",
        "name": "MusicGen",
        "vendor": "Meta",
        "license": "Code MIT / weights CC-BY-NC 4.0",
        "license_url": "https://huggingface.co/facebook/musicgen-small",
        "license_summary": (
            "The MusicGen CODE is MIT-licensed, but the model WEIGHTS are "
            "CC-BY-NC 4.0 — NON-COMMERCIAL use only. Audio generated with "
            "MusicGen must not be used in commercial releases (including "
            "monetized trailers). Use Stable Audio Open for anything you may "
            "publish commercially."
        ),
        "commercial_use": "no",
        "max_duration_s": 60,
        "good_for": ["music"],
    },
}

_ACK_KEY_PREFIX = "music_license_ack_"

# SFX are short by definition; music beds are bounded by the model cap.
_SFX_MAX_DURATION_S = 15.0

# ── Job registry ──────────────────────────────────────────────────────────────
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_MAX_JOBS = 50


def _prune_jobs_locked() -> None:
    if len(_jobs) <= _MAX_JOBS:
        return
    done = sorted(
        (jid for jid, j in _jobs.items() if j["state"] in ("done", "error")),
        key=lambda jid: _jobs[jid].get("finished_at", 0.0),
    )
    for jid in done[: len(_jobs) - _MAX_JOBS]:
        _jobs.pop(jid, None)


def _license_acked(db, model_id: str) -> bool:
    return db.get_setting(_ACK_KEY_PREFIX + model_id, "false") == "true"


def _music_gen_url(cfg) -> str:
    return str(getattr(cfg.serving, "music_gen_url", "") or "").strip().rstrip("/")


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/studio/music/status")
def music_status():
    """Sidecar reachability + per-model availability and license state.

    The UI hides music generation entirely when ``configured`` is false and
    disables it (with a hint) when ``reachable`` is false.
    """
    cfg = get_config()
    db = get_db()
    url = _music_gen_url(cfg)

    sidecar_models: dict = {}
    reachable = False
    device = None
    if url:
        try:
            import httpx
            resp = httpx.get(f"{url}/health", timeout=3)
            resp.raise_for_status()
            data = resp.json()
            reachable = True
            device = data.get("device")
            sidecar_models = data.get("models", {}) or {}
        except Exception as exc:
            logger.debug("music sidecar health failed: %s", exc)

    models = []
    for mid, spec in MUSIC_MODELS.items():
        side = sidecar_models.get(mid, {}) if isinstance(sidecar_models, dict) else {}
        models.append({
            **spec,
            "license_acked": _license_acked(db, mid),
            "installed": bool(side.get("installed", False)),
            "loaded": bool(side.get("loaded", False)),
            "load_error": side.get("load_error"),
        })

    return {
        "configured": bool(url),
        "reachable": reachable,
        "device": device,
        "models": models,
    }


# ── License acknowledgement ───────────────────────────────────────────────────

class LicenseAckBody(BaseModel):
    accepted: bool


@router.post("/studio/music/licenses/{model_id}/ack")
def acknowledge_music_license(model_id: str, body: LicenseAckBody):
    """Record (or revoke) the user's acknowledgement of a model's license."""
    if model_id not in MUSIC_MODELS:
        raise HTTPException(404, f"Unknown music model {model_id!r}")
    db = get_db()
    db.set_setting(_ACK_KEY_PREFIX + model_id,
                   "true" if body.accepted else "false", actor="user")
    return {"model": model_id, "license_acked": body.accepted}


# ── Generation ────────────────────────────────────────────────────────────────

class GenerateBody(BaseModel):
    prompt: str
    model: str = "stable_audio_open"
    kind: str = "music"          # "music" | "sfx"
    duration_s: float = 30.0
    negative_prompt: str = ""
    title: str = ""              # optional label for the library registration
    work_id: str | None = None   # link the output to a Work


def _run_music_job(job_id: str, body: GenerateBody, url: str, cfg) -> None:
    """Background worker: call the sidecar, store + register the audio."""
    def _fail(msg: str) -> None:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(state="error", error=msg, finished_at=time.time())

    try:
        import httpx
        resp = httpx.post(
            f"{url}/v1/music",
            json={
                "prompt": body.prompt,
                "duration_s": body.duration_s,
                "model": body.model,
                "negative_prompt": body.negative_prompt,
                "kind": body.kind,
            },
            # Diffusion on GPU can legitimately take minutes; connect fast-fails.
            timeout=httpx.Timeout(900.0, connect=10.0),
        )
    except Exception as exc:
        _fail(f"Music engine not reachable: {exc}")
        return

    if resp.status_code != 200 or not resp.content:
        detail = ""
        try:
            detail = resp.json().get("detail", "")
        except Exception:
            detail = (resp.text or "")[:200]
        _fail(f"Music engine error ({resp.status_code}): {detail or 'no detail'}")
        return

    try:
        out_dir = Path(cfg.data_dir) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = "sfx" if body.kind == "sfx" else "music"
        fname = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{job_id[:8]}.wav"
        out_path = out_dir / fname
        out_path.write_bytes(resp.content)

        # Register into the library BEFORE rotation (same order as TTS clips).
        from orivellum.api.routes.studio import _link_output_sync, _rotate_outputs
        title = body.title.strip() or (
            ("Sound effect — " if body.kind == "sfx" else "Trailer music — ")
            + body.prompt[:80]
        )
        prelinked = _link_output_sync(out_path)
        _rotate_outputs(out_dir)

        # Strict registration (unlike the fire-and-forget studio helper):
        # the audio file itself already exists either way, so a registration
        # failure is surfaced as partial success, not silently swallowed.
        registered = True
        warning: str | None = None
        try:
            from orivellum.capabilities.persist import register_and_index
            register_and_index(
                doc_path=out_path,
                text_content=body.prompt,
                kind="audio",
                db=get_db(),
                cfg=cfg,
                title=title,
                work_id=body.work_id,
                provenance_source="studio",
                _prelinked_rel=prelinked or None,
            )
        except Exception as reg_exc:
            registered = False
            warning = f"Audio saved, but library registration failed: {reg_exc}"
            logger.warning("music job %s registration failed: %s", job_id, reg_exc)

        with _jobs_lock:
            job = _jobs.get(job_id)
            if job is not None:
                job.update(
                    state="done",
                    output_path=fname,
                    output_name=fname,
                    engine=resp.headers.get("X-Music-Engine", body.model),
                    registered=registered,
                    warning=warning,
                    finished_at=time.time(),
                )
    except Exception as exc:
        logger.warning("music job %s post-processing failed: %s", job_id, exc)
        _fail(f"Could not store generated audio: {exc}")


@router.post("/studio/music/generate")
def generate_music(body: GenerateBody):
    """Start a music/SFX generation job.  Returns a job id to poll.

    Gates (in order):
      503 — sidecar not configured (music_gen_url empty)
      404 — unknown model
      422 — bad prompt / kind / duration
      403 — model license not acknowledged  ← the license gate
    """
    cfg = get_config()
    db = get_db()
    url = _music_gen_url(cfg)
    if not url:
        raise HTTPException(
            503,
            "Music generation is not configured — set music_gen_url in "
            "config.yaml and start the music sidecar (scripts/start-music-sidecar.ps1).",
        )

    spec = MUSIC_MODELS.get(body.model)
    if spec is None:
        raise HTTPException(404, f"Unknown music model {body.model!r}")

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(422, "prompt must not be empty")
    if len(prompt) > 2000:
        raise HTTPException(422, "prompt too long (max 2000 characters)")
    if body.kind not in ("music", "sfx"):
        raise HTTPException(422, "kind must be 'music' or 'sfx'")
    if body.kind == "sfx" and "sfx" not in spec["good_for"]:
        raise HTTPException(
            422,
            f"{spec['name']} is a music model — use Stable Audio Open for sound effects.",
        )

    cap = float(spec["max_duration_s"])
    if body.kind == "sfx":
        cap = min(cap, _SFX_MAX_DURATION_S)
    if not (0.5 <= float(body.duration_s) <= cap):
        raise HTTPException(
            422, f"duration_s must be between 0.5 and {cap:g} seconds for this request",
        )

    if not _license_acked(db, body.model):
        raise HTTPException(
            403,
            f"The {spec['name']} license has not been acknowledged. Review the "
            f"terms ({spec['license']}) and accept them before generating.",
        )

    body = body.model_copy(update={"prompt": prompt})
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "state": "running",
            "kind": body.kind,
            "model": body.model,
            "prompt": prompt[:200],
            "duration_s": float(body.duration_s),
            "started_at": time.time(),
            "error": None,
            "output_path": None,
        }
        _prune_jobs_locked()

    from orivellum.api.executor import submit_bg
    submit_bg(_run_music_job, job_id, body, url, cfg,
              kind="studio", label=f"music_gen:{job_id[:8]}")
    return {"job_id": job_id, "state": "running"}


@router.get("/studio/music/jobs/{job_id}")
def get_music_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"Music job {job_id!r} not found")
        return {"job_id": job_id, **dict(job)}
