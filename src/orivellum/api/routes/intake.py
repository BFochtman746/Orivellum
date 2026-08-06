"""Intake pipeline API routes — /api/intake/*

POST /api/intake                           Run the 5-stage intake pipeline for a stored document.
POST /api/intake/research                  Trigger on-demand web research (returns job_id immediately).
GET  /api/intake/{doc_id}/research-status  Poll the background research job status.
GET  /api/intake/{doc_id}                  Re-fetch a previously computed intake profile.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config
from orivellum.capabilities.intake import run_intake, IntakeProfile

logger = logging.getLogger("orivellum.api.intake")

router = APIRouter(prefix="/api", tags=["intake"])


# ── In-memory research job registry ──────────────────────────────────────────
# Keyed by doc_id (one active research job per document is natural).
#
# Entry shape:
#   status:           "pending" | "running" | "done" | "error"
#   research_summary: str | None
#   research_sources: list[dict]
#   error:            str | None
#   _terminal_at:     float (time.monotonic()) — set when status reaches done/error
#
# Records are lazily evicted once they are older than _TERMINAL_TTL_SECONDS.
# This prevents unbounded growth in long-running servers.
#
# NOTE: module-level dict is safe for single-process deployments.
# For multi-worker setups, move to a DB table (see related task).

_research_jobs: dict[str, dict] = {}
_research_jobs_lock = threading.Lock()

_TERMINAL_TTL_SECONDS = 300   # 5 minutes


def _maybe_evict_terminal_jobs() -> None:
    """Remove terminal jobs older than _TERMINAL_TTL_SECONDS.

    MUST be called with _research_jobs_lock already held.
    """
    now = time.monotonic()
    to_delete = [
        doc_id for doc_id, job in _research_jobs.items()
        if job.get("status") in ("done", "error")
        and (now - job.get("_terminal_at", now)) > _TERMINAL_TTL_SECONDS
    ]
    for doc_id in to_delete:
        del _research_jobs[doc_id]
        logger.debug("Evicted expired research job for doc=%s", doc_id)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class IntakeRequest(BaseModel):
    doc_id: str
    # Stage 4 (web research) is intentionally excluded from POST /intake.
    # Use POST /api/intake/research (with confirmed=true) for external egress.


class ResearchRequest(BaseModel):
    doc_id: str
    query: Optional[str] = None
    confirmed: bool = False   # user must explicitly confirm egress


class SuggestedActionOut(BaseModel):
    id: str
    label: str
    description: str
    kind: str


class IntakeProfileOut(BaseModel):
    doc_id: str
    what_it_is: str
    kind: str
    tier: str
    filed_to: Optional[str]
    filed_to_id: Optional[str]
    confidence: float
    summary: str
    word_count: int
    headings: list[str]
    text_snippet: Optional[str]   # first ~500 chars of extracted text for client-side chat grounding
    suggested_actions: list[SuggestedActionOut]
    research_summary: Optional[str]
    research_sources: list[dict]
    error: Optional[str]


class ResearchJobOut(BaseModel):
    """Response shape for the async research endpoint and status poll."""
    job_id: str        # same as doc_id
    status: str        # pending | running | done | error
    research_summary: Optional[str] = None
    research_sources: list[dict] = []
    error: Optional[str] = None


def _profile_to_out(p: IntakeProfile) -> IntakeProfileOut:
    return IntakeProfileOut(
        doc_id=p.doc_id,
        what_it_is=p.what_it_is,
        kind=p.kind,
        tier=p.tier,
        filed_to=p.filed_to,
        filed_to_id=p.filed_to_id,
        confidence=p.confidence,
        summary=p.summary,
        word_count=p.word_count,
        headings=p.headings,
        text_snippet=p.text_snippet,
        suggested_actions=[
            SuggestedActionOut(id=a.id, label=a.label, description=a.description, kind=a.kind)
            for a in p.suggested_actions
        ],
        research_summary=p.research_summary,
        research_sources=p.research_sources,
        error=p.error,
    )


# ── Background research worker ────────────────────────────────────────────────

def _run_research_background(
    doc_id: str,
    query: Optional[str],
    db,
    cfg,
) -> None:
    """Background thread: run stage-4 research and store result in the job registry.

    Failure handling:
    - ``run_intake(research=True)`` catches ALL Tavily/synthesis exceptions internally
      and returns a profile with ``research_summary=None``.  We detect this and store
      ``status='error'`` so clients don't see a silent success with no data.
    - Any exception that escapes ``run_intake`` itself (unexpected) is also caught and
      stored as ``status='error'``.
    """
    with _research_jobs_lock:
        _research_jobs[doc_id]["status"] = "running"
    try:
        profile = run_intake(doc_id, db=db, cfg=cfg, research=True, research_query=query)

        # run_intake silently swallows Tavily/synthesis failures and returns
        # research_summary=None.  Treat that as an error so the client knows.
        if profile.research_summary is None:
            with _research_jobs_lock:
                _research_jobs[doc_id].update({
                    "status": "error",
                    "research_summary": None,
                    "research_sources": [],
                    "error": (
                        "Web research returned no results. "
                        "Tavily may be unavailable, the API key may be missing, "
                        "or the query timed out. Check server logs for details."
                    ),
                    "_terminal_at": time.monotonic(),
                })
            logger.warning("Research produced no summary for doc=%s — storing as error", doc_id)
            return

        with _research_jobs_lock:
            _research_jobs[doc_id].update({
                "status": "done",
                "research_summary": profile.research_summary,
                "research_sources": profile.research_sources or [],
                "error": None,
                "_terminal_at": time.monotonic(),
            })
        logger.info("Background research done for doc=%s", doc_id)

    except Exception as exc:
        logger.exception("Background research raised unexpectedly for doc=%s", doc_id)
        with _research_jobs_lock:
            _research_jobs[doc_id].update({
                "status": "error",
                "research_summary": None,
                "research_sources": [],
                "error": str(exc),
                "_terminal_at": time.monotonic(),
            })


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/intake", response_model=IntakeProfileOut)
def run_intake_pipeline(body: IntakeRequest):
    """Run stages 1–3 for an uploaded document (identify, extract, embed).

    Stage 4 (web research) is excluded. Use POST /api/intake/research for that.
    """
    db = get_db()
    cfg = get_config()

    if not db.get_document(body.doc_id):
        raise HTTPException(status_code=404, detail=f"Document {body.doc_id!r} not found")

    try:
        profile = run_intake(body.doc_id, db=db, cfg=cfg, research=False)
    except Exception as exc:
        logger.exception("Intake pipeline failed for doc=%s", body.doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _profile_to_out(profile)


@router.post("/intake/research", response_model=ResearchJobOut)
def run_intake_research(body: ResearchRequest):
    """Start on-demand web research in a background thread.

    Returns ``{job_id, status: "pending"}`` immediately.  The caller must poll
    ``GET /api/intake/{doc_id}/research-status`` at ~2 s intervals until status
    is ``done`` or ``error``.

    The caller MUST set ``confirmed=true`` — this is the egress gate that ensures
    users are aware an external HTTP request will be made to Tavily.

    Calling this endpoint again while a job is already ``pending`` or ``running``
    returns the existing job without starting a duplicate thread (idempotent).

    Terminal records (done/error) are kept for up to 5 minutes so clients have
    time to read the result, then lazily evicted.
    """
    if not body.confirmed:
        raise HTTPException(
            status_code=422,
            detail=(
                "Web research makes an external network request to Tavily. "
                "Set confirmed=true to authorise the egress."
            ),
        )

    db = get_db()
    cfg = get_config()

    if not db.get_document(body.doc_id):
        raise HTTPException(status_code=404, detail=f"Document {body.doc_id!r} not found")

    with _research_jobs_lock:
        _maybe_evict_terminal_jobs()
        existing = _research_jobs.get(body.doc_id)
        if existing and existing["status"] in ("pending", "running"):
            # Idempotent — return the in-progress job without spawning another thread
            return ResearchJobOut(job_id=body.doc_id, status=existing["status"])
        # Register a new job
        _research_jobs[body.doc_id] = {
            "status": "pending",
            "research_summary": None,
            "research_sources": [],
            "error": None,
            "_terminal_at": None,
        }

    # Use the application's bounded shared executor so research jobs count
    # toward the process-wide worker cap and show up in the dashboard.
    # If submission fails (executor shutdown race), mark the job as error
    # immediately so it never stays stuck in "pending" indefinitely.
    try:
        from orivellum.api.executor import _tracked_submit
        _tracked_submit(
            _run_research_background,
            body.doc_id, body.query, db, cfg,
            kind="research",
            label=f"intake_research_{body.doc_id[:8]}",
        )
        logger.info("Research job queued for doc=%s", body.doc_id)
    except Exception as _submit_exc:
        logger.error(
            "Could not submit research job to executor for doc=%s: %s",
            body.doc_id, _submit_exc,
        )
        with _research_jobs_lock:
            _research_jobs[body.doc_id].update({
                "status": "error",
                "error": f"Could not queue research job: {_submit_exc}",
                "_terminal_at": time.monotonic(),
            })
        return ResearchJobOut(
            job_id=body.doc_id,
            status="error",
            error=f"Could not queue research job: {_submit_exc}",
        )

    return ResearchJobOut(job_id=body.doc_id, status="pending")


@router.get("/intake/{doc_id}/research-status", response_model=ResearchJobOut)
def get_research_status(doc_id: str):
    """Poll the status of a background research job.

    Returns current status: ``pending`` | ``running`` | ``done`` | ``error``.
    When ``status == 'done'``, ``research_summary`` and ``research_sources`` are
    populated.  When ``status == 'error'``, ``error`` explains what went wrong.

    Terminal records are evicted lazily after 5 minutes; this endpoint returns
    404 when the job no longer exists (expired or never started).
    """
    with _research_jobs_lock:
        _maybe_evict_terminal_jobs()
        job = dict(_research_jobs.get(doc_id) or {})

    if not job:
        raise HTTPException(
            status_code=404,
            detail="No research job found for this document. POST /api/intake/research first.",
        )

    return ResearchJobOut(
        job_id=doc_id,
        status=job.get("status", "unknown"),
        research_summary=job.get("research_summary"),
        research_sources=job.get("research_sources") or [],
        error=job.get("error"),
    )


@router.get("/intake/{doc_id}", response_model=IntakeProfileOut)
def get_intake_profile(doc_id: str):
    """Re-run intake (stages 1–3) for an existing document — no egress."""
    db = get_db()
    cfg = get_config()

    if not db.get_document(doc_id):
        raise HTTPException(status_code=404, detail=f"Document {doc_id!r} not found")

    try:
        profile = run_intake(doc_id, db=db, cfg=cfg, research=False)
    except Exception as exc:
        logger.exception("Intake profile fetch failed for doc=%s", doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _profile_to_out(profile)
