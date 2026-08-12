"""Tests for the G-M3 structural detectors (Gap Engine).

Covers:
  * mentioned-but-never-explained — repeated undefined term becomes a gap
    with frame citation; explained term does not; frequency stratified
  * dead-end citation — a knowledge claim citing a not-held source becomes a
    gap; held source does not; distinct from citation-closure identity
  * failure clustering — ≥2 failing dependents sharing a prerequisite emit a
    gap on the prerequisite; single failing dependent does not; graduated
    prerequisite exempt
  * the combined scan route
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_app(tmp_path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


def _ready_doc(db, work_id, title, chunks=()):
    doc = db.create_document(
        title=title,
        source=f"{title}.pdf",
        sha256=f"sha-{title}-{work_id}",
        kind="pdf",
        work_id=work_id,
    )
    db.update_document_extracted(doc["id"], f"text of {title}", 100, readiness="ready")
    for text in chunks:
        db.add_chunk(doc["id"], text)
    return doc["id"]


def _concept(db, work_id, subject):
    cid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO work_concepts(id,work_id,subject,created_at) VALUES(?,?,?,?)",
            (cid, work_id, subject, "2026-01-01T00:00:00+00:00"),
        )
        db._conn.commit()
    return cid


def _prereq_edge(db, concept_id, prereq_id):
    with db._lock:
        db._conn.execute(
            "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
            (concept_id, prereq_id),
        )
        db._conn.commit()


def _attempt(db, concept_id, score, passes=0):
    with db._lock:
        db._conn.execute(
            "INSERT INTO work_mastery(id,concept_id,score,consecutive_passes,created_at) "
            "VALUES(?,?,?,?,?)",
            (str(uuid.uuid4()), concept_id, score, passes, "2026-01-02T00:00:00+00:00"),
        )
        db._conn.commit()


# ── mentioned-but-never-explained ─────────────────────────────────────────────


def test_never_explained_detects_repeated_undefined_term(tmp_path):
    from orivellum.capabilities.gap_engine import detect_never_explained

    db = _make_db(tmp_path)
    work = db.create_work("W")
    doc_id = _ready_doc(
        db,
        work["id"],
        "Survey",
        chunks=[
            "The lmlk seals appear at Lachish. All lmlk seals are stamped.",
            "Distribution of lmlk seals suggests royal administration.",
        ],
    )
    # The corpus itself named the term (entity mention) — but nothing explains it
    db.create_knowledge_item(work["id"], "entity", "lmlk seals", subject="lmlk seals")
    result = detect_never_explained(work["id"], db)
    assert len(result["gaps"]) == 1
    gap = result["gaps"][0]
    assert gap["frame_node_id"] == "term:lmlk seals"
    assert f"doc:{doc_id}" in gap["frame_source_ref"]
    assert "no knowledge item explains" in gap["evidence_absent"]
    assert gap["force_check"] == "mentioned_never_explained"
    # frequency stratification is present in the output
    import json

    meta = json.loads(gap["meta"])
    assert meta["frequency_band"] in ("rare", "common")
    assert meta["mention_count"] >= 3


def test_never_explained_skips_explained_and_infrequent_terms(tmp_path):
    from orivellum.capabilities.gap_engine import detect_never_explained

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _ready_doc(
        db,
        work["id"],
        "Survey",
        chunks=[
            "The lmlk seals appear at Lachish. Every lmlk seal is stamped. "
            "More lmlk seals were found later. Ostraca were found once."
        ],
    )
    db.create_knowledge_item(work["id"], "entity", "lmlk seals", subject="lmlk seals")
    db.create_knowledge_item(
        work["id"],
        "concept",
        "lmlk seals are royal storage-jar stamps of the late 8th century Judahite kingdom",
        subject="lmlk seals",
    )
    # mentioned once only — below the recurrence floor
    db.create_knowledge_item(work["id"], "entity", "Ostraca", subject="Ostraca")
    result = detect_never_explained(work["id"], db)
    assert result["gaps"] == []


# ── dead-end citation ─────────────────────────────────────────────────────────


def test_dead_end_citation_flags_uncheckable_claim(tmp_path):
    from orivellum.capabilities.gap_engine import detect_dead_end_citations

    db = _make_db(tmp_path)
    work = db.create_work("W")
    doc_id = _ready_doc(db, work["id"], "Notes")
    kid = db.create_knowledge_item(
        work["id"],
        "claim",
        "The gate complex is 10th century according to Yadin (1958).",
        subject="Hazor gate",
        source_doc_id=doc_id,
    )
    result = detect_dead_end_citations(work["id"], db)
    assert len(result["gaps"]) == 1
    gap = result["gaps"][0]
    assert gap["frame_node_id"] == "deadend:yadin 1958"
    assert f"knowledge:{kid}" in gap["frame_source_ref"]
    assert "cannot be checked" in gap["evidence_absent"]
    assert gap["force_check"] == "dead_end_citation"


def test_dead_end_citation_skips_held_sources_and_rejected_items(tmp_path):
    from orivellum.capabilities.gap_engine import detect_dead_end_citations

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Yadin - Hazor Excavations")  # holds the cited work
    db.create_knowledge_item(
        work["id"], "claim", "Dated by Yadin (1958) to the 10th century.", subject="gate"
    )
    kid_rejected = db.create_knowledge_item(
        work["id"], "claim", "Per Albright (1940) this is certain.", subject="x"
    )
    db.update_knowledge_review_status(kid_rejected, "rejected")
    result = detect_dead_end_citations(work["id"], db)
    assert result["gaps"] == []


def test_dead_end_identity_distinct_from_citation_closure(tmp_path):
    """The same not-held work cited in chunks AND in a claim yields two gaps
    with different classes and different identities."""
    from orivellum.capabilities.gap_engine import (
        detect_citation_gaps,
        detect_dead_end_citations,
    )

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Survey", chunks=["As shown by Albright (1940)."])
    db.create_knowledge_item(work["id"], "claim", "Confirmed by Albright (1940).", subject="dating")
    g1 = detect_citation_gaps(work["id"], db)["gaps"]
    g2 = detect_dead_end_citations(work["id"], db)["gaps"]
    assert len(g1) == 1 and len(g2) == 1
    assert g1[0]["id"] != g2[0]["id"]
    assert g1[0]["gap_class"] == "citation_closure"
    assert g2[0]["gap_class"] == "dead_end_citation"


# ── failure clustering ────────────────────────────────────────────────────────


def test_failure_clustering_names_the_shared_prerequisite(tmp_path):
    from orivellum.capabilities.gap_engine import detect_failure_clusters

    db = _make_db(tmp_path)
    work = db.create_work("W")
    prereq = _concept(db, work["id"], "Verbal stems")
    dep_a = _concept(db, work["id"], "Hiphil semantics")
    dep_b = _concept(db, work["id"], "Niphal semantics")
    _prereq_edge(db, dep_a, prereq)
    _prereq_edge(db, dep_b, prereq)
    for cid in (dep_a, dep_b):
        _attempt(db, cid, 0.2)
        _attempt(db, cid, 0.3)
    result = detect_failure_clusters(work["id"], db)
    assert len(result["gaps"]) == 1
    gap = result["gaps"][0]
    assert gap["frame_node_id"] == f"concept:{prereq}"
    assert "Verbal stems" in gap["evidence_absent"]
    assert "no demonstrated mastery" in gap["evidence_absent"]
    assert gap["force_check"] == "failure_clustering"
    # the frame cites the learning-graph edges that establish the demand
    assert dep_a in gap["frame_source_ref"] and dep_b in gap["frame_source_ref"]


def test_failure_clustering_needs_two_dependents_and_repeated_failures(tmp_path):
    from orivellum.capabilities.gap_engine import detect_failure_clusters

    db = _make_db(tmp_path)
    work = db.create_work("W")
    prereq = _concept(db, work["id"], "P")
    dep_a = _concept(db, work["id"], "A")
    dep_b = _concept(db, work["id"], "B")
    _prereq_edge(db, dep_a, prereq)
    _prereq_edge(db, dep_b, prereq)
    # only one dependent fails repeatedly; the other failed once (a slip)
    _attempt(db, dep_a, 0.1)
    _attempt(db, dep_a, 0.2)
    _attempt(db, dep_b, 0.3)
    assert detect_failure_clusters(work["id"], db)["gaps"] == []


def test_failure_clustering_exempts_graduated_prerequisite(tmp_path):
    from orivellum.capabilities.gap_engine import detect_failure_clusters

    db = _make_db(tmp_path)
    work = db.create_work("W")
    prereq = _concept(db, work["id"], "P")
    dep_a = _concept(db, work["id"], "A")
    dep_b = _concept(db, work["id"], "B")
    _prereq_edge(db, dep_a, prereq)
    _prereq_edge(db, dep_b, prereq)
    for cid in (dep_a, dep_b):
        _attempt(db, cid, 0.1)
        _attempt(db, cid, 0.2)
    _attempt(db, prereq, 0.9, passes=3)  # graduated — foundation demonstrably present
    assert detect_failure_clusters(work["id"], db)["gaps"] == []


# ── scan route ────────────────────────────────────────────────────────────────


def test_scan_route_runs_all_detectors(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Survey", chunks=["As shown by Albright (1940)."])
    r = client.post(f"/api/works/{work['id']}/research-gaps/scan", json={})
    assert r.status_code == 200
    body = r.json()
    assert set(body["detectors"]) == {
        "citation_graph_closure",
        "mentioned_never_explained",
        "dead_end_citation",
        "failure_clustering",
    }
    assert body["total_gaps"] >= 1  # the citation gap

    r2 = client.post(
        f"/api/works/{work['id']}/research-gaps/scan",
        json={"detectors": ["nope"]},
    )
    assert r2.status_code == 422
