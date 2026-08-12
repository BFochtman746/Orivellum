"""Research-gap routes (Gap Engine G-M1/G-M2) + hygiene dismissals.

Research gaps live in the ``gap`` table with content-hash identity and a
governed lifecycle; corpus-hygiene findings are a separate concept served by
the existing ``/works/{id}/gaps`` endpoint and dismissed via
``/works/{id}/hygiene/dismiss``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_db, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.get("/works/{work_id}/research-gaps")
def list_research_gaps(work_id: str, status: str | None = None):
    """All research gaps for a Work, most severe first."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    import json as _json

    gaps = db.list_gaps(work_id, status=status)
    for g in gaps:
        try:
            g["meta"] = _json.loads(g.get("meta") or "{}")
        except Exception:
            g["meta"] = {}
    return {"work_id": work_id, "gaps": gaps, "total": len(gaps)}


@router.post("/works/{work_id}/research-gaps/citation-scan")
def run_citation_scan(work_id: str):
    """Run the citation-graph closure detector for a Work (zero model calls)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.gap_engine import detect_citation_gaps

    return detect_citation_gaps(work_id, db)


class GapTransitionRequest(BaseModel):
    to_status: str = Field(min_length=1)
    reason: str = ""
    signed_by: str = ""


@router.post("/research-gaps/{gap_id}/transition")
def transition_research_gap(gap_id: str, req: GapTransitionRequest):
    """Apply a lifecycle transition.  Dismissals require reason + signature."""
    db = get_db()
    try:
        row = db.transition_gap(gap_id, req.to_status, reason=req.reason, signed_by=req.signed_by)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return row


@router.get("/research-gaps/{gap_id}/history")
def research_gap_history(gap_id: str):
    """The full transition ledger for one gap."""
    db = get_db()
    if db.get_gap(gap_id) is None:
        raise HTTPException(404, f"Gap {gap_id!r} not found")
    return {"gap_id": gap_id, "transitions": db.list_gap_transitions(gap_id)}


class HygieneDismissRequest(BaseModel):
    finding_key: str = Field(min_length=1)
    reason: str = ""
    signed_by: str = ""


@router.post("/works/{work_id}/hygiene/dismiss")
def dismiss_hygiene(work_id: str, req: HygieneDismissRequest):
    """Persist a hygiene-finding dismissal — it never reappears."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    db.dismiss_hygiene_finding(work_id, req.finding_key, reason=req.reason, signed_by=req.signed_by)
    # Cached hygiene results still contain the dismissed finding — drop them.
    db.invalidate_gap_cache(work_id)
    return {"ok": True, "finding_key": req.finding_key}
