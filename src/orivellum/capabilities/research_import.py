"""Research writeback with a review gate (T-M2/T-M3 front half).

Consumes the outputs of an `orivellum-runner --job research` run:
- ``research_digests.json`` — verified claims with source URL, retrieval
  date, and supporting quote.  Each claim lands as a knowledge PROPOSAL
  (``review_status='proposed'``), never authority: it cannot ground learning
  questions or answer keys until a human ratifies it to 'approved' via the
  normal review flow (PATCH /api/knowledge/{id}/review), and that transition
  is recorded by governed_write.
- ``curriculum.json`` — training-plan items imported into work_concepts via
  ``learning.import_training_plan`` (six-field shape preserved; the
  verification question becomes the concept's first stored item).

Both imports are idempotent: knowledge dedups on (work_id, text) hash inside
``create_knowledge_item``; plan import reuses concepts by subject and item
rows are UNIQUE(concept_id, question).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


logger = logging.getLogger("orivellum.research_import")

_MAX_CLAIMS_PER_IMPORT = 500  # sanity ceiling; a digest set beyond this is malformed


def import_research_digests(db: Any, work_id: str, digests: dict) -> dict:
    """Land research digest claims as knowledge proposals with provenance.

    ``digests`` is the parsed content of ``research_digests.json``:
    ``{"topic": ..., "digests": [{"query", "origin", "sources": [...],
    "claims": [{"claim", "sources", "quote", "confidence"}], ...}]}``.

    Every stored item carries meta provenance: source URLs + titles,
    retrieval date, the verbatim supporting quote, the research query, and
    the runner's confidence.  Claims without a resolvable source are skipped
    and counted — never stored unsourced.
    """
    created, duplicate, skipped = 0, 0, 0
    total = 0
    resolved_requests = 0
    for dg in digests.get("digests") or []:
        if not isinstance(dg, dict):
            skipped += 1
            continue
        dg_created_before = created
        src_by_id = {s.get("id"): s for s in dg.get("sources") or [] if isinstance(s, dict)}
        query = str(dg.get("query") or "")[:300]
        for cl in dg.get("claims") or []:
            total += 1
            if total > _MAX_CLAIMS_PER_IMPORT:
                raise ValueError(
                    f"digest set exceeds {_MAX_CLAIMS_PER_IMPORT} claims — refusing import"
                )
            if not isinstance(cl, dict):
                skipped += 1
                continue
            text = str(cl.get("claim") or "").strip()[:2000]
            quote = str(cl.get("quote") or "").strip()
            # Provenance is mandatory: a usable source must carry a real URL
            # AND a retrieval date — a bare source id is not provenance.
            srcs = [
                src_by_id[s]
                for s in (cl.get("sources") or [])
                if s in src_by_id
                and str(src_by_id[s].get("url") or "").startswith(("http://", "https://"))
                and src_by_id[s].get("retrieved")
            ]
            if not text or not quote or not srcs:
                skipped += 1  # unsourced / undated — never stored
                continue
            meta = {
                "source": "research_run",
                "query": query,
                "origin": dg.get("origin"),
                "gap_id": dg.get("gap_id"),
                "request_id": dg.get("request_id"),
                "quote": quote[:500],
                "runner_confidence": cl.get("confidence", "low"),
                "sources": [
                    {
                        "url": str(s.get("url"))[:500],
                        "title": str(s.get("title") or "")[:200],
                        "retrieved": str(s.get("retrieved"))[:40],
                        "kind": str(s.get("kind") or "web")[:40],
                    }
                    for s in srcs[:5]
                ],
            }
            # create_knowledge_item dedups by (work_id, text) hash and returns
            # the existing id — check first so re-imports are counted honestly
            # and a previously ratified/rejected claim is never touched.
            with db._lock:
                existing = db._conn.execute(
                    "SELECT id FROM knowledge WHERE work_id IS ? AND text=?",
                    (work_id, text),
                ).fetchone()
            if existing:
                duplicate += 1
                continue
            db.create_knowledge_item(
                work_id=work_id,
                kind="research_claim",
                text=text,
                subject=query or None,
                confidence=0.5,  # proposal-grade until ratified
                review_status="proposed",
                meta=meta,
            )
            created += 1
        # A digest that answered a learner research request closes it — but
        # only when it actually landed new material (atomic conditional UPDATE).
        request_id = dg.get("request_id")
        if request_id and created > dg_created_before:
            with db._lock:
                cur = db._conn.execute(
                    "UPDATE research_requests SET status='resolved', resolved_at=? "
                    "WHERE id=? AND status='open'",
                    (_now_iso(), str(request_id)),
                )
                db._conn.commit()
            if cur.rowcount:
                resolved_requests += 1
    return {
        "proposals_created": created,
        "duplicates": duplicate,
        "skipped_unsourced": skipped,
        "research_requests_resolved": resolved_requests,
    }


def import_research_run(
    db: Any,
    work_id: str,
    digests: dict | None,
    curriculum: dict | None,
) -> dict:
    """One-call import of a finished research run: writeback + plan import.

    Returns a combined summary.  The caller is responsible for triggering a
    re-seed afterwards (the API route does, in the background) so the Learn
    screen reflects the new material without a button press.
    """
    result: dict = {}
    if digests:
        result["writeback"] = import_research_digests(db, work_id, digests)
    if curriculum:
        from orivellum.capabilities.learning import import_training_plan

        items = curriculum.get("items") if isinstance(curriculum, dict) else None
        result["plan_import"] = import_training_plan(db, work_id, items or [])
    return result
