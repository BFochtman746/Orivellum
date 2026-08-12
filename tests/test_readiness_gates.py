"""Acceptance tests — Books, canonical status, and completeness refuse to lie.

THE RE-PROJECTION Phases 7-8:

* Promote-to-Book is gated on three predicates and every refusal names the
  specific unmet reason (422 with reasons — never a bare failure).
* Canonical designation on a manuscript is an authored act: a system actor
  is refused, and provenance (lifecycle_by) is recorded so "zero manuscript
  canonical designations not made by the author" is checkable.
* auto_dedup never crosses doc_type, never touches manuscripts, and never
  touches documents whose Work has a book pipeline.
* Curriculum seeding refuses collections and unratified Works.
* The completeness report carries no assumed denominator (covered further
  in tests/test_completeness.py).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _uuid() -> str:
    return str(uuid.uuid4())


def _make_app(tmp_path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


def _pass_g8(db, work_id: str, book_id: str) -> None:
    """Seed a GENESIS book for the Work with the G8 gate signed PASSED."""
    from orivellum.database.db import _now

    now = _now()
    with db._lock:
        db._conn.execute(
            """INSERT INTO genesis_books (id, work_id, mode, length, acts, state,
                                          created_at, updated_at)
               VALUES (?, ?, 'library', 80, 4, 'G8', ?, ?)""",
            (book_id, work_id, now, now),
        )
        db._conn.execute(
            "INSERT INTO genesis_stages (id, book_id, stage_code, status) "
            "VALUES (?, ?, 'G8', 'PASSED')",
            (_uuid(), book_id),
        )
        db._conn.commit()


def _ready_manuscript(db, work_id: str, title: str = "Manuscript"):
    doc = db.create_document(title, work_id=work_id, doc_type="manuscript")
    db.update_document_extracted(doc["id"], "word " * 500, 500, readiness="ready")
    return doc


def _make_eligible_work(db, title: str = "Eligible Work"):
    """Work satisfying all three promotion predicates; returns (work, doc)."""
    work = db.create_work(title)
    doc = _ready_manuscript(db, work["id"])
    _pass_g8(db, work["id"], _uuid())
    db.update_document_lifecycle(doc["id"], "canonical", actor="author")
    return work, doc


# ── Promotion gate ─────────────────────────────────────────────────────────────


def test_promote_refuses_with_reason_for_every_unmet_predicate(tmp_path):
    """An empty Work fails all three checks and the 422 names each one."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Bare Work")

    r = client.post(f"/api/works/{work['id']}/pipeline", json={})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"]
    assert len(detail["reasons"]) == 3
    joined = " ".join(detail["reasons"])
    assert "manuscript" in joined.lower()
    assert "chapter" in joined.lower() and "ratif" in joined.lower()
    assert "canonical" in joined.lower()
    # No pipeline was created.
    assert db.get_book_pipeline_for_work(work["id"]) is None


