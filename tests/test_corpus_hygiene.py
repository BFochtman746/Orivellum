"""Tests for the research gap detection feature.

Covers all seven gap types:
  1. undocumented_doc   — ready doc with no extracted chapters
  2. uncovered_chapter  — chapter linked to doc with 0 knowledge items
  3. weak_coverage      — chapter linked to doc with < 3 knowledge items
  4. missing_sources    — knowledge items with source_doc_id = NULL
  5. orphaned_research  — knowledge items whose source doc is not in this work
  6. stale_source       — documents imported > 1 year ago
  7. duplicate_research — knowledge items with Jaccard similarity ≥ 0.8

Also verifies the Chao1/Good–Turing coverage report, suggested_queries, and
the GET endpoint (and that the removed self-referential coverage_pct never
reappears on the surface).
"""

from __future__ import annotations

import datetime

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


def _add_ready_doc(db, work_id: str, title: str = "Doc", created_at: str | None = None) -> str:
    """Create a document in 'ready' state linked to the given work."""
    doc = db.create_document(
        title=title,
        source=f"{title}.pdf",
        sha256=f"sha256-{title}-{work_id}",
        kind="pdf",
        work_id=work_id,
    )
    doc_id = doc["id"]
    db.update_document_extracted(doc_id, f"text of {title}", 100, readiness="ready")
    if created_at:
        with db._lock:
            db._conn.execute("UPDATE documents SET created_at=? WHERE id=?", (created_at, doc_id))
            db._conn.commit()
    return doc_id


def _add_chapters(db, doc_id: str, work_id: str, titles: list[str]) -> None:
    """Upsert a set of chapters for a document via the proper DB method.
    Replaces any existing chapters for that doc, so pass all desired
    chapter titles in one call.
    """
    chapters = [{"seq": i + 1, "level": 1, "title": t, "text": ""} for i, t in enumerate(titles)]
    db.upsert_book_chapters(doc_id, work_id, chapters)


def _add_knowledge(
    db,
    work_id: str,
    text: str,
    source_doc_id: str | None = None,
) -> str:
    """Create a knowledge item for the given work."""
    return db.create_knowledge_item(
        work_id=work_id,
        kind="fact",
        text=text,
        source_doc_id=source_doc_id,
    )


# ── unit tests — detect_hygiene() ────────────────────────────────────────────────


def test_empty_work_has_no_gaps(tmp_path):
    """A Work with no documents should produce no gaps."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Empty Work")
    report = detect_hygiene(work["id"], db)

    assert report.findings == []
    assert report.coverage["overall"]["completeness"] is None
    assert report.total_chapters == 0


def test_gap1_undocumented_doc(tmp_path):
    """Ready doc with no chapters → undocumented_doc gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work A")
    _add_ready_doc(db, work["id"], title="PlainDoc")

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "undocumented_doc" in kinds

    gap = next(g for g in report.findings if g.kind == "undocumented_doc")
    assert gap.severity == "medium"
    assert "PlainDoc" in gap.title
    assert "doc_id" in gap.metadata


def test_gap1_absent_when_chapters_exist(tmp_path):
    """No undocumented_doc gap when the doc has at least one chapter."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work B")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Introduction"])

    report = detect_hygiene(work["id"], db)
    kinds = [g.kind for g in report.findings]
    assert "undocumented_doc" not in kinds


def test_gap2_uncovered_chapter(tmp_path):
    """Chapter with zero knowledge items → uncovered_chapter (high severity)."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work C")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Methods"])

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "uncovered_chapter" in kinds

    gap = next(g for g in report.findings if g.kind == "uncovered_chapter")
    assert gap.severity == "high"
    assert "Methods" in gap.title


