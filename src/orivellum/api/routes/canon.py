"""Canon authority — /api/canon/*

The classified, sourced, signed fact substrate for the whole trilogy.

- GET  /api/canon/facts            list facts (filter by work/series/class/status)
- GET  /api/canon/facts/{id}       one fact
- POST /api/canon/facts            create a fact (author-signed; guards enforced)
- POST /api/canon/facts/{id}/retract   retract an active fact (author-signed)
- GET  /api/canon/counts           counts by classification × status

Authority rules (refused at the insert path, surfaced as 422):
  HISTORICAL needs a source_ref; INFERRED needs live parent_ids; INVENTED
  needs an author signature.  Revisions must explicitly supersede a live
  fact — there is no silent overwrite.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth
from orivellum.database.canon_store import CanonFactError, CanonStore

router = APIRouter(prefix="/api/canon", tags=["canon"], dependencies=[Depends(require_auth)])


class FactCreate(BaseModel):
    statement: str
    classification: str
    signed_by: str
    work_id: str | None = None  # None = series-wide (whole trilogy)
    source_ref: str = ""
    parent_ids: list[str] = []
    established_chapter: int | None = None
    established_offset: int | None = None
    supersedes: str | None = None


class RetractBody(BaseModel):
    signed_by: str
    reason: str = ""


@router.get("/facts")
def list_facts(
    work_id: str | None = Query(default=None),
    series_only: bool = Query(default=False),
    include_series: bool = Query(default=True),
    classification: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1000),
    db=Depends(get_db),
):
    facts = CanonStore(db).list_facts(
        work_id=work_id,
        series_only=series_only,
        include_series=include_series,
        classification=classification.upper() if classification else None,
        status=status,
        limit=limit,
    )
    return {"facts": facts, "count": len(facts)}


@router.get("/counts")
def counts(db=Depends(get_db)):
    return {"counts": CanonStore(db).counts()}


@router.get("/facts/{fact_id}")
def get_fact(fact_id: str, db=Depends(get_db)):
    fact = CanonStore(db).get_fact(fact_id)
    if not fact:
        raise HTTPException(404, f"Canon fact {fact_id!r} not found")
    return fact


@router.post("/facts")
def create_fact(req: FactCreate, db=Depends(get_db)):
    try:
        fact = CanonStore(db).create_fact(
            statement=req.statement,
            classification=req.classification,
            work_id=req.work_id,
            source_ref=req.source_ref,
            parent_ids=req.parent_ids,
            signed_by=req.signed_by,
            established_chapter=req.established_chapter,
            established_offset=req.established_offset,
            supersedes=req.supersedes,
        )
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    return fact


@router.post("/facts/{fact_id}/retract")
def retract_fact(fact_id: str, req: RetractBody, db=Depends(get_db)):
    try:
        result = CanonStore(db).retract_fact(fact_id, signed_by=req.signed_by, reason=req.reason)
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    if result == "not_found":
        raise HTTPException(404, f"Canon fact {fact_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Fact is not active (already superseded or retracted)")
    return {"ok": True, "id": fact_id, "status": "retracted"}
