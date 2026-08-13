"""Tests for GET/PUT /api/system/settings/ui-preferences.

The endpoint is the cross-install restore path for appearance/calibration
preferences. Its contract:
- PUT MERGES partial records (clients send only explicitly-chosen keys), so
  one device's update never clobbers another device's saved choices.
- Unknown keys and invalid values are rejected with 422.
- GET returns the merged record for fresh-install hydration.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.database.db import OrivellumDB

PREFS_URL = "/api/system/settings/ui-preferences"


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


class UiPreferencesApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(app)
        self.auth = {"X-Api-Key": "test-key"}
        import os

        os.environ.setdefault("SESSION_SECRET", "test-key")
        self._key_patch = os.environ["SESSION_SECRET"]
        self.auth = {"X-Api-Key": self._key_patch}

    def tearDown(self):
        self._tmp.cleanup()

    def test_get_empty_record_initially(self):
        r = self.client.get(PREFS_URL, headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {})

    def test_put_merges_partial_records(self):
        # Device A saves a full record.
        r = self.client.put(
            PREFS_URL,
            headers=self.auth,
            json={"theme": "hull", "textSize": "112", "measure": "focused"},
        )
        self.assertEqual(r.status_code, 200)
        # Device B updates ONLY the theme — other keys must survive.
        r = self.client.put(PREFS_URL, headers=self.auth, json={"theme": "daylight"})
        self.assertEqual(r.status_code, 200)
        got = self.client.get(PREFS_URL, headers=self.auth).json()
        self.assertEqual(
            got, {"theme": "daylight", "textSize": "112", "measure": "focused"}
        )

    def test_put_empty_body_changes_nothing(self):
        self.client.put(PREFS_URL, headers=self.auth, json={"readingFace": "serif"})
        self.client.put(PREFS_URL, headers=self.auth, json={})
        got = self.client.get(PREFS_URL, headers=self.auth).json()
        self.assertEqual(got, {"readingFace": "serif"})

    def test_put_rejects_unknown_key(self):
        r = self.client.put(PREFS_URL, headers=self.auth, json={"bogus": "x"})
        self.assertEqual(r.status_code, 422)

    def test_put_rejects_invalid_value(self):
        r = self.client.put(PREFS_URL, headers=self.auth, json={"theme": "neon"})
        self.assertEqual(r.status_code, 422)

    def test_invalid_put_leaves_record_untouched(self):
        self.client.put(PREFS_URL, headers=self.auth, json={"theme": "hull"})
        self.client.put(PREFS_URL, headers=self.auth, json={"theme": "neon"})
        got = self.client.get(PREFS_URL, headers=self.auth).json()
        self.assertEqual(got, {"theme": "hull"})

    def test_concurrent_puts_from_two_devices_both_survive(self):
        """Regression: the merge must be atomic under concurrency. Two devices
        PUTting different keys at the same time must BOTH land — a lost update
        here silently discards one device's saved preference."""
        import threading

        rounds = 20
        for i in range(rounds):
            theme = "hull" if i % 2 else "daylight"
            size = "125" if i % 2 else "112"
            barrier = threading.Barrier(2)
            errors: list[str] = []

            def put(payload: dict):
                barrier.wait()
                r = self.client.put(PREFS_URL, headers=self.auth, json=payload)
                if r.status_code != 200:
                    errors.append(f"{payload} -> {r.status_code}")

            t1 = threading.Thread(target=put, args=({"theme": theme},))
            t2 = threading.Thread(target=put, args=({"textSize": size},))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            self.assertEqual(errors, [])
            got = self.client.get(PREFS_URL, headers=self.auth).json()
            self.assertEqual(got.get("theme"), theme, f"round {i}: theme lost")
            self.assertEqual(got.get("textSize"), size, f"round {i}: textSize lost")

    def test_corrupt_stored_record_recovers(self):
        self.db.set_setting("ui_preferences", "not-json{{", actor="test")
        r = self.client.put(PREFS_URL, headers=self.auth, json={"theme": "daylight"})
        self.assertEqual(r.status_code, 200)
        got = self.client.get(PREFS_URL, headers=self.auth).json()
        self.assertEqual(got.get("theme"), "daylight")


if __name__ == "__main__":
    unittest.main()
