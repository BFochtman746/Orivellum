"""Tests for the PCWA detectors over the world graph (brutal review Part 1).

Deterministic fixture graphs per mechanism:
  * relation-metadata mining — functionality / card_k / mined max cardinality
    derived from counts, stored, and REPLACED wholesale on re-mining
  * functional closure — one value on a functional relation → machine-PROPOSED
    completeness assertion (never active, never suppressing)
  * card_k boundaries — ≥ k complete, < k not; k capped at 2
  * mined-cardinality gaps — members below the class max, statistical basis cited
  * peer-group local closure — relations peers carry that the target lacks
  * demand-weighted severity — measured retrieval traffic ordering, blocking
    gate still enforced for unmeasured detectors
  * assertion lifecycle — proposals never auto-ratify; ratification (signed)
    closes the region; human decisions are never overridden by the machine
  * frequency stratification — reports split rare/common, rare marked
    low-confidence
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


def _make_app(tmp_path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


def _node(db, wid, node_type, name):
    return db.create_graph_node(
        work_id=wid,
        chapter_id=None,
        node_type=node_type,
        name=name,
        evidence_quote=f"{name} appears",
        evidence_offset=0,
    )


def _edge(db, wid, src, dst, edge_type):
    return db.create_graph_edge(
        work_id=wid,
        chapter_id=None,
        src=src,
        dst=dst,
        edge_type=edge_type,
        evidence_quote="evidence",
        evidence_offset=0,
    )


def _functional_fixture(db, wid, n=6):
    """n Characters, each with exactly one located_at → a functional relation."""
    loc = _node(db, wid, "Location", "Hebron")
    chars = [_node(db, wid, "Character", f"Char{i}") for i in range(n)]
    for c in chars:
        _edge(db, wid, c, loc, "located_at")
    return chars, loc


def _cardinality_fixture(db, wid):
    """6 Characters with possesses: five carry 2 distinct values, one carries 1.
    → not functional; card_k = 2; mined max cardinality = 2 (5/6 ≈ 0.83)."""
    sword = _node(db, wid, "Object", "Sword")
    shield = _node(db, wid, "Object", "Shield")
    chars = [_node(db, wid, "Character", f"Bearer{i}") for i in range(6)]
    for c in chars[:5]:
        _edge(db, wid, c, sword, "possesses")
        _edge(db, wid, c, shield, "possesses")
    _edge(db, wid, chars[5], sword, "possesses")  # the under-carrying member
    return chars, sword, shield


# ── relation-metadata mining ──────────────────────────────────────────────────


def test_mining_derives_functionality_and_cardinalities(tmp_path):
    from orivellum.capabilities.pcwa import mine_relation_metadata

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid)
    _cardinality_fixture(db, wid)

    rows = {(r["node_type"], r["edge_type"]): r for r in mine_relation_metadata(wid, db)}

    fn = rows[("Character", "located_at")]
    assert fn["functional"] and fn["card_k"] == 1
    assert fn["functional_share"] == 1.0

    poss = rows[("Character", "possesses")]
    assert not poss["functional"]
    assert poss["card_k"] == 2  # modal count, capped at 2
    assert poss["max_cardinality"] == 2
    assert poss["max_cardinality_share"] == pytest.approx(5 / 6, abs=1e-3)
    assert poss["value_histogram"] == {"1": 1, "2": 5}

    # Stored and re-derivable: a second pass REPLACES the rows.
    stored = db.list_relation_meta(wid)
    assert len(stored) == len(rows)
    mine_relation_metadata(wid, db)
    assert len(db.list_relation_meta(wid)) == len(rows)  # no duplicates


def test_mining_needs_enough_subjects(tmp_path):
    """Below the subject floor, no functionality/card_k/max claims are made."""
    from orivellum.capabilities.pcwa import mine_relation_metadata

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    loc = _node(db, wid, "Location", "Gath")
    for i in range(3):  # 3 < _MIN_SUBJECTS
        _edge(db, wid, _node(db, wid, "Character", f"C{i}"), loc, "located_at")
    (row,) = mine_relation_metadata(wid, db)
    assert not row["functional"]
    assert row["card_k"] is None and row["max_cardinality"] is None


# ── mechanism 1: functional closure → machine proposals ───────────────────────


def test_functional_closure_proposes_never_ratifies(tmp_path):
    from orivellum.capabilities.pcwa import (
        GAP_CLASS_GRAPH_PAIR,
        detect_functional_closure,
    )

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid, n=6)

    report = detect_functional_closure(wid, db)
    assert report["proposed"] == 6
    for a in report["assertions"]:
        assert a["status"] == "proposed"
        assert a["signed_by"] == "machine:functional_closure"
        assert "functional relation" in a["basis"]

    # A proposal NEVER suppresses gap emission — the region is still open.
    scope = report["assertions"][0]["scope"]
    assert db.find_completeness_assertion(wid, GAP_CLASS_GRAPH_PAIR, scope) is None
    gap = db.create_or_refresh_gap(
        work_id=wid,
        gap_class=GAP_CLASS_GRAPH_PAIR,
        scope=scope,
        frame_node_id="graph:x",
        frame_source_ref="test",
        evidence_absent="still open",
        force_check="test",
    )
    assert gap is not None

    # Idempotent: re-running refreshes, it does not duplicate.
    report2 = detect_functional_closure(wid, db)
    assert report2["proposed"] == 6
    all_rows = db.list_completeness_assertions(wid, status="proposed")
    assert len(all_rows) == 6


def test_ratified_proposal_closes_region_and_is_ledgered(tmp_path):
    from orivellum.capabilities.pcwa import (
        GAP_CLASS_GRAPH_PAIR,
        detect_functional_closure,
    )

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid)
    prop = detect_functional_closure(wid, db)["assertions"][0]

    row = db.assert_completeness(
        work_id=wid,
        gap_class=prop["gap_class"],
        scope=prop["scope"],
        basis=prop["basis"],
        signed_by="brian",
    )
    assert row["status"] == "active"
    reasons = [t["reason"] for t in db.list_completeness_transitions(row["id"])]
    assert "ratified" in reasons  # proposed → active carries the ratified ledger row

    # Region now closed against every PCWA mechanism.
    assert (
        db.create_or_refresh_gap(
            work_id=wid,
            gap_class=GAP_CLASS_GRAPH_PAIR,
            scope=prop["scope"],
            frame_node_id="graph:x",
            frame_source_ref="test",
            evidence_absent="should be refused",
            force_check="test",
        )
        is None
    )


def test_machine_never_overrides_human_decisions(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]

    active = db.assert_completeness(
        work_id=wid, gap_class="graph_pair", scope="character:x|located_at",
        basis="checked by hand", signed_by="brian",
    )
    assert (
        db.propose_completeness(
            work_id=wid, gap_class="graph_pair", scope="character:x|located_at",
            basis="machine stats", proposed_by="machine:functional_closure",
        )
        is None
    )
    db.retract_completeness(active["id"], reason="reopening", signed_by="brian")
    # Retracted is ALSO a signed decision — the machine may not re-propose it.
    assert (
        db.propose_completeness(
            work_id=wid, gap_class="graph_pair", scope="character:x|located_at",
            basis="machine stats", proposed_by="machine:functional_closure",
        )
        is None
    )


def test_declining_a_proposal_is_signed_and_final(tmp_path):
    from orivellum.capabilities.pcwa import detect_functional_closure

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid)
    prop = detect_functional_closure(wid, db)["assertions"][0]
    row = db.retract_completeness(prop["id"], reason="not convinced", signed_by="brian")
    assert row["status"] == "retracted"
    # Re-detection cannot resurrect the declined proposal.
    detect_functional_closure(wid, db)
    assert db.get_completeness_assertion(prop["id"])["status"] == "retracted"


# ── mechanism 2: card_k boundaries ────────────────────────────────────────────


def test_card_k_boundary(tmp_path):
    from orivellum.capabilities.pcwa import candidates_card_k, detect_card_k

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)

    cands = candidates_card_k(wid, db)
    # exactly-k (2 values) counts as complete; the 1-value bearer does not
    names = {c["name"] for c in cands}
    assert names == {f"Bearer{i}" for i in range(5)}
    assert all("card_2" in c["basis"] for c in cands)

    report = detect_card_k(wid, db)
    assert report["proposed"] == 5
    assert all(a["status"] == "proposed" for a in report["assertions"])


def test_card_k_skips_functional_relations(tmp_path):
    """Functional relations are mechanism 1's territory — no double proposals."""
    from orivellum.capabilities.pcwa import candidates_card_k

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid)
    assert candidates_card_k(wid, db) == []


