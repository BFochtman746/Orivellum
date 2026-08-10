"""PKLOS — Personal Knowledge and Learning Operating System API routes.

Endpoints:
  POST /api/pklos/inventory          — ingest a Windows system inventory payload
  GET  /api/pklos/inventory          — return current inventory claims (subject=device:a01)
  GET  /api/pklos/status             — enforcement status: claim counts, last harvest, gaps
  GET  /api/pklos/enforcement        — run enforcement check on a sample query (diagnostics)

VER-INV-001: No claim may be presented as fact at a higher authority than its
evidence supports; and where a verification path exists, the system must take it
before asserting.

INV-REQ-001: Win32_VideoController.AdapterRAM must never be used as a VRAM
source on unified-memory architecture.  This endpoint enforces it at the boundary.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from orivellum.api._deps import get_db, require_auth
from orivellum.api.errors import internal_error
from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01
from orivellum.capabilities.pklos.policy_enforcer import PolicyEnforcer

router = APIRouter(prefix="/api/pklos", tags=["pklos"], dependencies=[Depends(require_auth)])
logger = logging.getLogger("orivellum.pklos.routes")


# ── Request / Response models ──────────────────────────────────────────────────

class InventoryPayload(BaseModel):
    """Structured JSON emitted by scripts/inventory_collector.ps1.

    FA-08: extras are forbidden so a client cannot mass-assign arbitrary
    top-level keys into the persisted payload. Any additional metadata the
    collector wants to carry goes through the explicit ``meta`` dict.
    """
    model_config = ConfigDict(extra="forbid")

    collector_version: str = "0.1.0"
    collected_at: str = ""
    subject: str = SUBJECT_DEVICE_A01
    cpu: dict = {}
    memory: dict = {}
    gpu: dict = {}
    vram: dict = {}
    os: dict = {}
    bios: dict = {}
    storage: dict = {}
    installed_models: list = []
    meta: dict = {}


class EnforcementCheck(BaseModel):
    query: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/inventory")
def ingest_inventory(body: InventoryPayload):
    """Ingest a Windows system inventory payload from the PowerShell collector.

    Runs each hardware fact through the ClaimVerifier and stores results in the
    claim ledger.  INV-REQ-001 is enforced: AdapterRAM is excluded from all
    VRAM predicates regardless of what the collector included.
    """
    db = get_db()
    adapter = WindowsInventoryAdapter(db)

    # FA-08: only the explicitly declared fields are persisted; model_extra is
    # no longer merged in (extras are forbidden by the model config above).
    payload = body.model_dump()

    try:
        result = adapter.ingest_inventory(payload)
    except Exception as exc:
        raise internal_error(logger, exc, "PKLOS inventory ingest") from exc

    if result["violations"]:
        logger.warning("INV-REQ-001 violations during ingest: %s", result["violations"])

    return {
        "ok": True,
        "subject": body.subject,
        "collected_at": body.collected_at,
        **result,
    }


@router.get("/inventory")
def get_inventory():
    """Return all current inventory claims for device:a01.

    A8 claims are never returned (VER-INV-001).
    Claims are sorted by predicate for readability.
    """
    db = get_db()
    raw = db.list_claims(subject=SUBJECT_DEVICE_A01, status=None, limit=200)
    claims = [
        c for c in raw
        if c.get("authority_tier") != "A8"
        and c.get("status") in (
            "VERIFIED", "PARTIALLY_VERIFIED", "USER_ASSERTED",
            "RETRIEVED", "CONFLICTED", "STALE", "CURRENT",
        )
    ]
    claims.sort(key=lambda c: c.get("predicate", ""))

    # Build a human-readable summary per claim
    summary = []
    for c in claims:
        display = c.get("meta", {}) or {}
        if isinstance(display, str):
            import json
            try:
                display = json.loads(display)
            except Exception:
                display = {}
        summary.append({
            "predicate": c.get("predicate"),
            "value": c.get("value"),
            "display_value": display.get("normalized_display_value") or c.get("value"),
            "status": c.get("status"),
            "authority": c.get("authority_tier"),
            "confidence": display.get("confidence"),
            "observed_at": display.get("observed_at") or c.get("updated_at"),
        })

    return {"subject": SUBJECT_DEVICE_A01, "claims": summary, "total": len(summary)}


@router.get("/status")
def get_status():
    """Return the current PKLOS enforcement status summary.

    Useful for the governance dashboard and diagnostics.
    """
    db = get_db()
    raw = db.list_claims(subject=None, status=None, limit=500)

    status_counts: dict[str, int] = {}
    authority_counts: dict[str, int] = {}
    for c in raw:
        if c.get("authority_tier") == "A8":
            continue
        s = c.get("status", "UNKNOWN")
        a = c.get("authority_tier", "UNKNOWN")
        status_counts[s] = status_counts.get(s, 0) + 1
        authority_counts[a] = authority_counts.get(a, 0) + 1

    verified_count = status_counts.get("VERIFIED", 0)
    user_asserted_count = status_counts.get("USER_ASSERTED", 0)

    return {
        "total_claims": sum(status_counts.values()),
        "verified": verified_count,
        "user_asserted": user_asserted_count,
        "conflicted": status_counts.get("CONFLICTED", 0),
        "stale": status_counts.get("STALE", 0),
        "by_status": status_counts,
        "by_authority": authority_counts,
        "enforcement_active": True,
        "ver_inv_001": "enforced",
        "inventory_adapter": "windows-inventory@0.1.0",
        "note": (
            "Inventory is USER_ASSERTED until a Windows collector payload "
            "is POSTed to /api/pklos/inventory."
            if verified_count == 0 else
            f"{verified_count} claims verified from hardware inventory."
        ),
    }


@router.post("/enforcement/check")
def check_enforcement(body: EnforcementCheck):
    """Run enforcement for a query and return the decision (diagnostics).

    Shows what context the model would receive and whether it must abstain.
    Does NOT call the AI — returns the enforcement decision only.
    """
    db = get_db()
    enforcer = PolicyEnforcer(db)
    decision = enforcer.enforce(body.query)

    return {
        "query": body.query,
        "request_class": decision.request_class.value,
        "must_abstain": decision.must_abstain,
        "abstention_reason": decision.abstention_reason,
        "verified_claims_count": len(decision.verified_claims),
        "unverified_claims_count": len(decision.unverified_claims),
        "verified_context": decision.verified_context,
        "policy_instruction_preview": decision.policy_instruction[:200] if decision.policy_instruction else "",
    }
