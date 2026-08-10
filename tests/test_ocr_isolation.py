"""OCR route thread-pool isolation tests.

Verifies three things:
  A) run_ocr handles valid input correctly (mocked pytesseract).
  B) A 60-second timeout returns HTTP 504 and does not swallow other errors.
  C) A slow OCR request does NOT delay a fast concurrent request — proves
     that asyncio.to_thread keeps the event loop free.

Run with:
    uv run --with pytest pytest tests/test_ocr_isolation.py -v
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")
_AUTH_HEADERS = {"X-Api-Key": os.environ["SESSION_SECRET"]}

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

try:
    from fastapi.testclient import TestClient
    import httpx
    from orivellum.api.app import create_app
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB
    _DEPS_AVAILABLE = True
    _MISSING = ""
except Exception as _e:
    _DEPS_AVAILABLE = False
    _MISSING = str(_e)


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
    return TestClient(app, raise_server_exceptions=False, headers=_AUTH_HEADERS)


def _make_app(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = OrivellumDB(str(data_dir / "test.db"))
    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
    )
    _deps.init(db=db, cfg=cfg)
    return create_app()


def _tiny_png_b64() -> str:
    """Return base64 of a 1×1 white PNG (smallest valid image)."""
    import struct, zlib
    def _chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(raw).decode()


# ── Phase A — happy path ──────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestOCRHappyPath(unittest.TestCase):
    """POST /api/studio/ocr returns text when Pillow + pytesseract are mocked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _ocr(self, b64: str) -> tuple[int, dict]:
        resp = self.client.post(
            "/api/studio/ocr",
            json={"content_b64": b64},
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"detail": resp.text}

    def test_ocr_returns_text_when_deps_mocked(self):
        """Mocked pytesseract should return 200 with the mocked text."""
        fake_img = MagicMock()
        with patch("PIL.Image.open", return_value=fake_img), \
             patch("pytesseract.image_to_string", return_value="Hello OCR"), \
             patch("orivellum.api.routes.studio._probe_tesseract_cmd"):
            status, body = self._ocr(_tiny_png_b64())
        self.assertEqual(status, 200, body)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("text"), "Hello OCR")

    def test_ocr_invalid_base64_returns_400(self):
        status, body = self._ocr("not valid base64!!!")
        self.assertEqual(status, 400)
        self.assertIn("base64", str(body.get("detail", "")).lower())

    def test_ocr_missing_deps_returns_503(self):
        """When PIL is not importable, route returns 503 with a clear message."""
        import builtins
        real_import = builtins.__import__

        def _blocked(name, *args, **kwargs):
            if name in ("PIL", "PIL.Image", "pytesseract"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_blocked):
            status, body = self._ocr(_tiny_png_b64())
        self.assertEqual(status, 503, body)