# ── mechanism 3: mined max cardinality → gaps ─────────────────────────────────


def test_mined_cardinality_gap_cites_statistics(tmp_path):
    from orivellum.capabilities.pcwa import (
        DETECTOR_MINED_CARD,
        GAP_CLASS_GRAPH_PAIR,
        detect_mined_cardinality,
    )

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)

    report = detect_mined_cardinality(wid, db)
    assert len(report["gaps"]) == 1
    gap = report["gaps"][0]
    assert gap["gap_class"] == GAP_CLASS_GRAPH_PAIR
    assert gap["force_check"] == DETECTOR_MINED_CARD
    assert "5 of 6" in gap["evidence_absent"] and "Bearer5 has 1" in gap["evidence_absent"]
    meta = json.loads(gap["meta"])
    assert meta["mechanism"] == DETECTOR_MINED_CARD
    assert "statistical_basis" in meta

    # Standard lifecycle discipline: dismissal is terminal against re-detection.
    db.transition_gap(gap["id"], "dismissed", reason="fine as is", signed_by="brian")
    report2 = detect_mined_cardinality(wid, db)
    assert db.get_gap(gap["id"])["status"] == "dismissed"


def test_zero_value_members_of_prevalent_relations_are_gaps(tmp_path):
    """A same-class node with NO values for a prevalent relation is flagged —
    the most important absence a cardinality oracle can surface."""
    from orivellum.capabilities.pcwa import candidates_mined_cardinality

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)  # 6 bearers; possesses max cardinality 2
    _node(db, wid, "Character", "Empty")  # zero possesses edges

    cands = candidates_mined_cardinality(wid, db)
    by_name = {c["name"]: c for c in cands}
    assert by_name["Empty"]["have"] == 0 and by_name["Empty"]["expected"] == 2
    assert "Empty has 0" in by_name["Empty"]["basis"]
    assert by_name["Bearer5"]["have"] == 1  # the under-carrier is still flagged


