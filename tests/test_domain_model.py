"""Tests for the Domain Model (G-M5/G-M6, interpretive layer).

Covers:
  * harvest triangulation — intersection across >=3 independent sources
    proposes ``required``; union-minus-intersection proposes ``optional``;
    conflicting parent placement proposes ``contested``; with <3 sources
    nothing is proposed as required
  * proposal-only — an unratified node generates NO gap; ratification
    requires a signature; a contested node cannot be ratified as required;
    double-resolve conflicts; re-harvest never clobbers a signed decision
  * G2 coverage — ratified node with insufficient evidence emits a gap with
    frame citation and computed severity; covered node does not
  * G4 frontier — ratified contested node routes to the decision queue and
    is never critical, whatever the counts say
  * harness — domain_coverage is a registered detector and measurable
  * relative recall — bibliography peer coverage math
"""

from __future__ import annotations

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


def _structure_doc(db, work_id, title, headings):
    """headings: list of (level, title)."""
    doc_id = _ready_doc(db, work_id, title)
    chapters = [
        {"seq": i, "level": level, "title": t, "text": f"section on {t}"}
        for i, (level, t) in enumerate(headings)
    ]
    db.upsert_book_chapters(doc_id, work_id, chapters)
    return doc_id


def _setup_three_sources(db, work_id, domain="theodicy"):
    """Three independent reference structures sharing one core heading."""
    ids = []
    ids.append(
        _structure_doc(
            db,
            work_id,
            "Handbook A",
            [
                (1, "Divine Justice"),
                (2, "Suffering of the Righteous"),
                (1, "Covenant Theology"),
            ],
        )
    )
    ids.append(
        _structure_doc(
            db,
            work_id,
            "Handbook B",
            [
                (1, "Divine Justice"),
                (2, "Suffering of the Righteous"),
                (1, "Kingship Ideology"),
            ],
        )
    )
    ids.append(
        _structure_doc(
            db,
            work_id,
            "Handbook C",
            [
                (1, "3. Divine Justice"),  # numbering strips to the same key
                (1, "Suffering of the Righteous"),  # DIFFERENT parent (top-level)
            ],
        )
    )
    for doc_id in ids:
        db.add_domain_source(work_id, domain, doc_id, "structure")
    return ids


# ── harvest + triangulation ───────────────────────────────────────────────────


def test_harvest_triangulation_classes(tmp_path):
    from orivellum.capabilities.domain_model import harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])

    result = harvest_domain(db, work["id"], "theodicy")
    assert result["sources"] == 3
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"], domain="theodicy")}

    # In all 3 sources, same placement -> required core.
    dj = nodes["divine justice"]
    assert dj["node_class"] == "required"
    assert dj["agreement"] == 3
    assert dj["status"] == "proposed"

    # In A+B under "divine justice", in C at top level -> contested.
    assert nodes["suffering of the righteous"]["node_class"] == "contested"

    # Only one source each -> optional periphery, sources recorded.
    assert nodes["covenant theology"]["node_class"] == "optional"
    assert nodes["kingship ideology"]["node_class"] == "optional"
    import json

    assert json.loads(nodes["covenant theology"]["sources"])[0]["ref"].startswith("doc:")


def test_harvest_under_three_sources_never_proposes_required(tmp_path):
    from orivellum.capabilities.domain_model import harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    for title in ("Only A", "Only B"):
        doc = _structure_doc(db, work["id"], title, [(1, "Divine Justice")])
        db.add_domain_source(work["id"], "theodicy", doc, "structure")

    result = harvest_domain(db, work["id"], "theodicy")
    assert result["required"] == 0
    assert "note" in result
    nodes = db.list_domain_nodes(work["id"])
    assert all(n["node_class"] != "required" for n in nodes)


def test_reharvest_never_clobbers_signed_decision(tmp_path):
    from orivellum.capabilities.domain_model import harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")

    node = next(n for n in db.list_domain_nodes(work["id"]) if n["node_key"] == "covenant theology")
    assert db.resolve_domain_node(node["id"], "reject", signed_by="me") == "updated"

    harvest_domain(db, work["id"], "theodicy")  # re-run
    refreshed = db.get_domain_node(node["id"])
    assert refreshed["status"] == "rejected"  # signed ruling stands


# ── ratification discipline ───────────────────────────────────────────────────


def test_ratify_requires_signature_and_contested_cannot_be_required(tmp_path):
    import pytest

    from orivellum.capabilities.domain_model import harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"])}

    with pytest.raises(ValueError, match="signature"):
        db.resolve_domain_node(nodes["divine justice"]["id"], "approve", signed_by="  ")

    contested = nodes["suffering of the righteous"]
    with pytest.raises(ValueError, match="contested"):
        db.resolve_domain_node(contested["id"], "approve", signed_by="me", node_class="required")

    # Double resolve -> conflict; transition ledgered once.
    assert db.resolve_domain_node(contested["id"], "approve", signed_by="me") == "updated"
    assert db.resolve_domain_node(contested["id"], "approve", signed_by="me") == "conflict"
    with db._lock:
        n = db._conn.execute(
            "SELECT COUNT(*) AS n FROM domain_node_transition WHERE node_id=?",
            (contested["id"],),
        ).fetchone()["n"]
    assert n == 1


