"""Microphone-recording upload format tests (POST /api/studio/transcribe).

Browser MediaRecorder captures arrive as .webm (Chrome/Firefox); the upload
route must accept them like any other audio format. The background worker is
mocked — these tests only cover the request-validation layer.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

from orivellum.api.routes import studio as studio_routes

# Minimal EBML header — enough for the magic-byte signature check.
_WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class TranscribeWebmUploadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        self._jobs_patch = mock.patch.object(studio_routes, "_transcribe_jobs", {})
        self._jobs_patch.start()
        # Never run the real worker — validation is all we test here.
        self._worker_patch = mock.patch.object(
            studio_routes, "_run_transcribe_job", lambda *a, **k: None)
        self._worker_patch.start()

    def tearDown(self):
        self._worker_patch.stop()
        self._jobs_patch.stop()
        self._tmp.cleanup()

    def _post(self, client, filename: str, content: bytes):
        return client.post(
            "/api/studio/transcribe",
            files={"file": (filename, content, "application/octet-stream")},
            data={"save_to_library": "false"},
        )

    def test_webm_recording_accepted(self):
        from orivellum.api import _deps
        with TestClient(self.app, headers=AUTH_HEADERS) as client:
            _deps.init(db=self.db, cfg=self.cfg)
            r = self._post(client, "recording-2026-08-10.webm", _WEBM_BYTES)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("job_id", r.json())

    def test_webm_mislabeled_content_rejected(self):
        """A .webm name with non-EBML bytes must fail the magic-byte gate."""
        from orivellum.api import _deps
        with TestClient(self.app, headers=AUTH_HEADERS) as client:
            _deps.init(db=self.db, cfg=self.cfg)
            r = self._post(client, "recording.webm", b"not really webm data")
        self.assertEqual(r.status_code, 415)

    def test_unsupported_extension_still_rejected(self):
        from orivellum.api import _deps
        with TestClient(self.app, headers=AUTH_HEADERS) as client:
            _deps.init(db=self.db, cfg=self.cfg)
            r = self._post(client, "clip.txt", b"hello")
        self.assertEqual(r.status_code, 422)
