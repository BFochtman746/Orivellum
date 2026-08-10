"""
Tests: Trailer Architect end-to-end pipeline and route guards.

Verified behaviours:
  1. Happy path: run_trailer_pipeline on a CANON Work with extracted text
     completes with status='ready', package.docs contains all 9 expected
     keys, and validation.status='READY'.
  2. Route guard: POST /api/works/{id}/trailer on a Work that has no
     documents with ready extracted text returns HTTP 422.
  3. Runner failure: run_trailer_pipeline on a Work with no text at all
     marks the trailer status='failed' with a descriptive error message.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from orivellum.database.db import OrivellumDB

# ---------------------------------------------------------------------------
# The 9 expected keys in package['docs'] for a 'full' format package
# ---------------------------------------------------------------------------
EXPECTED_DOCS_KEYS = {
    "production_package",
    "book_brief",
    "concepts",
    "method",
    "shotlist",
    "narration_script",
    "music_brief",
    "titles",
    "assembly_sheet",
}

# A validation result that makes the pipeline emit status='ready'
_READY_VALIDATION = {
    "status": "READY",
    "critical": 0,
    "findings": [],
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _make_db() -> OrivellumDB:
    """Fresh in-memory DB with all migrations applied."""
    return OrivellumDB(":memory:")


def _create_canon_work(db: OrivellumDB, title: str = "Test Work") -> dict:
    """Create a Work and promote its lifecycle to 'canon' in the objects table."""
    work = db.create_work(title=title, work_type="writing")
    with db._lock:
        db._conn.execute(
            "UPDATE objects SET lifecycle='canon' WHERE id=?", (work["id"],)
        )
        db._conn.commit()
    return db.get_work(work["id"])  # type: ignore[return-value]


def _add_ready_document(db: OrivellumDB, work_id: str, text: str) -> dict:
    """Create a document, mark it ready, and store extracted text."""
    doc = db.create_document(title="Chapter 1", work_id=work_id)
    with db._lock:
        db._conn.execute(
            "UPDATE documents SET readiness='ready', extracted_text=? WHERE id=?",
            (text, doc["id"]),
        )
        db._conn.commit()
    return doc


# ---------------------------------------------------------------------------
# Test 1 — happy path: full package on a CANON Work
# ---------------------------------------------------------------------------

def test_full_trailer_pipeline_happy_path():
    """run_trailer_pipeline on a CANON Work with ready text must complete with
    status='ready', a package that has all 9 docs keys, and validation.status='READY'.

    The pipeline runs in offline stub mode (no live LLM required).
    validate.check is patched to return READY so the offline stub's incomplete
    shot plans don't trigger BLOCKED — the focus is on the orchestration,
    DB writes, and package assembly, not on LLM prompt quality.
    """
    from orivellum.capabilities.trailer.runner import run_trailer_pipeline

    db = _make_db()
    work = _create_canon_work(db, "The Prometheus Codex")
    work_id = work["id"]
    _add_ready_document(
        db, work_id,
        "A sweeping story about fire stolen from the gods. " * 60,
    )

    trailer = db.create_trailer(work_id)
    trailer_id = trailer["id"]

    with patch(
        "orivellum.capabilities.trailer.runner.validate.check",
        return_value=_READY_VALIDATION,
    ):
        run_trailer_pipeline(db, work_id, trailer_id, fmt="full")

    result = db.get_trailer(trailer_id)
    assert result is not None, "Trailer record should still exist after the run"
    assert result["status"] == "ready", (
        f"Expected status='ready', got {result['status']!r}. "
        f"Error: {(result.get('error') or '')[:400]}"
    )
    assert result["package_json"], "Package JSON must be stored for a completed trailer"

    pkg = json.loads(result["package_json"])

    # Docs block: all 9 keys must be present
    assert "docs" in pkg, "Package must contain a 'docs' key"
    actual_keys = set(pkg["docs"].keys())
    assert actual_keys == EXPECTED_DOCS_KEYS, (
        f"Expected 9 docs keys {sorted(EXPECTED_DOCS_KEYS)}, "
        f"got {sorted(actual_keys)}"
    )

    # Each doc value must be a non-empty string (markdown content)
    for key in EXPECTED_DOCS_KEYS:
        assert isinstance(pkg["docs"][key], str) and pkg["docs"][key].strip(), (
            f"docs[{key!r}] must be a non-empty string"
        )

    # Validation block must reflect the mocked READY result
    assert "validation" in pkg, "Package must contain a 'validation' key"
    assert pkg["validation"]["status"] == "READY", (
        f"Expected validation.status='READY', got {pkg['validation'].get('status')!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — route guard: Work with no eligible documents → HTTP 422
# ---------------------------------------------------------------------------

def test_post_trailer_returns_422_when_work_has_no_eligible_documents():
    """POST /api/works/{id}/trailer must return 422 when the Work has no
    documents with readiness='ready' and non-empty extracted_text.

    This guard prevents the background pipeline from starting with no material
    and then silently producing a 'failed' package with no user-visible error
    on the HTTP response.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orivellum.api.routes.works import router

    db = _make_db()
    work = _create_canon_work(db, "Sparse Work — no docs")
    # Deliberately add no documents so eligible_docs == 0

    app = FastAPI()
    app.include_router(router)

    with patch("orivellum.api.routes.works.get_db", return_value=db):
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(f"/api/works/{work['id']}/trailer")

    assert resp.status_code == 422, (
        f"Expected HTTP 422 for a Work with no eligible documents; "
        f"got {resp.status_code}. Body: {resp.text[:400]}"
    )
    body = resp.json()
    assert "detail" in body, "422 response must include a 'detail' field"
    detail = body["detail"].lower()
    assert "document" in detail or "text" in detail, (
        f"422 detail should mention documents/text. Got: {body['detail']!r}"
    )


