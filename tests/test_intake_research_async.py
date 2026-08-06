"""Tests: async research job lifecycle for POST /intake/research.

Verified behaviours:
  1. POST with confirmed=False → 422 (egress gate).
  2. POST with unknown doc_id → 404.
  3. POST with confirmed=True → returns {status:"pending"} immediately (does NOT block).
  4. Duplicate POST while job is pending → same status, no second thread.
  5. GET /research-status returns 404 when no job exists.
  6. Successful research → status transitions to "done" with summary populated.
  7. Silent failure (run_intake returns research_summary=None) → status "error".
  8. Unexpected exception from run_intake → status "error".
  9. Terminal jobs are evicted after TTL expires.
"""
from __future__ import annotations

import time
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

# ── Module under test ─────────────────────────────────────────────────────────
from orivellum.api.routes.intake import (
    router,
    _research_jobs,
    _research_jobs_lock,
    _run_research_background,
    _maybe_evict_terminal_jobs,
    _TERMINAL_TTL_SECONDS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_profile(research_summary=None, research_sources=None):
    """Return a minimal IntakeProfile-like object."""
    from orivellum.capabilities.intake import IntakeProfile, SuggestedAction
    return IntakeProfile(
        doc_id="doc-abc",
        what_it_is="Test doc",
        kind="pdf",
        tier="source",
        filed_to=None,
        filed_to_id=None,
        confidence=0.8,
        summary="Short summary.",
        word_count=100,
        headings=[],
        research_summary=research_summary,
        research_sources=research_sources or [],
    )


def _make_db(has_doc: bool = True):
    db = MagicMock()
    db.get_document.return_value = {"id": "doc-abc", "title": "Test"} if has_doc else None
    return db


def _make_cfg():
    return MagicMock()


def _app_with_mocks(db=None, cfg=None):
    """Build a TestClient with mocked get_db / get_config."""
    app = FastAPI()
    app.include_router(router)
    # Patch deps at the route module level
    _db = db or _make_db()
    _cfg = cfg or _make_cfg()
    with patch("orivellum.api.routes.intake.get_db", return_value=_db), \
         patch("orivellum.api.routes.intake.get_config", return_value=_cfg):
        yield TestClient(app)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the in-memory job registry between tests."""
    with _research_jobs_lock:
        _research_jobs.clear()
    yield
    with _research_jobs_lock:
        _research_jobs.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEgressGate:
    def test_unconfirmed_returns_422(self):
        db = _make_db()
        cfg = _make_cfg()
        app = FastAPI(); app.include_router(router)
        with patch("orivellum.api.routes.intake.get_db", return_value=db), \
             patch("orivellum.api.routes.intake.get_config", return_value=cfg):
            client = TestClient(app)
            resp = client.post("/api/intake/research", json={"doc_id": "doc-abc"})
        assert resp.status_code == 422
        assert "confirmed" in resp.json()["detail"].lower()

    def test_unknown_doc_returns_404(self):
        db = _make_db(has_doc=False)
        cfg = _make_cfg()
        app = FastAPI(); app.include_router(router)
        with patch("orivellum.api.routes.intake.get_db", return_value=db), \
             patch("orivellum.api.routes.intake.get_config", return_value=cfg):
            client = TestClient(app)
            resp = client.post(
                "/api/intake/research",
                json={"doc_id": "missing", "confirmed": True},
            )
        assert resp.status_code == 404


class TestImmediateReturn:
    def test_post_returns_pending_immediately(self):
        """POST must return before the background thread finishes."""
        db = _make_db()
        cfg = _make_cfg()
        app = FastAPI(); app.include_router(router)

        barrier = threading.Event()

        def slow_run_intake(*_, **__):
            barrier.wait(timeout=5)   # block until test unblocks it
            return _make_profile(research_summary="Some result")

        with patch("orivellum.api.routes.intake.get_db", return_value=db), \
             patch("orivellum.api.routes.intake.get_config", return_value=cfg), \
             patch("orivellum.api.routes.intake.run_intake", side_effect=slow_run_intake):
            client = TestClient(app)
            t0 = time.monotonic()
            resp = client.post(
                "/api/intake/research",
                json={"doc_id": "doc-abc", "confirmed": True},
            )
            elapsed = time.monotonic() - t0

        barrier.set()   # let the background thread finish
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["job_id"] == "doc-abc"
        # Must return well under 1 second (Tavily takes up to 20s)
        assert elapsed < 1.0, f"POST took {elapsed:.2f}s — must be fire-and-forget"


class TestIdempotency:
    def test_duplicate_post_returns_existing_job(self):
        """A second POST while job is pending must not spawn a second thread."""
        db = _make_db()
        cfg = _make_cfg()
        app = FastAPI(); app.include_router(router)

        call_count = {"n": 0}
        barrier = threading.Event()

        def slow_run_intake(*_, **__):
            call_count["n"] += 1
            barrier.wait(timeout=5)
            return _make_profile(research_summary="Result")

        with patch("orivellum.api.routes.intake.get_db", return_value=db), \
             patch("orivellum.api.routes.intake.get_config", return_value=cfg), \
             patch("orivellum.api.routes.intake.run_intake", side_effect=slow_run_intake):
            client = TestClient(app)
            r1 = client.post("/api/intake/research",
                             json={"doc_id": "doc-abc", "confirmed": True})
            r2 = client.post("/api/intake/research",
                             json={"doc_id": "doc-abc", "confirmed": True})

        barrier.set()
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json()["status"] in ("pending", "running")
        # Background function must have been called exactly once
        time.sleep(0.1)   # let thread settle
        assert call_count["n"] == 1, "Duplicate POST started a second thread"


class TestStatusPolling:
    def test_status_404_when_no_job(self):
        app = FastAPI(); app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/intake/no-such-doc/research-status")
        assert resp.status_code == 404

    def test_status_done_after_successful_research(self):
        """After run_intake returns a non-None summary, status must be 'done'."""
        db = _make_db()
        cfg = _make_cfg()
        done_evt = threading.Event()

        def fast_run_intake(*_, **__):
            return _make_profile(
                research_summary="Great findings",
                research_sources=[{"title": "Wikipedia", "url": "https://en.wikipedia.org"}],
            )

        # Run the worker directly (synchronously) for determinism
        with _research_jobs_lock:
            _research_jobs["doc-abc"] = {
                "status": "pending", "research_summary": None,
                "research_sources": [], "error": None, "_terminal_at": None,
            }

        with patch("orivellum.api.routes.intake.run_intake", side_effect=fast_run_intake):
            _run_research_background("doc-abc", None, db, cfg)

        with _research_jobs_lock:
            job = dict(_research_jobs["doc-abc"])

        assert job["status"] == "done"
        assert job["research_summary"] == "Great findings"
        assert len(job["research_sources"]) == 1
        assert job["_terminal_at"] is not None

    def test_silent_failure_stored_as_error(self):
        """run_intake returning research_summary=None must result in status='error'."""
        db = _make_db()
        cfg = _make_cfg()

        with _research_jobs_lock:
            _research_jobs["doc-abc"] = {
                "status": "pending", "research_summary": None,
                "research_sources": [], "error": None, "_terminal_at": None,
            }

        # run_intake silently ate the Tavily timeout → returns None summary
        with patch("orivellum.api.routes.intake.run_intake",
                   return_value=_make_profile(research_summary=None)):
            _run_research_background("doc-abc", None, db, cfg)

        with _research_jobs_lock:
            job = dict(_research_jobs["doc-abc"])

        assert job["status"] == "error", "Silent failure must be stored as error, not done"
        assert job["error"] is not None
        assert job["research_summary"] is None

    def test_unexpected_exception_stored_as_error(self):
        """An exception escaping run_intake must result in status='error'."""
        db = _make_db()
        cfg = _make_cfg()

        with _research_jobs_lock:
            _research_jobs["doc-abc"] = {
                "status": "pending", "research_summary": None,
                "research_sources": [], "error": None, "_terminal_at": None,
            }

        with patch("orivellum.api.routes.intake.run_intake",
                   side_effect=RuntimeError("Unexpected crash")):
            _run_research_background("doc-abc", None, db, cfg)

        with _research_jobs_lock:
            job = dict(_research_jobs["doc-abc"])

        assert job["status"] == "error"
        assert "Unexpected crash" in job["error"]
        assert job["_terminal_at"] is not None


class TestTTLEviction:
    def test_terminal_jobs_evicted_after_ttl(self):
        """Jobs in terminal state older than _TERMINAL_TTL_SECONDS must be removed."""
        old_terminal_at = time.monotonic() - (_TERMINAL_TTL_SECONDS + 1)
        with _research_jobs_lock:
            _research_jobs["doc-old"] = {
                "status": "done",
                "research_summary": "result",
                "research_sources": [],
                "error": None,
                "_terminal_at": old_terminal_at,
            }
            # Fresh job must NOT be evicted
            _research_jobs["doc-fresh"] = {
                "status": "done",
                "research_summary": "result",
                "research_sources": [],
                "error": None,
                "_terminal_at": time.monotonic(),
            }
            _maybe_evict_terminal_jobs()
            keys = set(_research_jobs.keys())

        assert "doc-old" not in keys, "Expired terminal job was not evicted"
        assert "doc-fresh" in keys, "Fresh terminal job was incorrectly evicted"

    def test_pending_jobs_not_evicted(self):
        """In-progress jobs must never be evicted regardless of age."""
        with _research_jobs_lock:
            _research_jobs["doc-running"] = {
                "status": "running",
                "research_summary": None,
                "research_sources": [],
                "error": None,
                "_terminal_at": None,
            }
            _maybe_evict_terminal_jobs()
            assert "doc-running" in _research_jobs

    def test_status_endpoint_returns_404_after_eviction(self):
        """GET /research-status must return 404 when the job has been evicted."""
        app = FastAPI(); app.include_router(router)
        db = _make_db()
        cfg = _make_cfg()

        # Seed an already-expired job
        old_t = time.monotonic() - (_TERMINAL_TTL_SECONDS + 10)
        with _research_jobs_lock:
            _research_jobs["doc-abc"] = {
                "status": "done",
                "research_summary": "x",
                "research_sources": [],
                "error": None,
                "_terminal_at": old_t,
            }

        with patch("orivellum.api.routes.intake.get_db", return_value=db), \
             patch("orivellum.api.routes.intake.get_config", return_value=cfg):
            client = TestClient(app)
            resp = client.get("/api/intake/doc-abc/research-status")

        assert resp.status_code == 404, (
            "Status endpoint must return 404 once the expired job has been evicted"
        )
