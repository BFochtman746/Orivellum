"""
TTS concat failure acceptance tests — Task #718.

Confirms that when ffmpeg concat fails (returncode=1) after individual segments
synthesise successfully the server:
  1. Does NOT crash — a 'done' event is always emitted.
  2. Does NOT emit a 'concat' event (client falls back to last-segment URI).
  3. Does NOT include 'concat_path' in the 'done' event.
  4. Still emits valid 'segment' events so the user heard audio even without a merged file.

Also covers:
  5. Single-segment: concat event IS emitted (path reused, no ffmpeg needed).
  6. Empty concat file (size=0): treated as failure — no concat event.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-1234567890abcdef")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

from tests.conftest import AUTH_HEADERS  # noqa: E402

try:
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB

    _DEPS_OK = True
    _MISSING = ""
except Exception as _e:
    _DEPS_OK = False
    _MISSING = str(_e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_sse(body: str) -> list[dict]:
    """Extract all data: … frames from a buffered SSE body."""
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _make_client(tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = OrivellumDB(str(data_dir / "test.db"))
    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
    )
    _deps.init(db=db, cfg=cfg)
    app = create_app()
    return TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS)


async def _fake_synth_stub(
    text: str, voice: str, speed: float, out_dir: Path, cfg, quality: str = "final"
) -> Path:
    """Minimal synthesis mock: writes a small stub file (no ffmpeg needed).

    The concat step in _stream_tts_events only needs these files to exist in
    ok_paths so it can build the concat list.  We're testing the concat step
    itself (not audio quality), so real MP3 content is not required.
    """
    import secrets

    p = Path(out_dir) / f"seg_{secrets.token_hex(4)}.mp3"
    # Write a trivially small fake MP3 header so stat().st_size > 0
    p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 256)
    return p


def _make_concat_fail_run(*args, **kwargs) -> MagicMock:
    """subprocess.run replacement that always returns returncode=1."""
    m = MagicMock()
    m.returncode = 1
    m.stderr = b"ffmpeg: mocked concat failure"
    m.stdout = b""
    return m


# ── Multi-segment text fixture (≥ 2 segments at max_chars=900) ───────────────

_LONG_TEXT = ("The ancient library stretched across marble halls. " * 60).strip()[:2100]


# ---------------------------------------------------------------------------
# Test class A — ffmpeg concat fails (returncode=1)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestConcatFailurePath(unittest.TestCase):
    """Confirms the share-button fallback contract when ffmpeg concat fails."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        # Drain the shared background executor first: the TTS stream submits
        # fire-and-forget registration jobs (_register_output_bg) that keep
        # writing into this test's temp data dir after the response finishes.
        # Without the drain, TemporaryDirectory cleanup races those writes
        # (OSError: Directory not empty).  ignore_cleanup_errors covers the
        # rare untracked fallback thread spawned if a job submits new work
        # while the pool is shutting down.
        from orivellum.api import executor as _exec

        _exec.shutdown(wait=True)  # lazily re-created by the next test
        self._tmp.cleanup()

    def _post_multi_segment(self) -> list[dict]:
        """Stream a long text; synthesis mocked, ffmpeg concat mocked to fail."""
        with (
            patch(
                "orivellum.api.routes.studio._synthesize_text_to_mp3",
                side_effect=_fake_synth_stub,
            ),
            patch(
                "orivellum.api.routes.studio.subprocess.run",
                side_effect=_make_concat_fail_run,
            ),
        ):
            resp = self.client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_sarah", "speed": 1.0, "stream": True},
            )
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        return _parse_sse(resp.text)

    def test_done_event_always_emitted(self):
        """Server must emit done even when concat fails — no silent hang."""
        events = self._post_multi_segment()
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(
            len(done_events),
            1,
            f"Expected exactly 1 done event; got {len(done_events)}\n"
            f"All events: {[e.get('type') for e in events]}",
        )

    def test_no_concat_event_when_ffmpeg_fails(self):
        """concat event must NOT be emitted when ffmpeg returns non-zero."""
        events = self._post_multi_segment()
        concat_events = [e for e in events if e.get("type") == "concat"]
        self.assertEqual(
            len(concat_events),
            0,
            f"Unexpected concat event(s) emitted despite ffmpeg failure: {concat_events}",
        )

    def test_done_has_no_concat_path(self):
        """done event must not carry concat_path — the mobile UI reads this to detect failure."""
        events = self._post_multi_segment()
        done = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done, "done event not found")
        self.assertNotIn(
            "concat_path",
            done,
            f"concat_path must be absent when concat failed; got: {done}",
        )

    def test_segment_events_present_despite_concat_failure(self):
        """At least one segment event must still be present — audio was synthesised."""
        events = self._post_multi_segment()
        seg_events = [e for e in events if e.get("type") == "segment" and e.get("ok")]
        self.assertGreater(
            len(seg_events),
            0,
            "Expected ≥1 successful segment events even when concat fails",
        )

    def test_segment_paths_non_empty(self):
        """Segment paths must be non-empty strings (mobile builds share URL from them)."""
        events = self._post_multi_segment()
        for evt in events:
            if evt.get("type") == "segment" and evt.get("ok"):
                self.assertTrue(
                    evt.get("path", ""),
                    f"segment event has empty path: {evt}",
                )

    def test_done_ok_count_positive(self):
        """done.ok_count must be > 0 — synthesis succeeded even though concat didn't."""
        events = self._post_multi_segment()
        done = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done)
        self.assertGreater(
            done.get("ok_count", 0),
            0,
            f"done.ok_count must be > 0 when segments succeeded; got: {done}",
        )

    def test_done_emitted_after_segment_events(self):
        """done must be the last event — ordering matters for the mobile state machine."""
        events = self._post_multi_segment()
        if not events:
            self.fail("No SSE events received")
        self.assertEqual(
            events[-1].get("type"),
            "done",
            f"Last event must be 'done'; got '{events[-1].get('type')}'",
        )


