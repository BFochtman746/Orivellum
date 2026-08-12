"""Docling layout-aware PDF tier: ordering, gating, and fail-safe fallback.

The contract under test:
- Docling, when available AND enabled, is the FIRST PDF tier and its output
  carries extraction_method="docling" provenance.
- The docling_enabled setting (default: on) can switch the tier off.
- Docling being missing, disabled, crashing, or returning nothing NEVER
  fails ingestion — extraction proceeds through the existing tiers and the
  document still becomes ready, with the winning tier recorded.
- Extraction meta is MERGED into existing document meta, so import-time keys
  (e.g. from_zip provenance) survive re-extraction.

Docling is not installed in CI/test environments — every "docling works"
path is exercised through stubs; the "docling absent" path runs for real.
"""

from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from orivellum.capabilities import docling_extract
from orivellum.capabilities.extraction import ExtractionResult, PageSegment, extract

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pdf_bytes(text: str = "Orivellum docling tier test content.") -> bytes:
    from reportlab.pdfgen import canvas as rl_canvas

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _write_pdf(tmpdir: str, text: str) -> Path:
    p = Path(tmpdir) / "sample.pdf"
    p.write_bytes(_make_pdf_bytes(text))
    return p


def _docling_result(text: str = "# Heading\n\nDocling text.\n\n| a | b |\n|---|---|\n| 1 | 2 |"):
    return ExtractionResult(
        kind="pdf",
        full_text=text,
        word_count=len(text.split()),
        pages=[PageSegment(page=1, text=text, heading="Heading")],
        headings=["Heading"],
        meta={"extraction_method": "docling", "docling_tables": 1},
    )


def _available_probe():
    return {"available": True, "error": None, "install_hint": None}


def _unavailable_probe():
    return {
        "available": False,
        "error": "docling package is not installed",
        "install_hint": docling_extract.INSTALL_HINT,
    }


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Tier ordering & gating (extract() level — no server needed)
# ---------------------------------------------------------------------------


class TestTierOrdering(_TmpDirCase):
    def test_docling_wins_when_available_and_enabled(self):
        pdf = _write_pdf(self.tmp, "fallback text that must not be used")
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(
                docling_extract, "extract_pdf_docling", return_value=_docling_result()
            ) as stub,
        ):
            result = extract(pdf, "pdf")
        stub.assert_called_once()
        self.assertEqual(result.meta.get("extraction_method"), "docling")
        self.assertIn("| a | b |", result.full_text, "table markdown must survive")

    def test_setting_disabled_skips_docling(self):
        pdf = _write_pdf(self.tmp, "pdfplumber must win here")
        db = mock.Mock()
        db.get_setting.return_value = "false"
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(docling_extract, "extract_pdf_docling") as stub,
        ):
            result = extract(pdf, "pdf", db=db)
        stub.assert_not_called()
        db.get_setting.assert_called_with("docling_enabled", "true")
        self.assertEqual(result.meta.get("extraction_method"), "pdfplumber")
        self.assertIn("pdfplumber must win here", result.full_text)

    def test_docling_crash_falls_through_to_pdfplumber(self):
        pdf = _write_pdf(self.tmp, "recovered by pdfplumber")
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(
                docling_extract, "extract_pdf_docling", side_effect=RuntimeError("model exploded")
            ),
        ):
            result = extract(pdf, "pdf")
        self.assertTrue(result.ok)
        self.assertEqual(result.meta.get("extraction_method"), "pdfplumber")
        self.assertIn("recovered by pdfplumber", result.full_text)

    def test_docling_empty_output_falls_through(self):
        pdf = _write_pdf(self.tmp, "empty docling output is not a win")
        empty = ExtractionResult(kind="pdf", full_text="", word_count=0)
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(docling_extract, "extract_pdf_docling", return_value=empty),
        ):
            result = extract(pdf, "pdf")
        self.assertTrue(result.ok)
        self.assertEqual(result.meta.get("extraction_method"), "pdfplumber")

    def test_docling_unavailable_never_attempts_extraction(self):
        pdf = _write_pdf(self.tmp, "no docling installed anywhere")
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
            ),
            mock.patch.object(docling_extract, "extract_pdf_docling") as stub,
        ):
            result = extract(pdf, "pdf")
        stub.assert_not_called()
        self.assertTrue(result.ok)
        self.assertEqual(result.meta.get("extraction_method"), "pdfplumber")


