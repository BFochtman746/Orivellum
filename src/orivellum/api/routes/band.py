"""BAND — /api/band/* — surgical chapter edits + revision lineage (spec §2.4).

The edit runs synchronously (a handful of gateway calls) in the threadpool;
the per-chapter claim in :mod:`orivellum.capabilities.band` refuses a second
concurrent edit with 409.  Refused edits return 200 with ``committed: false``
plus the gate evidence so the UI can offer an explicit author acceptance.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities.band import (
    BandBusy,
    BandError,
    get_chapter_overview,
    restore_revision,
    surgical_edit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class EditBody(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=2000)
    base_fingerprint: str = Field(min_length=8, max_length=64)
    # Echo of the exact selected text — the server refuses when [start, end)
    # doesn't reproduce it (guards client/server offset-encoding drift).
    band_text: str = Field(min_length=1, max_length=20_000)
    author: str = Field(default="", max_length=200)
    accept_regression: bool = False


class RestoreBody(BaseModel):
    rev: int = Field(ge=1)
    author: str = Field(default="", max_length=200)


@router.get("/band/chapters/{chapter_id}")
def band_chapter_overview(chapter_id: str):
    db = get_db()
    try:
        return get_chapter_overview(db, chapter_id)
    except BandError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/band/revisions/{revision_id}")
def band_revision_detail(revision_id: str):
    db = get_db()
    row = db.get_chapter_revision(revision_id)
    if row is None:
        raise HTTPException(404, f"revision {revision_id!r} not found")
    return row


@router.post("/band/chapters/{chapter_id}/edit")
async def band_edit(chapter_id: str, body: EditBody):
    db, cfg = get_db(), get_config()
    try:
        return await run_in_threadpool(
            surgical_edit, db, cfg,
            chapter_id=chapter_id, start=body.start, end=body.end,
            instruction=body.instruction,
            base_fingerprint=body.base_fingerprint, band_text=body.band_text,
            author=body.author, accept_regression=body.accept_regression,
        )
    except BandBusy as e:
        raise HTTPException(409, str(e)) from e
    except BandError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/band/chapters/{chapter_id}/restore")
async def band_restore(chapter_id: str, body: RestoreBody):
    db = get_db()
    try:
        return await run_in_threadpool(
            restore_revision, db, chapter_id=chapter_id, rev=body.rev,
            author=body.author,
        )
    except BandBusy as e:
        raise HTTPException(409, str(e)) from e
    except BandError as e:
        raise HTTPException(422, str(e)) from e
