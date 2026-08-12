"""Domain Model for the interpretive layer (G-M5/G-M6, rescoped).

The factual spine of a corpus — people, places, dates, citations, terms —
is covered by the structural detectors and (planned) cardinality/peer-group
mechanisms.  This module reaches the place those cannot: narrative,
theological, and interpretive material, where the only defensible frame is
triangulation across independent reference structures (TOCs, syllabi,
lexica, indexes) that domain experts already curated.

Design rules:
  * Harvest structure, not prose — nodes come from the heading structure of
    reference documents already in the Library (``book_chapters`` rows), so
    every node carries a source ref.  Zero model calls.
  * Triangulate — intersection across >=3 independent sources proposes a
    ``required`` core; union minus intersection is ``optional`` periphery
    (with its sources recorded); structural disagreement (the same node
    filed under different parents) is ``contested`` and becomes a G4
    frontier gap after ratification.
  * Proposal-only — every node lands as a proposal in the review inbox.
    Nothing generates a gap until it is ratified with a signature.
  * Severity is computed — agreement level, centrality, dependents, and
    MEASURED demand (user queries touching the node).  Never a hand flag.
  * G4 frontier gaps route to a decision queue and are never critical as
    deficiencies — they are decisions the user owes, not research tasks.

Relative recall (review §4.5) also lives here: completeness measured
against a PEER reference (a bibliography or another edition), the cheap
external yardstick that needs no Domain Model at all.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from orivellum.capabilities.gap_engine import (
    GAP_CLASS_DOMAIN_COVERAGE,
    GAP_CLASS_DOMAIN_FRONTIER,
    _is_held,
    _library_haystack,
    extract_citations,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Detector names (== gap.force_check == oracle label key).
DETECTOR_DOMAIN_COVERAGE = GAP_CLASS_DOMAIN_COVERAGE

REQUIRED_MIN_SOURCES = 3  # intersection threshold for the consensus core
_MIN_EVIDENCE = 2  # knowledge items / passages below this = coverage gap
_MISSING_CAP = 25  # relative-recall missing list cap


# ── Structure normalisation ───────────────────────────────────────────────────

# Boilerplate headings that are book furniture, not domain nodes.
_BOILERPLATE = frozenset(
    {
        "introduction",
        "preface",
        "foreword",
        "prologue",
        "epilogue",
        "conclusion",
        "contents",
        "table of contents",
        "index",
        "bibliography",
        "references",
        "further reading",
        "notes",
        "endnotes",
        "footnotes",
        "glossary",
        "abbreviations",
        "acknowledgements",
        "acknowledgments",
        "appendix",
        "list of figures",
        "list of tables",
        "list of maps",
        "about the author",
        "copyright",
        "dedication",
    }
)

# Roman numerals only count as numbering when a separator follows ("IV."),
# otherwise ordinary words built from i/v/x/l/c/d/m letters ("Divine") would
# have their heads eaten.
_LEADING_NUMBERING = re.compile(
    r"^\s*(?:(?:chapter|part|section|unit|lecture|week|appendix)\s+)?"
    r"(?:[0-9]+(?:\.[0-9]+)*|[ivxlcdm]+(?=\s*[.:)\-–—]))?\s*[.:)\-–—]*\s*",
    re.IGNORECASE,
)


def normalize_node_key(title: str) -> str:
    """Deterministic node key from a heading: numbering off, case folded."""
    t = (title or "").strip()
    t = _LEADING_NUMBERING.sub("", t, count=1)
    t = re.sub(r"[^\w\s'&\-]", " ", t)
    return " ".join(t.lower().split())


def _structure_of(db: OrivellumDB, doc_id: str) -> dict[str, dict]:
    """Node map for one reference document: node_key -> {label, parent_key, ref}.

    Parents come from the heading levels — the nearest preceding heading with
    a shallower level is the parent.  First occurrence of a key wins (a key
    repeated inside one source is still one statement of membership).
    """
    chapters = db.get_book_chapters(doc_id)
    nodes: dict[str, dict] = {}
    stack: list[tuple[int, str]] = []  # (level, node_key)
    for ch in chapters:
        key = normalize_node_key(ch.get("title") or "")
        if len(key) < 3 or key in _BOILERPLATE:
            continue
        level = int(ch.get("level") or 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent_key = stack[-1][1] if stack else ""
        if key not in nodes:
            nodes[key] = {
                "label": (ch.get("title") or "").strip(),
                "parent_key": parent_key,
                "ref": f"doc:{doc_id} seq:{ch.get('seq')}",
            }
        stack.append((level, key))
    return nodes


# ── Harvest + triangulation ───────────────────────────────────────────────────


def _propose_nodes(
    db: OrivellumDB,
    work_id: str,
    domain: str,
    membership: dict[str, list[tuple[str, dict]]],
    children: dict[str, set[str]],
    n_sources: int,
    can_require: bool,
) -> Counter:
    """Classify each union node and upsert its proposal.  Returns class counts."""
    counts = Counter({"required": 0, "optional": 0, "contested": 0})
    for key in sorted(membership):
        holders = sorted(membership[key], key=lambda h: h[0])
        agreement = len(holders)
        # Placement disagreement includes "" (top level): filing a node at the
        # top in one source and under a parent in another IS a disagreement.
        parents = {node["parent_key"] for _d, node in holders}
        if agreement >= 2 and len(parents) > 1:
            node_class = "contested"  # sources organise the domain differently
        elif can_require and agreement >= REQUIRED_MIN_SOURCES:
            node_class = "required"  # intersection across independent sources
        else:
            node_class = "optional"  # periphery, with its sources recorded
        counts[node_class] += 1
        first = holders[0][1]
        db.upsert_domain_node_proposal(
            work_id=work_id,
            domain=domain,
            node_key=key,
            label=first["label"],
            parent_key=first["parent_key"],
            node_class=node_class,
            agreement=agreement,
            source_count=n_sources,
            sources=[
                {"doc_id": doc_id, "ref": node["ref"], "parent_key": node["parent_key"]}
                for doc_id, node in holders
            ],
            centrality=len(children.get(key, ())),
            meta=(
                {"parents_seen": sorted(p or "(top level)" for p in parents)}
                if len(parents) > 1
                else None
            ),
        )
    return counts


def harvest_domain(db: OrivellumDB, work_id: str, domain: str) -> dict:
    """Harvest node proposals for one domain from its registered sources.

    Independence = distinct documents.  With fewer than
    ``REQUIRED_MIN_SOURCES`` structure sources, no node can be proposed as
    ``required`` — the intersection of two sources is not a consensus core —
    and the result says so explicitly.
    """
    domain = (domain or "").strip().lower()
    sources = [s for s in db.list_domain_sources(work_id, domain) if s["kind"] == "structure"]
    if not sources:
        return {
            "domain": domain,
            "sources": 0,
            "proposed": 0,
            "required": 0,
            "optional": 0,
            "contested": 0,
            "note": "no structure sources registered for this domain",
        }

    per_source: dict[str, dict[str, dict]] = {}
    for src in sources:
        structure = _structure_of(db, src["doc_id"])
        if structure:
            per_source[src["doc_id"]] = structure
    n_sources = len(per_source)
    can_require = n_sources >= REQUIRED_MIN_SOURCES

    # Union of node keys, with per-source placement.
    membership: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for doc_id, structure in per_source.items():
        for key, node in structure.items():
            membership[key].append((doc_id, node))

    # Centrality = distinct child keys across the union structure.
    children: dict[str, set[str]] = defaultdict(set)
    for key, holders in membership.items():
        for _doc_id, node in holders:
            if node["parent_key"]:
                children[node["parent_key"]].add(key)

    counts = _propose_nodes(db, work_id, domain, membership, children, n_sources, can_require)

    result = {
        "domain": domain,
        "sources": n_sources,
        "proposed": sum(counts.values()),
        "required": counts["required"],
        "optional": counts["optional"],
        "contested": counts["contested"],
    }
    if not can_require:
        result["note"] = (
            f"only {n_sources} independent structure source(s) — a consensus "
            f"core needs at least {REQUIRED_MIN_SOURCES}; nothing proposed as required"
        )
    logger.info("domain harvest %s/%s: %s", work_id[:8], domain, result)
    return result


# ── Corpus evidence + measured demand ─────────────────────────────────────────


def _fts_phrase(term: str) -> str:
    """Quote a term for an FTS5 MATCH — phrase semantics, no query syntax."""
    return '"' + term.replace('"', '""') + '"'


def corpus_evidence_count(db: OrivellumDB, work_id: str, term: str) -> int:
    """Deterministic evidence count for a node: non-rejected knowledge items
    plus document passages in this Work matching the node phrase."""
    phrase = _fts_phrase(term)
    with db._lock:
        k = db._conn.execute(
            """SELECT COUNT(*) AS n FROM knowledge_fts f
               JOIN knowledge k ON k.id = f.knowledge_id
               WHERE knowledge_fts MATCH ? AND k.work_id = ?
                 AND k.review_status != 'rejected'""",
            (phrase, work_id),
        ).fetchone()["n"]
        c = db._conn.execute(
            """SELECT COUNT(*) AS n FROM chunks_fts f
               JOIN documents d ON d.id = f.doc_id
               WHERE chunks_fts MATCH ? AND d.work_id = ?
                 AND COALESCE(d.quarantined, 0) = 0""",
            (phrase, work_id),
        ).fetchone()["n"]
    return int(k) + int(c)


def demand_count(db: OrivellumDB, work_id: str, term: str) -> int:
    """MEASURED demand: user messages in this Work's conversations that touch
    the node phrase.  Retrospective usage, never a hand-written flag."""
    phrase = _fts_phrase(term)
    with db._lock:
        row = db._conn.execute(
            """SELECT COUNT(*) AS n FROM messages_fts f
               JOIN conversations c ON c.id = f.conversation_id
               WHERE messages_fts MATCH ? AND f.role = 'user' AND c.work_id = ?""",
            (phrase, work_id),
        ).fetchone()
    return int(row["n"])


# ── G2 coverage detector ──────────────────────────────────────────────────────


def candidates_domain_coverage(work_id: str, db: OrivellumDB) -> list[dict]:
    """Report-only candidates for the domain-coverage detector (harness use).

    Only RATIFIED, non-contested nodes can produce candidates — an
    unratified node is an unsigned opinion and generates nothing.
    ``frequency`` is the corpus mention count (for rare/common strata).
    """
    candidates: list[dict] = []
    for node in db.list_domain_nodes(work_id, status="ratified"):
        if node["node_class"] == "contested":
            continue
        evidence = corpus_evidence_count(db, work_id, node["node_key"])
        if evidence >= _MIN_EVIDENCE:
            continue
        candidates.append(
            {
                "pair_key": f"{node['domain']}|{node['node_key']}",
                "frequency": evidence,
                "node_id": node["id"],
                "domain": node["domain"],
                "node_key": node["node_key"],
                "label": node["label"],
                "node_class": node["node_class"],
                "agreement": node["agreement"],
                "centrality": node["centrality"],
                "evidence_count": evidence,
            }
        )
    candidates.sort(key=lambda c: (-c["agreement"], -c["centrality"], c["pair_key"]))
    return candidates


def _source_ref(db: OrivellumDB, node: dict) -> str:
    import json as _json

    try:
        sources = _json.loads(node["sources"] or "[]")
    except Exception:
        sources = []
    refs = "; ".join(s.get("ref", "") for s in sources[:3] if s.get("ref"))
    return refs or f"domain:{node['domain']}"


def detect_domain_coverage(work_id: str, db: OrivellumDB) -> dict:
    """G2: ratified domain nodes with insufficient corpus evidence.

    Severity is computed from agreement level, centrality, dependents, and
    measured demand — the db layer derives it; nothing here assigns one.
    """
    emitted = 0
    candidates = candidates_domain_coverage(work_id, db)
    for cand in candidates:
        node = db.get_domain_node(cand["node_id"])
        if node is None:
            continue
        demand = demand_count(db, work_id, node["node_key"])
        db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_DOMAIN_COVERAGE,
            scope=f"domain:{node['domain']}",
            frame_node_id=node["id"],
            frame_source_ref=_source_ref(db, node),
            evidence_absent=(
                f'knowledge or passages on "{node["label"]}" — searched this '
                f"Work's knowledge and document text; found {cand['evidence_count']} "
                f"(threshold {_MIN_EVIDENCE})"
            ),
            centrality=node["centrality"],
            dependent_count=node["centrality"],
            agreement=node["agreement"],
            demand=demand,
            unit=node["label"],
            force_check=DETECTOR_DOMAIN_COVERAGE,
            issue_type="domain_node_uncovered",
            classification="interpretive_frame",
            action="research",
            meta={
                "layer": "interpretive_frame",
                "domain": node["domain"],
                "node_class": node["node_class"],
                "agreement": node["agreement"],
                "measured_demand": demand,
                "pair_key": cand["pair_key"],
            },
        )
        emitted += 1
    return {"detector": DETECTOR_DOMAIN_COVERAGE, "candidates": candidates, "emitted": emitted}


# ── G4 frontier gaps ──────────────────────────────────────────────────────────


def detect_domain_frontier(work_id: str, db: OrivellumDB) -> dict:
    """G4: ratified CONTESTED nodes — the sources themselves disagree.

    Routed to the decision queue, never the research queue: no amount of
    research closes a disagreement between reference works.  Severity is
    capped below blocking levels in ``compute_severity`` — a frontier gap is
    never critical as a deficiency.
    """
    emitted = 0
    nodes = db.list_domain_nodes(work_id, status="ratified", node_class="contested")
    for node in nodes:
        parents = []
        try:
            import json as _json

            parents = _json.loads(node["meta"] or "{}").get("parents_seen", [])
        except Exception:
            parents = []
        db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_DOMAIN_FRONTIER,
            scope=f"domain:{node['domain']}",
            frame_node_id=node["id"],
            frame_source_ref=_source_ref(db, node),
            evidence_absent=(
                f'a settled placement for "{node["label"]}" — independent sources '
                f"file it under different structures ({', '.join(parents) or 'divergent parents'})"
            ),
            centrality=node["centrality"],
            dependent_count=node["centrality"],
            agreement=node["agreement"],
            unit=node["label"],
            force_check=GAP_CLASS_DOMAIN_FRONTIER,
            issue_type="structural_disagreement",
            classification="frontier",
            action="decide",
            meta={
                "layer": "interpretive_frame",
                "queue": "decision",
                "domain": node["domain"],
                "note": (
                    "a decision you owe, not a research deficiency — "
                    "this node may not be treated as settled no matter how "
                    "confidently a single source states it"
                ),
            },
        )
        emitted += 1
    return {"detector": GAP_CLASS_DOMAIN_FRONTIER, "emitted": emitted}


# ── Relative recall (review §4.5) ─────────────────────────────────────────────


def relative_recall(db: OrivellumDB, work_id: str) -> dict:
    """Completeness measured against PEER references, not against the world.

    * ``bibliography`` peers — the peer's citation list vs Library holdings
      (what the peer says matters vs what is actually held).
    * ``structure`` peers — the peer's heading structure vs this Work's
      corpus evidence (what the peer covers vs what the corpus can support).

    Honest framing: this is recall RELATIVE TO the chosen peer.  It says
    nothing about material the peer itself omits.
    """
    peers = db.list_domain_sources(work_id)
    haystack = _library_haystack(db)
    reports: list[dict] = []

    for peer in peers:
        if peer["kind"] == "bibliography":
            cited: Counter = Counter()
            with db._lock:
                rows = db._conn.execute(
                    "SELECT text FROM chunks WHERE doc_id=?", (peer["doc_id"],)
                ).fetchall()
            for row in rows:
                for author, year in extract_citations(row["text"] or ""):
                    cited[(author, year)] += 1
            if not cited:
                continue
            held = sum(1 for (a, y) in cited if _is_held(a, y, haystack))
            missing = sorted(
                ((a, y, n) for (a, y), n in cited.items() if not _is_held(a, y, haystack)),
                key=lambda t: (-t[2], t[0], t[1]),
            )[:_MISSING_CAP]
            reports.append(
                {
                    "peer_doc_id": peer["doc_id"],
                    "peer_title": peer["doc_title"],
                    "domain": peer["domain"],
                    "mode": "bibliography",
                    "peer_total": len(cited),
                    "matched": held,
                    "relative_recall": round(held / len(cited), 3),
                    "missing": [{"cited": f"{a} ({y})", "frequency": n} for a, y, n in missing],
                }
            )
        else:  # structure peer: heading coverage against corpus evidence
            structure = _structure_of(db, peer["doc_id"])
            if not structure:
                continue
            covered = 0
            missing_nodes: list[dict] = []
            for key in sorted(structure):
                evidence = corpus_evidence_count(db, work_id, key)
                if evidence >= _MIN_EVIDENCE:
                    covered += 1
                else:
                    missing_nodes.append({"heading": structure[key]["label"], "evidence": evidence})
            reports.append(
                {
                    "peer_doc_id": peer["doc_id"],
                    "peer_title": peer["doc_title"],
                    "domain": peer["domain"],
                    "mode": "structure",
                    "peer_total": len(structure),
                    "matched": covered,
                    "relative_recall": round(covered / len(structure), 3),
                    "missing": missing_nodes[:_MISSING_CAP],
                }
            )

    return {
        "work_id": work_id,
        "peers": reports,
        "note": (
            "recall relative to the chosen peer reference — an external "
            "yardstick, not a claim about the whole field"
        ),
    }