def test_promote_refusal_is_specific_per_missing_predicate(tmp_path):
    """With two predicates met, the refusal names only the remaining one."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Almost There")
    _ready_manuscript(db, work["id"])
    _pass_g8(db, work["id"], _uuid())

    r = client.post(f"/api/works/{work['id']}/pipeline", json={})
    assert r.status_code == 422
    reasons = r.json()["detail"]["reasons"]
    assert len(reasons) == 1
    assert "canonical" in reasons[0].lower()


def test_promote_succeeds_when_all_predicates_met(tmp_path):
    client, db = _make_app(tmp_path)
    work, _doc = _make_eligible_work(db)

    r = client.post(f"/api/works/{work['id']}/pipeline", json={})
    assert r.status_code == 200, r.text
    assert r.json()["pipeline"]["work_id"] == work["id"]


def test_promote_stays_idempotent_for_existing_pipelines(tmp_path):
    """An existing pipeline is returned unchanged — the gate is for NEW ones."""
    client, db = _make_app(tmp_path)
    work, doc = _make_eligible_work(db)
    first = client.post(f"/api/works/{work['id']}/pipeline", json={}).json()["pipeline"]

    # Even if eligibility later regresses, the existing pipeline is returned.
    db.update_document_lifecycle(doc["id"], "draft", actor="author")
    r = client.post(f"/api/works/{work['id']}/pipeline", json={})
    assert r.status_code == 200
    assert r.json()["pipeline"]["id"] == first["id"]


def test_eligibility_endpoint_reports_per_rule_status(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work("Check Me")
    _ready_manuscript(db, work["id"])

    r = client.get(f"/api/works/{work['id']}/promotion-eligibility")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    by_rule = {c["rule"]: c for c in body["checks"]}
    assert by_rule["manuscript_document"]["ok"] is True
    assert by_rule["chapter_structure_ratified"]["ok"] is False
    assert by_rule["chapter_structure_ratified"]["reason"]
    assert by_rule["canonical_by_author"]["ok"] is False


def test_system_picked_canonical_does_not_satisfy_the_gate(tmp_path):
    """A canonical whose provenance is not 'author' never counts."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Ghost Canonical")
    doc = _ready_manuscript(db, work["id"])
    _pass_g8(db, work["id"], _uuid())
    # Simulate a legacy/unknown-provenance canonical (lifecycle_by stays NULL).
    from orivellum.database.db import _now

    with db._lock:
        db._conn.execute(
            "UPDATE objects SET lifecycle='canonical', updated_at=? WHERE id=?",
            (_now(), doc["id"]),
        )
        db._conn.commit()

    r = client.get(f"/api/works/{work['id']}/promotion-eligibility")
    by_rule = {c["rule"]: c for c in r.json()["checks"]}
    assert by_rule["canonical_by_author"]["ok"] is False


# ── Canonical discipline ───────────────────────────────────────────────────────


def test_system_actor_cannot_designate_canonical_manuscript(tmp_path):
    """Acceptance: zero manuscript canonical designations not by the author."""
    import pytest

    _, db = _make_app(tmp_path)
    work = db.create_work("Canon Work")
    doc = _ready_manuscript(db, work["id"])

    with pytest.raises(ValueError, match="author"):
        db.update_document_lifecycle(doc["id"], "canonical", actor="system")

    # Nothing changed and the invariant query stays at zero.
    with db._lock:
        rows = db._conn.execute(
            """SELECT COUNT(*) AS n FROM documents d JOIN objects o ON o.id=d.id
               WHERE d.doc_type='manuscript' AND o.lifecycle='canonical'
                 AND COALESCE(d.lifecycle_by,'') != 'author'"""
        ).fetchone()
    assert rows["n"] == 0


def test_author_canonical_records_provenance(tmp_path):
    _, db = _make_app(tmp_path)
    work = db.create_work("Canon Work")
    doc = _ready_manuscript(db, work["id"])

    assert db.update_document_lifecycle(doc["id"], "canonical", actor="author")
    got = db.get_document(doc["id"])
    assert got["lifecycle"] == "canonical"
    assert got["lifecycle_by"] == "author"