def test_gap3_weak_coverage(tmp_path):
    """Chapter with 1-2 knowledge items → weak_coverage (low severity)."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work D")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Results"])
    # 2 items — below the MIN_ITEMS_PER_CHAPTER=3 threshold
    _add_knowledge(db, work["id"], "finding one", source_doc_id=doc_id)
    _add_knowledge(db, work["id"], "finding two", source_doc_id=doc_id)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "weak_coverage" in kinds
    assert "uncovered_chapter" not in kinds  # 2 items → weak, not uncovered

    gap = next(g for g in report.findings if g.kind == "weak_coverage")
    assert gap.severity == "low"
    assert "Results" in gap.title


def test_gap3_absent_when_sufficient_coverage(tmp_path):
    """Chapter with ≥3 items → no coverage gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work E")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Conclusion"])
    for i in range(3):
        _add_knowledge(db, work["id"], f"fact {i}", source_doc_id=doc_id)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "uncovered_chapter" not in kinds
    assert "weak_coverage" not in kinds


def test_gap4_missing_sources(tmp_path):
    """Knowledge item with source_doc_id=NULL → missing_sources gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work F")
    _add_knowledge(db, work["id"], "unsourced fact", source_doc_id=None)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "missing_sources" in kinds

    gap = next(g for g in report.findings if g.kind == "missing_sources")
    assert gap.severity == "medium"
    assert gap.metadata["count"] == 1


def test_gap4_absent_when_all_sourced(tmp_path):
    """No missing_sources gap when every knowledge item has a source doc."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work G")
    doc_id = _add_ready_doc(db, work["id"])
    _add_knowledge(db, work["id"], "sourced fact", source_doc_id=doc_id)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "missing_sources" not in kinds


def test_gap5_orphaned_research(tmp_path):
    """Knowledge item referencing a doc not linked to this work → orphaned_research."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work_a = db.create_work("Work H")
    work_b = db.create_work("Other Work")

    # doc belongs to work_b, not work_a
    doc_id = _add_ready_doc(db, work_b["id"], title="External")

    # Insert the orphaned knowledge item via the standard API but with a
    # source_doc_id that belongs to work_b, not work_a.
    _add_knowledge(
        db, work_a["id"], "orphaned research fact about external doc", source_doc_id=doc_id
    )

    report = detect_hygiene(work_a["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "orphaned_research" in kinds

    gap = next(g for g in report.findings if g.kind == "orphaned_research")
    assert gap.severity == "low"
    assert gap.metadata["count"] == 1


def test_gap6_stale_source(tmp_path):
    """Document created > 1 year ago → stale_source gap (low severity)."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work I")
    two_years_ago = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=730)
    ).isoformat()[:10]
    _add_ready_doc(db, work["id"], title="OldDoc", created_at=two_years_ago)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "stale_source" in kinds

    gap = next(g for g in report.findings if g.kind == "stale_source")
    assert gap.severity == "low"
    assert gap.metadata["count"] == 1


def test_gap6_absent_for_recent_doc(tmp_path):
    """Document created < 1 year ago → no stale_source gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work J")
    six_months_ago = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=180)
    ).isoformat()[:10]
    _add_ready_doc(db, work["id"], title="NewDoc", created_at=six_months_ago)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "stale_source" not in kinds


def test_gap7_duplicate_research(tmp_path):
    """Two knowledge items with near-identical text → duplicate_research gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work K")
    # Two texts that share all but one word → Jaccard ≥ 0.833, above 0.8 threshold.
    # create_knowledge_item deduplicates by SHA256(work_id:text), so texts must differ.
    text_a = "the quick brown fox jumps over the lazy dog and sleeps peacefully"
    text_b = "the quick brown fox jumps over the lazy dog and sleeps restfully"
    _add_knowledge(db, work["id"], text_a)
    _add_knowledge(db, work["id"], text_b)

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "duplicate_research" in kinds

    gap = next(g for g in report.findings if g.kind == "duplicate_research")
    assert gap.metadata["duplicate_pairs"] >= 1


