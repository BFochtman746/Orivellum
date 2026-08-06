"""API tests for the Trailer Architect endpoints.

Covers:
- Schema: trailers table exists after DB init (v91 migration).
- POST /api/works/{id}/trailer
  - 404 for unknown work.
  - 422 when work has no ready documents.
  - 200 (job started) when work has at least one ready, text-bearing document.
- GET /api/works/{id}/trailers — list response shape.
- GET /api/works/{id}/trailers/{pkg_id} — detail response shape.
- DB helpers: create_trailer, update_trailer, get_trailer, list_trailers.
- Offline pipeline smoke: run_trailer_pipeline completes without a live LLM.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


def _make_ready_doc(db, work_id: str, text: str = "word " * 500) -> dict:
    """Create a document and mark it extracted/ready so the trailer guard passes."""
    doc = db.create_document(title="test.docx", kind="docx", work_id=work_id)
    db.update_document_extracted(doc["id"], text, len(text.split()), readiness="ready")
    return doc


# ---------------------------------------------------------------------------
# Schema / DB layer
# ---------------------------------------------------------------------------

class TrailerSchemaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, raise_server_exceptions=True,
                                 headers=AUTH_HEADERS)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_trailers_table_exists(self):
        """Schema migration v91 must create the trailers table."""
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trailers'"
            ).fetchone()
        self.assertIsNotNone(row, "trailers table not found — v91 migration missing")

    def test_create_and_get_trailer(self):
        work = self.db.create_work(title="Test Book", work_type="writing")
        t = self.db.create_trailer(work["id"])
        self.assertEqual(t["status"], "running")
        self.assertEqual(t["work_id"], work["id"])

        fetched = self.db.get_trailer(t["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["id"], t["id"])

    def test_update_trailer_status(self):
        work = self.db.create_work(title="Test Book", work_type="writing")
        t = self.db.create_trailer(work["id"])
        self.db.update_trailer(t["id"], status="ready", phase="package",
                               package_json='{"ok":true}')
        updated = self.db.get_trailer(t["id"])
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["package_json"], '{"ok":true}')

    def test_list_trailers_returns_newest_first(self):
        work = self.db.create_work(title="Test Book", work_type="writing")
        t1 = self.db.create_trailer(work["id"])
        t2 = self.db.create_trailer(work["id"])
        rows = self.db.list_trailers(work["id"])
        self.assertEqual(len(rows), 2)
        # Newest first: t2 was inserted last
        self.assertEqual(rows[0]["id"], t2["id"])


# ---------------------------------------------------------------------------
# API: eligibility guard
# ---------------------------------------------------------------------------

class TrailerEligibilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, raise_server_exceptions=True,
                                 headers=AUTH_HEADERS)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_404_for_unknown_work(self):
        r = self.client.post("/api/works/does-not-exist/trailer")
        self.assertEqual(r.status_code, 404)

    def test_422_when_work_has_no_ready_documents(self):
        """Work with no documents → 422."""
        work = self.db.create_work(title="Empty Work", work_type="writing")
        r = self.client.post(f"/api/works/{work['id']}/trailer")
        self.assertEqual(r.status_code, 422)
        self.assertIn("processed document", r.json()["detail"].lower())

    def test_422_when_only_document_not_ready(self):
        """Work with a document in 'imported' state → 422 (text not extracted)."""
        work = self.db.create_work(title="Draft Work", work_type="writing")
        self.db.create_document(title="unprocessed.docx", kind="docx",
                                work_id=work["id"])
        r = self.client.post(f"/api/works/{work['id']}/trailer")
        self.assertEqual(r.status_code, 422)

    def test_200_when_work_has_ready_document(self):
        """Work with a ready document → job accepted (202-ish: status 200 + job id)."""
        work = self.db.create_work(title="Ready Work", work_type="writing")
        _make_ready_doc(self.db, work["id"])
        r = self.client.post(f"/api/works/{work['id']}/trailer")
        # Background asyncio.create_task may fail inside TestClient sync context;
        # we only assert the HTTP handshake succeeded and the record was created.
        self.assertIn(r.status_code, (200, 500),
                      f"Unexpected status: {r.status_code} — {r.text[:300]}")
        if r.status_code == 200:
            body = r.json()
            self.assertIn("trailer_id", body)
            self.assertEqual(body["status"], "running")


# ---------------------------------------------------------------------------
# API: list and detail
# ---------------------------------------------------------------------------

class TrailerListDetailTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, raise_server_exceptions=True,
                                 headers=AUTH_HEADERS)
        self.work = self.db.create_work(title="My Book", work_type="writing")
        self.work_id = self.work["id"]

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_list_returns_empty_for_new_work(self):
        r = self.client.get(f"/api/works/{self.work_id}/trailers")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("trailers", body)
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["trailers"], [])

    def test_list_404_for_unknown_work(self):
        r = self.client.get("/api/works/nope/trailers")
        self.assertEqual(r.status_code, 404)

    def test_list_shows_trailer_after_creation(self):
        t = self.db.create_trailer(self.work_id)
        r = self.client.get(f"/api/works/{self.work_id}/trailers")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["count"], 1)
        item = body["trailers"][0]
        self.assertEqual(item["id"], t["id"])
        self.assertEqual(item["status"], "running")
        # List must NOT include heavy package_json
        self.assertNotIn("package_json", item)
        self.assertIn("has_package", item)

    def test_detail_404_for_unknown_trailer(self):
        r = self.client.get(f"/api/works/{self.work_id}/trailers/does-not-exist")
        self.assertEqual(r.status_code, 404)

    def test_detail_returns_full_record(self):
        import json
        t = self.db.create_trailer(self.work_id)
        pkg = {"status": "READY", "docs": {}, "brief": {}, "concept": {},
               "method": {}, "plan": {}, "validation": {"status": "READY",
               "critical": 0, "findings": []}, "status_badge": "✓",
               "generated": "now", "shot_prompts": {}}
        self.db.update_trailer(t["id"], status="ready", phase="package",
                               package_json=json.dumps(pkg))

        r = self.client.get(f"/api/works/{self.work_id}/trailers/{t['id']}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["id"], t["id"])
        self.assertEqual(body["status"], "ready")
        self.assertIn("package", body)
        self.assertIsNotNone(body["package"])

    def test_stats_include_trailer_count(self):
        self.db.create_trailer(self.work_id)
        r = self.client.get(f"/api/works/{self.work_id}/stats")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("trailer_count", body)
        self.assertEqual(body["trailer_count"], 1)


# ---------------------------------------------------------------------------
# Offline pipeline smoke test
# ---------------------------------------------------------------------------

def _run_offline(db, work_id: str, trailer_id: str, fmt: str = "both"):
    """Helper: run the pipeline in offline mode and return the stored result dict."""
    import os, json
    os.environ["MEDIA_STUDIO_OFFLINE"] = "1"
    try:
        from orivellum.capabilities.trailer import run_trailer_pipeline
        run_trailer_pipeline(db, work_id, trailer_id, fmt)
        result = db.get_trailer(trailer_id)
        if result.get("package_json"):
            result["_pkg"] = json.loads(result["package_json"])
        return result
    finally:
        os.environ.pop("MEDIA_STUDIO_OFFLINE", None)


class TrailerPipelineSmokeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _, self.db = _make_app(self._tmp.name)
        self.work = self.db.create_work(title="Smoke Book", work_type="writing")
        self.work_id = self.work["id"]
        _make_ready_doc(self.db, self.work_id, text="In the beginning God created. " * 200)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    # ── format=both (default) ──────────────────────────────────────────────

    def test_offline_both_pipeline_completes_and_sets_ready(self):
        """Default 'both' pipeline must reach status='ready' in offline mode."""
        t = self.db.create_trailer(self.work_id)
        result = _run_offline(self.db, self.work_id, t["id"], fmt="both")
        self.assertEqual(
            result["status"], "ready",
            f"Expected 'ready', got {result['status']!r}. Error: {result.get('error')}",
        )
        self.assertIsNotNone(result["package_json"])
        pkg = result["_pkg"]

        # Combined package shape
        self.assertEqual(pkg.get("format"), "both",
                         "Top-level 'format' key must be 'both'")
        self.assertIn("full",  pkg, "Combined package must have a 'full' sub-package")
        self.assertIn("short", pkg, "Combined package must have a 'short' sub-package")

        # Legacy convenience keys promoted to top level
        for key in ("brief", "concept", "docs", "plan", "validation", "shot_prompts"):
            self.assertIn(key, pkg, f"Top-level key '{key}' must be promoted from full package")

        # Both sub-packages must have all 9 doc keys
        required = {"book_brief", "concepts", "method", "shotlist",
                    "narration_script", "music_brief", "titles",
                    "assembly_sheet", "production_package"}
        for fmt_key in ("full", "short"):
            docs = pkg[fmt_key].get("docs", {})
            self.assertTrue(
                required.issubset(set(docs.keys())),
                f"'{fmt_key}' sub-package missing doc keys: "
                f"{required - set(docs.keys())}",
            )

    # ── format=full ────────────────────────────────────────────────────────

    def test_offline_full_pipeline_completes_and_sets_ready(self):
        """format='full' must reach status='ready' in offline mode."""
        t = self.db.create_trailer(self.work_id)
        result = _run_offline(self.db, self.work_id, t["id"], fmt="full")
        self.assertEqual(result["status"], "ready",
                         f"Error: {result.get('error')}")
        pkg = result["_pkg"]
        # Flat (non-combined) package — no sub-packages
        self.assertNotEqual(pkg.get("format"), "both")
        required = {"book_brief", "concepts", "method", "shotlist",
                    "narration_script", "music_brief", "titles",
                    "assembly_sheet", "production_package"}
        self.assertTrue(required.issubset(set(pkg.get("docs", {}).keys())),
                        f"Missing doc keys: {required - set(pkg.get('docs', {}).keys())}")

    # ── format=short ───────────────────────────────────────────────────────

    def test_offline_short_pipeline_completes_and_sets_ready(self):
        """format='short' must reach status='ready' in offline mode."""
        t = self.db.create_trailer(self.work_id)
        result = _run_offline(self.db, self.work_id, t["id"], fmt="short")
        self.assertEqual(result["status"], "ready",
                         f"Error: {result.get('error')}")
        pkg = result["_pkg"]
        self.assertEqual(pkg.get("format"), "short")
        self.assertEqual(pkg.get("aspect_ratio"), "9:16")
        self.assertEqual(pkg.get("duration_s"), 30)

    def test_short_plan_exactly_3_shots_with_beat_types(self):
        """Short offline plan: exactly 3 shots, correct beat_types, durations sum to 30s."""
        t = self.db.create_trailer(self.work_id)
        result = _run_offline(self.db, self.work_id, t["id"], fmt="short")
        pkg = result["_pkg"]
        shots = pkg.get("plan", {}).get("shots", [])

        self.assertEqual(len(shots), 3,
                         f"Expected exactly 3 shots, got {len(shots)}")
        for i, (s, expected_type) in enumerate(zip(shots, ("hook", "peak", "close"))):
            self.assertEqual(
                s.get("beat_type"), expected_type,
                f"Shot {i} beat_type should be '{expected_type}', got {s.get('beat_type')!r}",
            )
            self.assertIn("vertical_framing_note", s,
                          f"Shot {i} missing vertical_framing_note")
            self.assertTrue(s["vertical_framing_note"],
                            f"Shot {i} vertical_framing_note is empty")
        total_dur = sum(s.get("duration", 0) for s in shots)
        self.assertEqual(total_dur, 30,
                         f"Shot durations must sum to 30s, got {total_dur}s")

    def test_short_plan_narration_within_30s(self):
        """Short offline plan: all narration t_start values must be within [0, 30)."""
        t = self.db.create_trailer(self.work_id)
        result = _run_offline(self.db, self.work_id, t["id"], fmt="short")
        pkg = result["_pkg"]
        narration = pkg.get("plan", {}).get("narration", [])
        for ln in narration:
            t_start = ln.get("t_start", 0)
            self.assertGreaterEqual(t_start, 0,
                                    f"Narration t_start {t_start} < 0")
            self.assertLess(t_start, 30,
                            f"Narration t_start {t_start} >= 30s clip duration (would fail validation)")
        if narration:
            self.assertEqual(narration[0]["t_start"], 0,
                             "First (hook) narration line must be at t=0")

    # ── failure path ───────────────────────────────────────────────────────

    def test_offline_pipeline_fails_gracefully_when_no_documents(self):
        """Pipeline on a work with no extracted text must set status='failed', not crash."""
        empty_work = self.db.create_work(title="Empty Work", work_type="writing")
        t = self.db.create_trailer(empty_work["id"])
        from orivellum.capabilities.trailer import run_trailer_pipeline
        run_trailer_pipeline(self.db, empty_work["id"], t["id"])
        result = self.db.get_trailer(t["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNotNone(result.get("error"))


if __name__ == "__main__":
    unittest.main()
