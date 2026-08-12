"""Research-gap routes (Gap Engine G-M1/G-M2) + hygiene dismissals.

Research gaps live in the ``gap`` table with content-hash identity and a
governed lifecycle; corpus-hygiene findings are a separate concept served by
the existing ``/works/{id}/gaps`` endpoint and dismissed via
``/works/{id}/hygiene/dismiss``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_db, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.get("/works/{work_id}/research-gaps")
def list_research_gaps(work_id: str, status: str | None = None):
    """All research gaps for a Work, most severe first."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    import json as _json

    gaps = db.list_gaps(work_id, status=status)
    for g in gaps:
        try:
            g["meta"] = _json.loads(g.get("meta") or "{}")
        except Exception:
            g["meta"] = {}
    return {"work_id": work_id, "gaps": gaps, "total": len(gaps)}


@router.post("/works/{work_id}/research-gaps/citation-scan")
def run_citation_scan(work_id: str):
    """Run the citation-graph closure detector for a Work (zero model calls)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.gap_engine import detect_citation_gaps

    return detect_citation_gaps(work_id, db)


class GapTransitionRequest(BaseModel):
    to_status: str = Field(min_length=1)
    reason: str = ""
    signed_by: str = ""


@router.post("/research-gaps/{gap_id}/transition")
def transition_research_gap(gap_id: str, req: GapTransitionRequest):
    """Apply a lifecycle transition.  Dismissals require reason + signature."""
    db = get_db()
    try:
        row = db.transition_gap(gap_id, req.to_status, reason=req.reason, signed_by=req.signed_by)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return row


@router.get("/research-gaps/{gap_id}/history")
def research_gap_history(gap_id: str):
    """The full transition ledger for one gap."""
    db = get_db()
    if db.get_gap(gap_id) is None:
        raise HTTPException(404, f"Gap {gap_id!r} not found")
    return {"gap_id": gap_id, "transitions": db.list_gap_transitions(gap_id)}


class HygieneDismissRequest(BaseModel):
    finding_key: str = Field(min_length=1)
    reason: str = ""
    signed_by: str = ""


@router.post("/works/{work_id}/hygiene/dismiss")
def dismiss_hygiene(work_id: str, req: HygieneDismissRequest):
    """Persist a hygiene-finding dismissal — it never reappears."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    db.dismiss_hygiene_finding(work_id, req.finding_key, reason=req.reason, signed_by=req.signed_by)
    # Cached hygiene results still contain the dismissed finding — drop them.
    db.invalidate_gap_cache(work_id)
    return {"ok": True, "finding_key": req.finding_key}


# ── G-M3/G-M4: structural detectors + golden oracle + open-world harness ─────


class GapScanRequest(BaseModel):
    detectors: list[str] | None = None  # default: all emitting detectors


@router.post("/works/{work_id}/research-gaps/scan")
def run_gap_scan(work_id: str, req: GapScanRequest | None = None):
    """Run the deterministic gap detectors for a Work (zero model calls)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities import domain_model as dm
    from orivellum.capabilities import gap_engine as ge

    emitters = {
        ge.DETECTOR_CITATION: ge.detect_citation_gaps,
        ge.DETECTOR_TERM: ge.detect_never_explained,
        ge.DETECTOR_DEADEND: ge.detect_dead_end_citations,
        ge.DETECTOR_FAILURE: ge.detect_failure_clusters,
        dm.DETECTOR_DOMAIN_COVERAGE: dm.detect_domain_coverage,
        ge.GAP_CLASS_DOMAIN_FRONTIER: dm.detect_domain_frontier,
    }
    wanted = (req.detectors if req and req.detectors else None) or list(emitters)
    unknown = [d for d in wanted if d not in emitters]
    if unknown:
        raise HTTPException(422, f"unknown detector(s): {unknown}")
    results = {name: emitters[name](work_id, db) for name in wanted}
    return {
        "work_id": work_id,
        "detectors": wanted,
        "results": results,
        "total_gaps": sum(len(r.get("gaps", [])) for r in results.values()),
    }


@router.get("/works/{work_id}/gap-oracle/candidates")
def oracle_candidates(work_id: str, detector: str):
    """Report-only detector candidates for annotation, with any existing label."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.gap_harness import DETECTOR_CANDIDATES

    fn = DETECTOR_CANDIDATES.get(detector)
    if fn is None:
        raise HTTPException(422, f"unknown detector {detector!r}")
    labels = {
        lb["pair_key"]: lb for lb in db.list_oracle_labels(detector=detector, work_id=work_id)
    }
    candidates = fn(work_id, db)
    for c in candidates:
        existing = labels.get(c["pair_key"])
        c["label"] = existing["label"] if existing else None
        c["labeled_by"] = existing["signed_by"] if existing else None
    # Labels whose pair the detector no longer flags still belong to the set —
    # they are the detector's misses (potential false negatives).
    flagged = {c["pair_key"] for c in candidates}
    unflagged = [
        {
            "pair_key": lb["pair_key"],
            "frequency": lb["frequency"],
            "label": lb["label"],
            "labeled_by": lb["signed_by"],
            "flagged": False,
        }
        for lb in labels.values()
        if lb["pair_key"] not in flagged
    ]
    return {
        "work_id": work_id,
        "detector": detector,
        "candidates": candidates,
        "unflagged_labels": unflagged,
    }