def test_gap7_absent_for_distinct_items(tmp_path):
    """Two clearly different knowledge items → no duplicate_research gap."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Work L")
    _add_knowledge(db, work["id"], "The mitochondria generates cellular energy")
    _add_knowledge(db, work["id"], "Photosynthesis converts sunlight into glucose in plants")

    report = detect_hygiene(work["id"], db)

    kinds = [g.kind for g in report.findings]
    assert "duplicate_research" not in kinds


# ── coverage estimate tests ───────────────────────────────────────────────────


def test_coverage_no_data_without_entity_mentions(tmp_path):
    """No entity/term mentions → coverage is honestly 'no data', never 0%."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Coverage Work")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Ch1", "Ch2"])
    # 'fact' items are not mention classes — they must not fabricate coverage.
    _add_knowledge(db, work["id"], "some fact", source_doc_id=doc_id)

    report = detect_hygiene(work["id"], db)
    assert report.coverage["overall"]["completeness"] is None
    assert report.coverage["overall"]["band"] == "no_data"
    assert report.total_chapters == 2


def test_coverage_report_is_upper_bound_with_unseen_count(tmp_path):
    """Entity mentions → Chao1 report with 'at most' framing + unseen count."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    work = db.create_work("Full Coverage")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Only Chapter"])
    # 3 entities: "Alpha" twice (doubleton), "Beta" once, "Gamma" once.
    for subject, times in (("Alpha", 2), ("Beta", 1), ("Gamma", 1)):
        for i in range(times):
            db.create_knowledge_item(
                work_id=work["id"], kind="entity", text=f"{subject} mention {i}",
                subject=subject, source_doc_id=doc_id,
            )

    report = detect_hygiene(work["id"], db)
    cov = report.coverage
    assert cov["framing"] == "upper_bound"
    overall = cov["overall"]
    # S_obs=3, f1=2, f2=1 → Chao1 = 3 + 4/2 = 5, unseen = 2, completeness = 0.6
    assert overall["s_obs"] == 3
    assert overall["s_est"] == 5.0
    assert overall["unseen_est"] == 2.0
    assert abs(overall["completeness"] - 0.6) < 1e-9
    assert "At most" in overall["summary"]
    entity_cls = next(c for c in cov["classes"] if c["class"] == "entity")
    assert entity_cls["band"] == "under_sampled"
    assert "entity" in cov["under_sampled_classes"]


# ── API endpoint test ─────────────────────────────────────────────────────────


def test_gaps_endpoint_returns_correct_shape(tmp_path):
    """GET /api/works/{id}/gaps returns GapReport with all required fields."""
    client, db = _make_app(tmp_path)

    work = db.create_work("API Work")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Intro"])  # uncovered chapter

    resp = client.get(f"/api/works/{work['id']}/gaps")
    assert resp.status_code == 200

    data = resp.json()
    assert "gaps" in data
    assert "coverage" in data
    assert "total_chapters" in data
    assert "suggested_queries" in data
    assert "evaluated_at" in data

    # The self-referential metric is gone — removed, not left alongside.
    assert "coverage_pct" not in data
    assert data["total_chapters"] == 1
    assert data["coverage"]["framing"] == "upper_bound"
    assert any(g["kind"] == "uncovered_chapter" for g in data["gaps"])


def test_gaps_endpoint_404_for_unknown_work(tmp_path):
    """GET /api/works/{bad_id}/gaps → 404."""
    client, _ = _make_app(tmp_path)

    resp = client.get("/api/works/nonexistent-work-id/gaps")
    assert resp.status_code == 404


def test_top_gaps_endpoint(tmp_path):
    """GET /api/gaps/top returns gaps across all active works."""
    client, db = _make_app(tmp_path)

    work = db.create_work("Active Work")
    doc_id = _add_ready_doc(db, work["id"])
    _add_chapters(db, doc_id, work["id"], ["Chapter One"])  # creates an uncovered_chapter high gap

    resp = client.get("/api/gaps/top?limit=5")
    assert resp.status_code == 200

    data = resp.json()
    assert "gaps" in data
    assert "total_works_analyzed" in data
    assert data["total_works_analyzed"] >= 1

    # Should contain at least one gap with work_id and work_title fields
    if data["gaps"]:
        g = data["gaps"][0]
        assert "work_id" in g
        assert "work_title" in g
        assert "severity" in g
        assert "kind" in g
