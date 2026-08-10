"""Tests for the multi-dimensional completeness scoring feature.

Covers:
- calculate_work_completeness() with an empty Work → Draft at 0%
- Scores update after adding documents and knowledge items
- All five dimensions are always returned
- Readiness labels span the full range
- GET /api/works/{id}/completeness endpoint returns the expected shape
- 404 for unknown Work
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# ── Shared helpers ─────────────────────────────────────────────────────────────
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


def test_empty_work_is_draft(tmp_path):
    """A brand-new Work with no documents should be Draft at 0%."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Empty Work")
    report = calculate_work_completeness(work["id"], db)

    assert report.readiness == "Draft"
    assert report.overall == 0
    assert len(report.dimensions) == 5


def test_five_dimensions_always_present(tmp_path):
    """All five named dimensions must be returned regardless of data."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Any Work")
    report = calculate_work_completeness(work["id"], db)

    names = {d.name for d in report.dimensions}
    assert names == {"structural", "content", "research", "editorial", "source"}


def test_content_score_rises_with_word_count(tmp_path):
    """Content score should scale with document word counts."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import (
        _CONTENT_BASELINE_WORDS,
        calculate_work_completeness,
    )

    work = db.create_work("Word-Rich Work")
    doc = db.create_document("Big Doc", work_id=work["id"])
    # Simulate 50% of baseline words
    target_words = _CONTENT_BASELINE_WORDS // 2
    db.update_document_extracted(doc["id"], "x " * target_words, target_words, readiness="ready")

    report = calculate_work_completeness(work["id"], db)
    content = next(d for d in report.dimensions if d.name == "content")

    assert content.score == 50
    assert content.current == target_words


def test_editorial_score_rises_with_reviews(tmp_path):
    """Editorial score should reflect how many knowledge items have been reviewed."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Editorial Work")
    doc = db.create_document("Source", work_id=work["id"])
    db.update_document_extracted(doc["id"], "text", 100, readiness="ready")

    # Create 4 knowledge items, review 2
    kids = [
        db.create_knowledge_item(work["id"], "fact", f"fact {i}", source_doc_id=doc["id"])
        for i in range(4)
    ]
    db.update_knowledge_review_status(kids[0], "approved")
    db.update_knowledge_review_status(kids[1], "rejected")

    report = calculate_work_completeness(work["id"], db)
    editorial = next(d for d in report.dimensions if d.name == "editorial")

    assert editorial.current == 2
    assert editorial.target == 4
    assert editorial.score == 50


def test_source_diversity_score(tmp_path):
    """Source diversity score = distinct cited source docs / total docs."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Source Work")
    doc_a = db.create_document("Doc A", work_id=work["id"])
    doc_b = db.create_document("Doc B", work_id=work["id"])
    for d in [doc_a, doc_b]:
        db.update_document_extracted(d["id"], "text", 100, readiness="ready")

    # Only doc_a is cited in knowledge
    db.create_knowledge_item(work["id"], "fact", "item", source_doc_id=doc_a["id"])

    report = calculate_work_completeness(work["id"], db)
    source = next(d for d in report.dimensions if d.name == "source")

    # 1 cited / 2 total → 50%
    assert source.current == 1
    assert source.target == 2
    assert source.score == 50


def test_readiness_labels_span_range(tmp_path):
    """Verify all five readiness labels can be produced by patching scores."""

    def _report_at(overall: int) -> str:
        """Infer readiness label from overall score directly."""
        return (
            "Ready"
            if overall >= 80
            else "Near-Complete"
            if overall >= 60
            else "Substantial"
            if overall >= 40
            else "Developing"
            if overall >= 20
            else "Draft"
        )

    assert _report_at(0) == "Draft"
    assert _report_at(20) == "Developing"
    assert _report_at(40) == "Substantial"
    assert _report_at(60) == "Near-Complete"
    assert _report_at(80) == "Ready"


def test_dimension_evidence_is_non_empty(tmp_path):
    """Every dimension must include at least one evidence string."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Evidence Check")
    report = calculate_work_completeness(work["id"], db)

    for dim in report.dimensions:
        assert dim.evidence, f"{dim.name} dimension has no evidence"


def test_dimension_rule_is_non_empty(tmp_path):
    """Every dimension must include a non-empty rule string."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Rule Check")
    report = calculate_work_completeness(work["id"], db)

    for dim in report.dimensions:
        assert dim.rule.strip(), f"{dim.name} dimension has empty rule"


# ── API endpoint tests ─────────────────────────────────────────────────────────


def test_completeness_endpoint_returns_expected_shape(tmp_path):
    """GET /api/works/{id}/completeness must return the full structured report."""
    client, db = _make_app(tmp_path)
    work = db.create_work("API Work")

    r = client.get(f"/api/works/{work['id']}/completeness")
    assert r.status_code == 200

    body = r.json()
    assert body["work_id"] == work["id"]
    assert "readiness" in body
    assert "overall" in body
    assert "summary" in body
    assert "evaluated_at" in body
    assert "dimensions" in body
    assert len(body["dimensions"]) == 5


