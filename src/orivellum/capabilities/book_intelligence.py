"""Book intelligence — a Work's full state as a Knowledge Object.

Composes what the system already knows about a Work into a single view:
canonical manuscript, manuscript versions, unified outline with per-chapter
status and research counts, completeness dimensions, gaps, and the single
next recommended action.

All data derives from existing records (documents.extracted_text,
book_chapters, knowledge) — nothing here mutates state.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.book_intelligence")

_WORD_BASELINE = 50_000  # typical full-length manuscript
_EXPECTED_CHAPTERS = 10  # fallback when no TOC/meta hint exists
_MIN_RESEARCH_PER_CHAPTER = 3  # chapters below this are "under-researched"
_INCOMPLETE_WORDS = 200  # chapters below this word count are "incomplete"

_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "for",
        "to",
        "with",
        "from",
        "by",
        "at",
        "is",
        "are",
        "was",
        "were",
        "chapter",
        "part",
        "section",
        "introduction",
        "conclusion",
    ]
)


def _word_count(text: str | None) -> int:
    return len(text.split()) if text else 0


def _title_tokens(title: str) -> list[str]:
    """Significant search tokens from a chapter title (max 4)."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", title or "")
    return [w for w in words if w.lower() not in _STOPWORDS][:4]


_KNOWLEDGE_SCAN_CAP = 3000  # bound the in-memory matching pass


def _load_knowledge_texts(db: OrivellumDB, work_id: str) -> list[str]:
    """Prefetch this Work's knowledge item texts once (lowercased).

    Chapter research counts are computed in a single in-memory pass instead of
    one FTS query per chapter — a Work with hundreds of chapters would
    otherwise issue hundreds of serialized FTS queries per page load.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT lower(coalesce(text,'') || ' ' || coalesce(subject,'')
                            || ' ' || coalesce(object,'')) AS blob
               FROM knowledge WHERE work_id=?
                 AND review_status NOT IN
                     ('rejected','superseded_duplicate','quarantined_reprojection')
               LIMIT ?""",
            (work_id, _KNOWLEDGE_SCAN_CAP),
        ).fetchall()
    return [r["blob"] for r in rows]


def _knowledge_count_for_title(title: str, knowledge_texts: list[str]) -> int:
    """Count knowledge items whose text mentions any significant title token."""
    tokens = _title_tokens(title)
    if not tokens or not knowledge_texts:
        return 0
    pattern = re.compile(r"\b(" + "|".join(re.escape(t.lower()) for t in tokens) + r")\b")
    return sum(1 for blob in knowledge_texts if pattern.search(blob))


