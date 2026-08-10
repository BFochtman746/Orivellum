"""Commonplace notes — /api/notes/*

Frictionless capture into a daily inbox, on-demand processing (the nightly
Night Scriptorium pass does the same), and the derived daily report.
Approval itself happens in the unified review inbox (/api/review).
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api import executor
from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities import notes as notes_cap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_day(day: str | None) -> str:
    if not day:
        return notes_cap.today_str()
    if not _DAY_RE.match(day):
        raise HTTPException(422, "day must be YYYY-MM-DD")
    return day


class CaptureBody(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    day: str | None = None
    source: str = "web"


@router.post("/notes")
def capture_note(body: CaptureBody):
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Note text is empty.")
    db = get_db()
    block = db.create_note_block(text, _valid_day(body.day), body.source[:40])
    return block


@router.get("/notes")
def list_notes(day: str | None = None, status: str | None = None):
    db = get_db()
    d = _valid_day(day)
    blocks = db.list_note_blocks(day=d, status=status)
    counts: dict[str, int] = {}
    for b in blocks:
        counts[b["status"]] = counts.get(b["status"], 0) + 1
    return {"day": d, "blocks": blocks, "counts": counts}


@router.delete("/notes/{block_id}")
def delete_note(block_id: str):
    db = get_db()
    if not db.delete_note_block(block_id):
        raise HTTPException(409, "Only unprocessed inbox notes can be deleted.")
    return {"deleted": True}


@router.post("/notes/process")
def process_now():
    """Classify everything in the inbox in the background (the manual
    'process now' button; the nightly pass does the same)."""
    db = get_db()
    pending = db.list_note_blocks(status="inbox", limit=1)
    if not pending:
        return {"started": False, "detail": "Inbox is empty — nothing to process."}
    cfg = get_config()

    def _worker():
        result = notes_cap.process_inbox(db, cfg)
        logger.info("notes: manual processing done: %s", result)

    executor.submit_bg(_worker, kind="notes", label="Process note inbox")
    return {"started": True}


@router.get("/notes/report")
def get_report(day: str | None = None, rebuild: bool = False):
    db = get_db()
    d = _valid_day(day)
    row = None if rebuild else db.get_note_report(d)
    if row is None:
        cfg = get_config()
        built = notes_cap.build_daily_report(db, cfg, d)
        return {"day": d, "report": built["report"], "block_count": built["block_count"]}
    return {"day": d, "report": row["report"], "updated_at": row["updated_at"]}


@router.get("/notes/policy")
def get_policy():
    return {"categories": notes_cap.CATEGORIES}
