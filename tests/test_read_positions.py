"""Cross-device Read Aloud position sync tests.

Read Aloud listening positions are stored server-side per document so a spot
saved on one device (phone) is visible on another (desktop). These cover the
GET/PUT/DELETE endpoints and the underlying DB upsert.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class ReadPositionApiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_get_missing_returns_null(self):
        r = self.client.get("/api/library/nope/read-position", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["position"])

    def test_put_then_get_roundtrip(self):
        body = {"part": 3, "time": 42.5, "part_count": 12, "saved_at": 1786400000000}
        r = self.client.put("/api/library/doc1/read-position", json=body, headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])

        pos = self.client.get("/api/library/doc1/read-position", headers=AUTH_HEADERS).json()["position"]
        self.assertEqual(pos["part"], 3)
        self.assertEqual(pos["time"], 42.5)
        self.assertEqual(pos["part_count"], 12)
        self.assertEqual(pos["saved_at"], 1786400000000)

    def test_put_upserts_single_row(self):
        first = {"part": 1, "time": 5.0, "part_count": 8, "saved_at": 1}
        second = {"part": 4, "time": 9.0, "part_count": 8, "saved_at": 2}
        self.client.put("/api/library/doc2/read-position", json=first, headers=AUTH_HEADERS)
        self.client.put("/api/library/doc2/read-position", json=second, headers=AUTH_HEADERS)
        pos = self.client.get("/api/library/doc2/read-position", headers=AUTH_HEADERS).json()["position"]
        self.assertEqual(pos["part"], 4)
        self.assertEqual(pos["saved_at"], 2)
        # Exactly one row for the document.
        self.assertEqual(self.db.read_conn().execute(
            "SELECT COUNT(*) AS c FROM read_positions WHERE doc_id='doc2'"
        ).fetchone()["c"], 1)

    def test_delete_clears(self):
        body = {"part": 2, "time": 1.0, "part_count": 5, "saved_at": 1}
        self.client.put("/api/library/doc3/read-position", json=body, headers=AUTH_HEADERS)
        r = self.client.delete("/api/library/doc3/read-position", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        pos = self.client.get("/api/library/doc3/read-position", headers=AUTH_HEADERS).json()["position"]
        self.assertIsNone(pos)

    def test_rejects_invalid_values(self):
        for bad in (
            {"part": -1, "time": 1.0, "part_count": 5, "saved_at": 1},
            {"part": 0, "time": -2.0, "part_count": 5, "saved_at": 1},
            {"part": 0, "time": 1.0, "part_count": 0, "saved_at": 1},
        ):
            r = self.client.put("/api/library/doc4/read-position", json=bad, headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 400, bad)

    def test_db_delete_missing_is_noop(self):
        # Deleting a position that was never stored must not raise.
        self.db.delete_read_position("never-existed")

    def test_stale_write_is_dropped(self):
        # Freshest-wins: an out-of-order PUT with an older saved_at must not
        # overwrite a newer stored position.
        newer = {"part": 5, "time": 10.0, "part_count": 20, "saved_at": 200}
        older = {"part": 1, "time": 2.0, "part_count": 20, "saved_at": 100}
        self.client.put("/api/library/doc5/read-position", json=newer, headers=AUTH_HEADERS)
        self.client.put("/api/library/doc5/read-position", json=older, headers=AUTH_HEADERS)
        pos = self.client.get("/api/library/doc5/read-position", headers=AUTH_HEADERS).json()["position"]
        self.assertEqual(pos["part"], 5)
        self.assertEqual(pos["saved_at"], 200)

    def test_equal_or_newer_write_applies(self):
        base = {"part": 1, "time": 2.0, "part_count": 20, "saved_at": 100}
        same_ts = {"part": 7, "time": 8.0, "part_count": 20, "saved_at": 100}
        self.client.put("/api/library/doc6/read-position", json=base, headers=AUTH_HEADERS)
        # Equal saved_at still applies (idempotent same-tick update is harmless).
        self.client.put("/api/library/doc6/read-position", json=same_ts, headers=AUTH_HEADERS)
        pos = self.client.get("/api/library/doc6/read-position", headers=AUTH_HEADERS).json()["position"]
        self.assertEqual(pos["part"], 7)


if __name__ == "__main__":
    unittest.main()
