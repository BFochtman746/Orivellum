"""Gap engine (G-M1 / G-M2) — gaps with identity, lifecycle, and true detectors.

A *gap* is a claim that the corpus provably lacks something a frame demands.
It is NOT a hygiene finding (see ``corpus_hygiene``): hygiene describes
problems with what the corpus already holds; a gap cites a frame node, the
evidence that is absent, and carries a lifecycle a human can govern.

Design rules (Engine Contract):
  * Identity   — content hash over (frame_node_id, gap_class, scope); the
                 same absence detected twice maps to one row (db layer).
  * Citation   — the insert path REFUSES gaps without frame_node_id,
                 frame_source_ref, and evidence_absent (db layer).
  * Severity   — computed here from (gap_class, centrality, dependent_count,
                 blocking_active_work).  Never asked of a model.
  * Detectors  — deterministic; ZERO model calls.

First true detector: **citation-graph closure** — works cited by held
documents that the Library does not hold.  Each gap cites the in-corpus
document that demands it, ranked by citation frequency.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

GAP_CLASS_CITATION = "citation_closure"

# ── Severity ──────────────────────────────────────────────────────────────────
# Deterministic scoring — never model-assigned.  centrality is how often the
# absent thing is referenced (total mentions); dependent_count is how many
# distinct held artifacts depend on it; blocking_active_work marks a gap that
# is holding up a live pipeline stage.

_CLASS_WEIGHT = {GAP_CLASS_CITATION: 1}  # room for future classes


def compute_severity(
    gap_class: str,
    *,
    centrality: int = 0,
    dependent_count: int = 0,
    blocking_active_work: bool = False,
) -> str:
    """Deterministic severity from evidence counts — never asked of a model."""
    score = dependent_count * 3 + min(max(centrality, 0), 12)
    score *= _CLASS_WEIGHT.get(gap_class, 1)
    if blocking_active_work:
        score += 12
    if score >= 18 and blocking_active_work:
        return "critical"
    if score >= 12:
        return "high"
    if score >= 5:
        return "medium"
    return "low"


# ── Citation extraction (deterministic, zero model calls) ─────────────────────
#
# Two in-text citation shapes are recognised:
#   narrative:     Smith (1998)     /  Smith and Jones (2004a)
#   parenthetical: (Smith 1998)     /  (Smith, 1998, p. 12)
#
# The cited-work key is the normalised "surname year" pair.

_AUTHOR = r"[A-Z][A-Za-z\u00C0-\u024F'\-]{2,}"
_PAIR = rf"{_AUTHOR}(?:\s+(?:and|&)\s+{_AUTHOR}|\s+et\s+al\.?)?"
_YEAR = r"(1[5-9]\d\d|20\d\d)[a-z]?"

_NARRATIVE_RE = re.compile(rf"\b({_PAIR})\s*\(\s*{_YEAR}\s*\)")
_PAREN_RE = re.compile(
    rf"\(\s*({_PAIR})\s*,?\s+{_YEAR}(?:\s*,\s*(?:pp?\.?\s*)?\d+(?:\s*[–\-]\s*\d+)?)?\s*\)"
)

# Capitalised words that start sentences / label figures — not author surnames.
_NOT_AUTHORS = frozenset(
    {
        "figure",
        "table",
        "chapter",
        "section",
        "appendix",
        "volume",
        "part",
        "page",
        "plate",
        "since",
        "after",
        "before",
        "circa",
        "around",
        "about",
        "during",
        "between",
        "the",
        "in",
        "by",
        "from",
        "see",
        "also",
        "and",
        "until",
        "year",
        "spring",
        "summer",
        "autumn",
        "fall",
        "winter",
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    }
)


def extract_citations(text: str) -> list[tuple[str, str]]:
    """Return (author, year) pairs cited in *text*.  Deterministic regex only."""
    found: list[tuple[str, str]] = []
    for rx in (_NARRATIVE_RE, _PAREN_RE):
        for m in rx.finditer(text):
            author, year = m.group(1).strip(), m.group(2)
            first_word = re.split(r"[\s\-']", author, maxsplit=1)[0].lower()
            if first_word in _NOT_AUTHORS:
                continue
            found.append((author, year))
    return found


def _citation_key(author: str, year: str) -> str:
    return f"{' '.join(author.lower().split())} {year}"


def _library_haystack(db: OrivellumDB) -> list[str]:
    """Lowercased searchable strings for every ready Library document."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT title, source, COALESCE(meta,'') AS meta FROM documents WHERE readiness='ready'"
        ).fetchall()
    return [f"{r['title']} {r['source']} {r['meta']}".lower() for r in rows]