def test_niche_relations_do_not_flag_the_whole_class(tmp_path):
    """A relation held by a small minority of the class never produces
    zero-value gaps for everyone else."""
    from orivellum.capabilities.pcwa import candidates_mined_cardinality

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)  # 6 possess-holders
    for i in range(10):  # …out of 16 Characters → prevalence 6/16 < 0.6
        _node(db, wid, "Character", f"Bystander{i}")
    names = {c["name"] for c in candidates_mined_cardinality(wid, db)}
    assert names == {"Bearer5"}  # only the genuine under-carrier


def test_duplicate_names_get_distinct_regions(tmp_path):
    """Two same-named nodes must NOT share a completeness scope — ratifying
    one entity's closure may never dismiss the other's gaps."""
    from orivellum.capabilities.pcwa import candidates_mined_cardinality

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    chars, sword, _shield = _cardinality_fixture(db, wid)
    # A second node ALSO named Bearer5, also under-carrying.
    twin = _node(db, wid, "Character", "Bearer5")
    _edge(db, wid, twin, sword, "possesses")

    cands = [c for c in candidates_mined_cardinality(wid, db) if c["name"] == "Bearer5"]
    assert len(cands) == 2
    assert cands[0]["pair_key"] != cands[1]["pair_key"]  # id-keyed regions

    # Closing one twin's region leaves the other's gap emission untouched.
    db.assert_completeness(
        work_id=wid, gap_class="graph_pair", scope=cands[0]["pair_key"],
        basis="checked this one by hand", signed_by="brian",
    )
    still_open = db.create_or_refresh_gap(
        work_id=wid, gap_class="graph_pair", scope=cands[1]["pair_key"],
        frame_node_id=f"graph:{cands[1]['node_id']}", frame_source_ref="test",
        evidence_absent="twin still open", force_check="test",
    )
    assert still_open is not None


def test_ratify_is_atomic_against_double_signing(tmp_path):
    from orivellum.capabilities.pcwa import detect_functional_closure

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _functional_fixture(db, wid)
    prop = detect_functional_closure(wid, db)["assertions"][0]
    row = db.ratify_completeness(prop["id"], signed_by="brian")
    assert row["status"] == "active" and row["signed_by"] == "brian"
    with pytest.raises(ValueError, match="not 'proposed'"):
        db.ratify_completeness(prop["id"], signed_by="mallory")
    # First signature survives.
    kept = db.find_completeness_assertion(wid, prop["gap_class"], prop["scope"])
    assert kept["signed_by"] == "brian"
    with pytest.raises(KeyError):
        db.ratify_completeness("no-such-id", signed_by="brian")


# ── mechanism 4: peer-group local closure ─────────────────────────────────────


def _peer_fixture(db, wid):
    """7 Characters: six carry performs+located_at, the target only performs."""
    ev = _node(db, wid, "Event", "Battle")
    loc = _node(db, wid, "Location", "Gath")
    full = [_node(db, wid, "Character", f"Peer{i}") for i in range(6)]
    for c in full:
        _edge(db, wid, c, ev, "performs")
        _edge(db, wid, c, loc, "located_at")
    target = _node(db, wid, "Character", "Achish")
    _edge(db, wid, target, ev, "performs")
    return target


def test_peer_group_flags_only_the_outlier(tmp_path):
    from orivellum.capabilities.pcwa import (
        DETECTOR_PEER,
        candidates_peer_group,
        detect_peer_group,
    )

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _peer_fixture(db, wid)

    cands = candidates_peer_group(wid, db)
    assert [c["name"] for c in cands] == ["Achish"]
    c = cands[0]
    assert c["edge_type"] == "located_at"
    assert "6 of 6 comparable Character entities have located_at" in c["basis"]

    report = detect_peer_group(wid, db)
    assert len(report["gaps"]) == 1
    assert report["gaps"][0]["force_check"] == DETECTOR_PEER


