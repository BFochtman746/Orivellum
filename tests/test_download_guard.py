"""FA-02 — /api/download path-guard tests.

The download endpoint must (a) reject traversal including prefix-sibling
bypass, (b) serve only the allowlisted user-content subtrees, and (c) never
serve DB files or key material even inside allowed subtrees.
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
    db = OrivellumDB(str(Path(tmp) / "orivellum.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class DownloadGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(self.app)
        # Servable content
        (self.data_dir / "library").mkdir(exist_ok=True)
        (self.data_dir / "library" / "doc.txt").write_text("hello")
        (self.data_dir / "outputs").mkdir(exist_ok=True)
        (self.data_dir / "outputs" / "render.mp3").write_bytes(b"a" * 10)
        # Sensitive content that must never be served
        (self.data_dir / "api_key.txt").write_text("SECRET")
        (self.data_dir / "library" / "sneaky.db").write_bytes(b"sqlite")
        # Prefix-sibling dir to test the old startswith bypass
        sibling = Path(self._tmp.name + "_sibling")
        sibling.mkdir(exist_ok=True)
        (sibling / "leak.txt").write_text("leaked")
        self._sibling = sibling

    def tearDown(self):
        import shutil
        shutil.rmtree(self._sibling, ignore_errors=True)
        self._tmp.cleanup()

    def _get(self, path: str):
        return self.client.get(f"/api/download/{path}", headers=AUTH_HEADERS)

    def test_allowed_library_file_serves(self):
        r = self._get("library/doc.txt")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"hello")

    def test_allowed_outputs_file_serves(self):
        self.assertEqual(self._get("outputs/render.mp3").status_code, 200)

    def test_data_dir_root_file_denied(self):
        # api_key.txt sits at the data-dir root — outside every allowed subtree.
        self.assertEqual(self._get("api_key.txt").status_code, 403)

    def test_sqlite_db_denied(self):
        self.assertEqual(self._get("orivellum.db").status_code, 403)

    def test_db_extension_denied_even_in_allowed_subtree(self):
        self.assertEqual(self._get("library/sneaky.db").status_code, 403)

    def test_traversal_denied(self):
        # Literal "../" is normalized away by HTTP clients before the server
        # sees it — attackers percent-encode it, which flows through to the
        # {path:path} param and must be caught by the resolver guard.
        self.assertEqual(self._get("..%2Fetc%2Fpasswd").status_code, 403)

    def test_prefix_sibling_bypass_denied(self):
        # Old guard was startswith(data_dir) — a sibling like
        # "<data_dir>_sibling" shares the string prefix and used to pass.
        rel = "..%2F" + self._sibling.name + "%2Fleak.txt"
        self.assertEqual(self._get(rel).status_code, 403)

    def test_resolver_rejects_traversal_directly(self):
        # Belt-and-braces: exercise the resolver itself, independent of any
        # client/proxy URL normalization.
        from fastapi import HTTPException
        from orivellum.api.routes import files as files_routes
        for bad in ("../etc/passwd", "../" + self._sibling.name + "/leak.txt"):
            with self.assertRaises(HTTPException) as ctx:
                files_routes._resolve_within_data_dir(bad)
            self.assertEqual(ctx.exception.status_code, 403)

    def test_missing_file_in_allowed_subtree_404s(self):
        self.assertEqual(self._get("library/nope.txt").status_code, 404)


if __name__ == "__main__":
    unittest.main()
