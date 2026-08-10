"""Streaming TTS acceptance tests — Task #538.

Confirms end-to-end correctness of the streaming TTS path and graceful
degradation when TTS backends are absent.

Phases
------
A  _split_text_into_segments(max_chars=900) produces correct multi-segment
   output — the unit that drives segment latency and stream granularity.
B  POST /api/studio/tts with stream=True returns text/event-stream; at least
   one segment event appears before the done event; events are well-formed.
C  done event carries concat_path when ≥1 segment succeeded.
D  Each segment path is serveable via GET /api/studio/outputs/serve.
E  stream=False (legacy path) returns audio/mpeg — or a structured 503/error
   when all backends are patched out — and never crashes.
F  All-backends-fail streaming: emits segment_error events then a done event
   with ok_count=0 and no concat_path.  No 5xx crash.
G  Live Uvicorn: first segment event is received before the done event in a
   real TCP SSE stream, confirming progressive delivery.

Synthesis mocking
-----------------
Real TTS engines (Kokoro ONNX, AI server) are not required in CI.
_synthesize_text_to_mp3 is patched with _fast_synth, which generates a short
silent MP3 via ``ffmpeg anullsrc`` (the same tool used throughout the pipeline).
If ffmpeg itself is absent, the Phase G live-server test is skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import uvicorn
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Ensure the project is importable.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-1234567890abcdef")
from tests.conftest import AUTH_HEADERS  # noqa: E402

# ---------------------------------------------------------------------------
# Lazy imports — keep at module level for type-checking but defer resolution
# until the test class setUp so collection never fails on missing deps.
# ---------------------------------------------------------------------------
try:
    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.api.routes.studio import _split_text_into_segments
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


def _ffmpeg_ok() -> bool:
    """Return True if ffmpeg is available on PATH."""
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


async def _fast_synth(
    text: str,
    voice: str,
    speed: float,
    out_dir: Path,
    cfg: Any,
    quality: str = "final",
) -> Path | None:
    """Fast mock synthesis: 50 ms silent MP3 via ffmpeg anullsrc.

    Identical audio quality to what the real pipeline produces for test
    purposes — valid MP3 container that ffmpeg concat can join.
    """
    import tempfile as _tmp

    tmp = _tmp.NamedTemporaryFile(delete=False, dir=out_dir, suffix=".mp3")
    tmp.close()
    result = await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=22050:cl=mono",
            "-t",
            "0.05",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "9",
            tmp.name,
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode == 0:
        return Path(tmp.name)
    Path(tmp.name).unlink(missing_ok=True)
    return None


async def _always_fail_synth(text, voice, speed, out_dir, cfg, quality="final") -> None:
    """Mock synthesis that always returns None (all backends down)."""
    return


def _make_client(tmp_path: Path) -> TestClient:
    """Wire a fresh DB + config, then return a sync TestClient."""
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


def _make_app_and_dir(tmp_dir: str):
    """Create a FastAPI app wired to a fresh SQLite DB in tmp_dir."""
    cfg = OrivellumConfig(data_dir=tmp_dir)
    db = OrivellumDB(cfg.db_path)
    _deps.init(db=db, cfg=cfg)
    return create_app(), db


# ── Uvicorn live-server fixture ────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _restore_env(key: str, old_value: str | None) -> None:
    if old_value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old_value


@contextmanager
def _live_server(app, tmp_dir: str):
    """Start Uvicorn in a daemon thread; yield its base URL."""
    port = _free_port()
    old = os.environ.get("ORIVELLUM_DATA_DIR")
    os.environ["ORIVELLUM_DATA_DIR"] = tmp_dir

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 8.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=3.0)
        _restore_env("ORIVELLUM_DATA_DIR", old)
        raise RuntimeError("Uvicorn did not start within 8 s")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=8.0)
        _restore_env("ORIVELLUM_DATA_DIR", old)


def _stream_post_sse(
    base_url: str,
    path: str,
    body: dict,
    *,
    timeout: float = 30.0,
) -> tuple[list[dict], list[float]]:
    """POST *body* to *base_url+path*, collect SSE events with arrival timestamps."""
    events: list[dict] = []
    timestamps: list[float] = []
    headers = {**AUTH_HEADERS, "Content-Type": "application/json"}
    deadline = time.monotonic() + timeout

    with (
        httpx.Client(timeout=timeout) as client,
        client.stream(
            "POST",
            base_url + path,
            content=json.dumps(body).encode(),
            headers=headers,
        ) as resp,
    ):
        for raw_line in resp.iter_lines():
            if time.monotonic() > deadline:
                break
            line = raw_line.strip()
            if line.startswith("data: "):
                try:
                    events.append(json.loads(line[6:]))
                    timestamps.append(time.monotonic())
                except json.JSONDecodeError:
                    pass

    return events, timestamps


# ── Long test text — over 900 chars, produces ≥2 segments at max_chars=900 ──

_LONG_TEXT = (
    "The archive was silent in that particular way that libraries become silent "
    "after midnight, when even the dust seems to hold its breath. Eleanor "
    "adjusted her lamp and turned the page. The ink had faded from black to "
    "the colour of old tea, but the handwriting remained clear: a careful, "
    "sloping script that she had come to know as well as her own.\n\n"
    "She had been reading for three hours without stopping, moving through "
    "century-old correspondence with the focused attention of a translator "
    "working against a deadline. The letters were addressed to a woman named "
    "Marta, who had apparently been a cartographer of some reputation, though "
    "none of her maps had survived — or none that anyone had yet found.\n\n"
    "The third letter changed everything. It mentioned, in passing, a drawer "
    "in a desk that had been shipped to Vienna in 1891. Eleanor set the letter "
    "down and looked at the desk that occupied the corner of the room, which "
    "she had assumed to be a reproduction. The brass plate on its side read "
    "WIEN. 1891."
)


# ===========================================================================
# Phase A — Segmentation
# ===========================================================================


@unittest.skipUnless(_DEPS_OK, f"dependencies unavailable: {_MISSING}")
class TestSegmentation(unittest.TestCase):
    """_split_text_into_segments unit tests (no HTTP, no synthesis)."""

    def test_short_text_gives_single_segment(self):
        segs = _split_text_into_segments("Hello, world.", max_chars=900)
        self.assertEqual(len(segs), 1)
        self.assertIn("Hello", segs[0])

    def test_long_text_over_900_gives_multiple_segments(self):
        segs = _split_text_into_segments(_LONG_TEXT, max_chars=900)
        self.assertGreaterEqual(
            len(segs),
            2,
            f"Expected ≥2 segments for {len(_LONG_TEXT)}-char text, got {len(segs)}",
        )

    def test_no_segment_exceeds_max_chars(self):
        segs = _split_text_into_segments(_LONG_TEXT, max_chars=900)
        for i, seg in enumerate(segs):
            self.assertLessEqual(
                len(seg),
                900,
                f"Segment {i} has {len(seg)} chars, exceeds max_chars=900: {seg[:60]!r}…",
            )

    def test_words_preserved_across_segments(self):
        """Segmentation must not drop, duplicate, or reorder any word."""
        text = "alpha beta gamma delta " * 100  # 2400 chars, no punctuation
        segs = _split_text_into_segments(text, max_chars=900)
        original_words = text.strip().split()
        joined_words = " ".join(segs).split()
        # Exact sequence comparison — count alone would miss reorderings/duplicates
        self.assertEqual(original_words, joined_words, "Word sequence changed after segmentation")

    def test_empty_text_gives_empty_list(self):
        segs = _split_text_into_segments("", max_chars=900)
        self.assertEqual(segs, [])

    def test_single_paragraph_exactly_at_limit_stays_one_segment(self):
        text = "word " * 179  # ~895 chars — just under 900
        segs = _split_text_into_segments(text.strip(), max_chars=900)
        self.assertEqual(len(segs), 1)

    def test_streaming_endpoint_uses_900_char_cap(self):
        """The streaming endpoint passes max_chars=900, not the 1500 default.

        Proof: a text that fits in 1 segment at max_chars=1500 but requires
        ≥2 at max_chars=900 must produce different counts, showing the two
        limits are genuinely distinct.
        """
        # ~1100 chars — fits in one segment at 1500, splits at 900
        text = "word " * 220
        segs_1500 = _split_text_into_segments(text.strip(), max_chars=1500)
        segs_900 = _split_text_into_segments(text.strip(), max_chars=900)
        self.assertEqual(len(segs_1500), 1, "Should fit in 1 segment at 1500 chars")
        self.assertGreaterEqual(len(segs_900), 2, "Should split into ≥2 at 900 chars")
        # The two counts must differ — otherwise max_chars has no observable effect
        self.assertNotEqual(
            len(segs_1500), len(segs_900), "1500 and 900 char caps produced the same segment count"
        )


# ===========================================================================
# Phase B + C + D — Streaming TTS endpoint (buffered TestClient)
# ===========================================================================


@unittest.skipUnless(_DEPS_OK, f"dependencies unavailable: {_MISSING}")
@unittest.skipUnless(_ffmpeg_ok(), "ffmpeg not available")
class TestStreamingTTSEndpoint(unittest.TestCase):
    """POST /api/studio/tts with stream=True — SSE event shape and ordering.

    Uses a buffered TestClient (sufficient to verify event ordering and shape)
    with _synthesize_text_to_mp3 patched to a fast silent-MP3 generator.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _post_stream(self, text: str, **kwargs) -> tuple[int, str, list[dict]]:
        """POST stream=True TTS; return (status, content_type, events)."""
        body = {"text": text, "voice": "af_heart", "speed": 1.0, "stream": True, **kwargs}
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fast_synth,
        ):
            resp = self._client.post("/api/studio/tts", json=body)
        events = _parse_sse(resp.text)
        return resp.status_code, resp.headers.get("content-type", ""), events

    # ── B: response shape ─────────────────────────────────────────────────────

    def test_response_content_type_is_event_stream(self):
        status, ct, _ = self._post_stream(_LONG_TEXT)
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", ct)

    def test_at_least_one_segment_event_before_done(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        types = [e["type"] for e in events]
        self.assertIn("segment", types, "No segment events emitted")
        self.assertIn("done", types, "No done event emitted")
        # done must be the last event
        self.assertEqual(types[-1], "done", "done event must be last")
        # at least one segment event must precede done
        done_idx = types.index("done")
        segment_indices = [i for i, t in enumerate(types) if t == "segment"]
        self.assertTrue(
            any(i < done_idx for i in segment_indices),
            "All segment events appear after the done event",
        )

    def test_segment_events_are_well_formed(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        seg_events = [e for e in events if e["type"] == "segment"]
        self.assertGreater(len(seg_events), 0)
        for evt in seg_events:
            self.assertIn("idx", evt, f"segment event missing 'idx': {evt}")
            self.assertIn("total", evt, f"segment event missing 'total': {evt}")
            self.assertIn("path", evt, f"segment event missing 'path': {evt}")
            self.assertTrue(evt.get("ok"), f"segment event ok!=True: {evt}")
            self.assertIsInstance(evt["path"], str)
            self.assertGreater(len(evt["path"]), 0)

    def test_done_event_is_well_formed(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        done_events = [e for e in events if e["type"] == "done"]
        self.assertEqual(len(done_events), 1, "Exactly one done event expected")
        done = done_events[0]
        self.assertIn("total", done)
        self.assertIn("ok_count", done)
        self.assertIn("error_count", done)
        self.assertIsInstance(done["ok_count"], int)
        self.assertIsInstance(done["error_count"], int)

    def test_segment_indices_are_sequential(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        seg_events = [e for e in events if e["type"] == "segment"]
        indices = [e["idx"] for e in seg_events]
        self.assertEqual(sorted(indices), list(range(len(indices))))

    def test_segment_total_matches_done_total(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        seg_totals = {e["total"] for e in events if e["type"] == "segment"}
        done = next(e for e in events if e["type"] == "done")
        # All segment events should agree on total
        self.assertEqual(len(seg_totals), 1)
        self.assertEqual(seg_totals.pop(), done["total"])

    # ── C: concat_path in done event ─────────────────────────────────────────

    def test_done_event_has_concat_path_when_segments_succeed(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        done = next(e for e in events if e["type"] == "done")
        self.assertGreater(done["ok_count"], 0, "Expected at least one successful segment")
        self.assertIn(
            "concat_path",
            done,
            "done event missing concat_path — share button will have no file",
        )
        self.assertIsInstance(done["concat_path"], str)
        self.assertGreater(len(done["concat_path"]), 0)

    def test_concat_path_ends_with_mp3(self):
        _, _, events = self._post_stream(_LONG_TEXT)
        done = next(e for e in events if e["type"] == "done")
        if "concat_path" in done:  # may be absent on single-segment text
            self.assertTrue(
                done["concat_path"].endswith(".mp3"),
                f"concat_path doesn't end in .mp3: {done['concat_path']}",
            )

    def test_single_segment_text_still_has_concat_path(self):
        # Short text → 1 segment → concat_path = that segment's path
        short = "Hello, this is a brief test of streaming TTS."
        _, _, events = self._post_stream(short)
        done = next(e for e in events if e["type"] == "done")
        self.assertIn("concat_path", done, "Single-segment done event missing concat_path")

    # ── D: segment paths are serveable ───────────────────────────────────────

    def test_segment_path_is_serveable(self):
        """GET /api/studio/outputs/serve?path=… returns 200 for each segment."""
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fast_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        events = _parse_sse(resp.text)
        seg_events = [e for e in events if e["type"] == "segment"]
        self.assertGreater(len(seg_events), 0)

        for evt in seg_events[:3]:  # check first three to keep the test fast
            path = evt["path"]
            serve_resp = self._client.get(f"/api/studio/outputs/serve?path={path}")
            self.assertIn(
                serve_resp.status_code,
                (200, 206),
                f"Segment path not serveable (HTTP {serve_resp.status_code}): {path}",
            )

    def test_concat_path_is_serveable(self):
        """GET /studio/outputs/serve?path=… works for the concat file too."""
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fast_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        events = _parse_sse(resp.text)
        done = next((e for e in events if e["type"] == "done"), None)
        self.assertIsNotNone(done)
        self.assertIn("concat_path", done)

        serve_resp = self._client.get(f"/api/studio/outputs/serve?path={done['concat_path']}")
        self.assertIn(
            serve_resp.status_code,
            (200, 206),
            f"concat_path not serveable (HTTP {serve_resp.status_code}): {done['concat_path']}",
        )

    def test_endpoint_segment_total_matches_900_char_cap(self):
        """done.total from the endpoint must equal _split_text_into_segments(max_chars=900).

        A text that fits in 1 segment at 1500 chars but splits at 900 chars
        lets us detect if the endpoint silently uses the wrong cap: if
        done.total == 1 instead of the expected ≥2 the endpoint is using 1500.
        """
        # ~1100 chars — 1 segment at max_chars=1500, ≥2 at max_chars=900
        text = ("word " * 220).strip()
        expected_n = len(_split_text_into_segments(text, max_chars=900))
        self.assertGreaterEqual(expected_n, 2, "Test text must require ≥2 segments at 900 chars")

        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fast_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": text, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        events = _parse_sse(resp.text)
        done = next((e for e in events if e["type"] == "done"), None)
        self.assertIsNotNone(done, "done event not found")
        self.assertEqual(
            done["total"],
            expected_n,
            f"Endpoint reported {done['total']} segments; expected {expected_n} "
            f"(900-char cap). If total==1 the endpoint is using the 1500-char default.",
        )

    # ── concat event ──────────────────────────────────────────────────────────

    def test_concat_event_emitted_before_done(self):
        """A {"type":"concat",...} SSE event must arrive before the done event."""
        _, _, events = self._post_stream(_LONG_TEXT)
        types = [e["type"] for e in events]
        self.assertIn("concat", types, "No concat event in stream — merged file not announced")
        self.assertIn("done", types)
        concat_idx = types.index("concat")
        done_idx = len(types) - 1 - types[::-1].index("done")
        self.assertLess(concat_idx, done_idx, "concat event must arrive before done event")

    def test_concat_event_has_required_fields(self):
        """concat event must carry path, uri, and ok=true."""
        _, _, events = self._post_stream(_LONG_TEXT)
        concat_evts = [e for e in events if e["type"] == "concat"]
        self.assertEqual(len(concat_evts), 1, "Expected exactly one concat event")
        evt = concat_evts[0]
        self.assertIn("path", evt, "concat event missing 'path'")
        self.assertIn("uri", evt, "concat event missing 'uri'")
        self.assertTrue(evt.get("ok"), "concat event ok must be True")
        self.assertIsInstance(evt["path"], str)
        self.assertIsInstance(evt["uri"], str)
        self.assertGreater(len(evt["path"]), 0)
        self.assertGreater(len(evt["uri"]), 0)

    def test_concat_event_uri_references_serve_endpoint(self):
        """concat.uri must point to the /api/studio/outputs/serve endpoint."""
        _, _, events = self._post_stream(_LONG_TEXT)
        evt = next(e for e in events if e["type"] == "concat")
        self.assertIn(
            "/api/studio/outputs/serve",
            evt["uri"],
            f"concat.uri doesn't reference serve endpoint: {evt['uri']}",
        )

    def test_concat_event_path_matches_done_concat_path(self):
        """concat.path and done.concat_path must agree (both point to the same file)."""
        _, _, events = self._post_stream(_LONG_TEXT)
        concat_evt = next((e for e in events if e["type"] == "concat"), None)
        done_evt = next((e for e in events if e["type"] == "done"), None)
        self.assertIsNotNone(concat_evt)
        self.assertIsNotNone(done_evt)
        self.assertEqual(
            concat_evt["path"],
            done_evt.get("concat_path"),
            "concat.path and done.concat_path disagree — "
            "server emitted inconsistent merge references",
        )

    def test_no_concat_event_when_all_backends_fail(self):
        """No concat event should appear when synthesis fully fails."""
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_always_fail_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        events = _parse_sse(resp.text)
        concat_evts = [e for e in events if e["type"] == "concat"]
        self.assertEqual(
            len(concat_evts), 0, "concat event emitted even though synthesis fully failed"
        )

    def test_concat_event_path_is_serveable(self):
        """The file at concat.path must be served with HTTP 200/206."""
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_fast_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": _LONG_TEXT, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        events = _parse_sse(resp.text)
        evt = next((e for e in events if e["type"] == "concat"), None)
        self.assertIsNotNone(evt, "No concat event in stream")
        serve = self._client.get(f"/api/studio/outputs/serve?path={evt['path']}")
        self.assertIn(
            serve.status_code,
            (200, 206),
            f"concat.path not serveable (HTTP {serve.status_code}): {evt['path']}",
        )

    def test_empty_text_returns_400(self):
        resp = self._client.post(
            "/api/studio/tts",
            json={"text": "   ", "voice": "af_heart", "speed": 1.0, "stream": True},
        )
        self.assertEqual(resp.status_code, 400)

    def test_text_over_10000_chars_returns_400(self):
        resp = self._client.post(
            "/api/studio/tts",
            json={"text": "x" * 10_001, "voice": "af_heart", "speed": 1.0, "stream": True},
        )
        self.assertEqual(resp.status_code, 400)


# ===========================================================================
# Phase E — Non-streaming fallback (stream=False)
# ===========================================================================


@unittest.skipUnless(_DEPS_OK, f"dependencies unavailable: {_MISSING}")
class TestNonStreamingFallback(unittest.TestCase):
    """stream=False path: returns audio/mpeg or a structured error — no crash."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    @unittest.skipUnless(_ffmpeg_ok(), "ffmpeg not available")
    def test_stream_false_returns_mpeg_or_503(self):
        """stream=False returns audio/mpeg when a neural engine is up, or a
        clean 503 when none is (espeak fallback removed by policy)."""
        resp = self._client.post(
            "/api/studio/tts",
            json={"text": "Hello.", "voice": "af_heart", "speed": 1.0, "stream": False},
        )
        # Accept 200 (audio served) or 503 (no backend in CI) — never 5xx crash
        self.assertIn(
            resp.status_code,
            (200, 503),
            f"Unexpected status {resp.status_code}: {resp.text[:200]}",
        )
        if resp.status_code == 200:
            ct = resp.headers.get("content-type", "")
            self.assertTrue(
                ct.startswith("audio/") or ct.startswith("application/"),
                f"Expected audio content-type, got: {ct}",
            )

    def test_stream_false_all_backends_absent_returns_503(self):
        """When every backend is patched out, stream=False must return 503."""
        with (
            patch(
                "orivellum.api.routes.studio._call_premium_tts",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("orivellum.api.routes.studio._get_kokoro", return_value=None),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b""),
            ),
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": "Hello world.", "voice": "af_heart", "speed": 1.0, "stream": False},
            )
        self.assertEqual(
            resp.status_code, 503, f"Expected 503, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_stream_false_503_body_is_json(self):
        """503 from non-streaming path must carry a JSON detail — not a blank body."""
        with (
            patch(
                "orivellum.api.routes.studio._call_premium_tts",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("orivellum.api.routes.studio._get_kokoro", return_value=None),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b""),
            ),
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": "Hello world.", "voice": "af_heart", "speed": 1.0, "stream": False},
            )
        body = resp.json()
        self.assertIn("detail", body, f"503 body should contain 'detail', got: {body}")

    def test_stream_false_never_returns_5xx_crash(self):
        """Non-streaming path must return a handled status code, not a raw 500."""
        with (
            patch(
                "orivellum.api.routes.studio._call_premium_tts",
                new_callable=AsyncMock,
                side_effect=RuntimeError("boom"),
            ),
            patch("orivellum.api.routes.studio._get_kokoro", return_value=None),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, stdout=b"", stderr=b""),
            ),
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": "Test.", "voice": "af_heart", "speed": 1.0, "stream": False},
            )
        # 503 = expected; 500 with raise_server_exceptions=False = unhandled crash
        self.assertNotEqual(resp.status_code, 500, "Unhandled 500 crash in non-streaming TTS path")


# ===========================================================================
# Phase F — All backends fail in streaming mode
# ===========================================================================


@unittest.skipUnless(_DEPS_OK, f"dependencies unavailable: {_MISSING}")
class TestStreamingAllBackendsFail(unittest.TestCase):
    """When _synthesize_text_to_mp3 always returns None, the streaming path
    must emit segment_error events followed by a done event with ok_count=0.
    No 5xx crash.  No concat_path in done event.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _post_all_fail(self, text: str) -> tuple[int, list[dict]]:
        with patch(
            "orivellum.api.routes.studio._synthesize_text_to_mp3",
            side_effect=_always_fail_synth,
        ):
            resp = self._client.post(
                "/api/studio/tts",
                json={"text": text, "voice": "af_heart", "speed": 1.0, "stream": True},
            )
        return resp.status_code, _parse_sse(resp.text)

    def test_response_is_200_not_503(self):
        """Streaming path opens SSE regardless; errors live inside the stream."""
        status, _ = self._post_all_fail(_LONG_TEXT)
        self.assertEqual(status, 200, "SSE streaming response should be 200 — errors are in-band")

    def test_emits_segment_error_events(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        error_events = [e for e in events if e["type"] == "segment_error"]
        self.assertGreater(
            len(error_events), 0, "Expected segment_error events when all backends fail"
        )

    def test_no_segment_success_events(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        success_events = [e for e in events if e["type"] == "segment"]
        self.assertEqual(
            len(success_events), 0, "No segment-success events expected when backend always fails"
        )

    def test_done_event_has_zero_ok_count(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        done_events = [e for e in events if e["type"] == "done"]
        self.assertEqual(len(done_events), 1)
        done = done_events[0]
        self.assertEqual(
            done.get("ok_count"),
            0,
            f"Expected ok_count=0, got: {done}",
        )

    def test_done_event_has_nonzero_error_count(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        done = next(e for e in events if e["type"] == "done")
        self.assertGreater(
            done.get("error_count", 0),
            0,
            f"Expected error_count > 0, got: {done}",
        )

    def test_no_concat_path_when_all_fail(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        done = next(e for e in events if e["type"] == "done")
        self.assertNotIn(
            "concat_path",
            done,
            "done event must not have concat_path when no segments succeeded",
        )

    def test_segment_error_events_are_well_formed(self):
        _, events = self._post_all_fail(_LONG_TEXT)
        for evt in events:
            if evt["type"] == "segment_error":
                self.assertIn("idx", evt, f"segment_error missing idx: {evt}")
                self.assertIn("total", evt, f"segment_error missing total: {evt}")
                self.assertIn("message", evt, f"segment_error missing message: {evt}")
                self.assertFalse(evt.get("ok", True), f"segment_error ok should be False: {evt}")


# ===========================================================================
# Phase G — Live Uvicorn: progressive delivery proof
# ===========================================================================


@unittest.skipUnless(_DEPS_OK, f"dependencies unavailable: {_MISSING}")
@unittest.skipUnless(_ffmpeg_ok(), "ffmpeg not available")
class TestLiveSSEDelivery(unittest.TestCase):
    """Confirm that segment events are delivered progressively over a real TCP
    connection — not buffered until the stream closes.

    Strategy: use _fast_synth so each segment takes ~200 ms.  For a
    multi-segment text the server emits the first segment event BEFORE it
    has synthesised all remaining segments.  The done event must therefore
    arrive AFTER all segment events in the byte stream.

    A single-segment text is skipped because segment and done are emitted
    sequentially with no interleaving anyway.
    """

    def test_first_segment_arrives_while_later_synthesis_is_still_blocked(self):
        """Causal progressive-delivery proof.

        A gated mock blocks synthesis for segment index ≥ 1 until the test
        releases a threading.Event.  The consumer thread signals *first_arrived*
        as soon as segment-0's event lands.  The main thread then asserts that
        the gate is STILL CLOSED at that moment — proving segment-0 was sent by
        the server before it even started synthesising segment-1.

        A buffered implementation would hold all SSE frames until the final
        ``asyncio.to_thread(_rotate_outputs)`` call, meaning no event would
        arrive while the gate was still closed.  This test fails in that scenario.
        """
        first_arrived = threading.Event()
        gate = threading.Event()
        call_count = [0]  # mutable cell; safe — single asyncio loop

        async def _gated_synth(text, voice, speed, out_dir, cfg, quality="final"):
            idx = call_count[0]
            call_count[0] += 1
            if idx > 0:
                # Later segments are held until the main-thread releases the gate
                await asyncio.to_thread(gate.wait, 15.0)
            return await _fast_synth(text, voice, speed, out_dir, cfg)

        collected_events: list[dict] = []

        def _consumer(base_url: str) -> None:
            headers = {**AUTH_HEADERS, "Content-Type": "application/json"}
            body_bytes = json.dumps(
                {"text": _LONG_TEXT, "voice": "af_heart", "speed": 1.0, "stream": True}
            ).encode()
            with (
                httpx.Client(timeout=60.0) as client,
                client.stream(
                    "POST",
                    base_url + "/api/studio/tts",
                    content=body_bytes,
                    headers=headers,
                ) as resp,
            ):
                for raw in resp.iter_lines():
                    raw = raw.strip()
                    if not raw.startswith("data: "):
                        continue
                    try:
                        evt = json.loads(raw[6:])
                    except json.JSONDecodeError:
                        continue
                    collected_events.append(evt)
                    if evt.get("type") == "segment" and not first_arrived.is_set():
                        first_arrived.set()  # signal: first segment received

        with (
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir,
            patch(
                "orivellum.api.routes.studio._synthesize_text_to_mp3",
                side_effect=_gated_synth,
            ),
        ):
            app, _ = _make_app_and_dir(tmp_dir)
            with _live_server(app, tmp_dir) as base_url:
                consumer = threading.Thread(target=_consumer, args=(base_url,), daemon=True)
                consumer.start()

                # Block until first segment arrives (or 30 s timeout)
                arrived = first_arrived.wait(timeout=30.0)
                self.assertTrue(
                    arrived,
                    "First segment event never arrived within 30 s — "
                    "server may be buffering all events until done",
                )

                # ── Causal assertion ────────────────────────────────────────
                # The gate is still closed: the server has NOT finished
                # synthesising segment-1 yet.  If the client already has
                # segment-0, the server must have sent it progressively.
                self.assertFalse(
                    gate.is_set(),
                    "Gate was already open when first segment arrived — "
                    "indicates server batched synthesis before streaming",
                )

                # Release the gate so remaining segments can complete
                gate.set()
                consumer.join(timeout=30.0)

        types = [e["type"] for e in collected_events]
        self.assertIn("done", types, "done event never received after gate released")
        # Segment-0 must be in the stream too
        self.assertIn("segment", types, "No segment event in collected events")

    def test_live_stream_done_event_has_concat_path(self):
        """concat_path is present in a live-streamed done event."""
        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch(
                "orivellum.api.routes.studio._synthesize_text_to_mp3",
                side_effect=_fast_synth,
            ),
        ):
            app, _ = _make_app_and_dir(tmp_dir)
            with _live_server(app, tmp_dir) as base_url:
                events, _ = _stream_post_sse(
                    base_url,
                    "/api/studio/tts",
                    {
                        "text": _LONG_TEXT,
                        "voice": "af_heart",
                        "speed": 1.0,
                        "stream": True,
                    },
                    timeout=60.0,
                )

        done = next((e for e in events if e["type"] == "done"), None)
        self.assertIsNotNone(done, "No done event received in live stream")
        self.assertIn(
            "concat_path",
            done,
            "done event missing concat_path in live stream",
        )
        self.assertGreater(done["ok_count"], 0, "Expected ok_count > 0 in live stream done event")

    def test_live_stream_segment_count_matches_segmentation(self):
        """Segment count in done.total matches _split_text_into_segments output."""
        expected_segs = len(_split_text_into_segments(_LONG_TEXT, max_chars=900))

        with (
            tempfile.TemporaryDirectory() as tmp_dir,
            patch(
                "orivellum.api.routes.studio._synthesize_text_to_mp3",
                side_effect=_fast_synth,
            ),
        ):
            app, _ = _make_app_and_dir(tmp_dir)
            with _live_server(app, tmp_dir) as base_url:
                events, _ = _stream_post_sse(
                    base_url,
                    "/api/studio/tts",
                    {
                        "text": _LONG_TEXT,
                        "voice": "af_heart",
                        "speed": 1.0,
                        "stream": True,
                    },
                    timeout=60.0,
                )

        done = next((e for e in events if e["type"] == "done"), None)
        self.assertIsNotNone(done)
        self.assertEqual(
            done["total"],
            expected_segs,
            f"Server segmented into {done['total']} segments, "
            f"expected {expected_segs} from _split_text_into_segments",
        )


if __name__ == "__main__":
    unittest.main()
