"""Tests for per-Work cover images.

Covers:
- POST /api/works/{id}/cover — upload happy path (PNG) updates works.cover_path
- Upload rejects unknown extensions (415) and magic-byte mismatches (415)
- Upload rejects empty files (422) and unknown Works (404)
- Replacing a cover with a different extension removes the old file
- GET /api/works/{id}/cover — serves the file; 404 when none or work missing
- DELETE /api/works/{id}/cover — removes the file and clears cover_path
- Work dicts from list_works/get_work expose cover_path
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# 1x1 transparent PNG (valid magic bytes + content)
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
_JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


class TestWorkCover(unittest.TestCase):
    def test_upload_get_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            work = db.create_work(title="Covered Work")
            client = TestClient(app)

            # No cover yet → 404
            r = client.get(f"/api/works/{work['id']}/cover", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 404)

            # Upload PNG
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("cover.png", _PNG_BYTES, "image/png")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()["work"]
            self.assertEqual(body["cover_path"], f"covers/{work['id']}.png")
            self.assertTrue((Path(tmp) / "covers" / f"{work['id']}.png").is_file())

            # get_work / list_works expose cover_path
            self.assertEqual(db.get_work(work["id"])["cover_path"], f"covers/{work['id']}.png")
            listed = [w for w in db.list_works() if w["id"] == work["id"]]
            self.assertEqual(listed[0]["cover_path"], f"covers/{work['id']}.png")

            # Serve it back
            r = client.get(f"/api/works/{work['id']}/cover", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["content-type"], "image/png")
            self.assertEqual(r.content, _PNG_BYTES)

            # Delete clears file + column
            r = client.delete(f"/api/works/{work['id']}/cover", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(r.json()["work"]["cover_path"])
            self.assertFalse((Path(tmp) / "covers" / f"{work['id']}.png").exists())
            r = client.get(f"/api/works/{work['id']}/cover", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 404)
            db.close()

    def test_replace_with_other_extension_removes_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            work = db.create_work(title="Recover")
            client = TestClient(app)
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("a.png", _PNG_BYTES, "image/png")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 200, r.text)
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("b.jpg", _JPEG_BYTES, "image/jpeg")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 200, r.text)
            covers = Path(tmp) / "covers"
            self.assertFalse((covers / f"{work['id']}.png").exists())
            self.assertTrue((covers / f"{work['id']}.jpg").is_file())
            self.assertEqual(r.json()["work"]["cover_path"], f"covers/{work['id']}.jpg")
            db.close()

    def test_upload_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            work = db.create_work(title="Validated")
            client = TestClient(app)

            # Unknown extension
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("cover.gif", b"GIF89a" + b"\x00" * 10, "image/gif")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 415)

            # Magic-byte mismatch (claims .png, is JPEG)
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("fake.png", _JPEG_BYTES, "image/png")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 415)

            # Empty file
            r = client.post(
                f"/api/works/{work['id']}/cover",
                files={"file": ("empty.png", b"", "image/png")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 422)

            # Unknown work
            r = client.post(
                "/api/works/nope/cover",
                files={"file": ("cover.png", _PNG_BYTES, "image/png")},
                headers=AUTH_HEADERS,
            )
            self.assertEqual(r.status_code, 404)
            r = client.get("/api/works/nope/cover", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 404)
            db.close()


if __name__ == "__main__":
    unittest.main()
