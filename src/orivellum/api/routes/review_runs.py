"""Series continuity review API — evidence-backed, coverage-honest.

Routes
------
GET   /api/review-runs/modes              → mode + resolution metadata
POST  /api/review-runs                    → create run + durable operation
GET   /api/review-runs                    → recent runs (work/series scoped)
GET   /api/review-runs/{run_id}           → run + coverage + operation state
GET   /api/review-runs/{run_id}/findings  → findings with evidence spans
PATCH /api/review-findings/{finding_id}   → author disposition (closed list)
GET   /api/works/{work_id}/ledger         → book ledger header + items
PATCH /api/ledger-items/{item_id}         → approve / reject a ledger item

Route order: the literal /modes path must precede the /{run_id} catch-all.

Runs execute on the operations runner (one ledger step per book, then a
reconcile step) so they checkpoint, pause/resume, and survive restarts.
The run's stored status only becomes ``done`` when the reconcile step
finalizes; otherwise the response derives an honest ``effective_status``
from the live operation state — a crashed run never masquerades as done.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities import series_review as sr

logger = logging.getLogger("orivellum.api.review_runs")
router = APIRouter(prefix="/api", tags=["review-runs"],
                   dependencies=[Depends(require_auth)])

_OP_STATE_MAP = {
    "pending": "pending",
    "running": "running",
    "paused": "running",
    "failed": "failed",
    "cancelled": "cancelled",
    "done": "running",  # op done but run not finalized → still not 'done'
}


def _with_effective(db, run: dict) -> dict:
    """Attach live operation state + an honest effective status."""
    from orivellum.capabilities.operations import store  # noqa: PLC0415

    op = store.get_operation(db, run["operation_id"]) if run.get("operation_id") else None
    if run["status"] == "done":
        eff = "done"
    elif op:
        eff = _OP_STATE_MAP.get(op["state"], run["status"])
    else:
        eff = run["status"]
    run["effective_status"] = eff
    run["operation"] = (
        {"id": op["id"], "state": op["state"], "steps": op.get("steps", [])}
        if op
        else None
    )
    return run


@router.get("/review-runs/modes")
def review_modes():
    return {
        "modes": list(sr.MODES),
        "resolution_choices": list(sr.RESOLUTION_CHOICES),
        "finding_statuses": list(sr.FINDING_STATUSES),
        "tool_version": sr.TOOL_VERSION,
    }


class CreateRunBody(BaseModel):
    mode: str
    work_id: str | None = None
    series_id: str | None = None
    chapter_id: str | None = None


@router.post("/review-runs")
def create_review_run(body: CreateRunBody):
    from orivellum.capabilities.operations import store
    from orivellum.capabilities.operations.runner import start_operation_run

    db = get_db()
    try:
        run = sr.create_run(
            db,
            mode=body.mode,
            work_id=body.work_id,
            series_id=body.series_id,
            chapter_id=body.chapter_id,
        )
    except sr.SeriesReviewError as exc:
        raise HTTPException(422, str(exc)) from exc

    scope = run.pop("scope")
    steps = [
        {
            "action_id": "series_review.ledger",
            "label": f"Build evidence ledger — {s['title']}",
            "params": {"work_id": s["work_id"]},
        }
        for s in scope
    ]
    steps.append(
        {
            "action_id": "series_review.reconcile",
            "label": "Reconcile across books + coverage manifest",
            "params": {"run_id": run["id"]},
        }
    )
    op_id = store.create_operation(
        db,
        title=f"Continuity review — {body.mode.replace('_', ' ')}",
        steps=steps,
        work_id=run["work_id"],
        params={"run_id": run["id"]},
    )
    from datetime import UTC, datetime

    with db._lock:
        db._conn.execute(
            "UPDATE review_run SET operation_id=?, status='running', updated_at=? WHERE id=?",
            (op_id, datetime.now(UTC).isoformat(), run["id"]),
        )
        db._conn.commit()
    if not start_operation_run(db, get_config(), op_id):
        raise HTTPException(409, "Could not start the review operation.")
    return {"run": _with_effective(db, sr.get_run(db, run["id"])), "scope": scope}


@router.get("/review-runs")
def list_review_runs(
    work_id: str | None = Query(None),
    series_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    db = get_db()
    runs = [_with_effective(db, r)
            for r in sr.list_runs(db, work_id=work_id, series_id=series_id, limit=limit)]
    return {"runs": runs, "count": len(runs)}


@router.get("/review-runs/{run_id}")
def get_review_run(run_id: str):
    db = get_db()
    run = sr.get_run(db, run_id)
    if run is None:
        raise HTTPException(404, "Review run not found")
    return {"run": _with_effective(db, run)}


@router.get("/review-runs/{run_id}/findings")
def get_review_findings(
    run_id: str,
    status: str | None = Query(None),
    severity: str | None = Query(None),
):
    db = get_db()
    if sr.get_run(db, run_id) is None:
        raise HTTPException(404, "Review run not found")
    findings = sr.list_findings(db, run_id, status=status, severity=severity)
    return {"findings": findings, "count": len(findings)}


class DispositionBody(BaseModel):
    status: str
    resolution: str | None = None
    note: str = ""


@router.patch("/review-findings/{finding_id}")
def disposition_finding(finding_id: str, body: DispositionBody):
    db = get_db()
    try:
        finding = sr.set_finding_disposition(
            db, finding_id, status=body.status,
            resolution=body.resolution, note=body.note,
        )
    except sr.SeriesReviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    if finding is None:
        raise HTTPException(404, "Finding not found")
    return {"finding": finding}


@router.get("/works/{work_id}/ledger")
def get_work_ledger(work_id: str, kind: str | None = Query(None)):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, "Work not found")
    ledger = sr.get_ledger(db, work_id)
    items = sr.list_ledger_items(db, work_id, kind=kind) if ledger else []
    return {"ledger": ledger, "items": items, "count": len(items)}


class LedgerItemBody(BaseModel):
    status: str


@router.patch("/ledger-items/{item_id}")
def review_ledger_item(item_id: str, body: LedgerItemBody):
    db = get_db()
    try:
        item = sr.set_ledger_item_status(db, item_id, body.status)
    except sr.SeriesReviewError as exc:
        raise HTTPException(422, str(exc)) from exc
    if item is None:
        raise HTTPException(404, "Ledger item not found")
    return {"item": item}
