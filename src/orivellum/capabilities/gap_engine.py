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

# Domain Model classes (G-M5/G-M6, interpretive layer — see domain_model.py).
# G2 coverage: a ratified domain node with insufficient corpus evidence.
# G4 frontier: sources structurally disagree — a decision owed, never a
# deficiency, so its severity is capped below blocking levels here.
GAP_CLASS_DOMAIN_COVERAGE = "domain_coverage"
GAP_CLASS_DOMAIN_FRONTIER = "domain_frontier"

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
    agreement: int = 0,
    demand: int = 0,
) -> str:
    """Deterministic severity from evidence counts — never asked of a model.

    ``agreement`` is the count of independent reference sources establishing
    the frame node (Domain Model consensus level); ``demand`` is MEASURED
    usage pressure (user queries touching the node) — never a hand flag.
    Both default to 0 so pre-domain detectors are unchanged.

    A frontier gap is a decision the user owes, not a deficiency: its
    severity is capped at ``medium`` no matter what the counts say.
    """
    score = dependent_count * 3 + min(max(centrality, 0), 12)
    score += min(max(agreement, 0), 6)
    score += min(max(demand, 0), 9)
    score *= _CLASS_WEIGHT.get(gap_class, 1)
    if gap_class == GAP_CLASS_DOMAIN_FRONTIER:
        return "medium" if score >= 5 else "low"
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