def _is_held(author: str, year: str, haystack: list[str]) -> bool:
    """A cited work is held when some Library doc mentions every author
    surname as a whole word.  The year is intentionally NOT required —
    editions and reprints legitimately differ — but surnames must match on
    word boundaries so "Smith" never matches "blacksmith"."""
    author = re.sub(r"\s+et\s+al\.?$", "", author)
    surnames = [s.lower() for s in re.split(r"\s+(?:and|&)\s+", author)]
    patterns = [re.compile(rf"\b{re.escape(s)}\b") for s in surnames]
    return any(all(p.search(hay) for p in patterns) for hay in haystack)


def detect_citation_gaps(work_id: str, db: OrivellumDB) -> dict:
    """Citation-graph closure detector (G-M2).  ZERO model calls.

    Scans the chunks of the Work's ready documents for in-text citations,
    checks each cited work against the whole Library, and records a gap for
    every cited-but-not-held work — ranked by citation frequency, each citing
    the in-corpus source that demands it.

    Gaps already dismissed / ruled out of scope stay that way (the db layer
    never resurrects them).
    """
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT id, title FROM documents WHERE work_id=? AND readiness='ready' "
            "AND COALESCE(quarantined,0)=0",
            (work_id,),
        ).fetchall()
        docs = {r["id"]: r["title"] for r in doc_rows}
        chunk_rows = []
        if docs:
            marks = ",".join("?" * len(docs))
            chunk_rows = db._conn.execute(
                f"SELECT doc_id, text FROM chunks WHERE doc_id IN ({marks})",
                tuple(docs),
            ).fetchall()

    # key -> {count, docs: {doc_id: mentions}, author, year}
    cited: dict[str, dict] = {}
    for row in chunk_rows:
        for author, year in extract_citations(row["text"] or ""):
            key = _citation_key(author, year)
            entry = cited.setdefault(
                key, {"author": author, "year": year, "count": 0, "docs": defaultdict(int)}
            )
            entry["count"] += 1
            entry["docs"][row["doc_id"]] += 1

    haystack = _library_haystack(db)
    gaps: list[dict] = []
    held = 0
    for key, entry in cited.items():
        if _is_held(entry["author"], entry["year"], haystack):
            held += 1
            continue
        citing = sorted(entry["docs"].items(), key=lambda kv: -kv[1])
        top_doc_id = citing[0][0]
        row = db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_CITATION,
            scope=key,
            frame_node_id=f"citation:{key}",
            frame_source_ref=f"doc:{top_doc_id} — {docs.get(top_doc_id, '')}".strip(" —"),
            evidence_absent=(
                f'Library holds no document matching "{entry["author"]} ({entry["year"]})", '
                f"cited {entry['count']}× across {len(citing)} held document(s)"
            ),
            centrality=entry["count"],
            dependent_count=len(citing),
            unit=f"work:{work_id}",
            force_check="citation_graph_closure",
            issue_type="cited_work_not_held",
            classification="coverage",
            action="acquire_or_research_cited_work",
            meta={
                "author": entry["author"],
                "year": entry["year"],
                "citation_count": entry["count"],
                "citing_docs": {d: n for d, n in citing},
            },
        )
        gaps.append(row)

    # Rank by citation frequency — the works most demanded by the corpus first.
    freq = {f"citation:{k}": v["count"] for k, v in cited.items()}
    gaps.sort(key=lambda g: -freq.get(g["frame_node_id"], 0))

    return {
        "work_id": work_id,
        "scanned_docs": len(docs),
        "scanned_chunks": len(chunk_rows),
        "distinct_citations": len(cited),
        "held": held,
        "gaps": gaps,
    }
