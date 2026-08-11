"""Built-in operation step actions.

Long-running steps poll and must call ``ctx.should_stop()`` between polls,
raising :class:`OperationInterrupted` when pause/cancel was requested so the
runner can revert the step and stop cleanly.
"""

from __future__ import annotations

import logging
import time

from orivellum.capabilities.operations import hooks
from orivellum.capabilities.operations.registry import (
    OpAction,
    OpContext,
    OperationInterrupted,
    register,
)

logger = logging.getLogger("orivellum.operations.builtin")

_PENDING_READINESS = ("imported", "transcribing")


# ── wait_for_extraction ────────────────────────────────────────────────────────


def _wait_for_extraction(ctx: OpContext, params: dict) -> dict:
    """Poll a Work's documents until none are still extracting.

    Once nothing is pending, the outcome is judged honestly instead of just
    reporting whatever happened as success:

    - documents in 'error' fail the step (pass allow_failed=true to proceed
      anyway when the rest of the Work is usable)
    - zero 'ready' documents fails the step — later steps like the audiobook
      render would only fail more confusingly
    """
    work_id = params.get("work_id") or ctx.work_id
    if not work_id:
        raise ValueError("wait_for_extraction needs a work_id")
    timeout_s = float(params.get("timeout_s") or 1800)
    poll_s = float(params.get("poll_s") or 5)
    allow_failed = bool(params.get("allow_failed"))
    deadline = time.monotonic() + timeout_s

    while True:
        # Large limit so the whole Work is enumerated, never a truncated view.
        docs = ctx.db.list_documents(work_id=work_id, limit=100000)
        pending = [d for d in docs if d.get("readiness") in _PENDING_READINESS]
        if not pending:
            by_state: dict[str, int] = {}
            for d in docs:
                r = d.get("readiness") or "unknown"
                by_state[r] = by_state.get(r, 0) + 1
            ready = by_state.get("ready", 0)
            failed = by_state.get("error", 0)
            if failed and not allow_failed:
                raise RuntimeError(
                    f"{failed} document(s) failed extraction ({by_state}). "
                    "Fix or remove them, or set allow_failed to continue anyway."
                )
            if ready == 0:
                raise RuntimeError(
                    f"No documents are ready ({by_state or 'no documents in this Work'}) "
                    "— nothing for the next step to work with."
                )
            return {
                "documents": len(docs),
                "by_readiness": by_state,
                "summary": f"{ready} of {len(docs)} documents ready"
                + (f" ({failed} failed, continuing)" if failed else ""),
            }
        if ctx.should_stop():
            raise OperationInterrupted()
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"Timed out after {int(timeout_s)}s — {len(pending)} document(s) still processing"
            )
        time.sleep(poll_s)


# ── render_audiobook ───────────────────────────────────────────────────────────


def _render_audiobook(ctx: OpContext, params: dict) -> dict:
    """Start (or attach to) a Work audiobook render and wait for it.

    If a render for the Work is already in progress the start route answers
    409 with the live job id — we attach to that job instead of failing. That
    makes pause/resume cheap while the server stays up: pause detaches, resume
    re-attaches to the same live render.

    The job registry is in-memory, so after a server RESTART there is no job
    to re-attach to: resume starts a fresh render, which fast-forwards through
    the work already done via the render's persistent segment cache.
    """
    from fastapi import HTTPException

    studio = hooks.HOOKS.studio
    if studio is None:
        raise RuntimeError("render_audiobook is not available — studio hook not configured")

    work_id = params.get("work_id") or ctx.work_id
    if not work_id:
        raise ValueError("render_audiobook needs a work_id")

    # Forward the COMPLETE render configuration. Step params are persisted in
    # the operations tables, so a resume — including after a server restart —
    # reconstructs exactly the render the user originally asked for. Silently
    # falling back to defaults here would change the audio output mid-run.
    body = studio.WorkAudiobookStartRequest(
        work_id=work_id,
        voice=str(params.get("voice") or "bm_george"),
        speed=float(params.get("speed") or 1.0),
        include_credits=bool(params.get("include_credits", True)),
        acx_mastering=bool(params.get("acx_mastering", True)),
        spatial=params.get("spatial"),
        spatial_mode=params.get("spatial_mode"),
        ambience_doc_id=params.get("ambience_doc_id"),
    )
    try:
        started = studio.start_work_audiobook_async(body)
        job_id = started["job_id"]
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        job_id = detail.get("job_id") if exc.status_code == 409 else None
        if not job_id:
            raise RuntimeError(f"Could not start the render: {exc.detail}") from exc

    poll_s = float(params.get("poll_s") or 5)
    while True:
        with studio._work_tts_jobs_lock:
            job = dict(studio._work_tts_jobs.get(job_id) or {})
        state = job.get("state")
        if state in studio._WORK_TTS_TERMINAL:
            if state == "done":
                return {
                    "job_id": job_id,
                    "output_path": job.get("output_path"),
                    "summary": "Audiobook rendered",
                }
            raise RuntimeError(job.get("error") or f"Render ended in state '{state}'")
        if not job:
            raise RuntimeError("The render job disappeared (server restarted?) — resume to retry")
        if ctx.should_stop():
            # Detach without cancelling the render — it keeps its own segment
            # cache, and resume re-attaches via the 409 path above.
            raise OperationInterrupted()
        time.sleep(poll_s)


# ── notify ─────────────────────────────────────────────────────────────────────


def _notify(ctx: OpContext, params: dict) -> dict:
    if hooks.HOOKS.notify is None:
        raise RuntimeError("notify is not available — notifier hook not configured")
    title = str(params.get("title") or "Operation finished")
    body = str(params.get("body") or "")
    hooks.HOOKS.notify("operation", title, body=body, url="/operations")
    return {"summary": f"Notified: {title}"}


# ── Registration ───────────────────────────────────────────────────────────────


def register_builtin_actions() -> None:
    register(
        OpAction(
            id="wait_for_extraction",
            label="Wait for documents to finish processing",
            description=(
                "Watches every document in the Work and continues once none are "
                "still being read in."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "work_id": {"type": "string", "description": "Work to watch"},
                    "timeout_s": {"type": "number", "description": "Give up after (seconds)"},
                },
                "required": [],
            },
            execute=_wait_for_extraction,
        )
    )
    register(
        OpAction(
            id="render_audiobook",
            label="Render the audiobook",
            description=(
                "Starts an audiobook render for the Work and waits for it to finish. "
                "Re-attaches to an in-progress render instead of starting a duplicate."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "work_id": {"type": "string", "description": "Work to render"},
                    "voice": {"type": "string", "description": "Narrator voice"},
                    "speed": {"type": "number", "description": "Speaking speed"},
                    "include_credits": {
                        "type": "boolean",
                        "description": "Opening/closing credits",
                    },
                    "acx_mastering": {"type": "boolean", "description": "ACX loudness mastering"},
                    "spatial": {
                        "type": "boolean",
                        "description": "Spatial audio (null = Work's saved setting)",
                    },
                    "spatial_mode": {"type": "string", "description": "Spatial preset"},
                    "ambience_doc_id": {"type": "string", "description": "Ambience bed document"},
                },
                "required": [],
            },
            execute=_render_audiobook,
        )
    )
    register(
        OpAction(
            id="notify",
            label="Send a notification",
            description="Posts a browser notification so you know the operation finished.",
            params_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": [],
            },
            execute=_notify,
        )
    )