def test_unratified_node_generates_no_gap(tmp_path):
    from orivellum.capabilities.domain_model import (
        detect_domain_coverage,
        detect_domain_frontier,
        harvest_domain,
    )

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")  # everything still proposed

    assert detect_domain_coverage(work["id"], db)["emitted"] == 0
    assert detect_domain_frontier(work["id"], db)["emitted"] == 0
    assert db.list_gaps(work["id"]) == []


# ── G2 coverage ───────────────────────────────────────────────────────────────


def test_g2_gap_for_uncovered_ratified_node(tmp_path):
    from orivellum.capabilities.domain_model import detect_domain_coverage, harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"])}
    db.resolve_domain_node(nodes["divine justice"]["id"], "approve", signed_by="me")

    result = detect_domain_coverage(work["id"], db)
    assert result["emitted"] == 1
    gaps = [g for g in db.list_gaps(work["id"]) if g["gap_class"] == "domain_coverage"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["frame_node_id"] == nodes["divine justice"]["id"]
    assert gap["frame_source_ref"]
    assert "divine justice" in gap["evidence_absent"].lower()
    assert gap["severity"] in ("low", "medium", "high")  # computed, never blocking here
    import json

    assert json.loads(gap["meta"])["layer"] == "interpretive_frame"


def test_no_g2_gap_when_evidence_sufficient(tmp_path):
    from orivellum.capabilities.domain_model import detect_domain_coverage, harvest_domain

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    # Two passages mentioning the node phrase = enough evidence.
    _ready_doc(
        db,
        work["id"],
        "Notes",
        chunks=[
            "A long meditation on divine justice in the ancient Near East.",
            "Later prophets reframed divine justice as covenant faithfulness.",
        ],
    )
    harvest_domain(db, work["id"], "theodicy")
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"])}
    db.resolve_domain_node(nodes["divine justice"]["id"], "approve", signed_by="me")

    result = detect_domain_coverage(work["id"], db)
    assert result["emitted"] == 0


def test_severity_uses_measured_demand(tmp_path):
    from orivellum.capabilities.domain_model import demand_count
    from orivellum.capabilities.gap_engine import compute_severity

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    conv = db.create_conversation(title="C", work_id=work["id"])
    for _ in range(3):
        db.add_message(conv["id"], "user", "what do we know about divine justice here?")

    demand = demand_count(db, work["id"], "divine justice")
    assert demand == 3

    quiet = compute_severity("domain_coverage", agreement=3, centrality=1, demand=0)
    loud = compute_severity("domain_coverage", agreement=3, centrality=1, demand=demand)
    order = ["low", "medium", "high", "critical"]
    assert order.index(loud) >= order.index(quiet)


# ── G4 frontier ───────────────────────────────────────────────────────────────


def test_frontier_routes_to_decision_queue_and_is_never_critical(tmp_path):
    from orivellum.capabilities.domain_model import detect_domain_frontier, harvest_domain
    from orivellum.capabilities.gap_engine import compute_severity

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"])}
    contested = nodes["suffering of the righteous"]
    db.resolve_domain_node(contested["id"], "approve", signed_by="me")

    result = detect_domain_frontier(work["id"], db)
    assert result["emitted"] == 1
    gaps = [g for g in db.list_gaps(work["id"]) if g["gap_class"] == "domain_frontier"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["action"] == "decide"
    import json

    assert json.loads(gap["meta"])["queue"] == "decision"
    assert gap["severity"] in ("low", "medium")

    # Even absurd counts + blocking-level demand cannot make a frontier gap critical.
    assert (
        compute_severity(
            "domain_frontier",
            centrality=50,
            dependent_count=50,
            demand=50,
            agreement=6,
        )
        == "medium"
    )


# ── harness registration ──────────────────────────────────────────────────────


def test_domain_coverage_is_a_measurable_detector(tmp_path):
    from orivellum.capabilities.domain_model import harvest_domain
    from orivellum.capabilities.gap_harness import DETECTOR_CANDIDATES, evaluate_detector

    assert "domain_coverage" in DETECTOR_CANDIDATES

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")
    nodes = {n["node_key"]: n for n in db.list_domain_nodes(work["id"])}
    db.resolve_domain_node(nodes["divine justice"]["id"], "approve", signed_by="me")

    candidates = DETECTOR_CANDIDATES["domain_coverage"](work["id"], db)
    assert candidates and candidates[0]["pair_key"] == "theodicy|divine justice"

    db.upsert_oracle_label(
        work_id=work["id"],
        detector="domain_coverage",
        pair_key="theodicy|divine justice",
        label="is_gap",
        signed_by="me",
        frequency=0,
    )
    measurement = evaluate_detector(db, "domain_coverage", persist=False)
    assert measurement["tp"] == 1
    assert measurement["precision"] == 1.0


# ── relative recall ───────────────────────────────────────────────────────────


def test_relative_recall_against_bibliography_peer(tmp_path):
    from orivellum.capabilities.domain_model import relative_recall

    db = _make_db(tmp_path)
    work = db.create_work(title="W")
    # A held survey by Alter — its title makes the citation "held".
    _ready_doc(db, work["id"], "Alter Biblical Narrative Survey")
    # The peer bibliography cites Alter (held) and Brueggemann (not held).
    peer = _ready_doc(
        db,
        work["id"],
        "Course Reading List",
        chunks=[
            "Essential: Alter (1981) on narrative artistry.",
            "Also read Brueggemann (1997) on Old Testament theology.",
        ],
    )
    db.add_domain_source(work["id"], "theology", peer, "bibliography")

    report = relative_recall(db, work["id"])
    assert report["peers"], "bibliography peer should produce a report"
    bib = report["peers"][0]
    assert bib["mode"] == "bibliography"
    assert bib["peer_total"] == 2
    assert bib["matched"] == 1
    assert bib["relative_recall"] == 0.5
    assert bib["missing"][0]["cited"].startswith("Brueggemann")
    assert "peer" in report["note"]


# ── API surface ───────────────────────────────────────────────────────────────


def test_domain_api_roundtrip(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])

    r = client.post(f"/api/works/{work['id']}/domain/harvest", json={"domain": "theodicy"})
    assert r.status_code == 200
    assert r.json()["required"] == 1

    r = client.get(f"/api/works/{work['id']}/domain/nodes")
    assert r.status_code == 200
    body = r.json()
    assert body["layer"] == "interpretive_frame"
    assert any(n["node_key"] == "divine justice" for n in body["nodes"])

    # Proposals appear in the review inbox; resolving without a signature fails.
    node = next(n for n in body["nodes"] if n["node_key"] == "divine justice")
    queue = client.get("/api/review/queue").json()
    assert any(i["id"] == f"domain_node:{node['id']}" for i in queue["items"])
    r = client.post(
        f"/api/review/domain_node:{node['id']}/resolve",
        json={"decision": "approve", "author": ""},
    )
    assert r.status_code == 422
    r = client.post(
        f"/api/review/domain_node:{node['id']}/resolve",
        json={"decision": "approve", "author": "me"},
    )
    assert r.status_code == 200

    r = client.post(f"/api/works/{work['id']}/domain/scan")
    assert r.status_code == 200
    assert r.json()["coverage"]["emitted"] == 1

    r = client.get(f"/api/works/{work['id']}/relative-recall")
    assert r.status_code == 200


def test_source_delete_is_work_scoped(tmp_path):
    client, db = _make_app(tmp_path)
    work_a = db.create_work(title="A")
    work_b = db.create_work(title="B")
    doc = _structure_doc(db, work_a["id"], "Handbook", [(1, "Divine Justice")])
    src = db.add_domain_source(work_a["id"], "theodicy", doc, "structure")

    # Another Work's scope must NOT be able to delete A's source.
    r = client.delete(f"/api/works/{work_b['id']}/domain/sources/{src['id']}")
    assert r.status_code == 404
    assert db.list_domain_sources(work_a["id"])  # still there

    # Nonexistent work -> 404 on every domain surface.
    assert client.get("/api/works/nope/domain/sources").status_code == 404
    assert client.get("/api/works/nope/domain/nodes").status_code == 404
    assert client.delete(f"/api/works/nope/domain/sources/{src['id']}").status_code == 404

    # The owning Work can delete it.
    r = client.delete(f"/api/works/{work_a['id']}/domain/sources/{src['id']}")
    assert r.status_code == 200
    assert db.list_domain_sources(work_a["id"]) == []


def test_domain_node_defer_in_review_inbox(tmp_path):
    from orivellum.capabilities.domain_model import harvest_domain

    client, db = _make_app(tmp_path)
    work = db.create_work(title="W")
    _setup_three_sources(db, work["id"])
    harvest_domain(db, work["id"], "theodicy")
    node = db.list_domain_nodes(work["id"])[0]

    r = client.post(
        f"/api/review/domain_node:{node['id']}/resolve",
        json={"decision": "defer", "author": "me"},
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "defer"
    queue = client.get("/api/review/queue").json()
    assert not any(i["id"] == f"domain_node:{node['id']}" for i in queue["items"])

    # Deferring a nonexistent node is a clean 404, not a 500.
    r = client.post(
        "/api/review/domain_node:dn-nope/resolve",
        json={"decision": "defer", "author": "me"},
    )
    assert r.status_code == 404
