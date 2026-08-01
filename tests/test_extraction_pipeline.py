"""Integration tests: import → extract → search round-trip.

Covers:
- PDF, DOCX, plain-text, and CSV files imported via POST /api/library/import
- Readiness transitions to 'ready' after pipeline completes
- Chunks are created and FTS search returns relevant results
- Knowledge nodes appear in GET /api/works/:id/knowledge when work_id supplied
- File-path resolution via content_path survives a simulated server restart
  (process_document called with a stale absolute path falls back to content_path)
"""
from __future__ import annotations

import base64
import io
import tempfile
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# Test app factory (mirrors test_library_api.py)
# ---------------------------------------------------------------------------

def _make_app(tmp: str):
    """Return (app, db) wired to a fresh temp directory."""
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _make_pdf_bytes(text: str = "Orivellum pipeline test content.") -> bytes:
    """Return a minimal PDF containing *text* using reportlab."""
    from reportlab.pdfgen import canvas as rl_canvas
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _make_docx_bytes(text: str = "Orivellum pipeline test content.") -> bytes:
    """Return a minimal DOCX containing *text* using python-docx."""
    import docx as _docx
    doc = _docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_text_bytes(text: str = "Orivellum pipeline test content.") -> bytes:
    return text.encode("utf-8")


def _make_csv_bytes(content: str = "name,value\nOrivellum,42\npipeline,test\n") -> bytes:
    return content.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import(client, filename: str, data: bytes, work_id: str | None = None):
    """POST /api/library/import and return the response JSON."""
    payload: dict = {"filename": filename, "content_b64": _b64(data)}
    if work_id:
        payload["work_id"] = work_id
    resp = client.post("/api/library/import", json=payload)
    assert resp.status_code == 200, f"import failed: {resp.text}"
    return resp.json()


def _chunk_count(db, doc_id: str) -> int:
    """Return the number of chunks stored for *doc_id*."""
    with db._lock:
        row = db._conn.execute(
            "SELECT COUNT(*) as n FROM chunks WHERE doc_id=?", (doc_id,)
        ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Base class with shared app/client fixture
# ---------------------------------------------------------------------------

class _PipelineBase(unittest.TestCase):
    """Sets up a fresh app + TestClient per test method."""

    def setUp(self):
        from fastapi.testclient import TestClient
        self._tmpdir = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmpdir.name)
        self.client = TestClient(self.app, raise_server_exceptions=True)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Plain-text import
# ---------------------------------------------------------------------------

class TestTextImportPipeline(_PipelineBase):
    """Plain-text file: full round-trip."""

    CONTENT = "Orivellum pipeline integration test. This document discusses knowledge extraction."

    def setUp(self):
        super().setUp()
        result = _import(self.client, "sample.txt", _make_text_bytes(self.CONTENT))
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready",
                         f"Expected readiness=ready, got {doc['readiness']!r}; "
                         f"error_message={doc.get('error_message')!r}")

    def test_chunks_created(self):
        self.assertGreater(_chunk_count(self.db, self.doc_id), 0,
                           "At least one chunk must be created after extraction")

    def test_fts_search_returns_result(self):
        resp = self.client.get("/api/library/search", params={"q": "Orivellum pipeline"})
        self.assertEqual(resp.status_code, 200)
        results = resp.json()["results"]
        doc_ids = [r["doc_id"] for r in results]
        self.assertIn(self.doc_id, doc_ids,
                      "FTS search must return the imported text document")


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

class TestCsvImportPipeline(_PipelineBase):
    """CSV file: readiness, chunks, and search."""

    CONTENT = "project,status,owner\nOrivellum,active,pipeline\nExtraction,ready,test\n"

    def setUp(self):
        super().setUp()
        result = _import(self.client, "data.csv", _make_csv_bytes(self.CONTENT))
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready",
                         f"readiness={doc['readiness']!r}; "
                         f"error_message={doc.get('error_message')!r}")

    def test_chunks_created(self):
        self.assertGreater(_chunk_count(self.db, self.doc_id), 0)

    def test_fts_search_returns_result(self):
        resp = self.client.get("/api/library/search", params={"q": "Orivellum"})
        self.assertEqual(resp.status_code, 200)
        doc_ids = [r["doc_id"] for r in resp.json()["results"]]
        self.assertIn(self.doc_id, doc_ids)


# ---------------------------------------------------------------------------
# DOCX import
# ---------------------------------------------------------------------------

class TestDocxImportPipeline(_PipelineBase):
    """DOCX file: readiness, chunks, and search."""

    CONTENT = "Orivellum DOCX integration test. Knowledge extraction from word documents."

    def setUp(self):
        super().setUp()
        result = _import(self.client, "report.docx", _make_docx_bytes(self.CONTENT))
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready",
                         f"readiness={doc['readiness']!r}; "
                         f"error_message={doc.get('error_message')!r}")

    def test_chunks_created(self):
        self.assertGreater(_chunk_count(self.db, self.doc_id), 0)

    def test_fts_search_returns_result(self):
        resp = self.client.get("/api/library/search", params={"q": "DOCX integration"})
        self.assertEqual(resp.status_code, 200)
        doc_ids = [r["doc_id"] for r in resp.json()["results"]]
        self.assertIn(self.doc_id, doc_ids)


