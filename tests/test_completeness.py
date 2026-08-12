"""Tests for the honest completeness report (THE RE-PROJECTION Phases 7-8).

The report refuses to guess:
- predicates are true/false facts (ratified structure, author canonical)
- counts have observed denominators only (knowledge reviewed of total)
- progress shows raw numbers; a target appears ONLY when the author set one
- no overall score, no readiness label, no default denominator anywhere
- coverage comes from Chao1/Good-Turing with upper-bound framing
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp_path):
    """Return (TestClient, db) wired to an isolated temp database."""
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


# ── Calculator unit tests ──────────────────────────────────────────────────────


def test_empty_work_has_false_predicates_and_no_targets(tmp_path):
    """A brand-new Work reports false predicates and raw zero counts — no %."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Empty Work")
    report = calculate_work_completeness(work["id"], db)

    by_name = {p["name"]: p for p in report["predicates"]}
    assert by_name["manuscript_document"]["value"] is False
    assert by_name["chapter_structure_ratified"]["value"] is False
    assert by_name["canonical_by_author"]["value"] is False

    assert report["progress"]["words"] == 0
    assert report["progress"]["word_target"] is None
    assert report["progress"]["chapter_target"] is None
    # The old guessed fields must be gone.
    assert "overall" not in report
    assert "readiness" not in report
    assert "dimensions" not in report


def test_no_default_denominators_exist(tmp_path):
    """The module-level assumed targets are deleted, not just unused."""
    import orivellum.capabilities.completeness as compl

    assert not hasattr(compl, "_CONTENT_BASELINE_WORDS")
    assert not hasattr(compl, "_EXPECTED_CHAPTERS_DEFAULT")


def test_targets_appear_only_when_author_set(tmp_path):
    """Progress carries a target only after the author sets one on the Work."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Essay")
    doc = db.create_document("Essay Doc", work_id=work["id"])
    db.update_document_extracted(doc["id"], "word " * 2000, 2000, readiness="ready")

    report = calculate_work_completeness(work["id"], db)
    assert report["progress"]["words"] == 2000
    assert report["progress"]["word_target"] is None
    assert report["progress"]["note"]  # explains raw-counts-only

    db.update_work(
        work["id"], meta={"completeness_targets": {"word_target": 5000, "chapter_target": 4}}
    )
    report2 = calculate_work_completeness(work["id"], db)
    assert report2["progress"]["word_target"] == 5000
    assert report2["progress"]["chapter_target"] == 4
    assert report2["progress"]["note"] is None


def test_knowledge_reviewed_count_uses_observed_denominator(tmp_path):
    """Knowledge-reviewed is a count of an observed total, never a guess."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Editorial Work")
    doc = db.create_document("Source", work_id=work["id"])
    db.update_document_extracted(doc["id"], "text", 100, readiness="ready")

    kids = [
        db.create_knowledge_item(work["id"], "fact", f"fact {i}", source_doc_id=doc["id"])
        for i in range(4)
    ]
    db.update_knowledge_review_status(kids[0], "approved")
    db.update_knowledge_review_status(kids[1], "rejected")

    report = calculate_work_completeness(work["id"], db)
    reviewed = next(c for c in report["counts"] if c["name"] == "knowledge_reviewed")
    assert reviewed["current"] == 2
    assert reviewed["total"] == 4


def test_coverage_uses_chao1_upper_bound_framing(tmp_path):
    """The coverage block is the Chao1/Good-Turing estimator, framed honestly."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Coverage Work")
    report = calculate_work_completeness(work["id"], db)
    assert report["coverage"]["method"] == "chao1_good_turing"
    assert report["coverage"]["framing"] == "upper_bound"


def test_predicates_flip_true_when_facts_exist(tmp_path):
    """Manuscript doc + G8 PASSED + author canonical flip all predicates."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness
    from tests.test_readiness_gates import _pass_g8, _uuid

    work = db.create_work("Real Book")
    doc = db.create_document("Draft MS", work_id=work["id"], doc_type="manuscript")
    db.update_document_extracted(doc["id"], "text " * 100, 100, readiness="ready")
    _pass_g8(db, work["id"], _uuid())
    db.update_document_lifecycle(doc["id"], "canonical", actor="author")

    report = calculate_work_completeness(work["id"], db)
    by_name = {p["name"]: p for p in report["predicates"]}
    assert by_name["manuscript_document"]["value"] is True
    assert by_name["chapter_structure_ratified"]["value"] is True
    assert by_name["canonical_by_author"]["value"] is True


# ── API endpoint tests ─────────────────────────────────────────────────────────


def test_completeness_endpoint_returns_honest_shape(tmp_path):
    """GET /api/works/{id}/completeness returns predicates/counts/progress."""
    client, db = _make_app(tmp_path)
    work = db.create_work("API Work")

    r = client.get(f"/api/works/{work['id']}/completeness")
    assert r.status_code == 200
    body = r.json()
    assert body["work_id"] == work["id"]
    assert "predicates" in body
    assert "counts" in body
    assert "progress" in body
    assert "coverage" in body
    assert "evaluated_at" in body
    # No invented figures in the response.
    assert "overall" not in body
    assert "readiness" not in body
    assert "dimensions" not in body


def test_completeness_endpoint_404_for_unknown_work(tmp_path):
    client, _ = _make_app(tmp_path)
    r = client.get("/api/works/no-such-work/completeness")
    assert r.status_code == 404
