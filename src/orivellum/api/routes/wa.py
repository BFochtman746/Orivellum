"""Writing Architect decomposition API — /api/wa/*  (Pipeline M0 / DECOMPOSE).

Exposes the archive coverage inventory, extracted doctrine records
(engine contracts, runtime policies, schemas, voice envelope, POSITION
spec, provenance spec), and the canon-fact proposal queue.

Authority rule: nothing here writes canon.  Proposals stay 'proposed'
until the author explicitly approves or rejects them via PATCH.
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.configuration.config import ROOT
from orivellum.database.wa_store import WAStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wa", dependencies=[Depends(require_auth)])

# The API server's working directory is not the repo root (workflows run from
# the artifact dir), so attached_assets/ must always be anchored on ROOT.
_ASSETS_ROOT = ROOT / "attached_assets"


class DecomposeBody(BaseModel):
    archive_path: str | None = None  # defaults to attached_assets/WRITING_ARCHITECT_*.zip


class ProposalDecisionBody(BaseModel):
    status: str  # approved | rejected | proposed (re-open)


def _default_archive() -> Path | None:
    # Newest by modification time — name sort would pick the wrong archive
    # (e.g. "..._BUILD_PACKAGE_..." sorts after a newer timestamped upload).
    candidates = sorted(
        _ASSETS_ROOT.glob("WRITING_ARCHITECT*.zip"), key=lambda p: p.stat().st_mtime
    )
    return candidates[-1] if candidates else None


# Hard ceiling for an uploaded archive — the real archive is ~tens of MB;
# 500 MB bounds the damage of a runaway upload without blocking growth.
_MAX_ARCHIVE_BYTES = 500 * 1024 * 1024

# Module-level singleton so the route default doesn't call File() per definition (B008).
_UPLOAD_FILE = File(...)


@router.post("/upload")
async def upload_archive(file: UploadFile = _UPLOAD_FILE):
    """Streaming upload of a Writing Architect archive (.zip).

    Saves into ``attached_assets/`` (the only root ``/decompose`` accepts)
    and returns the stored path so the client can immediately decompose it.
    """
    name = (file.filename or "").strip()
    if not name.lower().endswith(".zip"):
        raise HTTPException(422, "Archive must be a .zip file")

    _ASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:80] or "archive"
    dest = _ASSETS_ROOT / f"WRITING_ARCHITECT_upload_{int(time.time())}_{stem}.zip"
    tmp = dest.with_suffix(".part")
    size = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_ARCHIVE_BYTES:
                    raise HTTPException(
                        413,
                        f"Archive too large (limit {_MAX_ARCHIVE_BYTES // (1024 * 1024)} MB)",
                    )
                fh.write(chunk)
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")
        # Signature check — reject anything that isn't actually a zip before
        # it can ever reach the decomposer.
        if not zipfile.is_zipfile(tmp):
            raise HTTPException(422, "File is not a valid zip archive")
        tmp.rename(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(dest), "size_bytes": size, "filename": dest.name}


@router.post("/decompose")
def run_decompose_route(body: DecomposeBody):
    """Run the full archive decomposition (synchronous; ~seconds)."""
    from orivellum.capabilities.wa_decompose import run_decompose

    db = get_db()
    config = get_config()
    path = Path(body.archive_path) if body.archive_path else _default_archive()
    # Normalize BEFORE the existence check: relative caller paths (e.g.
    # "attached_assets/x.zip") are anchored on ROOT so they resolve the same
    # regardless of the server's cwd, then containment is enforced.
    if body.archive_path:
        if not path.is_absolute():
            path = ROOT / path
        if not path.resolve().is_relative_to(_ASSETS_ROOT.resolve()):
            raise HTTPException(
                status_code=422,
                detail="archive_path must point inside attached_assets/",
            )
    if path is None or not path.exists():
        raise HTTPException(
            status_code=404,
            detail="Writing Architect archive not found; provide archive_path",
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
