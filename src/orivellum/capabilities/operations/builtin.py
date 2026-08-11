"""Built-in operation step actions.

Long-running steps poll and must call ``ctx.should_stop()`` between polls,
raising :class:`OperationInterrupted` when pause/cancel was requested so the
runner can revert the step and stop cleanly.
"""

from __future__ import annotations

import logging
import time

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
    """Poll a Work's documents until none are still extracting."""
    work_id = params.get("work_id") or ctx.work_id
    if not work_id:
        raise ValueError("wait_for_extraction needs a work_id")
    timeout_s = float(params.get("timeout_s") or 1800)
    poll_s = float(params.get("poll_s") or 5)
    deadline = time.monotonic() + timeout_s

    while True:
        docs = ctx.db.list_documents(work_id=work_id, limit=500)
        pending = [d for d in docs if d.get("readiness") in _PENDING_READINESS]
        if not pending:
            by_state: dict[str, int] = {}
            for d in docs:
                r = d.get("readiness") or "unknown"
                by_state[r] = by_state.get(r, 0) + 1
            return {
                "documents": len(docs),
                "by_readiness": by_state,
                "summary": f"{by_state.get('ready', 0)} of {len(docs)} documents ready",
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
    """Start (or re-attach to) a Work audiobook render and wait for it.

    If a render for the Work is already in progress the start route answers
    409 with the live job id — we attach to that job instead of failing, which
    also makes this step resumable: after a pause or restart, resume simply
    re-attaches (and the render itself reuses its persistent segment cache).
    """
    from fastapi import HTTPException

    from orivellum.api.routes import studio

    work_id = params.get("work_id") or ctx.work_id
    if not work_id:
        raise ValueError("render_audiobook needs a work_id")

    body = studio.WorkAudiobookStartRequest(
        work_id=work_id,
        voice=str(params.get("voice") or "bm_george"),
        speed=float(params.get("speed") or 1.0),
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
    from orivellum.api import notifications

    title = str(params.get("title") or "Operation finished")
    body = str(params.get("body") or "")
    notifications.emit("operation", title, body=body, url="/operations")
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
