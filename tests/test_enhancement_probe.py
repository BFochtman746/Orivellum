"""Tests for the DeepFilterNet3 live re-probe (no-restart registration).

deepfilternet is not installed in the test environment, so probes fail —
which is exactly the surface being tested: the failure must be described
(error text + interpreter path), the failed result must be cached, and a
forced re-probe must clear that cache and retry the import fresh.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

from orivellum.capabilities import enhancement


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class TestProbeCapability(unittest.TestCase):

    def setUp(self):
        enhancement._df_model = None
        enhancement._last_error = None

    def tearDown(self):
        enhancement._df_model = None
        enhancement._last_error = None

    def test_probe_reports_error_and_interpreter(self):
        result = enhancement.probe()
        self.assertFalse(result["available"])
        self.assertIn("ImportError", result["error"] or "")
        self.assertEqual(result["python"], sys.executable)
        self.assertIn("uv add deepfilternet", result["install_hint"])

    def test_failed_probe_is_cached_until_forced(self):
        enhancement.probe()
        self.assertIs(enhancement._df_model, False)
        # Non-forced probe keeps the cached failure
        enhancement.probe(force=False)
        self.assertIs(enhancement._df_model, False)
        # Forced probe clears the cache and re-attempts the import
        # (it fails again here, but the cache was reset first)
        seen = []
        orig = enhancement._get_df_model

        def _spy():
            seen.append(enhancement._df_model)
            return orig()

        enhancement._get_df_model = _spy
        try:
            enhancement.probe(force=True)
        finally:
            enhancement._get_df_model = orig
        self.assertEqual(seen, [None], "force=True must reset the cached failure")

    def test_force_never_discards_a_loaded_model(self):
        sentinel = ("model", "state")
        enhancement._df_model = sentinel
        result = enhancement.probe(force=True)
        self.assertTrue(result["available"])
        self.assertIs(enhancement._df_model, sentinel)


class TestProbeEndpoints(unittest.TestCase):

    def setUp(self):
        enhancement._df_model = None
        enhancement._last_error = None

    def tearDown(self):
        enhancement._df_model = None
        enhancement._last_error = None

    def test_probe_endpoint_returns_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            resp = client.post("/api/system/audio-enhance/probe")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["installed"])
            self.assertIn("ImportError", body["error"])
            self.assertEqual(body["python"], sys.executable)
            self.assertIn("uv add deepfilternet", body["install_hint"])

    def test_settings_get_includes_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            resp = client.get("/api/system/settings/audio-enhance")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertFalse(body["installed"])
            self.assertIn("error", body)
            self.assertEqual(body["python"], sys.executable)

    def test_put_enable_reprobes(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, headers=AUTH_HEADERS)
            # Prime a cached failure
            enhancement.probe()
            self.assertIs(enhancement._df_model, False)
            resp = client.put("/api/system/settings/audio-enhance",
                              json={"enabled": True})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["enabled"])
            self.assertFalse(body["installed"])
            self.assertEqual(
                db.get_setting("audio_enhance_enabled", "false"), "true")
            # Disabling never probes
            resp = client.put("/api/system/settings/audio-enhance",
                              json={"enabled": False})
            self.assertIsNone(resp.json()["installed"])


if __name__ == "__main__":
    unittest.main()
