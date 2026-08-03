"""PKLOS claim ledger REST API — /api/claims/*

Endpoints:
  POST   /api/claims              — capture a new claim (or upsert)
  GET    /api/claims              — list claims (filter by subject, status)
  GET    /api/claims/{id}         — get a single claim with evidence
  PATCH  /api/claims/{id}/status  — transition claim status
  DELETE /api/claims/{id}         — mark claim as UNAVAILABLE (soft delete)
  POST   /api/claims/search       — search claims for context injection

VER-INV-001: A8 claims are never returned by any read endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db

router = APIRouter(prefix="/api/claims", tags=["claims"])

_A8_BLOCKED = "A8"  # Never surface A8 through the API


# ── Request / Response models ──────────────────────────────────────────────────

class ClaimCreate(BaseModel):
    subject: str
    predicate: str
    value: str
    unit: str | None = None
    authority_tier: str = "A7"
    source_id: str | None = None
    conv_id: str | None = None
    ttl_class: str = "DURABLE"
    evidence_text: str | None = None
    meta: dict | None = None


class ClaimStatusPatch(BaseModel):
    status: str
    actor: str = "user"
    reason: str | None = None


class ClaimSearch(BaseModel):
    query: str
    subject: str | None = None
    limit: int = 10


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("")
def create_claim(body: ClaimCreate):
    """Capture a claim.  Upserts if a CURRENT claim already exists for
    (subject, predicate) at equal or lower authority."""
    db = get_db()
    claim_id = db.upsert_claim(
        body.subject,
        body.predicate,
        body.value,
        unit=body.unit,
        authority_tier=body.authority_tier,
        source_id=body.source_id,
        conv_id=body.conv_id,
        ttl_class=body.ttl_class,
        evidence_text=body.evidence_text,
        meta=body.meta,
    )
    claim = db.get_claim(claim_id)
    return {"claim": claim}


@router.get("")
def list_claims(
    subject: str | None = None,
    status: str | None = "CURRENT",
    limit: int = 50,
):
    """List claims.  A8 claims are never returned."""
    db = get_db()
    claims = db.list_claims(subject=subject, status=status, limit=min(limit, 200))
    safe = [c for c in claims if c.get("authority_tier") != _A8_BLOCKED]
    return {"claims": safe, "count": len(safe)}


@router.get("/{claim_id}")
def get_claim(claim_id: str):
    """Get a single claim with its evidence records."""
    db = get_db()
    claim = db.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, f"Claim {claim_id!r} not found")
    if claim.get("authority_tier") == _A8_BLOCKED:
        raise HTTPException(404, f"Claim {claim_id!r} not found")

    # Fetch evidence
    with db._lock:
        evidence = db._conn.execute(
            "SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY created_at",
            (claim_id,),
        ).fetchall()
    with db._lock:
        transitions = db._conn.execute(
            """SELECT * FROM claim_transitions WHERE claim_id=?
               ORDER BY created_at""",
            (claim_id,),
        ).fetchall()

    return {
        "claim": claim,
        "evidence": [dict(e) for e in evidence],
        "transitions": [dict(t) for t in transitions],
    }


@router.patch("/{claim_id}/status")
def update_claim_status(claim_id: str, body: ClaimStatusPatch):
    """Transition a claim's status (CURRENT / STALE / CONFLICTED / UNAVAILABLE)."""
    valid = {"CURRENT", "STALE", "CONFLICTED", "UNAVAILABLE", "ABSTAINED"}
    if body.status not in valid:
        raise HTTPException(422, f"status must be one of {sorted(valid)}")

    db = get_db()
    claim = db.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, f"Claim {claim_id!r} not found")

    changed = db.update_claim_status(
        claim_id, body.status,
        actor=body.actor or "user",
        reason=body.reason,
    )
    return {"claim": db.get_claim(claim_id), "changed": changed}


@router.delete("/{claim_id}")
def delete_claim(claim_id: str):
    """Soft-delete a claim by marking it UNAVAILABLE."""
    db = get_db()
    claim = db.get_claim(claim_id)
    if not claim:
        raise HTTPException(404, f"Claim {claim_id!r} not found")
    db.update_claim_status(claim_id, "UNAVAILABLE", actor="user", reason="deleted")
    return {"ok": True, "claim_id": claim_id}


@router.post("/search")
def search_claims(body: ClaimSearch):
    """Search claims for context injection.  A8 claims are never returned."""
    db = get_db()
    results = db.search_claims_for_context(
        body.query, subject=body.subject, limit=min(body.limit, 20)
    )
    return {"claims": results, "count": len(results)}


@router.get("/subject/{subject}")
def claims_by_subject(subject: str, status: str | None = "CURRENT"):
    """Return all claims for a subject (e.g. 'user_system')."""
    db = get_db()
    claims = db.list_claims(subject=subject, status=status, limit=200)
    safe = [c for c in claims if c.get("authority_tier") != _A8_BLOCKED]
    return {"claims": safe, "count": len(safe), "subject": subject}