class TestConverterSerialization(_TmpDirCase):
    """Docling's cached pipeline is not safe for concurrent execute() calls —
    all convert() calls must be serialized through the conversion lock."""

    def test_concurrent_converts_never_overlap(self):
        import threading

        overlaps: list[int] = []
        active = threading.Lock()
        in_flight = [0]

        class _BlockingConverter:
            def convert(self, _path):
                with active:
                    in_flight[0] += 1
                    if in_flight[0] > 1:
                        overlaps.append(in_flight[0])
                import time

                time.sleep(0.05)  # long enough for a racing thread to collide
                with active:
                    in_flight[0] -= 1

                class _Doc:
                    def iterate_items(self):
                        return iter([])

                    def export_to_markdown(self):
                        return "converted text"

                class _Conv:
                    document = _Doc()

                return _Conv()

        pdf = _write_pdf(self.tmp, "irrelevant")
        results: list = []
        with mock.patch.object(
            docling_extract, "_get_converter", return_value=_BlockingConverter()
        ):
            threads = [
                threading.Thread(
                    target=lambda: results.append(docling_extract.extract_pdf_docling(pdf))
                )
                for _ in range(4)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(overlaps, [], "convert() calls overlapped — conversion lock is broken")
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.full_text == "converted text" for r in results))

    def test_one_failed_convert_does_not_poison_later_ingestion(self):
        pdf = _write_pdf(self.tmp, "healthy after a docling failure")
        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _available_probe()
        ):
            with mock.patch.object(
                docling_extract, "extract_pdf_docling", side_effect=RuntimeError("first run dies")
            ):
                first = extract(pdf, "pdf")
            # Second run: docling still failing must not leave any poisoned state
            with mock.patch.object(
                docling_extract, "extract_pdf_docling", side_effect=RuntimeError("still dying")
            ):
                second = extract(pdf, "pdf")
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(second.meta.get("extraction_method"), "pdfplumber")


class TestProbe(unittest.TestCase):
    def test_probe_reports_unavailable_when_import_spec_missing(self):
        with mock.patch("importlib.util.find_spec", return_value=None):
            pr = docling_extract.probe(force=True)
        self.assertFalse(pr["available"])
        self.assertIn("not installed", pr["error"])
        self.assertEqual(pr["install_hint"], docling_extract.INSTALL_HINT)
        # Restore the cache to the real environment for other tests.
        docling_extract.probe(force=True)

    def test_probe_never_raises(self):
        with mock.patch("importlib.util.find_spec", side_effect=RuntimeError("boom")):
            pr = docling_extract.probe(force=True)
        self.assertFalse(pr["available"])
        docling_extract.probe(force=True)


# ---------------------------------------------------------------------------
# Full pipeline: ingestion completes without Docling; provenance is stored
# ---------------------------------------------------------------------------


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


