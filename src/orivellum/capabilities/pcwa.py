"""PCWA — Partial Closed World Assumption detectors over the world graph.

The field's productive core for detecting absence from presence alone
(brutal review Part 1): no Domain Model, no external frame — four mechanisms
operating on typed entity-relation pairs from the ATLAS world graph:

  1. **Functional-predicate closure** — when the data shows a relation is
     functional for a class (nearly every subject carries exactly one value),
     one value present means complete.  Emits POSITIVE-polarity completeness
     proposals, never gaps.
  2. **card_k oracles** — a subject is complete for a relation once it has at
     least *k* distinct values, with *k* set per relation from the data
     (k > 2 is rarely useful, so k is capped at 2).  Also positive polarity.
  3. **Mined maximum cardinalities** — if most members of a class carry
     exactly *n* values for a relation, members with fewer are gaps
     ("9 of 10 Characters with kinship_with carry exactly 2 values").
  4. **Peer-group local closure** (the UnCommonSense pattern) — relations
     present for comparable entities but absent for the target are gap
     candidates ("7 of 10 comparable Characters have located_at; X has none").

Discipline:
  * Relation metadata (functionality, per-relation k, mined max cardinality)
    is DERIVED from the graph and stored re-derivably — every mining pass
    replaces the Work's rows wholesale (``graph_relation_meta``).
  * Every gap goes through ``db.create_or_refresh_gap`` — standard identity /
    lifecycle / citation discipline; each cites its mechanism (force_check)
    and the statistical basis in evidence_absent and frame_source_ref.
  * Completeness inferences are MACHINE-PROPOSED assertions
    (``db.propose_completeness``) — accumulated closure knowledge that never
    suppresses gaps and never auto-ratifies; a human signature activates.
  * Severity demand is MEASURED retrieval/query traffic
    (domain_model.demand_count) — never a hand flag.
  * Popularity bias is surfaced, not hidden: every report stratifies output
    by entity frequency (graph degree), and rare-entity findings are marked
    ``confidence: "low"``.
  * ZERO model calls.  Everything here is counting.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from orivellum.capabilities.gap_engine import frequency_band

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# One gap class for all pair-scoped PCWA findings: the REGION is the
# (entity, relation) pair, so a single completeness assertion on the pair
# closes it against every mechanism.  The mechanism itself travels in
# force_check and meta — "each gap cites the mechanism".
GAP_CLASS_GRAPH_PAIR = "graph_pair"

DETECTOR_FUNCTIONAL = "functional_closure"
DETECTOR_CARDK = "card_k_oracle"
DETECTOR_MINED_CARD = "mined_cardinality"
DETECTOR_PEER = "peer_group_closure"

# ── Mining thresholds (deterministic, data-derived) ───────────────────────────
_MIN_SUBJECTS = 5  # a (class, relation) pair needs this many subjects to mine
_FUNCTIONAL_SHARE = 0.9  # share with exactly 1 value to call a relation functional
_MAXCARD_SHARE = 0.6  # share carrying exactly n values to mine n as class max
_CARDK_CAP = 2  # the literature's note: k > 2 is rarely useful
_PEER_POOL = 10  # peer group size (top-similarity comparables)
_MIN_PEERS = 5  # smallest peer pool worth reasoning over
_PEER_SHARE = 0.7  # share of peers carrying a relation to flag its absence
_MAX_EMIT = 100  # per-detector emission cap (reports still count everything)


def _pair_key(node_id: str, edge_type: str) -> str:
    """Region identity for a (entity, relation) pair — keyed by NODE ID.

    Names are not unique (two Characters can both be "John"); keying the
    completeness scope by name would let ratifying one entity's closure
    dismiss the other's gaps.  Node ids are unique; if re-extraction replaces
    a node the old assertion simply never matches again — it fails OPEN
    (detection resumes), never closed.  Names travel in basis/meta for display.
    """
    return f"{node_id}|{edge_type}"


def _subject_value_counts(work_id: str, db: OrivellumDB) -> list[dict]:
    """(node_type, edge_type, subject) → distinct-value count, one query."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT n.node_type AS node_type, e.edge_type AS edge_type,
                      e.src AS node_id, n.name AS name,
                      COUNT(DISTINCT e.dst) AS vals
               FROM graph_edge e JOIN graph_node n ON n.id = e.src
               WHERE e.work_id = ?
               GROUP BY n.node_type, e.edge_type, e.src""",
            (work_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _node_degrees(work_id: str, db: OrivellumDB) -> dict[str, int]:
    """Entity frequency proxy: total edges touching each node."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT node_id, SUM(n) AS degree FROM (
                   SELECT src AS node_id, COUNT(*) AS n FROM graph_edge
                       WHERE work_id=? GROUP BY src
                   UNION ALL
                   SELECT dst AS node_id, COUNT(*) AS n FROM graph_edge
                       WHERE work_id=? GROUP BY dst
               ) GROUP BY node_id""",
            (work_id, work_id),
        ).fetchall()
    return {r["node_id"]: int(r["degree"] or 0) for r in rows}


# ── Step 1: relation metadata mining ──────────────────────────────────────────


def mine_relation_metadata(work_id: str, db: OrivellumDB, *, store: bool = True) -> list[dict]:
    """Derive per-(class, relation) statistics from the graph and store them.

    Functionality, per-relation card_k, and mined max cardinality are all
    computed from the distinct-value distribution of subjects that carry the
    relation.  Wholesale replace on store — the metadata is re-derivable and
    tracks the graph as it grows.
    """
    dist: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in _subject_value_counts(work_id, db):
        dist[(r["node_type"], r["edge_type"])].append(int(r["vals"]))

    rows: list[dict] = []
    for (node_type, edge_type), counts in sorted(dist.items()):
        n = len(counts)
        hist = Counter(counts)
        share_one = hist.get(1, 0) / n
        functional = n >= _MIN_SUBJECTS and share_one >= _FUNCTIONAL_SHARE

        card_k = None
        if n >= _MIN_SUBJECTS:
            # modal distinct-value count, capped: "k above 2 is rarely useful"
            mode = min(hist.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            card_k = 1 if functional else min(mode, _CARDK_CAP)

        max_card = None
        max_share = None
        if n >= _MIN_SUBJECTS:
            mode, mode_n = min(hist.items(), key=lambda kv: (-kv[1], kv[0]))
            if mode_n / n >= _MAXCARD_SHARE:
                max_card = mode
                max_share = mode_n / n

        rows.append(
            {
                "node_type": node_type,
                "edge_type": edge_type,
                "n_subjects": n,
                "functional": functional,
                "functional_share": round(share_one, 4),
                "card_k": card_k,
                "max_cardinality": max_card,
                "max_cardinality_share": round(max_share, 4) if max_share else None,
                "value_histogram": {str(k): v for k, v in sorted(hist.items())},
            }
        )
    if store:
        db.replace_relation_meta(work_id, rows)
    return rows


def _meta_by_pair(work_id: str, db: OrivellumDB) -> dict[tuple[str, str], dict]:
    stored = db.list_relation_meta(work_id)
    if not stored:  # never mined yet — derive (and store) now
        stored_rows = mine_relation_metadata(work_id, db, store=True)
        return {(r["node_type"], r["edge_type"]): r for r in stored_rows}
    return {(r["node_type"], r["edge_type"]): r for r in stored}


# ── Mechanisms 1 + 2: completeness inference (positive polarity) ──────────────


def candidates_functional_closure(work_id: str, db: OrivellumDB) -> list[dict]:
    """Complete (entity, relation) pairs by functional-predicate closure."""
    meta = _meta_by_pair(work_id, db)
    degrees = _node_degrees(work_id, db)
    out = []
    for r in _subject_value_counts(work_id, db):
        m = meta.get((r["node_type"], r["edge_type"]))
        if not m or not m["functional"] or int(r["vals"]) != 1:
            continue
        deg = degrees.get(r["node_id"], 0)
        share = float(m["functional_share"])
        out.append(
            {
                "pair_key": _pair_key(r["node_id"], r["edge_type"]),
                "node_id": r["node_id"],
                "name": r["name"],
                "node_type": r["node_type"],
                "edge_type": r["edge_type"],
                "frequency": deg,
                "frequency_band": frequency_band(deg),
                "basis": (
                    f"functional relation: {share:.0%} of {m['n_subjects']} "
                    f"{r['node_type']} subjects with {r['edge_type']} carry exactly "
                    f"one value; {r['name']} has one — locally complete"
                ),
            }
        )
    out.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return out


def candidates_card_k(work_id: str, db: OrivellumDB) -> list[dict]:
    """Complete pairs by the card_k oracle (non-functional relations only —
    functional relations are already covered by mechanism 1 at k=1)."""
    meta = _meta_by_pair(work_id, db)
    degrees = _node_degrees(work_id, db)
    out = []
    for r in _subject_value_counts(work_id, db):
        m = meta.get((r["node_type"], r["edge_type"]))
        if not m or m["functional"] or not m["card_k"]:
            continue
        k = int(m["card_k"])
        if int(r["vals"]) < k:
            continue
        deg = degrees.get(r["node_id"], 0)
        out.append(
            {
                "pair_key": _pair_key(r["node_id"], r["edge_type"]),
                "node_id": r["node_id"],
                "name": r["name"],
                "node_type": r["node_type"],
                "edge_type": r["edge_type"],
                "frequency": deg,
                "frequency_band": frequency_band(deg),
                "basis": (
                    f"card_{k} oracle: k={k} mined from {m['n_subjects']} "
                    f"{r['node_type']} subjects of {r['edge_type']}; "
                    f"{r['name']} has {int(r['vals'])} ≥ {k} distinct values — "
                    "locally complete"
                ),
            }
        )
    out.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return out


def _propose_closures(
    work_id: str, db: OrivellumDB, candidates: list[dict], detector: str
) -> dict:
    proposed, skipped = [], 0
    for c in candidates[:_MAX_EMIT]:
        row = db.propose_completeness(
            work_id=work_id,
            gap_class=GAP_CLASS_GRAPH_PAIR,
            scope=c["pair_key"],
            basis=c["basis"],
            proposed_by=f"machine:{detector}",
            frame_node_id=f"graph:{c['node_id']}",
            frame_source_ref=f"relation-meta:{c['node_type']}/{c['edge_type']}",
            meta={
                "mechanism": detector,
                "frequency_band": c["frequency_band"],
                "confidence": "low" if c["frequency_band"] == "rare" else "normal",
            },
        )
        if row is None:
            skipped += 1  # a signed human decision already covers the region
        else:
            proposed.append(row)
    strata = {"rare": 0, "common": 0}
    for c in candidates:
        strata[c["frequency_band"]] += 1
    return {
        "work_id": work_id,
        "candidates": len(candidates),
        "strata": strata,
        "proposed": len(proposed),
        "skipped_signed": skipped,
        "assertions": proposed,
    }


def detect_functional_closure(work_id: str, db: OrivellumDB) -> dict:
    """Propose completeness for functionally-closed pairs (never auto-ratified)."""
    return _propose_closures(
        work_id, db, candidates_functional_closure(work_id, db), DETECTOR_FUNCTIONAL
    )


def detect_card_k(work_id: str, db: OrivellumDB) -> dict:
    """Propose completeness for card_k-satisfied pairs (never auto-ratified)."""
    return _propose_closures(work_id, db, candidates_card_k(work_id, db), DETECTOR_CARDK)


# ── Mechanism 3: mined maximum cardinality → gaps ─────────────────────────────


def candidates_mined_cardinality(work_id: str, db: OrivellumDB) -> list[dict]:
    """Subjects carrying FEWER values than the class's mined max cardinality.

    Zero counts too: a same-class node with NO values for the relation is the
    most important absence a cardinality oracle can surface — but only when
    the relation is prevalent in the class (carried by ≥ the max-cardinality
    support share of ALL class members), so a niche relation held by a handful
    of members does not flag the whole class.
    """
    meta = _meta_by_pair(work_id, db)
    degrees = _node_degrees(work_id, db)
    with db._lock:
        nodes = [
            dict(r)
            for r in db._conn.execute(
                "SELECT id, node_type, name FROM graph_node WHERE work_id=?", (work_id,)
            ).fetchall()
        ]
    class_size = Counter(n["node_type"] for n in nodes)

    def _candidate(node_id, name, node_type, edge_type, have, m):
        n_exp = int(m["max_cardinality"])
        deg = degrees.get(node_id, 0)
        share = float(m["max_cardinality_share"] or 0)
        conforming = int(round(share * m["n_subjects"]))
        return {
            "pair_key": _pair_key(node_id, edge_type),
            "node_id": node_id,
            "name": name,
            "node_type": node_type,
            "edge_type": edge_type,
            "have": have,
            "expected": n_exp,
            "frequency": deg,
            "frequency_band": frequency_band(deg),
            "basis": (
                f"{conforming} of {m['n_subjects']} {node_type} subjects "
                f"with {edge_type} carry exactly {n_exp} value(s); "
                f"{name} has {have}"
            ),
        }

    out = []
    carriers: dict[tuple[str, str], set[str]] = defaultdict(set)
    for r in _subject_value_counts(work_id, db):
        carriers[(r["node_type"], r["edge_type"])].add(r["node_id"])
        m = meta.get((r["node_type"], r["edge_type"]))
        if not m or not m["max_cardinality"]:
            continue
        have = int(r["vals"])
        if have >= int(m["max_cardinality"]):
            continue
        out.append(_candidate(r["node_id"], r["name"], r["node_type"], r["edge_type"], have, m))

    # Zero-value members of prevalent relations.
    for (node_type, edge_type), m in meta.items():
        if not m["max_cardinality"]:
            continue
        total = class_size.get(node_type, 0)
        holders = carriers.get((node_type, edge_type), set())
        if total == 0 or len(holders) / total < _MAXCARD_SHARE:
            continue  # relation not prevalent enough to expect of every member
        for n in nodes:
            if n["node_type"] != node_type or n["id"] in holders:
                continue
            out.append(_candidate(n["id"], n["name"], node_type, edge_type, 0, m))

    out.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return out


# ── Mechanism 4: peer-group local closure → gaps ──────────────────────────────


def candidates_peer_group(work_id: str, db: OrivellumDB) -> list[dict]:
    """Relations present for comparable entities but absent for the target.

    Peers are same-class nodes ranked by Jaccard similarity over their
    outgoing relation-type profiles (deterministic; name as tiebreak).  A
    relation carried by ≥ ``_PEER_SHARE`` of the top-``_PEER_POOL`` peers and
    absent for the target is a gap candidate — the UnCommonSense pattern.
    """
    with db._lock:
        nodes = db._conn.execute(
            "SELECT id, node_type, name FROM graph_node WHERE work_id=?",
            (work_id,),
        ).fetchall()
    profiles: dict[str, set[str]] = defaultdict(set)
    for r in _subject_value_counts(work_id, db):
        profiles[r["node_id"]].add(r["edge_type"])
    degrees = _node_degrees(work_id, db)

    by_type: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_type[n["node_type"]].append(dict(n))

    out = []
    for node_type, members in sorted(by_type.items()):
        if len(members) <= _MIN_PEERS:
            continue
        for target in sorted(members, key=lambda m: m["name"].lower()):
            t_prof = profiles.get(target["id"], set())
            if not t_prof:
                continue  # nothing to compare — no shared basis for peers
            scored = []
            for other in members:
                if other["id"] == target["id"]:
                    continue
                o_prof = profiles.get(other["id"], set())
                inter = len(t_prof & o_prof)
                if inter == 0:
                    continue
                jac = inter / len(t_prof | o_prof)
                scored.append((-jac, other["name"].lower(), other["id"], o_prof))
            scored.sort()
            peers = scored[:_PEER_POOL]
            if len(peers) < _MIN_PEERS:
                continue
            rel_counts: Counter[str] = Counter()
            for _, _, _, o_prof in peers:
                rel_counts.update(o_prof)
            for edge_type, cnt in sorted(rel_counts.items()):
                if edge_type in t_prof or cnt / len(peers) < _PEER_SHARE:
                    continue
                deg = degrees.get(target["id"], 0)
                out.append(
                    {
                        "pair_key": _pair_key(target["id"], edge_type),
                        "node_id": target["id"],
                        "name": target["name"],
                        "node_type": node_type,
                        "edge_type": edge_type,
                        "peers_with": cnt,
                        "peer_pool": len(peers),
                        "frequency": deg,
                        "frequency_band": frequency_band(deg),
                        "basis": (
                            f"{cnt} of {len(peers)} comparable {node_type} entities "
                            f"have {edge_type}; {target['name']} has none"
                        ),
                    }
                )
    out.sort(key=lambda c: (-c["frequency"], c["pair_key"]))
    return out


# ── Gap emitters (mechanisms 3 + 4) ───────────────────────────────────────────


def _emit_gaps(
    work_id: str, db: OrivellumDB, candidates: list[dict], detector: str, issue_type: str
) -> dict:
    from orivellum.capabilities.domain_model import demand_count

    gaps = []
    demand_cache: dict[str, int] = {}  # one entity → one measurement per run
    for c in candidates[:_MAX_EMIT]:
        key = c["name"].strip().lower()
        if key not in demand_cache:
            demand_cache[key] = demand_count(db, work_id, c["name"])
        demand = demand_cache[key]
        row = db.create_or_refresh_gap(
            work_id=work_id,
            gap_class=GAP_CLASS_GRAPH_PAIR,
            scope=c["pair_key"],
            frame_node_id=f"graph:{c['node_id']}",
            frame_source_ref=(
                f"relation-meta:{c['node_type']}/{c['edge_type']} — {c['basis']}"
            ),
            evidence_absent=(
                f"{c['name']} ({c['node_type']}) lacks expected {c['edge_type']} "
                f"value(s): {c['basis']}"
            ),
            centrality=c["frequency"],
            demand=demand,
            unit=f"work:{work_id}",
            force_check=detector,
            issue_type=issue_type,
            classification="coverage",
            action="research_entity_relation",
            meta={
                "mechanism": detector,
                "entity": c["name"],
                "node_type": c["node_type"],
                "edge_type": c["edge_type"],
                "frequency_band": c["frequency_band"],
                "confidence": "low" if c["frequency_band"] == "rare" else "normal",
                "measured_demand": demand,
                "statistical_basis": c["basis"],
            },
        )
        if row is not None:  # None = region under an active completeness assertion
            gaps.append(row)
    strata = {"rare": 0, "common": 0}
    for c in candidates:
        strata[c["frequency_band"]] += 1
    return {"work_id": work_id, "candidates": len(candidates), "strata": strata, "gaps": gaps}


def detect_mined_cardinality(work_id: str, db: OrivellumDB) -> dict:
    """Emit mined-max-cardinality gaps (frequency-stratified)."""
    return _emit_gaps(
        work_id,
        db,
        candidates_mined_cardinality(work_id, db),
        DETECTOR_MINED_CARD,
        "below_mined_cardinality",
    )


def detect_peer_group(work_id: str, db: OrivellumDB) -> dict:
    """Emit peer-group local-closure gaps (frequency-stratified)."""
    return _emit_gaps(
        work_id,
        db,
        candidates_peer_group(work_id, db),
        DETECTOR_PEER,
        "absent_among_peers",
    )


# ── Full scan ─────────────────────────────────────────────────────────────────


def run_pcwa_scan(work_id: str, db: OrivellumDB) -> dict:
    """Mine relation metadata, then run all four PCWA mechanisms."""
    relations = mine_relation_metadata(work_id, db, store=True)
    results = {
        DETECTOR_FUNCTIONAL: detect_functional_closure(work_id, db),
        DETECTOR_CARDK: detect_card_k(work_id, db),
        DETECTOR_MINED_CARD: detect_mined_cardinality(work_id, db),
        DETECTOR_PEER: detect_peer_group(work_id, db),
    }
    return {
        "work_id": work_id,
        "relations_mined": len(relations),
        "results": results,
        "total_gaps": sum(len(r.get("gaps", [])) for r in results.values()),
        "total_proposed_assertions": sum(
            r.get("proposed", 0) for r in results.values()
        ),
    }