# ── Phase B — timeout enforcement ────────────────────────────────────────────

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestOCRTimeout(unittest.TestCase):
    """A slow pytesseract call must be cancelled and return HTTP 504."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.client = _make_client(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_timeout_returns_504(self):
        """Patch _OCR_TIMEOUT to 0.05 s so the test completes in < 1 s."""
        import orivellum.api.routes.studio as _studio

        def _slow_ocr(_img):
            time.sleep(5)
            return "should not reach here"

        fake_img = MagicMock()
        orig_timeout = _studio._OCR_TIMEOUT
        _studio._OCR_TIMEOUT = 0.05   # 50 ms — far shorter than the 5 s sleep

        try:
            with patch("PIL.Image.open", return_value=fake_img), \
                 patch("pytesseract.image_to_string", side_effect=_slow_ocr), \
                 patch("orivellum.api.routes.studio._probe_tesseract_cmd"):
                resp = self.client.post(
                    "/api/studio/ocr",
                    json={"content_b64": _tiny_png_b64()},
                )
        finally:
            _studio._OCR_TIMEOUT = orig_timeout

        self.assertEqual(resp.status_code, 504, resp.text)
        detail = resp.json().get("detail", "")
        self.assertIn("timed out", detail.lower(),
                      f"504 detail should mention timeout: {detail!r}")

    def test_ocr_exception_returns_500_not_504(self):
        """A non-timeout exception inside pytesseract must yield 500, not 504."""
        fake_img = MagicMock()

        def _boom(_img):
            raise RuntimeError("tesseract process crashed")

        with patch("PIL.Image.open", return_value=fake_img), \
             patch("pytesseract.image_to_string", side_effect=_boom), \
             patch("orivellum.api.routes.studio._probe_tesseract_cmd"):
            resp = self.client.post(
                "/api/studio/ocr",
                json={"content_b64": _tiny_png_b64()},
            )
        self.assertEqual(resp.status_code, 500, resp.text)


# ── Phase C — concurrency: slow OCR does not block fast requests ──────────────

@unittest.skipUnless(_DEPS_AVAILABLE, f"deps missing: {_MISSING}")
class TestOCRConcurrency(unittest.TestCase):
    """A slow OCR request running in asyncio.to_thread must not block a fast
    concurrent async request — the event loop stays free for other work.

    Strategy: use httpx.AsyncClient with the ASGI app so both requests run on
    the same asyncio event loop.  The slow OCR sleeps in a thread; the fast
    request (a lightweight ping-like endpoint) should return while OCR is
    still running.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._app = _make_app(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_fast_request_not_blocked_by_slow_ocr(self):
        """The fast GET /api/studio/status request must return well before
        the slow OCR call finishes."""
        import threading

        # CI runners can be heavily loaded — give the fast request a much
        # wider window there so scheduling jitter can't flake the test.
        ocr_duration = 2.0 if os.environ.get("CI") else 0.5

        ocr_started = threading.Event()
        ocr_finish_time: list[float] = []
        fast_finish_time: list[float] = []

        async def _run_both():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers=_AUTH_HEADERS,
            ) as client:

                async def _slow_ocr_call():
                    def _blocking(_img):
                        ocr_started.set()
                        time.sleep(ocr_duration)    # simulate slow OCR
                        return "scanned text"

                    fake_img = MagicMock()
                    with patch("PIL.Image.open", return_value=fake_img), \
                         patch("pytesseract.image_to_string", side_effect=_blocking), \
                         patch("orivellum.api.routes.studio._probe_tesseract_cmd"):
                        await client.post(
                            "/api/studio/ocr",
                            json={"content_b64": _tiny_png_b64()},
                        )
                    ocr_finish_time.append(time.monotonic())

                async def _fast_call():
                    # Wait until the OCR worker thread has actually started,
                    # so the fast request gets the full OCR duration as its
                    # completion window (a fixed sleep flakes on slow runners).
                    await asyncio.to_thread(ocr_started.wait, 10)
                    await client.get("/api/studio/status")
                    fast_finish_time.append(time.monotonic())

                # Run both concurrently on the same event loop
                await asyncio.gather(_slow_ocr_call(), _fast_call())

        self._run(_run_both())

        self.assertEqual(len(fast_finish_time), 1, "Fast request never completed")
        self.assertEqual(len(ocr_finish_time),  1, "OCR request never completed")

        # Fast request must have finished BEFORE the slow OCR completed.
        self.assertLess(
            fast_finish_time[0], ocr_finish_time[0],
            "Fast request was blocked by slow OCR — asyncio.to_thread isolation broken. "
            f"fast={fast_finish_time[0]:.3f}s, ocr={ocr_finish_time[0]:.3f}s",
        )

    def test_concurrent_ocr_requests_run_in_parallel(self):
        """Two OCR requests submitted simultaneously both complete faster than
        if they ran sequentially.

        On CI the simulated OCR is slowed to 1.0 s each and the budget widened
        so runner jitter (~0.4 s observed) can never blur the parallel (~1x)
        vs sequential (~2x) distinction.
        """
        import os
        ocr_duration = 1.0 if os.environ.get("CI") else 0.5
        budget = 1.7 if os.environ.get("CI") else 0.85
        results: list[float] = []

        async def _run_two():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers=_AUTH_HEADERS,
            ) as client:

                def _slow(_img):
                    time.sleep(ocr_duration)
                    return "text"

                fake_img = MagicMock()

                async def _one():
                    await client.post(
                        "/api/studio/ocr",
                        json={"content_b64": _tiny_png_b64()},
                    )

                # Patch ONCE around both requests. Never patch the same target
                # per-coroutine: overlapping enter/exit of two patches on one
                # target restores in the wrong order and permanently leaks the
                # MagicMock into PIL.Image.open, breaking every later PIL user
                # in the test session (this happened — see test_thumbnail).
                with patch("PIL.Image.open", return_value=fake_img), \
                     patch("pytesseract.image_to_string", side_effect=_slow), \
                     patch("orivellum.api.routes.studio._probe_tesseract_cmd"):
                    t0 = time.monotonic()
                    await asyncio.gather(_one(), _one())
                    results.append(time.monotonic() - t0)

        self._run(_run_two())

        elapsed = results[0]
        # Sequential would take >= 2 x ocr_duration; the budget sits well
        # below that while leaving generous headroom for slow shared runners.
        self.assertLess(
            elapsed, budget,
            f"Two {ocr_duration:.1f} s OCR calls took {elapsed:.2f}s — expected parallel "
            f"execution (~{ocr_duration:.1f} s), not sequential (~{2*ocr_duration:.1f} s). "
            "asyncio.to_thread isolation may be broken.",
        )


if __name__ == "__main__":
    unittest.main()