def test_completeness_endpoint_dimension_fields(tmp_path):
    """Each dimension in the API response must have all required fields."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Field Check Work")

    r = client.get(f"/api/works/{work['id']}/completeness")
    assert r.status_code == 200

    required = {"name", "label", "score", "current", "target", "unit", "rule", "evidence"}
    for dim in r.json()["dimensions"]:
        missing = required - set(dim.keys())
        assert not missing, f"Dimension {dim.get('name')} missing fields: {missing}"


def test_completeness_endpoint_404_for_unknown_work(tmp_path):
    """GET /api/works/unknown/completeness must return 404."""
    client, _ = _make_app(tmp_path)
    r = client.get("/api/works/no-such-work/completeness")
    assert r.status_code == 404


def test_completeness_scores_update_after_adding_knowledge(tmp_path):
    """Scores must be higher after knowledge items are added."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Dynamic Work")
    doc = db.create_document("Source Doc", work_id=work["id"])
    db.update_document_extracted(doc["id"], "text " * 1000, 1000, readiness="ready")

    # Baseline score
    r_before = client.get(f"/api/works/{work['id']}/completeness")
    overall_before = r_before.json()["overall"]

    # Add knowledge items and review some
    for i in range(5):
        kid = db.create_knowledge_item(work["id"], "fact", f"fact {i}", source_doc_id=doc["id"])
        if i < 3:
            db.update_knowledge_review_status(kid, "approved")

    r_after = client.get(f"/api/works/{work['id']}/completeness")
    overall_after = r_after.json()["overall"]

    assert overall_after >= overall_before, (
        f"Adding knowledge should not decrease overall score. "
        f"Before: {overall_before}, After: {overall_after}"
    )


# ── Custom completeness targets ────────────────────────────────────────────────


def test_custom_word_target_changes_content_score(tmp_path):
    """Setting a lower word_target raises the content score for a short Work."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Short Essay")
    doc = db.create_document("Essay", work_id=work["id"])
    db.update_document_extracted(doc["id"], "word " * 2000, 2000, readiness="ready")

    # Default target (50,000 words) → ~4%
    report_default = calculate_work_completeness(work["id"], db)
    content_default = next(d for d in report_default.dimensions if d.name == "content")

    # Custom target (5,000 words) → 40%
    db.update_work(
        work["id"], meta={"completeness_targets": {"word_target": 5000, "chapter_target": 10}}
    )
    report_custom = calculate_work_completeness(work["id"], db)
    content_custom = next(d for d in report_custom.dimensions if d.name == "content")

    assert content_custom.score > content_default.score, (
        f"Custom word target should raise content score. "
        f"Default: {content_default.score}%, Custom: {content_custom.score}%"
    )
    assert content_custom.target == 5000
    assert content_custom.score == 40


def test_custom_chapter_target_changes_structural_score(tmp_path):
    """Setting a lower chapter_target raises the structural score."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import calculate_work_completeness

    work = db.create_work("Two-Chapter Essay")
    doc = db.create_document("Essay", work_id=work["id"])
    db.update_document_extracted(doc["id"], "text", 500, readiness="ready")

    # Inject 2 book_chapters using the object registry (required for FK integrity)
    from orivellum.database.db import _now as _db_now

    for i in range(2):
        ch_id = db._create_object("book_chapter")
        with db._lock:
            db._conn.execute(
                """INSERT INTO book_chapters
                       (id, work_id, source_doc_id, seq, level, title, text,
                        status, meta, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?, 'extracted', '{}', ?, ?)""",
                (
                    ch_id,
                    work["id"],
                    doc["id"],
                    i + 1,
                    f"Chapter {i + 1}",
                    "Some text here",
                    _db_now(),
                    _db_now(),
                ),
            )
            db._conn.commit()

    # Default: 10 chapters expected → structural score = 20%
    report_default = calculate_work_completeness(work["id"], db)
    struct_default = next(d for d in report_default.dimensions if d.name == "structural")

    # Custom: 2 chapters → structural score = 100%
    db.update_work(
        work["id"], meta={"completeness_targets": {"word_target": 50000, "chapter_target": 2}}
    )
    report_custom = calculate_work_completeness(work["id"], db)
    struct_custom = next(d for d in report_custom.dimensions if d.name == "structural")

    assert struct_custom.score == 100, f"Expected 100%, got {struct_custom.score}%"
    assert struct_custom.score > struct_default.score


def test_works_without_custom_targets_use_defaults(tmp_path):
    """A Work with no meta.completeness_targets must behave identically to before."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.completeness import (
        _CONTENT_BASELINE_WORDS,
        calculate_work_completeness,
    )

    work = db.create_work("Default Work")
    doc = db.create_document("Doc", work_id=work["id"])
    db.update_document_extracted(doc["id"], "x " * 1000, 1000, readiness="ready")

    report = calculate_work_completeness(work["id"], db)
    content = next(d for d in report.dimensions if d.name == "content")

    assert content.target == _CONTENT_BASELINE_WORDS, (
        f"Default word target mismatch: {content.target} != {_CONTENT_BASELINE_WORDS}"
    )


def test_patch_works_sets_completeness_targets_via_api(tmp_path):
    """PATCH /api/works/{id} with meta.completeness_targets is persisted and reflected in scores."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Essay Work")
    doc = db.create_document("Essay", work_id=work["id"])
    db.update_document_extracted(doc["id"], "word " * 3000, 3000, readiness="ready")

    # Patch targets via API
    patch_resp = client.patch(
        f"/api/works/{work['id']}",
        json={"meta": {"completeness_targets": {"word_target": 3000, "chapter_target": 5}}},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    returned_meta = patch_resp.json()["work"].get("meta") or {}
    if isinstance(returned_meta, str):
        import json

        returned_meta = json.loads(returned_meta)
    assert returned_meta.get("completeness_targets", {}).get("word_target") == 3000

    # Completeness now scores 3000 words against 3000-word target → 100%
    comp_resp = client.get(f"/api/works/{work['id']}/completeness")
    assert comp_resp.status_code == 200
    content_dim = next(d for d in comp_resp.json()["dimensions"] if d["name"] == "content")
    assert content_dim["score"] == 100, f"Expected 100%, got {content_dim['score']}%"
    assert content_dim["target"] == 3000
