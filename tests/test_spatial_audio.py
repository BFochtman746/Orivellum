"""Spatial audiobook rendering tests.

Unit tests cover the deterministic pan mapping and filter construction
(no external tools needed).  End-to-end audio tests are skipped when
ffmpeg is unavailable (CI runners lack it).  API tests cover the per-Work
spatial settings endpoints.

Run with:
    uv run --with pytest pytest tests/test_spatial_audio.py -v
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")
_AUTH_HEADERS = {"X-Api-Key": os.environ["SESSION_SECRET"]}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

from orivellum.capabilities.spatial import (  # noqa: E402
    SPATIAL_MODES,
    finish_spatial,
    needs_finish_pass,
    pan_filter,
    spatialize_parts,
    voice_pan,
)

_FFMPEG = shutil.which("ffmpeg") is not None

try:
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB

    _DEPS_AVAILABLE = True
    _MISSING = ""
except Exception as _e:  # pragma: no cover
    _DEPS_AVAILABLE = False
    _MISSING = str(_e)


# ── Pan mapping (pure, deterministic) ────────────────────────────────────────


class TestVoicePan(unittest.TestCase):
    def test_narrator_is_center(self):
        self.assertEqual(voice_pan("bm_george", "bm_george"), 0.0)

    def test_silence_is_center(self):
        self.assertEqual(voice_pan(None, "bm_george"), 0.0)

    def test_cast_voice_is_off_center(self):
        self.assertNotEqual(voice_pan("af_bella", "bm_george"), 0.0)

    def test_deterministic_across_calls(self):
        a = voice_pan("af_bella", "bm_george")
        for _ in range(5):
            self.assertEqual(voice_pan("af_bella", "bm_george"), a)

    def test_within_bounds(self):
        for vid in ("af_bella", "am_adam", "bf_emma", "clone_x1", "voice-42"):
            p = voice_pan(vid, "bm_george")
            self.assertLessEqual(abs(p), 0.35 + 1e-9, vid)

    def test_distinct_voices_get_distinct_positions(self):
        pans = {voice_pan(v, "narrator") for v in ("af_bella", "am_adam", "bf_emma", "bm_lewis")}
        # jitter guarantees near-uniqueness even on slot collision
        self.assertGreaterEqual(len(pans), 3)


class TestPanFilter(unittest.TestCase):
    def test_center_is_equal_power(self):
        f = pan_filter(0.0)
        self.assertIn("pan=stereo", f)
        # cos(pi/4) == sin(pi/4) ~= 0.7071
        self.assertIn("0.7071", f)

    def test_full_left(self):
        f = pan_filter(-1.0)
        # left coefficient 1.0, right 0.0
        self.assertIn("c0=1.0000*c0", f)
        self.assertIn("c1=0.0000*c0", f)

    def test_constant_power_property(self):
        for p in (-0.35, -0.1, 0.0, 0.2, 0.35):
            theta = (p + 1.0) * math.pi / 4.0
            self.assertAlmostEqual(math.cos(theta) ** 2 + math.sin(theta) ** 2, 1.0, places=9)


class TestNeedsFinishPass(unittest.TestCase):
    def test_subtle_no_bed_skips(self):
        self.assertFalse(needs_finish_pass("subtle", None))

    def test_wide_requires_pass(self):
        self.assertTrue(needs_finish_pass("wide", None))

    def test_bed_requires_pass(self):
        self.assertTrue(needs_finish_pass("subtle", Path("/tmp/x.mp3")))

    def test_modes_constant(self):
        self.assertEqual(set(SPATIAL_MODES), {"subtle", "wide"})


# ── ffmpeg-dependent end-to-end (skipped on CI: no ffmpeg) ───────────────────


def _make_sine(path: Path, seconds: float = 1.0, freq: int = 440) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:duration={seconds}:sample_rate=22050",
            "-ac",
            "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _channel_rms(path: Path) -> tuple[float, float]:
    """Per-channel RMS of a 16-bit stereo WAV."""
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 2
        raw = w.readframes(w.getnframes())
    import struct

    samples = struct.unpack(f"<{len(raw) // 2}h", raw)
    left = samples[0::2]
    right = samples[1::2]
    rms = lambda xs: math.sqrt(sum(x * x for x in xs) / max(1, len(xs)))  # noqa: E731
    return rms(left), rms(right)


@unittest.skipUnless(_FFMPEG, "ffmpeg not available")
class TestSpatializeParts(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_pans_produce_stereo_with_expected_balance(self):
        a = self.tmp / "a.wav"
        b = self.tmp / "b.wav"
        _make_sine(a)
        _make_sine(b)
        # narrator part + a cast voice part
        out = spatialize_parts([a, b], ["narrator", "af_bella"], "narrator", self.tmp)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), 2)
        # Narrator: equal power in both channels
        l0, r0 = _channel_rms(out[0])
        self.assertAlmostEqual(l0, r0, delta=max(l0, r0) * 0.02)
        # Cast voice: channels differ according to its pan sign
        l1, r1 = _channel_rms(out[1])
        pan = voice_pan("af_bella", "narrator")
        if pan < 0:
            self.assertGreater(l1, r1)
        else:
            self.assertGreater(r1, l1)

    def test_mismatched_lengths_fall_back(self):
        a = self.tmp / "a.wav"
        _make_sine(a)
        self.assertIsNone(spatialize_parts([a], [], "narrator", self.tmp))

    def test_missing_file_falls_back(self):
        self.assertIsNone(spatialize_parts([self.tmp / "ghost.wav"], ["v"], "narrator", self.tmp))


@unittest.skipUnless(_FFMPEG, "ffmpeg not available")
class TestFinishSpatial(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.speech = self.tmp / "speech.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=300:duration=2:sample_rate=44100",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                str(self.speech),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_wide_mode_produces_output(self):
        out = self.tmp / "wide.mp3"
        self.assertTrue(finish_spatial(str(self.speech), str(out), "wide"))
        self.assertGreater(out.stat().st_size, 0)

    def test_ambience_bed_mixes_and_ducks(self):
        bed = self.tmp / "bed.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anoisesrc=duration=1:sample_rate=44100:amplitude=0.3",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                str(bed),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        out = self.tmp / "mixed.mp3"
        self.assertTrue(finish_spatial(str(self.speech), str(out), "subtle", bed))
        self.assertGreater(out.stat().st_size, 0)

    def test_unreadable_bed_fails_cleanly(self):
        bad = self.tmp / "not_audio.mp3"
        bad.write_text("this is not audio")
        out = self.tmp / "o.mp3"
        self.assertFalse(finish_spatial(str(self.speech), str(out), "subtle", bad))


# ── Settings endpoints ───────────────────────────────────────────────────────


@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestSpatialSettingsAPI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        data_dir = self.tmp / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = OrivellumDB(str(data_dir / "test.db"))
        cfg = OrivellumConfig(
            data_dir=str(data_dir),
            serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
        )
        _deps.init(db=self.db, cfg=cfg)
        self.client = TestClient(create_app(), raise_server_exceptions=False, headers=_AUTH_HEADERS)
        self.work_id = self.db.create_work("Spatial Test Book")["id"]

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults(self):
        r = self.client.get(f"/api/studio/works/{self.work_id}/spatial")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["enabled"])
        self.assertEqual(data["mode"], "subtle")
        self.assertIsNone(data["ambience_doc_id"])

    def test_put_then_get_roundtrip(self):
        r = self.client.put(
            f"/api/studio/works/{self.work_id}/spatial",
            json={"enabled": True, "mode": "wide", "ambience_doc_id": None},
        )
        self.assertEqual(r.status_code, 200)
        data = self.client.get(f"/api/studio/works/{self.work_id}/spatial").json()
        self.assertTrue(data["enabled"])
        self.assertEqual(data["mode"], "wide")

    def test_disable_resets_to_defaults(self):
        self.client.put(
            f"/api/studio/works/{self.work_id}/spatial", json={"enabled": True, "mode": "wide"}
        )
        self.client.put(
            f"/api/studio/works/{self.work_id}/spatial", json={"enabled": False, "mode": "subtle"}
        )
        work = self.db.get_work(self.work_id)
        self.assertNotIn("spatial_audio", work.get("meta") or {})

    def test_rejects_unknown_mode(self):
        r = self.client.put(
            f"/api/studio/works/{self.work_id}/spatial", json={"enabled": True, "mode": "atmos"}
        )
        self.assertEqual(r.status_code, 422)

    def test_rejects_non_audio_ambience_doc(self):
        doc = self.db.create_document(title="Chapter One", kind="pdf")
        doc_id = doc["id"] if isinstance(doc, dict) else doc
        r = self.client.put(
            f"/api/studio/works/{self.work_id}/spatial",
            json={"enabled": True, "mode": "subtle", "ambience_doc_id": doc_id},
        )
        self.assertEqual(r.status_code, 422)
        self.assertIn("not an audio file", r.json()["detail"])

    def test_accepts_audio_ambience_doc(self):
        doc = self.db.create_document(title="Rain Loop", kind="mp3")
        doc_id = doc["id"] if isinstance(doc, dict) else doc
        r = self.client.put(
            f"/api/studio/works/{self.work_id}/spatial",
            json={"enabled": True, "mode": "subtle", "ambience_doc_id": doc_id},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["ambience_doc_id"], doc_id)

    def test_rejects_missing_ambience_doc(self):
        r = self.client.put(
            f"/api/studio/works/{self.work_id}/spatial",
            json={"enabled": True, "mode": "subtle", "ambience_doc_id": "nope"},
        )
        self.assertEqual(r.status_code, 422)

    def test_missing_work_404(self):
        r = self.client.get("/api/studio/works/does-not-exist/spatial")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
