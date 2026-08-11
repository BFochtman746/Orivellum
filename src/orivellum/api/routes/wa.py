"""Writing Architect decomposition API — /api/wa/*  (Pipeline M0 / DECOMPOSE).

Exposes the archive coverage inventory, extracted doctrine records
(engine contracts, runtime policies, schemas, voice envelope, POSITION
spec, provenance spec), and the canon-fact proposal queue.

Authority rule: nothing here writes canon.  Proposals stay 'proposed'
until the author explicitly approves or rejects them via PATCH.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.database.wa_store import WAStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wa", dependencies=[Depends(require_auth)])


class DecomposeBody(BaseModel):
    archive_path: str | None = None  # defaults to attached_assets/WRITING_ARCHITECT_*.zip


class ProposalDecisionBody(BaseModel):
    status: str  # approved | rejected | proposed (re-open)


def _default_archive() -> Path | None:
    candidates = sorted(Path("attached_assets").glob("WRITING_ARCHITECT*.zip"))
    return candidates[-1] if candidates else None


@router.post("/decompose")
def run_decompose_route(body: DecomposeBody):
    """Run the full archive decomposition (synchronous; ~seconds)."""
    from orivellum.capabilities.wa_decompose import run_decompose

    db = get_db()
    config = get_config()
    path = Path(body.archive_path) if body.archive_path else _default_archive()
    if path is None or not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Writing Architect archive not found; provide archive_path",
        )
    # Restrict caller-supplied paths to the attached_assets import root.
    assets_root = Path("attached_assets").resolve()
    if body.archive_path and not path.resolve().is_relative_to(assets_root):
        raise HTTPException(
            status_code=422,
            detail="archive_path must point inside attached_assets/",
        )
    data_dir = Path(getattr(config, "data_dir", "data"))
    try:
        return run_decompose(path, db, data_dir=data_dir)
    except Exception as exc:  # surface loudly — never a silent partial run
        logger.exception("WA decompose failed")
        raise HTTPException(status_code=500, detail=f"Decompose failed: {exc}") from exc


@router.get("/inventory")
def list_inventory(status: str | None = Query(default=None)):
    store = WAStore(get_db())
    items = store.list_inventory(status=status)
    return {"items": items, "count": len(items)}


@router.get("/coverage")
def get_coverage():
    return WAStore(get_db()).coverage()


@router.get("/records")
def list_records(record_type: str | None = Query(default=None)):
    store = WAStore(get_db())
    items = store.list_records(record_type=record_type)
    return {"items": items, "count": len(items)}


@router.get("/records/{record_id}")
def get_record(record_id: str):
    rec = WAStore(get_db()).get_record(record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return rec


@router.get("/canon-proposals")
def list_canon_proposals(
    status: str | None = Query(default=None),
    classification: str | None = Query(default=None),
):
    store = WAStore(get_db())
    items = store.list_proposals(status=status, classification=classification)
    return {"items": items, "count": len(items)}


@router.patch("/canon-proposals/{proposal_id}")
def decide_canon_proposal(proposal_id: str, body: ProposalDecisionBody):
    if body.status not in ("approved", "rejected", "proposed"):
        raise HTTPException(status_code=422, detail="status must be approved|rejected|proposed")
    row = WAStore(get_db()).decide_proposal(proposal_id, body.status)
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row
