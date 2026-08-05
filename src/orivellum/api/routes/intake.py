"""Intake pipeline API routes — /api/intake/*

POST /api/intake           Run the 5-stage intake pipeline for a stored document.
POST /api/intake/research  Trigger on-demand web research for a document.
GET  /api/intake/{doc_id}  Re-fetch a previously computed intake profile.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config
from orivellum.capabilities.intake import run_intake, IntakeProfile

logger = logging.getLogger("orivellum.api.intake")

router = APIRouter(prefix="/api", tags=["intake"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class IntakeRequest(BaseModel):
    doc_id: str
    # Note: research is intentionally excluded here. Stages 1-3 only.
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


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/intake", response_model=IntakeProfileOut)
def run_intake_pipeline(body: IntakeRequest):
    """Run stages 1–3 for an uploaded document (identify, extract, embed).

    Stage 4 (web research) is intentionally excluded from this endpoint.
    Use POST /api/intake/research with confirmed=true for external egress.
    """
    db = get_db()
    cfg = get_config()

    if not db.get_document(body.doc_id):
        raise HTTPException(status_code=404, detail=f"Document {body.doc_id!r} not found")

    try:
        # POST /api/intake runs stages 1-3 only; no external egress.
        profile = run_intake(body.doc_id, db=db, cfg=cfg, research=False)
    except Exception as exc:
        logger.exception("Intake pipeline failed for doc=%s", body.doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _profile_to_out(profile)


@router.post("/intake/research", response_model=IntakeProfileOut)
def run_intake_research(body: ResearchRequest):
    """Trigger on-demand web research for a document (stage 4 only).

    The caller MUST set `confirmed=true` — this is the egress gate that ensures
    users are aware an external HTTP request will be made.
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

    try:
        profile = run_intake(
            body.doc_id,
            db=db,
            cfg=cfg,
            research=True,
            research_query=body.query,
        )
    except Exception as exc:
        logger.exception("Intake research failed for doc=%s", body.doc_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return _profile_to_out(profile)


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
