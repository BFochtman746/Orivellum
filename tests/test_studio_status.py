"""Smoke tests for Studio service-status endpoint and graceful TTS failure.

Tests:
  A) GET /api/studio/status returns correct structure for all three services.
  B) When all TTS backends are unavailable, POST /api/studio/tts returns a
     structured 503 within 3 seconds — never a spinner-inducing timeout.
  C) OCR status reflects tesseract availability without actually running OCR.
"""
from __future__ import annotations

import importlib
import importlib.util as _iutil
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Test auth key — must be set before any import of the FastAPI app
# ---------------------------------------------------------------------------
os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")
_AUTH_HEADERS = {"X-Api-Key": os.environ["SESSION_SECRET"]}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

try:
    from fastapi.testclient import TestClient
    from orivellum.api.app import create_app
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB
    _DEPS_AVAILABLE = True
    _MISSING = ""
except Exception as _e:  # pragma: no cover
    _DEPS_AVAILABLE = False
    _MISSING = str(_e)


def _make_client(tmp_path: Path) -> TestClient:
    """Wire a throwaway DB + config into _deps, then return a TestClient."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = OrivellumDB(str(data_dir / "test.db"))
    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
    )
    # Wire deps directly (bypasses lifespan; correct for unit tests)
    _deps.init(db=db, cfg=cfg)
    app = create_app()
    return TestClient(app, raise_server_exceptions=False, headers=_AUTH_HEADERS)


# ---------------------------------------------------------------------------
# Phase A — /studio/status structure
# ---------------------------------------------------------------------------

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestStudioStatusEndpoint(unittest.TestCase):
    """GET /api/studio/status returns the correct shape."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_returns_200(self):
        resp = self.client.get("/api/studio/status")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_status_has_tts_section(self):
        data = self.client.get("/api/studio/status").json()
        self.assertIn("tts", data)
        tts = data["tts"]
        self.assertIn("available", tts)
        self.assertIn("best_strategy", tts)
        self.assertIn("strategies", tts)
        self.assertIsInstance(tts["strategies"], list)
        self.assertGreaterEqual(len(tts["strategies"]), 3, "Expected at least 3 TTS strategies")

    def test_status_tts_strategy_keys(self):
        data = self.client.get("/api/studio/status").json()
        expected_keys = {"name", "key", "available", "latency_ms"}
        for s in data["tts"]["strategies"]:
            self.assertTrue(expected_keys.issubset(s.keys()),
                            f"Strategy missing keys: {s}")

    def test_status_tts_strategy_names(self):
        data = self.client.get("/api/studio/status").json()
        names = [s["name"] for s in data["tts"]["strategies"]]
        self.assertIn("AI Server", names)
        self.assertIn("Kokoro ONNX", names)
        self.assertIn("espeak-ng", names)

    def test_status_has_image_gen_section(self):
        data = self.client.get("/api/studio/status").json()
        self.assertIn("image_gen", data)
        img = data["image_gen"]
        self.assertIn("available", img)
        self.assertIn("backends", img)
        self.assertIsInstance(img["backends"], list)

    def test_status_has_ocr_section(self):
        data = self.client.get("/api/studio/status").json()
        self.assertIn("ocr", data)
        ocr = data["ocr"]
        self.assertIn("available", ocr)
        self.assertIn("engine", ocr)
        self.assertIn("missing", ocr)
        self.assertIsInstance(ocr["missing"], list)

    def test_status_has_last_checked(self):
        data = self.client.get("/api/studio/status").json()
        self.assertIn("last_checked", data)
        ts = data["last_checked"]
        self.assertIsInstance(ts, str)
        self.assertIn("T", ts, "last_checked should be ISO 8601")

    def test_ai_server_unreachable_makes_tts_strategy_false(self):
        """AI server at port 99999 is unreachable; ai_server strategy must be False."""
        data = self.client.get("/api/studio/status").json()
        ai_strategy = next(s for s in data["tts"]["strategies"] if s["key"] == "ai_server")
        self.assertFalse(ai_strategy["available"],
                         "AI server at 99999 should not be reachable in tests")

    def test_image_gen_ai_server_unreachable(self):
        """AI server image backend is also unreachable in test environment."""
        data = self.client.get("/api/studio/status").json()
        ai_backend = next(
            (b for b in data["image_gen"]["backends"] if b["name"] == "AI Server"),
            None,
        )
        self.assertIsNotNone(ai_backend)
        self.assertFalse(ai_backend["online"])

    def test_status_completes_within_10s(self):
        """Probes must complete in under 10 seconds even with unreachable hosts.

        This is an environment-dependent baseline. The stall tests below prove
        the deadline is enforced deterministically.
        """
        import time
        t0 = time.monotonic()
        self.client.get("/api/studio/status")
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 10.0,
                        f"Studio status probe took {elapsed:.1f}s — probe timeouts too long?")

    def test_stalled_probes_complete_within_global_deadline(self):
        """Even when every URL probe hangs (blackhole), the endpoint must return
        within the global deadline + 1 s buffer — never block indefinitely.

        _STATUS_GLOBAL_TIMEOUT = 5 s  →  we assert completion within 7 s.
        """
        import time
        from orivellum.api.routes import studio as _studio_mod

        # Sleep 0.5 s — longer than the 0.3 s shrunk deadline but short enough
        # that the background threads finish before pytest teardown.
        def _stall(*_a, **_kw):
            import time as _t
            _t.sleep(0.5)
            return False, None

        original_timeout = _studio_mod._STATUS_GLOBAL_TIMEOUT
        # Shrink the deadline so the test completes in < 1 s
        _studio_mod._STATUS_GLOBAL_TIMEOUT = 0.3
        try:
            with patch("orivellum.api.routes.studio._url_probe", side_effect=_stall), \
                 patch("orivellum.api.routes.studio._probe_tesseract_ok", return_value=False):
                t0 = time.monotonic()
                resp = self.client.get("/api/studio/status")
                elapsed = time.monotonic() - t0
        finally:
            _studio_mod._STATUS_GLOBAL_TIMEOUT = original_timeout

        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 2.0,
                        f"Status with stalled probes took {elapsed:.2f}s — global deadline not enforced")

    def test_stalled_probes_with_custom_comfy_url(self):
        """The custom ComfyUI URL path fires TWO extra probes (/system_stats + root).
        Even with all probes stalled, the endpoint must complete within the deadline.
        """
        import time
        from orivellum.api.routes import studio as _studio_mod

        # Inject a fake custom ComfyUI URL so both probe paths are exercised
        _studio_mod.get_db().set_setting("image_gen_url", "http://10.0.0.1:8188")

        def _stall(*_a, **_kw):
            import time as _t
            _t.sleep(0.5)
            return False, None

        original_timeout = _studio_mod._STATUS_GLOBAL_TIMEOUT
        _studio_mod._STATUS_GLOBAL_TIMEOUT = 0.3
        try:
            with patch("orivellum.api.routes.studio._url_probe", side_effect=_stall), \
                 patch("orivellum.api.routes.studio._probe_tesseract_ok", return_value=False):
                t0 = time.monotonic()
                resp = self.client.get("/api/studio/status")
                elapsed = time.monotonic() - t0
        finally:
            _studio_mod._STATUS_GLOBAL_TIMEOUT = original_timeout
            _studio_mod.get_db().set_setting("image_gen_url", "")

        self.assertEqual(resp.status_code, 200)
        self.assertLess(elapsed, 2.0,
                        f"Custom-ComfyUI stall test took {elapsed:.2f}s — deadline not enforced")

    def test_tts_available_or_has_reason(self):
        """If TTS is unavailable, best_strategy must be None; otherwise non-null."""
        data = self.client.get("/api/studio/status").json()
        tts = data["tts"]
        if tts["available"]:
            self.assertIsNotNone(tts["best_strategy"])
        else:
            self.assertIsNone(tts["best_strategy"])

    def test_ocr_missing_is_empty_when_available(self):
        """If OCR is available, the missing list must be empty."""
        data = self.client.get("/api/studio/status").json()
        ocr = data["ocr"]
        if ocr["available"]:
            self.assertEqual(ocr["missing"], [])