# ---------------------------------------------------------------------------
# PDF import
# ---------------------------------------------------------------------------

class TestPdfImportPipeline(_PipelineBase):
    """PDF file: readiness, chunks, and search."""

    CONTENT = "Orivellum PDF integration test. Pipeline extraction from PDF documents."

    def setUp(self):
        super().setUp()
        result = _import(self.client, "document.pdf", _make_pdf_bytes(self.CONTENT))
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready",
                         f"readiness={doc['readiness']!r}; "
                         f"error_message={doc.get('error_message')!r}")

    def test_chunks_created(self):
        self.assertGreater(_chunk_count(self.db, self.doc_id), 0)

    def test_fts_search_returns_result(self):
        resp = self.client.get("/api/library/search", params={"q": "PDF integration"})
        self.assertEqual(resp.status_code, 200)
        doc_ids = [r["doc_id"] for r in resp.json()["results"]]
        self.assertIn(self.doc_id, doc_ids)


# ---------------------------------------------------------------------------
# Knowledge nodes via work_id
# ---------------------------------------------------------------------------

class TestKnowledgeViaWorkId(_PipelineBase):
    """Knowledge items appear in /api/works/:id/knowledge after import."""

    CONTENT = (
        "Orivellum was founded in 2022. "
        "The CEO is Alice Smith. "
        "Revenue reached 5 million dollars in Q3."
    )

    def setUp(self):
        super().setUp()
        # Create a work so we can attach the document to it
        work = self.db.create_work(title="Test Work", work_type="research")
        self.work_id = work["id"]
        result = _import(
            self.client, "brief.txt",
            _make_text_bytes(self.CONTENT),
            work_id=self.work_id,
        )
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready")

    def test_knowledge_items_appear_for_work(self):
        resp = self.client.get(f"/api/works/{self.work_id}/knowledge")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["knowledge"]
        self.assertGreater(len(items), 0,
                           "At least one knowledge item must be harvested and linked to the work")

    def test_knowledge_items_linked_to_doc(self):
        # All harvested items must be traceable back to our document
        resp = self.client.get(f"/api/works/{self.work_id}/knowledge")
        items = resp.json()["knowledge"]
        source_doc_ids = {item.get("source_doc_id") for item in items}
        self.assertIn(self.doc_id, source_doc_ids,
                      "Harvested knowledge items must reference the source document")


# ---------------------------------------------------------------------------
# Content-path fallback: simulates a server restart with a stale file_path
# ---------------------------------------------------------------------------

class TestContentPathFallback(_PipelineBase):
    """resolve_file_path falls back to content_path when the absolute path is stale.

    Simulates a server restart where the in-memory file_path variable no longer
    points to the file on disk (e.g. after data_dir reconfiguration).  The
    pipeline must still succeed using the content_path stored in the document.
    """

    CONTENT = "Orivellum restart simulation. Content path fallback test."

    def test_pipeline_succeeds_with_stale_file_path(self):
        from orivellum.capabilities.pipeline import process_document

        # Import the document through the API so file is stored and
        # content_path is written to the DB.
        result = _import(self.client, "restart.txt", _make_text_bytes(self.CONTENT))
        doc_id = result["document"]["id"]

        # Confirm it's already ready from the background task
        doc = self.db.get_document(doc_id)
        self.assertEqual(doc["readiness"], "ready")

        # Now simulate a restart: reset the document to 'imported' and delete
        # chunks so we can reprocess with a stale absolute path.
        self.db.update_document_extracted(doc_id, "", 0, readiness="imported",
                                          error_message=None)
        self.db.delete_chunks(doc_id)

        self.assertEqual(_chunk_count(self.db, doc_id), 0, "Chunks must be cleared")

        # Call process_document directly with a stale/wrong absolute path
        stale_path = "/tmp/nonexistent/restart.txt"
        process_document(
            doc_id=doc_id,
            file_path=stale_path,
            kind="text",
            work_id=None,
            title="restart.txt",
            db=self.db,
        )

        # Pipeline must succeed via content_path fallback
        doc = self.db.get_document(doc_id)
        self.assertEqual(
            doc["readiness"], "ready",
            f"readiness={doc['readiness']!r} after content_path fallback; "
            f"error_message={doc.get('error_message')!r}"
        )
        self.assertGreater(_chunk_count(self.db, doc_id), 0,
                           "Chunks must be re-created via content_path fallback")


# ---------------------------------------------------------------------------
# Edge-case: password-protected PDF
# ---------------------------------------------------------------------------

