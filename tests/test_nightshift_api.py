"""Endpoint-level tests for the on-demand nightshift API.

Covers:
- POST /api/system/nightshift/run-now spawns a background run and returns {started: true}
- POST /api/system/nightshift/run-now returns 409 when a run is already in progress
- GET  /api/system/nightshift/last-report empty state (no runs yet)
- GET  /api/system/nightshift/last-report populated state (reads report_path file)

run_nightshift is monkeypatched to a no-op so the tests stay fast.
"""
from __future__ import annotations

import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    """Return a configured FastAPI test app wired to a temp DB."""
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


def _seed_run(db, report_path: str | None) -> str:
    """Insert a nightshift_runs row. Returns the ran_at timestamp."""
    run_id = str(uuid.uuid4())
    ran_at = datetime.now(timezone.utc).isoformat()
    with db._lock:
        db._conn.execute(
            "INSERT INTO nightshift_runs(id,ran_at,docs_processed,items_added,report_path)"
            " VALUES(?,?,?,?,?)",
            (run_id, ran_at, 5, 12, report_path),
        )
        db._conn.commit()
    return ran_at


class TestNightshiftRunNow(unittest.TestCase):

    def test_run_now_starts_background_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            # Replace the actual run with a no-op so the thread finishes instantly.
            with patch("orivellum.capabilities.nightshift._run_nightshift_passes",
                       lambda db, cfg: None):
                resp = client.post("/api/system/nightshift/run-now")
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.json().get("started"))

    def test_run_now_conflicts_when_already_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            from orivellum.capabilities import nightshift as ns

            # Force the status tracker to report an in-progress run.
            with ns._status_lock:
                ns._status["running"] = True
            try:
                resp = client.post("/api/system/nightshift/run-now")
                self.assertEqual(resp.status_code, 409)
            finally:
                with ns._status_lock:
                    ns._status["running"] = False


class TestNightshiftLastReport(unittest.TestCase):

    def test_last_report_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            resp = client.get("/api/system/nightshift/last-report")
            self.assertEqual(resp.status_code, 200)
            self.assertIsNone(resp.json().get("report_markdown"))

    def test_last_report_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            report_path = Path(tmp) / "nightshift" / "2024-01-01.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text("# Night Report\n\n- Did some work\n", encoding="utf-8")

            _seed_run(db, str(report_path))

            resp = client.get("/api/system/nightshift/last-report")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertIn("Night Report", body["report_markdown"])
            self.assertEqual(body["docs_processed"], 5)
            self.assertEqual(body["items_added"], 12)

    def test_last_report_missing_file_is_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            _seed_run(db, str(Path(tmp) / "nightshift" / "does-not-exist.md"))

            resp = client.get("/api/system/nightshift/last-report")
            self.assertEqual(resp.status_code, 200)
            # Metadata present but markdown None since the file is missing.
            self.assertIsNone(resp.json().get("report_markdown"))
            self.assertEqual(resp.json().get("docs_processed"), 5)


class TestNightshiftStatus(unittest.TestCase):

    def test_status_reports_last_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            _seed_run(db, None)
            resp = client.get("/api/system/nightshift/status")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertIn("running", body)
            self.assertIsNotNone(body["last_run"])
            self.assertEqual(body["last_run"]["items_added"], 12)


if __name__ == "__main__":
    unittest.main()
