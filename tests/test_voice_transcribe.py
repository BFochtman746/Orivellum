"""Tests for the synchronous voice quick-transcribe route
(POST /api/studio/voice/transcribe — chat voice mode).

The transcription engines are never invoked — extraction.extract is mocked.
Coverage:

  - format gate: unsupported extension → 422
  - empty upload → 422
  - magic-byte validation: mislabeled .wav content → 415; valid webm passes
  - no engine available (metadata-only result) → 503
  - success path: transcript text/engine/word_count/duration/language returned
  - oversized clip → 413
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from orivellum.api.routes import studio as studio_routes
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


from contextlib import contextmanager


@contextmanager
def _client(app, db, cfg):
    """TestClient whose lifespan-driven _deps re-init is overridden with the
    test's own db/cfg (the app lifespan wires the real ones on startup)."""
    from orivellum.api import _deps

    with TestClient(app) as client:
        _deps.init(db=db, cfg=cfg)
        yield client


# Minimal valid magic-byte payloads
_WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 64  # EBML header
_WAV_BYTES = b"RIFF" + b"\x00" * 64


def _fake_result(
    text: str = "hello world",
    engine: str | None = "faster-whisper (base)",
    duration: float | None = 1.5,
    language: str | None = "en",
):
    from orivellum.capabilities.extraction import ExtractionResult, PageSegment

    meta: dict = {}
    if engine:
        meta["transcription"] = engine
    else:
        meta["reason"] = "No transcription engine available"
    if duration is not None:
        meta["duration"] = duration
    if language:
        meta["language"] = language
    return ExtractionResult(
        kind="audio",
        full_text=f"[Audio transcript: clip]\n\n{text}",
        word_count=len(text.split()),
        pages=[PageSegment(page=1, text=text)],
        meta=meta,
    )


class VoiceTranscribeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _post(self, client, filename: str, content: bytes):
        return client.post(
            "/api/studio/voice/transcribe",
            files={"file": (filename, content, "application/octet-stream")},
            headers=AUTH_HEADERS,
        )

    def test_unsupported_extension_422(self):
        with _client(self.app, self.db, self.cfg) as client:
            r = self._post(client, "clip.txt", b"not audio")
        self.assertEqual(r.status_code, 422)
        self.assertIn("Unsupported audio format", r.json()["detail"])

    def test_empty_upload_422(self):
        with _client(self.app, self.db, self.cfg) as client:
            r = self._post(client, "clip.webm", b"")
        self.assertEqual(r.status_code, 422)

    def test_magic_byte_mismatch_415(self):
        with _client(self.app, self.db, self.cfg) as client:
            r = self._post(client, "clip.wav", b"THIS IS NOT A WAV FILE AT ALL....")
        self.assertEqual(r.status_code, 415)

    def test_webm_signature_accepted(self):
        with (
            mock.patch(
                "orivellum.capabilities.extraction.extract", return_value=_fake_result()
            ) as m,
            _client(self.app, self.db, self.cfg) as client,
        ):
            r = self._post(client, "clip.webm", _WEBM_BYTES)
        self.assertEqual(r.status_code, 200)
        m.assert_called_once()

    def test_no_engine_503(self):
        with (
            mock.patch(
                "orivellum.capabilities.extraction.extract", return_value=_fake_result(engine=None)
            ),
            _client(self.app, self.db, self.cfg) as client,
        ):
            r = self._post(client, "clip.wav", _WAV_BYTES)
        self.assertEqual(r.status_code, 503)
        self.assertIn("Transcription unavailable", r.json()["detail"])

    def test_success_returns_transcript_fields(self):
        with (
            mock.patch(
                "orivellum.capabilities.extraction.extract",
                return_value=_fake_result(text="the quick brown fox"),
            ),
            _client(self.app, self.db, self.cfg) as client,
        ):
            r = self._post(client, "clip.webm", _WEBM_BYTES)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Pages carry the raw transcript, not the "[Audio transcript…]" header
        self.assertEqual(body["text"], "the quick brown fox")
        self.assertEqual(body["engine"], "faster-whisper (base)")
        self.assertEqual(body["word_count"], 4)
        self.assertEqual(body["duration_sec"], 1.5)
        self.assertEqual(body["language"], "en")

    def test_oversized_clip_413(self):
        big = _WEBM_BYTES + b"\x00" * (studio_routes._MAX_VOICE_BYTES + 1)
        with _client(self.app, self.db, self.cfg) as client:
            r = self._post(client, "clip.webm", big)
        self.assertEqual(r.status_code, 413)

    def test_temp_files_cleaned_up(self):
        with mock.patch("orivellum.capabilities.extraction.extract", return_value=_fake_result()):
            with _client(self.app, self.db, self.cfg) as client:
                r = self._post(client, "clip.webm", _WEBM_BYTES)
        self.assertEqual(r.status_code, 200)
        import glob

        leftovers = glob.glob(str(Path(tempfile.gettempdir()) / "orv-voice-*"))
        # Directories may exist from other runs; ensure none contain our clip
        for d in leftovers:
            self.assertFalse(list(Path(d).glob("clip.webm")), f"temp clip not cleaned up in {d}")


if __name__ == "__main__":
    unittest.main()