def build_book_intelligence(work_id: str, db: OrivellumDB) -> dict:
    """Assemble the full book-intelligence payload for one Work."""
    work = db.get_work(work_id)
    if not work:
        raise ValueError(f"Work {work_id!r} not found")

    # ── Manuscript versions ──────────────────────────────────────────────
    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind, d.readiness, d.created_at, d.source,
                      COALESCE(o.lifecycle, 'draft') AS lifecycle,
                      o.updated_at AS lifecycle_updated_at,
                      (length(coalesce(d.extracted_text,''))
                       - length(replace(coalesce(d.extracted_text,''), ' ', ''))
                       + CASE WHEN coalesce(d.extracted_text,'')='' THEN 0 ELSE 1 END)
                        AS word_count
               FROM documents d LEFT JOIN objects o ON o.id = d.id
               WHERE d.work_id=? AND COALESCE(o.lifecycle,'draft') != 'deleted'
               ORDER BY word_count DESC, d.created_at DESC""",
            (work_id,),
        ).fetchall()
    versions = [dict(r) for r in doc_rows]

    # Canonical: explicit lifecycle wins; otherwise the most complete
    # (highest-word-count) DOCX, falling back to the most complete document.
    # Lifecycle demotion is per Work+kind, so docs of different kinds can both
    # be 'canonical' — the Book view needs exactly one, and the user's most
    # recent declaration must win (objects.updated_at is bumped on lifecycle
    # changes).
    declared = sorted(
        (v for v in versions if v["lifecycle"] == "canonical"),
        key=lambda v: v.get("lifecycle_updated_at") or "",
        reverse=True,
    )
    canonical: dict | None = declared[0] if declared else None
    canonical_source = "declared" if canonical else None
    if canonical is None:
        docx = [
            v
            for v in versions
            if (v["kind"] or "").lower() in ("docx", "doc") and v["word_count"] > 0
        ]
        pool = docx or [v for v in versions if v["word_count"] > 0]
        canonical = pool[0] if pool else None
        canonical_source = "auto" if canonical else None
    for v in versions:
        v["is_canonical"] = bool(canonical and v["id"] == canonical["id"])

    # ── Unified outline from book_chapters ───────────────────────────────
    with db._lock:
        ch_rows = db._conn.execute(
            """SELECT bc.id, bc.seq, COALESCE(bc.level,1) AS level, bc.title,
                      bc.source_doc_id, bc.status, bc.extraction_method,
                      (length(coalesce(bc.text,''))
                       - length(replace(coalesce(bc.text,''), ' ', ''))
                       + CASE WHEN coalesce(bc.text,'')='' THEN 0 ELSE 1 END)
                        AS word_count
               FROM book_chapters bc
               WHERE bc.work_id=?
               ORDER BY bc.source_doc_id, bc.seq""",
            (work_id,),
        ).fetchall()
    all_chapters = [dict(r) for r in ch_rows]

    # Outline preference: the canonical document's chapters when it has any;
    # otherwise merge every document's chapters (dedup by normalized title).
    if canonical:
        outline = [c for c in all_chapters if c["source_doc_id"] == canonical["id"]]
    else:
        outline = []
    if not outline:
        seen_titles: set[str] = set()
        for c in all_chapters:
            key = re.sub(r"\W+", " ", (c["title"] or "").lower()).strip()
            if key and key in seen_titles:
                continue
            seen_titles.add(key)
            outline.append(c)

    knowledge_texts = _load_knowledge_texts(db, work_id)
    for c in outline:
        c["knowledge_count"] = _knowledge_count_for_title(c["title"] or "", knowledge_texts)
        if c["word_count"] <= 1:
            c["chapter_status"] = "missing"  # placeholder heading, no body
        elif c["word_count"] < _INCOMPLETE_WORDS:
            c["chapter_status"] = "incomplete"
        else:
            c["chapter_status"] = "present"

    # ── Completeness dimensions ──────────────────────────────────────────
    expected_chapters = 0
    try:
        expected_chapters = int((work.get("meta") or {}).get("expected_chapters") or 0)
    except Exception:
        expected_chapters = 0
    if expected_chapters <= 0:
        expected_chapters = max(_EXPECTED_CHAPTERS, len(outline))

    present_chapters = sum(1 for c in outline if c["chapter_status"] == "present")
    total_words = sum(v["word_count"] for v in versions)
    canonical_words = canonical["word_count"] if canonical else 0

    with db._lock:
        krow = db._conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN review_status IN ('approved','reviewed') THEN 1 ELSE 0 END) AS reviewed
               FROM knowledge WHERE work_id=?""",
            (work_id,),
        ).fetchone()
    knowledge_total = int(krow["total"] or 0)
    knowledge_reviewed = int(krow["reviewed"] or 0)

    researched = sum(1 for c in outline if c["knowledge_count"] >= _MIN_RESEARCH_PER_CHAPTER)

    def pct(n: float, d: float) -> int:
        return min(100, round(100 * n / d)) if d else 0

    completeness = {
        "structural_pct": pct(present_chapters, expected_chapters),
        "content_pct": pct(canonical_words or total_words, _WORD_BASELINE),
        "research_pct": pct(researched, len(outline)),
        "editorial_pct": pct(knowledge_reviewed, knowledge_total),
    }

    # ── Gaps + ranked next action ────────────────────────────────────────
    gaps: list[dict] = []

    unresearched = [c for c in outline if c["knowledge_count"] < _MIN_RESEARCH_PER_CHAPTER]
    zero_research = [c for c in unresearched if c["knowledge_count"] == 0]
    placeholders = [c for c in outline if c["chapter_status"] == "missing"]

    # Conflicting canonical declarations across document kinds
    if len(declared) > 1:
        gaps.append(
            {
                "kind": "canonical_conflict",
                "severity": "high",
                "title": f"{len(declared)} documents are marked canonical",
                "description": "Documents of different kinds are each marked canonical — the most recently declared one is used. Demote the others to remove ambiguity.",
            }
        )
    # Multiple substantial manuscripts with no declared canonical
    substantial = [v for v in versions if v["word_count"] > 500]
    if canonical_source == "auto" and len(substantial) > 1:
        gaps.append(
            {
                "kind": "canonical_unconfirmed",
                "severity": "high",
                "title": f"{len(substantial)} manuscript versions found",
                "description": "Multiple substantial documents exist — confirm which one is the canonical manuscript.",
            }
        )
    for c in zero_research:
        gaps.append(
            {
                "kind": "no_research",
                "severity": "high",
                "title": f"“{c['title']}” has no supporting research",
                "description": "No knowledge items reference this chapter — add sources.",
                "chapter_id": c["id"],
            }
        )
    for c in unresearched:
        if c["knowledge_count"] > 0:
            gaps.append(
                {
                    "kind": "weak_research",
                    "severity": "medium",
                    "title": f"“{c['title']}” is under-researched ({c['knowledge_count']} item{'s' if c['knowledge_count'] != 1 else ''})",
                    "description": f"Fewer than {_MIN_RESEARCH_PER_CHAPTER} knowledge items support this chapter.",
                    "chapter_id": c["id"],
                }
            )
    for c in placeholders:
        gaps.append(
            {
                "kind": "placeholder_chapter",
                "severity": "medium",
                "title": f"“{c['title']}” is a heading with no content",
                "description": "This chapter exists in the outline but has no words yet.",
                "chapter_id": c["id"],
            }
        )
    # Missing standard book sections
    titles_lower = " | ".join((c["title"] or "").lower() for c in outline)
    if outline:
        if not re.search(r"\b(introduction|preface|foreword|prologue)\b", titles_lower):
            gaps.append(
                {
                    "kind": "missing_section",
                    "severity": "low",
                    "title": "No introduction detected",
                    "description": "The outline has no introduction, preface, or prologue section.",
                }
            )
        if not re.search(r"\b(conclusion|epilogue|afterword|summary)\b", titles_lower):
            gaps.append(
                {
                    "kind": "missing_section",
                    "severity": "low",
                    "title": "No conclusion detected",
                    "description": "The outline has no conclusion, epilogue, or afterword section.",
                }
            )
    if not outline and versions:
        gaps.append(
            {
                "kind": "no_structure",
                "severity": "high",
                "title": "No chapter structure detected",
                "description": "No headings could be extracted from the linked documents — the outline is empty.",
            }
        )
    if not versions:
        gaps.append(
            {
                "kind": "no_documents",
                "severity": "high",
                "title": "No documents linked",
                "description": "Link manuscript or research documents to this Work to build its intelligence view.",
            }
        )

    sev_rank = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: sev_rank.get(g["severity"], 3))

    # Next action: highest-severity gap wins, phrased as an instruction.
    if not versions:
        next_action = "Link your manuscript and research documents to this Work."
    elif canonical_source == "auto" and len(substantial) > 1:
        # Confirm canonical before anything else — the outline derives from it.
        next_action = "Two or more manuscript versions found — confirm which is canonical in the Versions panel."
    elif not outline:
        next_action = "Reprocess the manuscript so chapters can be extracted, or check that it contains headings."
    elif zero_research:
        next_action = f"“{zero_research[0]['title']}” has no research — add sources for it first."
    elif placeholders:
        next_action = f"“{placeholders[0]['title']}” is an empty heading — draft its content or remove it from the outline."
    elif unresearched:
        c = next(c for c in unresearched if c["knowledge_count"] > 0)
        next_action = f"“{c['title']}” has only {c['knowledge_count']} supporting item{'s' if c['knowledge_count'] != 1 else ''} — strengthen its research."
    elif completeness["content_pct"] < 100:
        next_action = f"The manuscript is at {completeness['content_pct']}% of a full-length draft — keep writing."
    elif completeness["editorial_pct"] < 100 and knowledge_total:
        next_action = "Research is in place — review the remaining unreviewed knowledge items."
    else:
        next_action = "All dimensions look complete — consider a final read-through."

    return {
        "work_id": work_id,
        "work": {
            "id": work["id"],
            "title": work.get("title"),
            "description": work.get("description"),
            "work_type": work.get("work_type"),
        },
        "canonical": ({**canonical, "canonical_source": canonical_source} if canonical else None),
        "versions": versions,
        "outline": outline,
        "expected_chapters": expected_chapters,
        "completeness": completeness,
        "knowledge_total": knowledge_total,
        "knowledge_reviewed": knowledge_reviewed,
        "gaps": gaps,
        "next_action": next_action,
    }