def test_post_trailer_returns_422_when_document_has_no_extracted_text():
    """POST /api/works/{id}/trailer must also return 422 when the Work has a
    document with readiness='ready' but the extracted_text column is empty —
    the guard checks both readiness AND text presence."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orivellum.api.routes.works import router

    db = _make_db()
    work = _create_canon_work(db, "Work with empty-text doc")

    # Create a doc, mark ready, but leave extracted_text NULL/empty
    doc = db.create_document(title="Empty Doc", work_id=work["id"])
    with db._lock:
        db._conn.execute(
            "UPDATE documents SET readiness='ready', extracted_text='' WHERE id=?",
            (doc["id"],),
        )
        db._conn.commit()

    app = FastAPI()
    app.include_router(router)

    with patch("orivellum.api.routes.works.get_db", return_value=db):
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.post(f"/api/works/{work['id']}/trailer")

    assert resp.status_code == 422, (
        f"Expected HTTP 422 for a Work whose documents have no extracted text; "
        f"got {resp.status_code}. Body: {resp.text[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — runner failure: Work with no text → trailer status='failed'
# ---------------------------------------------------------------------------

def test_trailer_runner_sets_failed_when_work_has_no_text():
    """run_trailer_pipeline marks the trailer status='failed' (and stores an
    error message) when book_text_from_work() returns an empty string.

    This can happen even after the route guard passes if the only ready doc
    loses its extracted text between the guard check and the background run,
    or when calling the runner directly without the guard.  The runner must
    never leave the trailer in status='running' on this path.
    """
    from orivellum.capabilities.trailer.runner import run_trailer_pipeline

    db = _make_db()
    work = _create_canon_work(db, "No-content Work")
    work_id = work["id"]
    # No documents — book_text_from_work() returns ""

    trailer = db.create_trailer(work_id)
    trailer_id = trailer["id"]

    run_trailer_pipeline(db, work_id, trailer_id, fmt="full")

    result = db.get_trailer(trailer_id)
    assert result is not None
    assert result["status"] == "failed", (
        f"Expected status='failed' when Work has no text; got {result['status']!r}"
    )
    assert result.get("error"), (
        "A failed trailer must store a non-empty error message"
    )
    assert "text" in result["error"].lower() or "extract" in result["error"].lower(), (
        f"Error message should mention text/extraction. Got: {result['error']!r}"
    )
