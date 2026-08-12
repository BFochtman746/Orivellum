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

# Module-level singleton so route defaults don't call Depends() per definition (B008).
_DB = Depends(get_db)


class FactCreate(BaseModel):
    statement: str
    classification: str
    signed_by: str
    work_id: str | None = None  # None = global (or series-wide via series_id)
    source_ref: str = ""
    parent_ids: list[str] = []
    established_chapter: int | None = None
    established_offset: int | None = None
    supersedes: str | None = None
    series_id: str | None = None  # scope the fact to ONE series (work_id must be None)
    overrides: str | None = None  # book-scoped fact overriding a series/global fact


class RetractBody(BaseModel):
    signed_by: str
    reason: str = ""


class RatifyBody(BaseModel):
    author: str
    classification: str | None = None
    statement: str | None = None
    source_ref: str | None = None
    work_id: str | None = None
    parent_ids: list[str] | None = None


class RatifyApprovedBody(BaseModel):
    author: str
    work_id: str | None = None


@router.get("/facts")
def list_facts(
    work_id: str | None = Query(default=None),
    series_only: bool = Query(default=False),
    include_series: bool = Query(default=True),
    series_id: str | None = Query(default=None),
    classification: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=1000),
    db=_DB,
):
    facts = CanonStore(db).list_facts(
        work_id=work_id,
        series_only=series_only,
        include_series=include_series,
        series_id=series_id,
        classification=classification.upper() if classification else None,
        status=status,
        limit=limit,
    )
    return {"facts": facts, "count": len(facts)}


@router.get("/counts")
def counts(db=_DB):
    return {"counts": CanonStore(db).counts()}


@router.get("/facts/{fact_id}")
def get_fact(fact_id: str, db=_DB):
    fact = CanonStore(db).get_fact(fact_id)
    if not fact:
        raise HTTPException(404, f"Canon fact {fact_id!r} not found")
    return fact


@router.get("/facts/{fact_id}/ripple")
def fact_ripple(
    fact_id: str,
    work_id: str | None = Query(default=None),
    depth: int | None = Query(default=None, ge=1, le=6),
    db=_DB,
):
    """RIPPLE preview (E12): what changing/retracting this fact would cost.

    Read-only — walks the ATLAS world graph from every node whose evidence
    is linked to this fact and reports affected chapters, characters, and
    downstream facts BEFORE any change is committed.  Series-scoped facts
    (no work of their own) require an explicit ``work_id``.
    """
    from orivellum.capabilities.ripple import RippleError, simulate_ripple

    fact = CanonStore(db).get_fact(fact_id)
    if not fact:
        raise HTTPException(404, f"Canon fact {fact_id!r} not found")
    scope = work_id or fact.get("work_id")
    if not scope:
        raise HTTPException(
            422,
            "this fact is series-scoped — pass ?work_id= to choose the work "
            "whose graph the ripple should walk",
        )
    try:
        return simulate_ripple(db, scope, canon_fact_id=fact_id, depth=depth)
    except RippleError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/facts")
def create_fact(req: FactCreate, db=_DB):
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
            series_id=req.series_id,
            overrides=req.overrides,
        )
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    return fact


@router.post("/proposals/{proposal_id}/ratify")
def ratify_proposal(proposal_id: str, req: RatifyBody, db=_DB):
    """Turn one Writing Architect proposal into a signed canon fact.

    Claims the proposal row (proposed or approved → ratified) and writes
    the fact in one governed transaction; a ratified proposal can never
    be ratified twice.
    """
    author = (req.author or "").strip()
    if not author:
        raise HTTPException(422, "Ratification requires your signature (author)")
    try:
        result = CanonStore(db).ratify_proposal(
            proposal_id,
            decision="approve",
            author=author,
            classification=req.classification,
            statement=req.statement,
            source_ref=req.source_ref,
            work_id=req.work_id,
            parent_ids=req.parent_ids,
        )
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    if result["result"] == "not_found":
        raise HTTPException(404, f"Proposal {proposal_id!r} not found")
    if result["result"] == "conflict":
        raise HTTPException(409, "Proposal was already ratified or rejected")
    return {"ok": True, "fact": result["fact"]}


@router.post("/proposals/ratify-approved")
def ratify_approved_proposals(req: RatifyApprovedBody, db=_DB):
    """Batch-ratify every approved Writing Architect proposal.

    Each proposal is claimed and written in its OWN governed transaction
    (one claim + one fact per write), so one refused fact never blocks the
    rest.  Refusals (e.g. a non-series scope that needs an explicit Work,
    or an INFERRED fact without parents) are reported per proposal, and
    those rows stay 'approved' for the author to ratify individually.
    """
    author = (req.author or "").strip()
    if not author:
        raise HTTPException(422, "Ratification requires your signature (author)")
    store = CanonStore(db)
    with db._lock:
        rows = db._conn.execute(
            "SELECT id FROM wa_canon_proposals WHERE status='approved' "
            "ORDER BY source_path, source_location"
        ).fetchall()
    ids = [r["id"] for r in rows]
    ratified: list[str] = []
    refused: list[dict] = []
    skipped: list[str] = []
    for pid in ids:
        try:
            result = store.ratify_proposal(
                pid, decision="approve", author=author, work_id=req.work_id
            )
        except CanonFactError as e:
            refused.append({"id": pid, "error": str(e)})
            continue
        if result["result"] == "ok":
            ratified.append(pid)
        else:
            skipped.append(pid)  # raced: someone else claimed it meanwhile
    return {
        "ok": True,
        "ratified": ratified,
        "refused": refused,
        "skipped": skipped,
        "counts": {"ratified": len(ratified), "refused": len(refused), "skipped": len(skipped)},
    }


@router.post("/facts/{fact_id}/retract")
def retract_fact(fact_id: str, req: RetractBody, db=_DB):
    try:
        result = CanonStore(db).retract_fact(fact_id, signed_by=req.signed_by, reason=req.reason)
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    if result == "not_found":
        raise HTTPException(404, f"Canon fact {fact_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Fact is not active (already superseded or retracted)")
    return {"ok": True, "id": fact_id, "status": "retracted"}
