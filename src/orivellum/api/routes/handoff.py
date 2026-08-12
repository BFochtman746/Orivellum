"""Handoff contract API — End-State Packages, Opening Contracts, Audit runs.

Routes
------
GET    /api/works/{work_id}/handoff/package           → latest End-State Package
GET    /api/works/{work_id}/handoff/packages          → all packages
POST   /api/works/{work_id}/handoff/package           → build new package
POST   /api/works/{work_id}/handoff/package/{pkg_id}/ratify
POST   /api/works/{work_id}/handoff/package/{pkg_id}/intent

GET    /api/works/{work_id}/handoff/contract          → latest Opening Contract
GET    /api/works/{work_id}/handoff/contracts         → all contracts
POST   /api/works/{work_id}/handoff/contract          → build new contract

GET    /api/handoff-audits                            → list audits (query: prior_work_id)
POST   /api/handoff-audits                            → run audit
GET    /api/handoff-audits/{audit_id}                 → audit + coverage
GET    /api/handoff-audits/{audit_id}/findings        → findings for audit
PATCH  /api/handoff-findings/{finding_id}             → resolve finding

GET    /api/series/{series_id}/handoff-map            → per-seam health map
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities import handoff as hf

logger = logging.getLogger("orivellum.api.handoff")
router = APIRouter(prefix="/api", tags=["handoff"],
                   dependencies=[Depends(require_auth)])


# ── Pydantic models ──────────────────────────────────────────────────────────


class IntentBody(BaseModel):
    author_intent: str


class ContractBody(BaseModel):
    prior_package_id: str | None = None


class AuditBody(BaseModel):
    prior_work_id: str
    successor_work_id: str
    prior_package_id: str | None = None
    successor_contract_id: str | None = None


class ResolveBody(BaseModel):
    status: str
    resolution_note: str = ""


# ── Package endpoints ────────────────────────────────────────────────────────


@router.get("/works/{work_id}/handoff/packages")
def list_packages(work_id: str):
    db = get_db()
    return {"packages": hf.list_packages(db, work_id)}


@router.get("/works/{work_id}/handoff/package")
def get_latest_package(work_id: str):
    db = get_db()
    pkg = hf.latest_package(db, work_id)
    if pkg is None:
        raise HTTPException(404, "No End-State Package found for this work")
    return {"package": pkg}


@router.post("/works/{work_id}/handoff/package", status_code=201)
def build_package(work_id: str):
    db = get_db()
    cfg = get_config()
    try:
        pkg = hf.build_end_state_package(db, cfg, work_id)
    except hf.HandoffError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"package": pkg}


@router.post("/works/{work_id}/handoff/package/{pkg_id}/ratify")
def ratify_package(work_id: str, pkg_id: str):
    db = get_db()
    try:
        pkg = hf.ratify_package(db, pkg_id, actor="author")
    except hf.HandoffError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"package": pkg}


@router.post("/works/{work_id}/handoff/package/{pkg_id}/intent")
def update_intent(work_id: str, pkg_id: str, body: IntentBody):
    db = get_db()
    pkg = hf.get_package(db, pkg_id)
    if not pkg or pkg["work_id"] != work_id:
        raise HTTPException(404, "Package not found")
    updated = hf.update_package_intent(db, pkg_id, body.author_intent)
    return {"package": updated}


# ── Opening Contract endpoints ───────────────────────────────────────────────


@router.get("/works/{work_id}/handoff/contracts")
def list_contracts(work_id: str):
    db = get_db()
    return {"contracts": hf.list_contracts(db, work_id)}


@router.get("/works/{work_id}/handoff/contract")
def get_latest_contract(work_id: str):
    db = get_db()
    c = hf.latest_contract(db, work_id)
    if c is None:
        raise HTTPException(404, "No Opening Contract found for this work")
    return {"contract": c}


@router.post("/works/{work_id}/handoff/contract", status_code=201)
def build_contract(work_id: str, body: ContractBody):
    db = get_db()
    cfg = get_config()
    try:
        c = hf.build_opening_contract(
            db, cfg, work_id, prior_package_id=body.prior_package_id
        )
    except hf.HandoffError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"contract": c}


# ── Audit endpoints ──────────────────────────────────────────────────────────


@router.get("/handoff-audits")
def list_audits(
    prior_work_id: str = Query(None),
    successor_work_id: str = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    db = get_db()
    audits = hf.list_audits(
        db,
        prior_work_id=prior_work_id,
        successor_work_id=successor_work_id,
        limit=limit,
    )
    return {"audits": audits}


@router.post("/handoff-audits", status_code=201)
def run_audit(body: AuditBody):
    db = get_db()
    cfg = get_config()
    try:
        audit = hf.run_handoff_audit(
            db,
            cfg,
            body.prior_work_id,
            body.successor_work_id,
            prior_package_id=body.prior_package_id,
            successor_contract_id=body.successor_contract_id,
        )
    except hf.HandoffError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"audit": audit}


@router.get("/handoff-audits/{audit_id}")
def get_audit(audit_id: str):
    db = get_db()
    audit = hf.get_audit(db, audit_id)
    if audit is None:
        raise HTTPException(404, "Audit not found")
    return {"audit": audit}


@router.get("/handoff-audits/{audit_id}/findings")
def get_findings(audit_id: str):
    db = get_db()
    audit = hf.get_audit(db, audit_id)
    if audit is None:
        raise HTTPException(404, "Audit not found")
    findings = hf.list_findings(db, audit_id)
    return {"findings": findings, "audit": audit}


@router.patch("/handoff-findings/{finding_id}")
def resolve_finding(finding_id: str, body: ResolveBody):
    db = get_db()
    try:
        updated = hf.resolve_finding(
            db, finding_id, body.status, body.resolution_note
        )
    except hf.HandoffError as exc:
        raise HTTPException(422, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Finding not found")
    return {"finding": updated}


# ── Series handoff map ───────────────────────────────────────────────────────


@router.get("/series/{series_id}/handoff-map")
def get_handoff_map(series_id: str):
    db = get_db()
    return {"seams": hf.series_handoff_map(db, series_id)}
