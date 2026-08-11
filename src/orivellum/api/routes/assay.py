"""ASSAY routes — the governed quality-instrument registry (/api/assay/*).

Registry, runs, findings, gate signatures, and the voice baseline.
Deterministic instruments run synchronously; LLM-backed instruments
(gate.d14, gate.d15\u201317, judge.hierarchical) run in the background —
the run row is the claim, created before dispatch.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.errors import internal_error
from orivellum.api.executor import submit_bg
from orivellum.capabilities import assay
from orivellum.capabilities.assay.judge import JudgeModelError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

# Instruments whose runs make gateway calls — dispatched to the background.
_BACKGROUND_KEYS = {
    "gate.d14",
    "gate.d15",
    "gate.d16",
    "gate.d17",
    "judge.hierarchical",
}


def _require_work(db, work_id: str) -> None:
    if db.get_work(work_id) is None:
        raise HTTPException(status_code=404, detail="work not found")


@router.get("/assay/instruments")
def list_instruments():
    db = get_db()
    assay.seed_instruments(db)
    return {"instruments": [assay.contract_public(i) for i in db.list_assay_instruments()]}


class RunRequest(BaseModel):
    work_id: str
    chapter_id: str | None = None


@router.post("/assay/instruments/{key}/run")
def run_instrument(key: str, req: RunRequest):
    db = get_db()
    cfg = get_config()
    assay.seed_instruments(db)
    instrument = db.get_assay_instrument(key)
    if instrument is None:
        raise HTTPException(status_code=404, detail="instrument not registered")
    _require_work(db, req.work_id)
    try:
        run_id = db.create_assay_run(
            instrument_id=instrument["id"],
            work_id=req.work_id,
            chapter_id=req.chapter_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if key in _BACKGROUND_KEYS:
        def _job() -> None:
            try:
                assay.run_instrument(
                    db, cfg, key=key, work_id=req.work_id,
                    chapter_id=req.chapter_id, run_id=run_id,
                )
            except Exception:
                # run_instrument already marked the run row 'error'.
                logger.exception("assay background run failed: %s", key)

        if not submit_bg(_job, kind="assay", label=f"assay:{key}"):
            db.finish_assay_run(run_id, status="error", error="executor rejected job")
            raise HTTPException(status_code=503, detail="background executor unavailable")
        return {"run_id": run_id, "status": "running", "background": True}

    try:
        run = assay.run_instrument(
            db, cfg, key=key, work_id=req.work_id,
            chapter_id=req.chapter_id, run_id=run_id,
        )
    except (assay.AssayError, JudgeModelError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise internal_error(logger, "assay run failed", exc) from exc
    return {"run": run, "findings": db.list_assay_findings(run["id"]), "background": False}


@router.get("/assay/runs")
def list_runs(
    work_id: str = Query(...),
    instrument_key: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    db = get_db()
    _require_work(db, work_id)
    instrument_id = None
    if instrument_key:
        instrument = db.get_assay_instrument(instrument_key)
        if instrument is None:
            raise HTTPException(status_code=404, detail="instrument not registered")
        instrument_id = instrument["id"]
    return {"runs": db.list_assay_runs(work_id, instrument_id=instrument_id, limit=limit)}


@router.get("/assay/runs/{run_id}")
def get_run(run_id: str):
    db = get_db()
    run = db.get_assay_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": run, "findings": db.list_assay_findings(run_id)}


class SignatureRequest(BaseModel):
    author: str = Field(min_length=1)
    decision: str = Field(default="open", pattern="^(open|go|no_go)$")
    note: str = ""


@router.post("/assay/works/{work_id}/gates/{gate_key}/signature")
def sign_gate(work_id: str, gate_key: str, req: SignatureRequest):
    if gate_key not in ("gate.d14", "gate.d15", "gate.d16", "gate.d17"):
        raise HTTPException(status_code=422, detail="not a signature-bearing gate")
    db = get_db()
    _require_work(db, work_id)
    try:
        sig_id = db.create_assay_signature(
            work_id=work_id, gate_key=gate_key,
            author=req.author, decision=req.decision, note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"signature_id": sig_id, "signature": db.latest_assay_signature(work_id, gate_key)}


@router.get("/assay/works/{work_id}/signatures")
def list_signatures(work_id: str):
    db = get_db()
    _require_work(db, work_id)
    return {"signatures": db.list_assay_signatures(work_id)}


class BaselineRequest(BaseModel):
    reference_text: str | None = None
    character_names: list[str] = Field(default_factory=list)


@router.put("/assay/works/{work_id}/voice-baseline")
def set_voice_baseline(work_id: str, req: BaselineRequest):
    db = get_db()
    _require_work(db, work_id)
    try:
        payload = assay.build_voice_baseline(
            db, work_id,
            reference_text=req.reference_text,
            character_names=req.character_names,
        )
    except assay.AssayError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"baseline": payload}


@router.get("/assay/works/{work_id}/voice-baseline")
def get_voice_baseline(work_id: str):
    db = get_db()
    _require_work(db, work_id)
    baseline = db.get_assay_baseline(work_id, "voice_envelope")
    if baseline is None:
        raise HTTPException(status_code=404, detail="no voice baseline stored")
    return {"baseline": baseline}
