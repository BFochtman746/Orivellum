"""Per-domain knowledge ontologies (THE RE-PROJECTION Phases 5-6).

One global fiction ontology over Excel documentation is how *Power Automate*
became a "Concept" and a test-catalog row became a 75%-confidence "fact".
The ontology is a property of the Work's ratified domain — a closed set of
knowledge kinds per domain:

- narrative   — the ATLAS-O node schema, reused exactly (single source of
                truth: ``atlas.NODE_TYPES``), lower-cased to match the
                knowledge table's kind convention.
- technical   — Function, Feature, Platform, Constraint, TestCase, Version,
                Defect
- governance  — Rule, Gate, Engine, Artifact, Decision, Standard
- reference   — Entity, Term, Source, Claim, Period

**Anything off-schema is discarded and counted, never coerced.**  The discard
count is a quality signal about the extractor and belongs in the harvest
report.

Harvest gating applies to machine extraction (review_status 'auto'/'ai_auto',
always document-backed).  Author-curated items — notes, insights, approved
research — are the author's own and are not constrained here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orivellum.capabilities.atlas import NODE_TYPES as _ATLAS_NODE_TYPES

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Closed kind-set per ratified domain.  Kinds are stored lowercase in the
# knowledge table; membership checks normalise case, never coerce values.
DOMAIN_ONTOLOGIES: dict[str, frozenset[str]] = {
    # Reuses the ATLAS-O node schema EXACTLY — if the graph schema gains or
    # loses a node type, the narrative ontology follows automatically.
    "narrative": frozenset(t.lower() for t in _ATLAS_NODE_TYPES),
    "technical": frozenset(
        {"function", "feature", "platform", "constraint", "testcase", "version", "defect"}
    ),
    "governance": frozenset({"rule", "gate", "engine", "artifact", "decision", "standard"}),
    "reference": frozenset({"entity", "term", "source", "claim", "period"}),
}

# Which doc_types a domain's re-harvest may read.  A closed permit list: NULL
# doc_type is NOT permitted (unclassified residue never seeds knowledge), and
# correspondence/generated/unknown are refused globally by
# ``assert_doc_type_harvestable`` regardless of this table.
PERMITTED_DOC_TYPES: dict[str, frozenset[str]] = {
    "narrative": frozenset({"manuscript"}),
    "technical": frozenset({"reference", "test_catalog", "code", "workbook"}),
    "governance": frozenset({"doctrine", "reference"}),
    "reference": frozenset({"reference", "doctrine"}),
}

# The one status that marks the pre-reprojection harvest, kept as evidence.
QUARANTINED_STATUS = "quarantined_reprojection"

# Machine-extraction statuses the ontology invariant governs.
_MACHINE_STATUSES = ("auto", "ai_auto")


def allowed_kinds_for_domain(domain: str | None) -> frozenset[str] | None:
    """Closed kind-set for *domain*, or None when the domain is unknown/unset
    (no ontology gate applies — legacy Works without a ratified domain)."""
    if not domain:
        return None
    return DOMAIN_ONTOLOGIES.get(domain)


def work_domain(db: OrivellumDB, work_id: str | None) -> str | None:
    """The Work's ratified domain, or None (no Work / no domain / bad id)."""
    if not work_id:
        return None
    try:
        work = db.get_work(work_id)
    except Exception:
        return None
    return (work or {}).get("domain") or None


def is_kind_allowed(kind: str, domain: str | None) -> bool:
    """True when *kind* may be written for a Work of *domain*.

    Works without a ratified domain have no ontology gate (returns True) —
    the gate arrives with ratification, never retroactively at write time.
    """
    allowed = allowed_kinds_for_domain(domain)
    if allowed is None:
        return True
    return (kind or "").strip().lower() in allowed


def find_ontology_violations(db: OrivellumDB, limit: int = 500) -> list[dict]:
    """Machine-extracted knowledge whose kind is off its Work's domain ontology.

    The acceptance invariant: every machine-extracted (auto/ai_auto,
    document-backed) knowledge item's kind is in its Work's domain ontology.
    Quarantined evidence and author-curated items are out of scope.
    Returns offending rows (id, work_id, domain, kind, text) — empty is a pass.
    """
    with db._lock:
        rows = db._conn.execute(
            f"""SELECT k.id, k.work_id, k.kind, k.text, k.review_status, w.domain
                FROM knowledge k
                JOIN works w ON w.id = k.work_id
                WHERE w.domain IS NOT NULL
                  AND k.source_doc_id IS NOT NULL
                  AND k.review_status IN ({",".join("?" * len(_MACHINE_STATUSES))})
                LIMIT 100000""",
            _MACHINE_STATUSES,
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        if not is_kind_allowed(r["kind"], r["domain"]):
            out.append(
                {
                    "id": r["id"],
                    "work_id": r["work_id"],
                    "domain": r["domain"],
                    "kind": r["kind"],
                    "review_status": r["review_status"],
                    "text": (r["text"] or "")[:200],
                }
            )
            if len(out) >= limit:
                break
    return out