# ---------------------------------------------------------------------------
# Test class B — single segment: concat event IS emitted (no ffmpeg needed)
# ---------------------------------------------------------------------------


@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestConcatSingleSegmentPath(unittest.TestCase):
    """Single-segment text: server reuses the segment path; concat event emitted."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        # Drain the shared background executor first: the TTS stream submits
        # fire-and-forget registration jobs (_register_output_bg) that keep
        # writing into this test's temp data dir after the response finishes.
        # Without the drain, TemporaryDirectory cleanup races those writes
        # (OSError: Directory not empty).  ignore_cleanup_errors covers the
        # rare untracked fallback thread spawned if a job submits new work
        # while the pool is shutting down.
        from orivellum.api import executor as _exec

        _exec.shutdown(wait=True)  # lazily re-created by the next test
        self._tmp.cleanup()

    # Short text = exactly 1 segment (well under 900 chars)
    _SHORT_TEXT = "The traveller arrived at dusk, weary but hopeful."

    def _post_single_segment(self) -> list[dict]:
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fake_synth_stub,
        ):
            resp = self.client.post(
                "/api/studio/tts",
                json={"text": self._SHORT_TEXT, "voice": "af_sarah", "speed": 1.0, "stream": True},
            )
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        return _parse_sse(resp.text)

    def test_concat_event_emitted_for_single_segment(self):
        """Server must emit concat event (path reuse) so the share button gets a URI."""
        events = self._post_single_segment()
        concat_events = [e for e in events if e.get("type") == "concat"]
        self.assertEqual(
            len(concat_events),
            1,
            f"Expected 1 concat event for single-segment text; got {len(concat_events)}\n"
            f"All event types: {[e.get('type') for e in events]}",
        )

    def test_concat_event_has_path(self):
        events = self._post_single_segment()
        concat = next((e for e in events if e.get("type") == "concat"), None)
        self.assertIsNotNone(concat, "No concat event found")
        self.assertTrue(concat.get("path"), f"concat.path is empty: {concat}")

    def test_done_includes_concat_path(self):
        """done backward-compat field concat_path must be set for single-segment success."""
        events = self._post_single_segment()
        done = next((e for e in events if e.get("type") == "done"), None)
        self.assertIsNotNone(done)
        self.assertIn(
            "concat_path",
            done,
            f"done must carry concat_path for single-segment success; got: {done}",
        )

    def test_concat_ok_flag_true(self):
        events = self._post_single_segment()
        concat = next((e for e in events if e.get("type") == "concat"), None)
        self.assertIsNotNone(concat)
        self.assertTrue(concat.get("ok"), f"concat.ok must be True; got: {concat}")


# ---------------------------------------------------------------------------
# Test class C — empty concat output (size=0) is treated as failure
# ---------------------------------------------------------------------------


@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestConcatEmptyOutputFallback(unittest.TestCase):
    """When ffmpeg exits 0 but the concat file is 0 bytes, treat it as failure."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        # Drain the shared background executor first: the TTS stream submits
        # fire-and-forget registration jobs (_register_output_bg) that keep
        # writing into this test's temp data dir after the response finishes.
        # Without the drain, TemporaryDirectory cleanup races those writes
        # (OSError: Directory not empty).  ignore_cleanup_errors covers the
        # rare untracked fallback thread spawned if a job submits new work
        # while the pool is shutting down.
        from orivellum.api import executor as _exec

        _exec.shutdown(wait=True)  # lazily re-created by the next test
        self._tmp.cleanup()

    def _post_with_empty_concat(self) -> list[dict]:
        """ffmpeg succeeds (returncode=0) but produces an empty file."""

        def _zero_byte_run(*args, **kwargs) -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            m.stderr = b""
            m.stdout = b""
            # The concat_mp3 path is the last positional arg to ffmpeg.
            # Write 0 bytes to it so stat().st_size == 0.
            try:
                cmd = args[0] if args else kwargs.get("args", [])
                if isinstance(cmd, (list, tuple)):
                    out_file = str(cmd[-1])
                    Path(out_file).write_bytes(b"")
            except Exception:
                pass
            return m

        with (
            patch(
                "orivellum.api.routes.studio._synthesize_text_to_mp3",
                side_effect=_fake_synth_stub,
            ),
            patch(
                "orivellum.api.routes.studio.subprocess.run",
                side_effect=_zero_byte_run,
            ),
        ):
            resp = self.client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_sarah", "speed": 1.0, "stream": True},
            )
        self.assertEqual(resp.status_code, 200, resp.text[:400])
        return _parse_sse(resp.text)

    def test_no_concat_event_when_output_is_empty(self):
        """Empty output file must be suppressed — not advertised as a valid concat."""
        events = self._post_with_empty_concat()
        concat_events = [e for e in events if e.get("type") == "concat"]
        self.assertEqual(
            len(concat_events),
            0,
            f"concat event must not be emitted when output file is 0 bytes: {concat_events}",
        )

    def test_done_event_emitted_despite_empty_concat(self):
        events = self._post_with_empty_concat()
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(
            len(done_events), 1, "done must still be emitted when concat output is empty"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
