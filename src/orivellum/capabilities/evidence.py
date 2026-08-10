"""Evidence scoring & contradiction detection — the learning loop.

MONARCH-inspired: confidence on knowledge items is derived from measurable
evidence signals instead of static defaults, and conflicting claims are
detected automatically so they can be adjudicated in the governance queue.

Evidence signals (adapted from the MONARCH rubric to what Orivellum stores):
  • base           — how the item was produced (rule kind / LLM)
  • corroboration  — same subject asserted by other source documents
  • recency        — age of the source document
  • review         — human approval is the strongest signal

All scoring is deterministic and cheap (pure SQL + arithmetic) so it can run
every night over the whole library.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.evidence")

# ── Scoring weights ──────────────────────────────────────────────────────────
_W_BASE = 0.45          # production method
_W_CORROBORATION = 0.25 # independent sources agreeing
_W_RECENCY = 0.10       # source document age
_W_REVIEW = 0.20        # human review status

# Base score by origin: rule-based summaries are near-verbatim (high),
# LLM claims are inferences (lower).
_BASE_BY_KIND = {
    "summary": 0.95, "heading": 0.85, "concept": 0.80,
    "excerpt": 0.75, "fact": 0.70, "claim": 0.65,
    "entity": 0.55, "relationship": 0.60,
}
_DEFAULT_BASE = 0.65

_MAX_RECENCY_DAYS = 730  # sources older than 2 years get zero recency credit


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def compute_evidence_score(item: dict, corroborating_sources: int,
                           source_created_at: str | None,
                           now: datetime | None = None) -> tuple[float, dict]:
    """Return (confidence 0..1, components dict) for a knowledge item."""
    now = now or datetime.now(UTC)

    base = _BASE_BY_KIND.get(item.get("kind", ""), _DEFAULT_BASE)
    meta = item.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if meta.get("source") == "llm":
        base = min(base, 0.70)

    # Corroboration: 0 → 0, 1 extra source → 0.6, 2 → 0.85, 3+ → 1.0
    corro = min(1.0, (0.0, 0.6, 0.85)[corroborating_sources]
                if corroborating_sources < 3 else 1.0)

    # Recency: linear decay over _MAX_RECENCY_DAYS
    recency = 0.5  # neutral when unknown
    src_dt = _parse_ts(source_created_at)
    if src_dt is not None:
        age_days = max(0.0, (now - src_dt).total_seconds() / 86400)
        recency = max(0.0, 1.0 - age_days / _MAX_RECENCY_DAYS)

    review = {"approved": 1.0, "auto": 0.6, "unreviewed": 0.5,
              "ai_auto": 0.4, "rejected": 0.0}.get(
        item.get("review_status", "unreviewed"), 0.5)

    score = (_W_BASE * base + _W_CORROBORATION * corro
             + _W_RECENCY * recency + _W_REVIEW * review)
    score = round(max(0.05, min(1.0, score)), 4)
    components = {
        "base": round(base, 2), "corroboration": round(corro, 2),
        "recency": round(recency, 2), "review": round(review, 2),
        "corroborating_sources": corroborating_sources,
    }
    return score, components


def rescore_work(work_id: str, db: OrivellumDB, limit: int = 500) -> int:
    """Re-score confidence for all knowledge items in a Work.

    Rejected items are skipped (their confidence is moot). Returns the number
    of items whose stored confidence actually changed.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT k.id, k.kind, k.subject, k.review_status, k.meta,
                      k.confidence, k.source_doc_id, d.created_at AS src_created
               FROM knowledge k
               LEFT JOIN documents d ON d.id = k.source_doc_id
               WHERE k.work_id = ? AND k.review_status != 'rejected'
               LIMIT ?""",
            (work_id, limit),
        ).fetchall()

        # subject → set of distinct source docs (for corroboration counting)
        subj_sources: dict[str, set] = {}
        for r in rows:
            subj = (r["subject"] or "").strip().lower()
            if subj:
                subj_sources.setdefault(subj, set()).add(r["source_doc_id"])

    changed = 0
    now = datetime.now(UTC)
    for r in rows:
        item = dict(r)
        subj = (item.get("subject") or "").strip().lower()
        others = 0
        if subj and item.get("source_doc_id"):
            others = len(subj_sources.get(subj, set()) - {item["source_doc_id"]})
        score, components = compute_evidence_score(
            item, others, item.get("src_created"), now)
        # Always persist current evidence components (so meta stays fresh even
        # when the score is stable); count only real confidence changes.
        db.update_knowledge_confidence(item["id"], score, evidence=components)
        if abs(score - (item.get("confidence") or 0)) >= 0.01:
            changed += 1
    return changed


# ── Contradiction detection ─────────────────────────────────────────────────

_NEGATORS = re.compile(r"\b(not|never|no longer|cannot|can't|isn't|aren't|"
                       r"doesn't|don't|won't|false|incorrect)\b", re.I)


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def detect_contradictions(work_id: str, db: OrivellumDB,
                          limit: int = 400) -> int:
    """Detect conflicting claims within a Work and record them in `conflicts`.

    Two heuristics:
      1. Structured: same subject + same predicate, different object.
      2. Negation: same subject, one text contains a negator the other lacks.

    Returns the number of NEW conflicts recorded (existing pairs skipped).
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, subject, predicate, object, text FROM knowledge
               WHERE work_id = ? AND review_status != 'rejected'
                 AND subject IS NOT NULL AND subject != ''
               LIMIT ?""",
            (work_id, limit),
        ).fetchall()

    # Preload already-recorded pairs once so per-pair existence queries are
    # avoided in the loops below.
    item_ids = {r["id"] for r in rows}
    with db._lock:
        existing_rows = db._conn.execute(
            "SELECT claim_a_id, claim_b_id FROM conflicts").fetchall()
    existing_pairs = {frozenset((r["claim_a_id"], r["claim_b_id"]))
                      for r in existing_rows
                      if r["claim_a_id"] in item_ids or r["claim_b_id"] in item_ids}

    pending: list[tuple[str, str, str]] = []  # (a_id, b_id, type)

    def _queue(a: dict, b: dict, ctype: str) -> None:
        key = frozenset((a["id"], b["id"]))
        if key not in existing_pairs:
            existing_pairs.add(key)
            pending.append((a["id"], b["id"], ctype))

    by_subject: dict[str, list] = {}
    for r in rows:
        by_subject.setdefault(_norm(r["subject"]), []).append(dict(r))

    _MAX_NEGATION_PAIRS = 200  # per-subject cap on the O(n²) heuristic

    for subj, items in by_subject.items():
        if len(items) < 2:
            continue

        # Heuristic 1 (structured): group by (predicate), then split by object.
        # Only cross-object pairs conflict — same-object claims agree.
        by_pred: dict[str, dict[str, list]] = {}
        for it in items:
            if it.get("predicate") and it.get("object"):
                by_pred.setdefault(_norm(it["predicate"]), {}) \
                       .setdefault(_norm(it["object"]), []).append(it)
        for obj_groups in by_pred.values():
            if len(obj_groups) < 2:
                continue
            groups = list(obj_groups.values())
            for gi in range(len(groups)):
                for gj in range(gi + 1, len(groups)):
                    # one representative pair per differing object pair
                    _queue(groups[gi][0], groups[gj][0], "conflicting_values")

        # Heuristic 2 (negation): only compare negated vs non-negated texts,
        # capped to keep the pass cheap on high-cardinality subjects.
        neg = [it for it in items if _NEGATORS.search(it.get("text") or "")]
        pos = [it for it in items if not _NEGATORS.search(it.get("text") or "")]
        compared = 0
        for a in neg:
            ta = set(_norm(a.get("text")).split())
            for b in pos:
                if compared >= _MAX_NEGATION_PAIRS:
                    break
                compared += 1
                tb = set(_norm(b.get("text")).split())
                smaller = min(len(ta), len(tb))
                if smaller >= 4 and len(ta & tb) / smaller >= 0.6:
                    _queue(a, b, "negation")
            if compared >= _MAX_NEGATION_PAIRS:
                break

    new_conflicts = db.create_conflicts_batch(pending)
    if new_conflicts:
        logger.info("Work %s — %d new conflict(s) detected", work_id[:8], new_conflicts)
    return new_conflicts