def detect_citation_gaps(work_id: str, db: OrivellumDB, *, emit: bool = True) -> dict:
    """Citation-graph closure detector (G-M2).  ZERO model calls.

    Scans the chunks of the Work's ready documents for in-text citations,
    checks each cited work against the whole Library, and records a gap for
    every cited-but-not-held work — ranked by citation frequency, each citing
    the in-corpus source that demands it.

    With ``emit=False`` nothing is written: the detector acts as a pure
    completeness oracle and returns its candidates for the harness to score.

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
    candidates: list[dict] = []
    held = 0
    for key, entry in cited.items():
        if _is_held(entry["author"], entry["year"], haystack):
            held += 1
            continue
        citing = sorted(entry["docs"].items(), key=lambda kv: (-kv[1], kv[0]))
        top_doc_id = citing[0][0]
        candidates.append(
            {
                "pair_key": key,
                "frequency": entry["count"],
                "frequency_band": frequency_band(entry["count"]),
                "author": entry["author"],
                "year": entry["year"],
                "top_doc_id": top_doc_id,
                "top_doc_title": docs.get(top_doc_id, ""),
                "citing_docs": {d: n for d, n in citing},
            }
        )
        if not emit:
            continue
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
            force_check=DETECTOR_CITATION,
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
        if row is not None:  # None = region under an active completeness assertion
            gaps.append(row)

    # Rank by citation frequency — the works most demanded by the corpus first.
    freq = {f"citation:{k}": v["count"] for k, v in cited.items()}
    gaps.sort(key=lambda g: -freq.get(g["frame_node_id"], 0))
    candidates.sort(key=lambda c: (-c["frequency"], c["pair_key"]))

    return {
        "work_id": work_id,
        "scanned_docs": len(docs),
        "scanned_chunks": len(chunk_rows),
        "distinct_citations": len(cited),
        "held": held,
        "gaps": gaps,
        "candidates": candidates,
    }


# ══════════════════════════════════════════════════════════════════════════════
# G-M3 — structural detectors.  All deterministic, ZERO model calls.  Each has
# a *candidates* function (report-only, no writes — the shape the open-world
# harness measures: given a pair, is it complete or not) and an emitting
# detector that writes standard gaps through the governed insert path.
# ══════════════════════════════════════════════════════════════════════════════

GAP_CLASS_TERM = "mentioned_never_explained"
GAP_CLASS_DEADEND = "dead_end_citation"
GAP_CLASS_FAILURE = "failure_clustering"

# Detector names — the value stored in gap.force_check, the key the golden
# oracle labels against, and the key harness measurements are recorded under.
DETECTOR_CITATION = "citation_graph_closure"
DETECTOR_TERM = "mentioned_never_explained"
DETECTOR_DEADEND = "dead_end_citation"
DETECTOR_FAILURE = "failure_clustering"

# Knowledge kinds that EXPLAIN a term (vs merely mentioning it).
_EXPLANATORY_KINDS = frozenset({"summary", "concept", "claim"})
_MIN_EXPLANATION_CHARS = 40  # shorter than this is a label, not an explanation
_MIN_TERM_MENTIONS = 3  # a term must recur before its absence is a gap
_MIN_TERM_LEN, _MAX_TERM_LEN = 3, 60
_FAIL_SCORE = 0.5  # a study attempt below this is a wrong answer
_MIN_FAILING_DEPENDENTS = 2  # dependents that must fail before the prereq is the gap
_MIN_FAILURES_PER_CONCEPT = 2  # repeated wrong answers, not a single slip

# Frequency stratification boundary: entities/terms observed this often or
# less are "rare".  Every harness metric is reported per band — a detector
# with known popularity bias must never hide behind a single number.
RARE_FREQ_MAX = 3


def frequency_band(freq: int) -> str:
    return "rare" if freq <= RARE_FREQ_MAX else "common"


def candidates_citation_closure(work_id: str, db: OrivellumDB) -> list[dict]:
    """Report-only candidates for the citation-closure detector (harness use)."""
    result = detect_citation_gaps(work_id, db, emit=False)
    return result["candidates"]


# ── G5.1 Mentioned-but-never-explained ────────────────────────────────────────


def _norm_term(term: str) -> str:
    return " ".join(term.lower().split())


def candidates_never_explained(work_id: str, db: OrivellumDB) -> list[dict]:
    """Terms the Work's sources use repeatedly but no knowledge item explains.

    Term universe: distinct knowledge subjects of the Work (harvested, so the
    corpus itself named them).  A term is *explained* when an explanatory-kind
    knowledge item (summary / concept / claim, ≥ 40 chars) carries it as
    subject; entity/excerpt rows are mentions, not explanations.  Mentions are
    counted across the chunks of the Work's ready documents with word-boundary
    matching.  KNOWN POPULARITY BIAS: output is stratified by mention
    frequency and must be read per band.
    """
    with db._lock:
        kn_rows = db._conn.execute(
            "SELECT subject, kind, LENGTH(text) AS n FROM knowledge "
            "WHERE work_id=? AND subject IS NOT NULL AND subject != '' "
            "AND review_status != 'rejected'",
            (work_id,),
        ).fetchall()
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

    terms: dict[str, str] = {}  # norm -> display
    explained: set[str] = set()
    for r in kn_rows:
        norm = _norm_term(r["subject"])
        if not (_MIN_TERM_LEN <= len(norm) <= _MAX_TERM_LEN):
            continue
        terms.setdefault(norm, r["subject"].strip())
        if r["kind"] in _EXPLANATORY_KINDS and (r["n"] or 0) >= _MIN_EXPLANATION_CHARS:
            explained.add(norm)

    lowered = [(r["doc_id"], (r["text"] or "").lower()) for r in chunk_rows]
    candidates: list[dict] = []
    for norm, display in terms.items():
        if norm in explained:
            continue
        pattern = re.compile(rf"\b{re.escape(norm)}\b")
        mentions = 0
        by_doc: dict[str, int] = defaultdict(int)
        for doc_id, text in lowered:
            n = len(pattern.findall(text))
            if n:
                mentions += n
                by_doc[doc_id] += n
        if mentions < _MIN_TERM_MENTIONS:
            continue
        top_doc = max(sorted(by_doc), key=by_doc.get)  # type: ignore[arg-type]
        candidates.append(
            {
                "pair_key": norm,
                "frequency": mentions,
                "frequency_band": frequency_band(mentions),
                "term": display,
                "top_doc_id": top_doc,
                "top_doc_title": docs.get(top_doc, ""),
                "mentioning_docs": dict(by_doc),
            }
        )
    candidates.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return candidates


def detect_never_explained(work_id: str, db: OrivellumDB) -> dict:
    """Emit mentioned-but-never-explained gaps (frequency-stratified)."""
    candidates = candidates_never_explained(work_id, db)
    gaps = []
    for c in candidates:
        row = db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_TERM,
            scope=c["pair_key"],
            frame_node_id=f"term:{c['pair_key']}",
            frame_source_ref=f"doc:{c['top_doc_id']} — {c['top_doc_title']}".strip(" —"),
            evidence_absent=(
                f'Sources mention "{c["term"]}" {c["frequency"]}× across '
                f"{len(c['mentioning_docs'])} document(s) but no knowledge item "
                "explains or defines it"
            ),
            centrality=c["frequency"],
            dependent_count=len(c["mentioning_docs"]),
            unit=f"work:{work_id}",
            force_check=DETECTOR_TERM,
            issue_type="term_never_explained",
            classification="coverage",
            action="research_or_define_term",
            meta={
                "term": c["term"],
                "mention_count": c["frequency"],
                "frequency_band": c["frequency_band"],
                "mentioning_docs": c["mentioning_docs"],
            },
        )
        if row is not None:  # None = region under an active completeness assertion
            gaps.append(row)
    strata = {"rare": 0, "common": 0}
    for c in candidates:
        strata[c["frequency_band"]] += 1
    return {"work_id": work_id, "candidates": len(candidates), "strata": strata, "gaps": gaps}


# ── G5.5 Dead-end citation ────────────────────────────────────────────────────


def candidates_dead_end(work_id: str, db: OrivellumDB) -> list[dict]:
    """Knowledge claims that cite a source the Library does not hold.

    Distinct from the missing-citation hygiene finding (no citation at all)
    and from citation closure (which scans document chunks): this scans the
    extracted knowledge itself — a claim citing something uncheckable.
    """
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, text, source_doc_id FROM knowledge "
            "WHERE work_id=? AND review_status != 'rejected' ORDER BY id",
            (work_id,),
        ).fetchall()
    haystack = _library_haystack(db)
    cited: dict[str, dict] = {}
    for r in rows:
        for author, year in extract_citations(r["text"] or ""):
            key = _citation_key(author, year)
            entry = cited.setdefault(
                key, {"author": author, "year": year, "items": [], "docs": set()}
            )
            entry["items"].append(r["id"])
            if r["source_doc_id"]:
                entry["docs"].add(r["source_doc_id"])
    candidates = []
    for key, entry in cited.items():
        if _is_held(entry["author"], entry["year"], haystack):
            continue
        candidates.append(
            {
                "pair_key": key,
                "frequency": len(entry["items"]),
                "frequency_band": frequency_band(len(entry["items"])),
                "author": entry["author"],
                "year": entry["year"],
                "item_ids": entry["items"],
                "source_doc_ids": sorted(entry["docs"]),
            }
        )
    candidates.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return candidates


def detect_dead_end_citations(work_id: str, db: OrivellumDB) -> dict:
    """Emit dead-end-citation gaps: claims resting on sources you cannot check."""
    candidates = candidates_dead_end(work_id, db)
    gaps = []
    for c in candidates:
        first_item = c["item_ids"][0]
        src = f" (doc:{c['source_doc_ids'][0]})" if c["source_doc_ids"] else ""
        row = db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_DEADEND,
            scope=c["pair_key"],
            frame_node_id=f"deadend:{c['pair_key']}",
            frame_source_ref=f"knowledge:{first_item}{src}",
            evidence_absent=(
                f"{len(c['item_ids'])} knowledge claim(s) cite "
                f'"{c["author"]} ({c["year"]})" but the Library holds no matching '
                "document — the claims cannot be checked"
            ),
            centrality=c["frequency"],
            dependent_count=len(c["item_ids"]),
            unit=f"work:{work_id}",
            force_check=DETECTOR_DEADEND,
            issue_type="claim_source_not_held",
            classification="verifiability",
            action="acquire_source_or_flag_claim",
            meta={
                "author": c["author"],
                "year": c["year"],
                "item_ids": c["item_ids"][:20],
                "frequency_band": c["frequency_band"],
            },
        )
        if row is not None:  # None = region under an active completeness assertion
            gaps.append(row)
    return {"work_id": work_id, "candidates": len(candidates), "gaps": gaps}


# ── G5.4 Failure clustering ───────────────────────────────────────────────────


def candidates_failure_clusters(work_id: str, db: OrivellumDB) -> list[dict]:
    """Shared prerequisites of concepts the learner repeatedly fails.

    When ≥ 2 dependent concepts each accumulate ≥ 2 wrong answers and share a
    prerequisite, the shared prerequisite is the gap — even though nothing
    named it.  Pure graph arithmetic over attempt history; graduated
    prerequisites are exempt (the foundation is demonstrably present).
    """
    with db._lock:
        concepts = {
            r["id"]: r["subject"]
            for r in db._conn.execute(
                "SELECT id, subject FROM work_concepts WHERE work_id=?", (work_id,)
            ).fetchall()
        }
        if not concepts:
            return []
        marks = ",".join("?" * len(concepts))
        attempt_rows = db._conn.execute(
            f"SELECT concept_id, score FROM work_mastery WHERE concept_id IN ({marks})",
            tuple(concepts),
        ).fetchall()
        edge_rows = db._conn.execute(
            f"SELECT concept_id, prereq_id FROM work_concept_prereqs WHERE concept_id IN ({marks})",
            tuple(concepts),
        ).fetchall()
        pass_rows = db._conn.execute(
            f"""WITH ranked AS (
                    SELECT concept_id, consecutive_passes,
                           ROW_NUMBER() OVER (PARTITION BY concept_id
                               ORDER BY created_at DESC, rowid DESC) AS rn
                    FROM work_mastery WHERE concept_id IN ({marks})
                ) SELECT concept_id, consecutive_passes FROM ranked WHERE rn=1""",
            tuple(concepts),
        ).fetchall()

    failures: dict[str, int] = defaultdict(int)
    for r in attempt_rows:
        if (r["score"] or 0.0) < _FAIL_SCORE:
            failures[r["concept_id"]] += 1
    struggling = {cid for cid, n in failures.items() if n >= _MIN_FAILURES_PER_CONCEPT}
    latest_passes = {r["concept_id"]: r["consecutive_passes"] for r in pass_rows}

    by_prereq: dict[str, set[str]] = defaultdict(set)
    prereq_dependents: dict[str, int] = defaultdict(int)
    for r in edge_rows:
        if r["prereq_id"] not in concepts:
            continue  # cross-Work edge — never counted
        prereq_dependents[r["prereq_id"]] += 1
        if r["concept_id"] in struggling:
            by_prereq[r["prereq_id"]].add(r["concept_id"])

    candidates = []
    for prereq_id, deps in by_prereq.items():
        if len(deps) < _MIN_FAILING_DEPENDENTS:
            continue
        if latest_passes.get(prereq_id, 0) >= 3:
            continue  # prerequisite is graduated — the foundation is present
        total_failures = sum(failures[d] for d in deps)
        candidates.append(
            {
                "pair_key": prereq_id,
                "frequency": total_failures,
                "frequency_band": frequency_band(total_failures),
                "prereq_subject": concepts[prereq_id],
                "failing_dependents": sorted(deps),
                "dependent_subjects": sorted(concepts[d] for d in deps),
                "graph_dependents": prereq_dependents[prereq_id],
            }
        )
    candidates.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return candidates


def detect_failure_clusters(work_id: str, db: OrivellumDB) -> dict:
    """Emit failure-clustering gaps: the unnamed shared prerequisite."""
    candidates = candidates_failure_clusters(work_id, db)
    gaps = []
    for c in candidates:
        deps = ", ".join(c["dependent_subjects"])
        row = db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_FAILURE,
            scope=c["pair_key"],
            frame_node_id=f"concept:{c['pair_key']}",
            frame_source_ref=(
                f"learning-graph edges: [{deps}] each require "
                f'"{c["prereq_subject"]}" (concepts:{",".join(c["failing_dependents"])})'
            ),
            evidence_absent=(
                f"{len(c['failing_dependents'])} dependent concept(s) ({deps}) "
                f"accumulated {c['frequency']} wrong answers; shared prerequisite "
                f'"{c["prereq_subject"]}" has no demonstrated mastery'
            ),
            centrality=c["graph_dependents"],
            dependent_count=len(c["failing_dependents"]),
            unit=f"work:{work_id}",
            force_check=DETECTOR_FAILURE,
            issue_type="shared_prerequisite_gap",
            classification="understanding",
            action="train_or_research_prerequisite",
            meta={
                "prereq_subject": c["prereq_subject"],
                "failing_dependents": c["failing_dependents"],
                "dependent_subjects": c["dependent_subjects"],
                "total_failures": c["frequency"],
                "frequency_band": c["frequency_band"],
            },
        )
        if row is not None:  # None = region under an active completeness assertion
            gaps.append(row)
    return {"work_id": work_id, "candidates": len(candidates), "gaps": gaps}