# ---------------------------------------------------------------------------
# Phase B — Graceful TTS failure (all backends down)
# ---------------------------------------------------------------------------

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestTTSGracefulFailure(unittest.TestCase):
    """POST /api/studio/tts returns a structured 503 within 3 s when all
    backends are unavailable — never a 500 or an indefinite hang.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self.client = _make_client(self._tmp_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _synthesize(self, text: str = "Hello world") -> tuple[int, dict]:
        resp = self.client.post(
            "/api/studio/tts",
            json={"text": text, "voice": "af_heart", "speed": 1.0},
        )
        try:
            body = resp.json()
        except Exception:
            body = {"detail": resp.text}
        return resp.status_code, body

    def test_tts_espeak_not_found_returns_503(self):
        """When espeak-ng binary is missing (FileNotFoundError), we get a 503
        with service=tts in the detail, not a 500 or a hang."""
        import time

        def _fake_run(args, **kwargs):
            if args and "espeak-ng" in str(args[0]):
                raise FileNotFoundError("espeak-ng: command not found")
            m = MagicMock()
            m.returncode = 1
            m.stdout = b""
            m.stderr = b"not found"
            return m

        with patch("orivellum.api.routes.studio._get_kokoro", return_value=None), \
             patch("subprocess.run", side_effect=_fake_run):
            t0 = time.monotonic()
            status, body = self._synthesize()
            elapsed = time.monotonic() - t0

        self.assertEqual(status, 503, f"Expected 503, got {status}: {body}")
        self.assertLess(elapsed, 3.0,
                        f"TTS failure took {elapsed:.1f}s — must fail fast within 3s")

        detail = body.get("detail", {})
        self.assertIsInstance(detail, dict, "503 detail should be a structured dict")
        self.assertEqual(detail.get("service"), "tts",
                         f"Expected service='tts' in detail: {detail}")
        self.assertIn("strategies_tried", detail)
        # espeak-ng was attempted (just not found) — it must appear in strategies_tried
        self.assertIn("espeak-ng", detail["strategies_tried"],
                      "espeak-ng was attempted; must appear in strategies_tried")
        # reason must be a human-readable string, not [object Object]
        reason = detail.get("reason", "")
        self.assertIsInstance(reason, str, "reason must be a string")
        self.assertGreater(len(reason), 0, "reason must be non-empty")

    def test_tts_espeak_runtime_error_returns_503_not_500(self):
        """An espeak-ng runtime error (process crashes) must yield 503, not 500."""
        import time

        def _fake_run(args, **kwargs):
            if args and "espeak-ng" in str(args[0]):
                raise RuntimeError("espeak-ng: segfault")
            m = MagicMock()
            m.returncode = 1
            m.stdout = b""
            m.stderr = b""
            return m

        with patch("orivellum.api.routes.studio._get_kokoro", return_value=None), \
             patch("subprocess.run", side_effect=_fake_run):
            t0 = time.monotonic()
            status, body = self._synthesize()
            elapsed = time.monotonic() - t0

        self.assertEqual(status, 503, f"Expected 503, got {status}: {body}")
        self.assertLess(elapsed, 3.0,
                        f"TTS failure must surface within 3s, took {elapsed:.1f}s")

    def test_tts_empty_text_returns_400(self):
        status, body = self._synthesize(text="   ")
        self.assertEqual(status, 400)

    def test_tts_too_long_returns_400(self):
        status, body = self._synthesize(text="x" * 10_001)
        self.assertEqual(status, 400)

    def test_tts_503_detail_is_not_generic_500(self):
        """503 detail must not be the generic 'Internal Server Error' — must
        clearly identify the TTS pipeline as the failing component."""
        def _fake_run(args, **kwargs):
            if args and "espeak-ng" in str(args[0]):
                raise FileNotFoundError("no espeak-ng")
            m = MagicMock()
            m.returncode = 1
            return m

        with patch("orivellum.api.routes.studio._get_kokoro", return_value=None), \
             patch("subprocess.run", side_effect=_fake_run):
            status, body = self._synthesize()

        self.assertEqual(status, 503)
        body_str = str(body).lower()
        self.assertNotIn("internal server error", body_str,
                         "503 must not be a generic server error")


# ---------------------------------------------------------------------------
# Phase C — OCR status reflects environment
# ---------------------------------------------------------------------------

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestOCRStatus(unittest.TestCase):
    """OCR status section correctly reflects actual environment state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_ocr_unavailable_when_tesseract_missing(self):
        """When the tesseract probe returns False, ocr.available must be False."""
        with patch("orivellum.api.routes.studio._probe_tesseract_ok", return_value=False):
            data = self.client.get("/api/studio/status").json()
        ocr = data["ocr"]
        self.assertFalse(ocr["available"])
        self.assertIsNone(ocr["engine"])
        self.assertTrue(
            any("tesseract" in m.lower() for m in ocr["missing"]),
            f"missing list should mention tesseract: {ocr['missing']}",
        )

    def test_ocr_available_when_tesseract_present(self):
        """When the tesseract probe returns True and Python deps are present, OCR is available."""
        with patch("orivellum.api.routes.studio._probe_tesseract_ok", return_value=True):
            data = self.client.get("/api/studio/status").json()
        ocr = data["ocr"]
        # Tesseract binary found — engine should be set, and binary not in missing list
        self.assertEqual(ocr["engine"], "tesseract")
        self.assertNotIn("tesseract binary", ocr["missing"])
        # Full availability also requires Pillow + pytesseract (env-specific)
        if ocr["available"]:
            self.assertEqual(ocr["missing"], [])


if __name__ == "__main__":
    unittest.main()