def _make_encrypted_pdf_bytes() -> bytes:
    """Return a minimal PDF encrypted with a user password using pypdf."""
    from reportlab.pdfgen import canvas as rl_canvas
    import pypdf

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf)
    c.drawString(72, 720, "Secret content — should not be extractable without password.")
    c.save()
    buf.seek(0)

    reader = pypdf.PdfReader(buf)
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("hunter2")

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class TestEncryptedPdfPipeline(_PipelineBase):
    """Password-protected PDF must land in 'error' or 'no_text' with an error_message."""

    def setUp(self):
        super().setUp()
        result = _import(self.client, "secret.pdf", _make_encrypted_pdf_bytes())
        self.doc_id = result["document"]["id"]

    def test_readiness_is_error_or_no_text(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            f"Expected error/no_text for encrypted PDF, got {doc['readiness']!r}",
        )

    def test_error_message_is_set(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            "readiness must be non-ready before checking error_message",
        )
        self.assertTrue(
            doc.get("error_message"),
            "error_message must be a non-empty string for an encrypted PDF",
        )


# ---------------------------------------------------------------------------
# Edge-case: corrupt / malformed DOCX
# ---------------------------------------------------------------------------

def _make_corrupt_docx_bytes() -> bytes:
    """Return bytes that look vaguely like a DOCX but are not valid."""
    # Starts with the PK zip magic to fool naive sniffers, then garbage
    return b"PK\x03\x04" + b"\xff\xfe\xfa\x00" * 200 + b"not a valid docx"


class TestCorruptDocxPipeline(_PipelineBase):
    """Malformed DOCX must land in 'error' or 'no_text' with an error_message."""

    def setUp(self):
        super().setUp()
        result = _import(self.client, "broken.docx", _make_corrupt_docx_bytes())
        self.doc_id = result["document"]["id"]

    def test_readiness_is_error_or_no_text(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            f"Expected error/no_text for corrupt DOCX, got {doc['readiness']!r}",
        )

    def test_error_message_is_set(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            "readiness must be non-ready before checking error_message",
        )
        self.assertTrue(
            doc.get("error_message"),
            "error_message must be a non-empty string for a corrupt DOCX",
        )


# ---------------------------------------------------------------------------
# Edge-case: zero-byte upload
# ---------------------------------------------------------------------------

class TestZeroBytePipeline(_PipelineBase):
    """A zero-byte file must land in 'no_text' with an error_message."""

    def setUp(self):
        super().setUp()
        result = _import(self.client, "empty.txt", b"")
        self.doc_id = result["document"]["id"]

    def test_readiness_is_no_text(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            f"Expected no_text/error for zero-byte file, got {doc['readiness']!r}",
        )

    def test_error_message_is_set(self):
        doc = self.db.get_document(self.doc_id)
        self.assertIn(
            doc["readiness"], ("error", "no_text"),
            "readiness must be non-ready before checking error_message",
        )
        self.assertTrue(
            doc.get("error_message"),
            "error_message must be a non-empty string for a zero-byte upload",
        )


# ---------------------------------------------------------------------------
# Happy-path: Excel (.xlsx) with multiple sheets
# ---------------------------------------------------------------------------

def _make_xlsx_multi_sheet_bytes() -> bytes:
    """Return a minimal .xlsx workbook with two named sheets."""
    import openpyxl
    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "Revenue"
    ws1.append(["Quarter", "Amount"])
    ws1.append(["Q1", 100_000])
    ws1.append(["Q2", 150_000])
    ws1.append(["Q3", 130_000])

    ws2 = wb.create_sheet("Expenses")
    ws2.append(["Category", "Cost"])
    ws2.append(["Marketing", 50_000])
    ws2.append(["Operations", 30_000])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestXlsxMultiSheetPipeline(_PipelineBase):
    """Excel workbook with multiple sheets: readiness, chunks, both sheet names in text."""

    def setUp(self):
        super().setUp()
        result = _import(self.client, "financials.xlsx", _make_xlsx_multi_sheet_bytes())
        self.doc_id = result["document"]["id"]

    def test_readiness_is_ready(self):
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(
            doc["readiness"], "ready",
            f"Expected readiness=ready for multi-sheet XLSX, got {doc['readiness']!r}; "
            f"error_message={doc.get('error_message')!r}",
        )

    def test_chunks_created(self):
        self.assertGreater(
            _chunk_count(self.db, self.doc_id), 0,
            "At least one chunk must be created from the XLSX workbook",
        )

    def test_both_sheet_names_in_extracted_text(self):
        doc = self.db.get_document(self.doc_id)
        extracted = doc.get("extracted_text", "") or ""
        self.assertIn(
            "Revenue", extracted,
            "Sheet name 'Revenue' must appear in the extracted text",
        )
        self.assertIn(
            "Expenses", extracted,
            "Sheet name 'Expenses' must appear in the extracted text",
        )

    def test_fts_search_returns_result(self):
        resp = self.client.get("/api/library/search", params={"q": "Revenue"})
        self.assertEqual(resp.status_code, 200)
        doc_ids = [r["doc_id"] for r in resp.json()["results"]]
        self.assertIn(self.doc_id, doc_ids,
                      "FTS search must return the imported XLSX document")


if __name__ == "__main__":
    unittest.main()