def test_lifecycle_route_signs_as_author(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work("Route Work")
    doc = _ready_manuscript(db, work["id"])

    r = client.patch(f"/api/library/{doc['id']}/lifecycle", json={"lifecycle": "canonical"})
    assert r.status_code == 200, r.text
    assert db.get_document(doc["id"])["lifecycle_by"] == "author"


def test_system_can_still_supersede_manuscripts(tmp_path):
    """Only CANONICAL designation is author-gated; supersede provenance is
    recorded but allowed (dedup guards prevent it happening automatically)."""
    _, db = _make_app(tmp_path)
    work = db.create_work("Supersede Work")
    doc = _ready_manuscript(db, work["id"])
    assert db.update_document_lifecycle(doc["id"], "superseded", actor="system")
    assert db.get_document(doc["id"])["lifecycle_by"] == "system"


# ── auto_dedup discipline ──────────────────────────────────────────────────────


def _record_dupe(db, doc_a_id: str, doc_b_id: str, kind: str = "near_duplicate") -> str:
    from orivellum.database.db import _now

    dupe_id = _uuid()
    with db._lock:
        db._conn.execute(
            """INSERT INTO doc_dupes (id, doc_a_id, doc_b_id, similarity, kind,
                                      resolved, created_at)
               VALUES (?, ?, ?, 0.92, ?, 0, ?)""",
            (dupe_id, doc_a_id, doc_b_id, kind, _now()),
        )
        db._conn.commit()
    return dupe_id


def _dupe_resolved(db, dupe_id: str) -> bool:
    with db._lock:
        row = db._conn.execute("SELECT resolved FROM doc_dupes WHERE id=?", (dupe_id,)).fetchone()
    return bool(row["resolved"])


def test_auto_dedup_skips_cross_type_pairs(tmp_path):
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.auto_dedup import auto_resolve_duplicates

    work = db.create_work("Mixed Types")
    a = db.create_document("Rules", work_id=work["id"], doc_type="rulebook")
    b = db.create_document("Draft", work_id=work["id"], doc_type="reference")
    for d in (a, b):
        db.update_document_extracted(d["id"], "text " * 50, 50, readiness="ready")
    dupe_id = _record_dupe(db, a["id"], b["id"])

    result = auto_resolve_duplicates(db)
    assert result["superseded"] == 0
    assert result["skipped"] >= 1
    assert not _dupe_resolved(db, dupe_id)


def test_auto_dedup_never_touches_manuscripts(tmp_path):
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.auto_dedup import auto_resolve_duplicates

    work = db.create_work("MS Work")
    a = _ready_manuscript(db, work["id"], "MS v1")
    b = _ready_manuscript(db, work["id"], "MS v2")
    dupe_id = _record_dupe(db, a["id"], b["id"])

    result = auto_resolve_duplicates(db)
    assert result["superseded"] == 0
    assert not _dupe_resolved(db, dupe_id)
    # Neither manuscript changed lifecycle.
    assert db.get_document(a["id"])["lifecycle"] != "superseded"
    assert db.get_document(b["id"])["lifecycle"] != "superseded"


def test_auto_dedup_skips_docs_in_book_pipeline_works(tmp_path):
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.auto_dedup import auto_resolve_duplicates

    work = db.create_work("Pipeline Work")
    a = db.create_document("Notes v1", work_id=work["id"], doc_type="reference")
    b = db.create_document("Notes v2", work_id=work["id"], doc_type="reference")
    for d in (a, b):
        db.update_document_extracted(d["id"], "text " * 50, 50, readiness="ready")
    db.create_book_pipeline(work["id"], "The Book")
    dupe_id = _record_dupe(db, a["id"], b["id"])

    result = auto_resolve_duplicates(db)
    assert result["superseded"] == 0
    assert not _dupe_resolved(db, dupe_id)


def test_auto_dedup_still_resolves_eligible_pairs(tmp_path):
    """Same-type, non-manuscript, no-pipeline pairs still auto-resolve."""
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.auto_dedup import auto_resolve_duplicates

    work = db.create_work("Tidy Work")
    a = db.create_document("Notes v1", work_id=work["id"], doc_type="reference")
    b = db.create_document("Notes v2", work_id=work["id"], doc_type="reference")
    db.update_document_extracted(a["id"], "text " * 50, 50, readiness="ready")
    db.update_document_extracted(b["id"], "text " * 80, 80, readiness="ready")
    dupe_id = _record_dupe(db, a["id"], b["id"])

    result = auto_resolve_duplicates(db)
    assert result["superseded"] == 1
    assert _dupe_resolved(db, dupe_id)


def test_auto_dedup_import_hits_apply_same_guards(tmp_path):
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.auto_dedup import auto_resolve_import_hits

    work = db.create_work("Import Work")
    a = _ready_manuscript(db, work["id"], "MS old")
    b = _ready_manuscript(db, work["id"], "MS new")
    dupe_id = _record_dupe(db, a["id"], b["id"])

    result = auto_resolve_import_hits(b["id"], [(a["id"], 0.92, "near_duplicate")], db)
    assert result["superseded"] == 0
    assert result["skipped"] == 1
    assert not _dupe_resolved(db, dupe_id)


# ── Curriculum gate ────────────────────────────────────────────────────────────


def test_learning_seed_refuses_unratified_work(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work("No Domain Work")
    r = client.post(f"/api/works/{work['id']}/learning/seed")
    assert r.status_code == 409
    assert "domain" in r.json()["detail"].lower()


def test_learning_seed_refuses_collections(tmp_path):
    client, db = _make_app(tmp_path)
    coll = db.create_work("Import batch", work_type="collection")
    r = client.post(f"/api/works/{coll['id']}/learning/seed")
    assert r.status_code in (409, 422)
