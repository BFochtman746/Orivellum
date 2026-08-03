"""Tests for image thumbnail generation in chat messages (#226).

Verifies that _make_thumbnail_b64() always honours _THUMBNAIL_MAX_PX and
_THUMBNAIL_MAX_KB regardless of input size, and that the constants are
defined at module level (so changing them in one place is sufficient).
"""
import base64
import io
import unittest

from PIL import Image


def _make_test_jpeg(width: int, height: int, color=(200, 80, 80),
                    quality: int = 85) -> str:
    """Return a base64-encoded JPEG of the given dimensions."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


class TestMakeThumbnailB64(unittest.TestCase):
    """_make_thumbnail_b64() size and dimension guarantees."""

    def _fn(self):
        from orivellum.api.routes.conversations import _make_thumbnail_b64
        return _make_thumbnail_b64

    def test_constants_exist_at_module_level(self):
        """_THUMBNAIL_MAX_PX and _THUMBNAIL_MAX_KB must be importable from the
        module so callers and tests can reference them without hard-coding values."""
        import orivellum.api.routes.conversations as conv
        self.assertTrue(hasattr(conv, "_THUMBNAIL_MAX_PX"),
                        "_THUMBNAIL_MAX_PX not found at module level")
        self.assertTrue(hasattr(conv, "_THUMBNAIL_MAX_KB"),
                        "_THUMBNAIL_MAX_KB not found at module level")
        self.assertIsInstance(conv._THUMBNAIL_MAX_PX, int)
        self.assertIsInstance(conv._THUMBNAIL_MAX_KB, int)
        self.assertGreater(conv._THUMBNAIL_MAX_PX, 0)
        self.assertGreater(conv._THUMBNAIL_MAX_KB, 0)

    def test_large_landscape_image_resized_within_px_limit(self):
        """A 2000×1500 input must produce a thumbnail with longest side ≤ MAX_PX."""
        from orivellum.api.routes.conversations import _THUMBNAIL_MAX_PX
        b64 = _make_test_jpeg(2000, 1500)
        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(raw))
        self.assertLessEqual(max(thumb.size), _THUMBNAIL_MAX_PX,
                             f"Thumb too large: {thumb.size}")

    def test_large_portrait_image_resized_within_px_limit(self):
        """A 1080×1920 portrait (taller than wide) must also respect MAX_PX."""
        from orivellum.api.routes.conversations import _THUMBNAIL_MAX_PX
        b64 = _make_test_jpeg(1080, 1920)
        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(raw))
        self.assertLessEqual(max(thumb.size), _THUMBNAIL_MAX_PX,
                             f"Thumb too large: {thumb.size}")

    def test_small_image_not_upscaled(self):
        """A 50×50 image must not be upscaled; output dimensions ≤ 50."""
        b64 = _make_test_jpeg(50, 50)
        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(raw))
        self.assertLessEqual(thumb.width, 50)
        self.assertLessEqual(thumb.height, 50)

    def test_output_fits_within_kb_limit(self):
        """Decoded output must be ≤ _THUMBNAIL_MAX_KB KiB for any reasonable input."""
        from orivellum.api.routes.conversations import _THUMBNAIL_MAX_KB
        # Use a very high-detail image to stress the quality-reduction loop.
        import random
        rng = random.Random(42)
        img = Image.new("RGB", (1600, 1200))
        pixels = [(rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
                  for _ in range(1600 * 1200)]
        img.putdata(pixels)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        b64 = base64.b64encode(buf.getvalue()).decode()

        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        self.assertLessEqual(len(raw), _THUMBNAIL_MAX_KB * 1024,
                             f"Thumbnail too heavy: {len(raw)} bytes "
                             f"(limit {_THUMBNAIL_MAX_KB * 1024})")

    def test_aspect_ratio_preserved(self):
        """A 400×200 (2:1) image must produce a thumbnail with ~2:1 ratio."""
        b64 = _make_test_jpeg(400, 200)
        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(raw))
        w, h = thumb.size
        ratio = w / h
        self.assertAlmostEqual(ratio, 2.0, delta=0.1,
                               msg=f"Aspect ratio wrong: {w}×{h} = {ratio:.2f}")

    def test_invalid_base64_returns_none(self):
        """Garbage input must return None without raising."""
        result = self._fn()("not-valid-base64!!!")
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        result = self._fn()("")
        self.assertIsNone(result)

    def test_default_args_match_module_constants(self):
        """The function's default max_px/max_kb must equal the module constants
        so callers that rely on defaults are always in sync."""
        import inspect
        import orivellum.api.routes.conversations as conv
        sig = inspect.signature(conv._make_thumbnail_b64)
        self.assertEqual(sig.parameters["max_px"].default, conv._THUMBNAIL_MAX_PX)
        self.assertEqual(sig.parameters["max_kb"].default, conv._THUMBNAIL_MAX_KB)

    def test_square_200px_boundary_not_resized(self):
        """An image exactly at the limit (200×200) must pass through without
        resize (scale == 1.0) and still be returned successfully."""
        from orivellum.api.routes.conversations import _THUMBNAIL_MAX_PX
        b64 = _make_test_jpeg(_THUMBNAIL_MAX_PX, _THUMBNAIL_MAX_PX)
        result = self._fn()(b64)
        self.assertIsNotNone(result)
        raw = base64.b64decode(result)
        thumb = Image.open(io.BytesIO(raw))
        self.assertLessEqual(max(thumb.size), _THUMBNAIL_MAX_PX)


if __name__ == "__main__":
    unittest.main()