class _AppCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from tests.conftest import AUTH_HEADERS

        self._tmpdir = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmpdir.name)
        self.client = TestClient(self.app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def _import_pdf(self, filename: str, text: str) -> str:
        resp = self.client.post(
            "/api/library/import",
            json={
                "filename": filename,
                "content_b64": base64.b64encode(_make_pdf_bytes(text)).decode(),
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["document"]["id"]


class TestIngestionWithoutDocling(_AppCase):
    """Docling truly absent (or force-broken): ingestion must still complete."""

    def test_pdf_import_ready_with_fallback_provenance(self):
        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
        ):
            doc_id = self._import_pdf("plain.pdf", "Docling-free ingestion still works fine.")
        doc = self.db.get_document(doc_id)
        self.assertEqual(doc["readiness"], "ready", f"error_message={doc.get('error_message')!r}")
        self.assertEqual((doc.get("meta") or {}).get("extraction_method"), "pdfplumber")

    def test_docling_crash_mid_pipeline_never_fails_ingestion(self):
        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(
                docling_extract, "extract_pdf_docling", side_effect=OSError("models not downloaded")
            ),
        ):
            doc_id = self._import_pdf("crashy.pdf", "Crash in docling must be invisible.")
        doc = self.db.get_document(doc_id)
        self.assertEqual(doc["readiness"], "ready")
        self.assertEqual((doc.get("meta") or {}).get("extraction_method"), "pdfplumber")

    def test_provenance_visible_via_document_api(self):
        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
        ):
            doc_id = self._import_pdf("api.pdf", "Provenance must surface through the API.")
        resp = self.client.get(f"/api/library/{doc_id}")
        self.assertEqual(resp.status_code, 200)
        meta = resp.json()["document"].get("meta") or {}
        self.assertEqual(meta.get("extraction_method"), "pdfplumber")


class TestReprocessAndMetaMerge(_AppCase):
    def test_reprocess_records_docling_provenance(self):
        """The existing reprocess flow re-extracts through the new tier."""
        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
        ):
            doc_id = self._import_pdf("re.pdf", "First pass without docling.")
        self.assertEqual(
            (self.db.get_document(doc_id).get("meta") or {}).get("extraction_method"),
            "pdfplumber",
        )

        with (
            mock.patch.object(
                docling_extract, "probe", side_effect=lambda force=False: _available_probe()
            ),
            mock.patch.object(
                docling_extract, "extract_pdf_docling", return_value=_docling_result()
            ),
        ):
            resp = self.client.post(f"/api/library/{doc_id}/reprocess", params={"force": "true"})
            self.assertEqual(resp.status_code, 200, resp.text)

        doc = self.db.get_document(doc_id)
        self.assertEqual(doc["readiness"], "ready")
        self.assertEqual((doc.get("meta") or {}).get("extraction_method"), "docling")

    def test_extraction_meta_merges_with_import_time_keys(self):
        """Re-extraction must not clobber import-time meta (e.g. ZIP provenance)."""
        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
        ):
            doc_id = self._import_pdf("merge.pdf", "Meta merge under test.")

        # Simulate import-time provenance keys (as ZIP children get).
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE documents SET meta=? WHERE id=?",
                (json.dumps({"from_zip": "archive-1", "zip_folder": "chapter1"}), doc_id),
            )
            self.db._conn.commit()

        with mock.patch.object(
            docling_extract, "probe", side_effect=lambda force=False: _unavailable_probe()
        ):
            resp = self.client.post(f"/api/library/{doc_id}/reprocess", params={"force": "true"})
            self.assertEqual(resp.status_code, 200, resp.text)

        meta = self.db.get_document(doc_id).get("meta") or {}
        self.assertEqual(meta.get("from_zip"), "archive-1", "import-time keys must survive")
        self.assertEqual(meta.get("zip_folder"), "chapter1")
        self.assertEqual(meta.get("extraction_method"), "pdfplumber")


# ---------------------------------------------------------------------------
# Settings endpoints
# ---------------------------------------------------------------------------


class TestDoclingSettings(_AppCase):
    def test_get_defaults_to_enabled(self):
        resp = self.client.get("/api/system/settings/docling")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["enabled"], "docling tier must default to enabled")
        self.assertIn("installed", body)

    def test_put_toggles_setting(self):
        resp = self.client.put("/api/system/settings/docling", json={"enabled": False})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.db.get_setting("docling_enabled", "true"), "false")
        resp = self.client.get("/api/system/settings/docling")
        self.assertFalse(resp.json()["enabled"])

        resp = self.client.put("/api/system/settings/docling", json={"enabled": True})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.client.get("/api/system/settings/docling").json()["enabled"])

    def test_probe_endpoint_reports_availability(self):
        resp = self.client.post("/api/system/docling/probe")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("installed", body)
        if not body["installed"]:
            self.assertTrue(body["install_hint"])


if __name__ == "__main__":
    unittest.main()