class OracleLabelRequest(BaseModel):
    detector: str = Field(min_length=1)
    pair_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    signed_by: str = Field(min_length=1)
    frequency: int = 0
    note: str = ""


@router.post("/works/{work_id}/gap-oracle/labels")
def upsert_oracle_label(work_id: str, req: OracleLabelRequest):
    """Record one signed three-way golden-oracle label (upsert on revision)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    try:
        row = db.upsert_oracle_label(
            work_id,
            req.detector,
            req.pair_key,
            req.label,
            signed_by=req.signed_by,
            frequency=req.frequency,
            note=req.note,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return row


@router.get("/gap-oracle/labels")
def list_oracle_labels(detector: str | None = None, work_id: str | None = None):
    db = get_db()
    labels = db.list_oracle_labels(detector=detector, work_id=work_id)
    return {"labels": labels, "total": len(labels)}


class OracleEvaluateRequest(BaseModel):
    detector: str = Field(min_length=1)


@router.post("/gap-oracle/evaluate")
def evaluate_oracle(req: OracleEvaluateRequest):
    """Run the open-world harness for one detector and persist the measurement."""
    db = get_db()
    from orivellum.capabilities.gap_harness import evaluate_detector

    try:
        return evaluate_detector(db, req.detector)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/gap-oracle/measurements")
def list_measurements():
    """Every persisted harness measurement, plus the blocking floor."""
    db = get_db()
    import json as _json

    rows = db.list_detector_measurements()
    for r in rows:
        try:
            r["strata"] = _json.loads(r.get("strata") or "{}")
        except Exception:
            r["strata"] = {}
        r["meets_blocking_floor"] = r["n_labeled"] >= db.MIN_ORACLE_LABELED
    return {"measurements": rows, "min_labeled_for_blocking": db.MIN_ORACLE_LABELED}


# ── G-M5/G-M6: Domain Model — the interpretive layer ─────────────────────────
#
# Surfaces here measure the INTERPRETIVE FRAME (triangulated reference
# structures) — distinct from the factual-spine detectors above and from
# entity-coverage estimates.  Every node is proposal-only: ratification
# happens in the review inbox with a signature.


class DomainSourceRequest(BaseModel):
    domain: str
    doc_id: str
    kind: str = "structure"  # structure | bibliography


@router.post("/works/{work_id}/domain/sources")
def add_domain_source(work_id: str, req: DomainSourceRequest):
    """Register a reference-structure document (TOC, syllabus, lexicon,
    bibliography) as an independent source for a domain."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    try:
        return db.add_domain_source(work_id, req.domain, req.doc_id, req.kind)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/works/{work_id}/domain/sources")
def list_domain_sources(work_id: str, domain: str | None = None):
    db = get_db()
    return {"sources": db.list_domain_sources(work_id, domain)}


@router.delete("/works/{work_id}/domain/sources/{source_id}")
def remove_domain_source(work_id: str, source_id: str):
    db = get_db()
    if not db.remove_domain_source(source_id):
        raise HTTPException(404, f"domain source {source_id!r} not found")
    return {"ok": True}


class DomainHarvestRequest(BaseModel):
    domain: str


@router.post("/works/{work_id}/domain/harvest")
def harvest_domain(work_id: str, req: DomainHarvestRequest):
    """Harvest node proposals from the domain's registered structure sources.

    Deterministic, zero model calls.  Proposals land in the review inbox;
    nothing generates a gap until ratified with a signature.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.domain_model import harvest_domain as _harvest

    return _harvest(db, work_id, req.domain)


@router.get("/works/{work_id}/domain/nodes")
def list_domain_nodes(
    work_id: str,
    domain: str | None = None,
    status: str | None = None,
    node_class: str | None = None,
):
    db = get_db()
    import json as _json

    nodes = db.list_domain_nodes(work_id, domain=domain, status=status, node_class=node_class)
    for n in nodes:
        try:
            n["sources"] = _json.loads(n.get("sources") or "[]")
            n["meta"] = _json.loads(n.get("meta") or "{}")
        except Exception:
            pass
    return {
        "nodes": nodes,
        "layer": "interpretive_frame",
        "note": (
            "this layer measures the interpretive frame — distinct from the "
            "factual-spine detectors and entity-coverage estimates"
        ),
    }


@router.post("/works/{work_id}/domain/scan")
def run_domain_scan(work_id: str):
    """Emit G2 coverage gaps and G4 frontier gaps over RATIFIED nodes only.

    G4 frontier gaps route to the decision queue and are never critical as
    deficiencies; G2 blocking status stays suppressed until the detector is
    measured on the golden-oracle harness.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities import domain_model as dm

    coverage = dm.detect_domain_coverage(work_id, db)
    frontier = dm.detect_domain_frontier(work_id, db)
    return {
        "work_id": work_id,
        "coverage": {"emitted": coverage["emitted"]},
        "frontier": {"emitted": frontier["emitted"]},
        "layer": "interpretive_frame",
    }


@router.get("/works/{work_id}/relative-recall")
def get_relative_recall(work_id: str):
    """Completeness measured against peer references (review §4.5) —
    surfaced alongside the coverage estimates, with honest peer framing."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.domain_model import relative_recall

    return relative_recall(db, work_id)