def test_peer_group_needs_a_pool(tmp_path):
    """Too few same-class members → no peer reasoning, no candidates."""
    from orivellum.capabilities.pcwa import candidates_peer_group

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    ev = _node(db, wid, "Event", "Battle")
    loc = _node(db, wid, "Location", "Gath")
    for i in range(3):
        c = _node(db, wid, "Character", f"C{i}")
        _edge(db, wid, c, ev, "performs")
        _edge(db, wid, c, loc, "located_at")
    t = _node(db, wid, "Character", "T")
    _edge(db, wid, t, ev, "performs")
    assert candidates_peer_group(wid, db) == []


# ── demand weighting + frequency stratification ───────────────────────────────


def test_demand_is_measured_from_retrieval_traffic(tmp_path):
    """Logged knowledge retrievals raise a gap's measured demand and severity;
    the blocking gate still suppresses unmeasured detectors."""
    from orivellum.capabilities.domain_model import demand_count
    from orivellum.capabilities.pcwa import detect_mined_cardinality

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)

    # Real retrieval traffic: knowledge about the under-carrying entity was
    # injected into chat 7 times (the knowledge_retrievals log).
    kid = db.create_knowledge_item(wid, "note", "carries the sword", subject="Bearer5")
    conv = db.create_conversation(title="t")
    with db._lock:
        for i in range(7):
            db._conn.execute(
                "INSERT INTO knowledge_retrievals (id, knowledge_id, conv_id, retrieved_at) "
                "VALUES (?,?,?,?)",
                (f"kr{i}", kid, conv["id"], "2026-08-12T00:00:00Z"),
            )
        db._conn.commit()
    assert demand_count(db, wid, "Bearer5") >= 7

    report = detect_mined_cardinality(wid, db)
    gap = report["gaps"][0]
    meta = json.loads(gap["meta"])
    assert meta["measured_demand"] >= 7
    # demand ≥ DEMAND_BLOCKING on an UNMEASURED detector → blocking suppressed
    assert "blocking_suppressed" in meta
    assert gap["severity"] != "critical"


def test_severity_orders_by_measured_demand():
    from orivellum.capabilities.gap_engine import compute_severity

    rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    quiet = compute_severity("graph_pair", centrality=2, demand=0)
    busy = compute_severity("graph_pair", centrality=2, demand=9)
    assert rank[busy] > rank[quiet]


def test_reports_stratify_by_entity_frequency(tmp_path):
    from orivellum.capabilities.pcwa import detect_peer_group

    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    _peer_fixture(db, wid)
    report = detect_peer_group(wid, db)
    assert set(report["strata"]) == {"rare", "common"}
    assert sum(report["strata"].values()) == report["candidates"]
    # The target has 1 edge → rare band → marked lower-confidence.
    meta = json.loads(report["gaps"][0]["meta"])
    assert meta["frequency_band"] == "rare"
    assert meta["confidence"] == "low"


# ── harness registration ──────────────────────────────────────────────────────


def test_pcwa_gap_detectors_are_measurable():
    from orivellum.capabilities.gap_harness import DETECTOR_CANDIDATES
    from orivellum.capabilities.pcwa import DETECTOR_MINED_CARD, DETECTOR_PEER

    assert DETECTOR_MINED_CARD in DETECTOR_CANDIDATES
    assert DETECTOR_PEER in DETECTOR_CANDIDATES


# ── API round-trip ────────────────────────────────────────────────────────────


def test_pcwa_api_roundtrip(tmp_path):
    client, db = _make_app(tmp_path)
    wid = db.create_work("W")["id"]
    _cardinality_fixture(db, wid)
    _functional_fixture(db, wid)

    r = client.post(f"/api/works/{wid}/pcwa/scan")
    assert r.status_code == 200
    body = r.json()
    assert body["relations_mined"] >= 2
    assert body["total_gaps"] >= 1
    assert body["total_proposed_assertions"] >= 1

    r = client.get(f"/api/works/{wid}/pcwa/relations")
    assert r.status_code == 200
    assert r.json()["total"] == body["relations_mined"]

    # Ratify one machine proposal with a human signature.
    proposed = db.list_completeness_assertions(wid, status="proposed")
    assert proposed
    aid = proposed[0]["id"]
    r = client.post(
        f"/api/completeness-assertions/{aid}/ratify", json={"signed_by": "brian"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    # A second ratify is a state conflict, not a validation error.
    r = client.post(
        f"/api/completeness-assertions/{aid}/ratify", json={"signed_by": "brian"}
    )
    assert r.status_code == 409

    r = client.post(f"/api/works/{'no-such'}/pcwa/scan")
    assert r.status_code == 404
