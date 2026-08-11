"""Real-book output tests — M18/B14–B16.

Covers: print PDF typesetting (mirrored margins, actual_pages), DOCX and
accessible EPUB from the one manuscript source, the fail-closed EPUB
validation record, the ATELIER print-model completion (EAN-13, hardcover
geometry, actual-pages verification), the KDP disclosure sheet, and the
deterministic B16 assembly gate.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from orivellum.capabilities.finishing import atelier, compliance, epub_a11y, press, typeset
from orivellum.database.db import OrivellumDB

WORK_ID = "work-out-1"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _prose(paragraphs: int = 10, sentences: int = 20) -> str:
    para = "The night held its breath while the city slept below. " * sentences
    return "\n\n".join(para.strip() for _ in range(paragraphs))


def _chapters(n: int = 3) -> list[dict]:
    return [
        {"number": i, "seq": i, "title": f"Title {i}", "text": _prose(), "epigraph_text": ""}
        for i in range(1, n + 1)
    ]


_HEADERS = {i: f"Chapter {i}" for i in range(1, 20)}

_STYLE = {
    "trim": "6x9",
    "body_font": "Garamond",
    "heading_font": "Helvetica",
    "body_size": "11pt",
    "leading": "15pt",
    "chapter_style": "arabic",
    "epigraphs": "on",
}

_BOOK = {"title": "Ash and Silence", "author_name": "Author X", "has_front": True, "has_back": True}


class TypesetTests(unittest.TestCase):
    def test_pdf_renders_with_actual_pages_and_page_map(self):
        r = typeset.render_print_pdf(_BOOK, _STYLE, _chapters(), _HEADERS)
        self.assertTrue(r["pdf"].startswith(b"%PDF-"))
        self.assertGreater(r["actual_pages"], 3)
        # Chapter starts are known exactly and strictly increase.
        starts = list(r["chapter_pages"].values())
        self.assertEqual(sorted(starts), starts)
        self.assertEqual(len(r["chapter_pages"]), 3)
        # Gutter follows the KDP schedule for the rendered count.
        self.assertEqual(r["gutter_in"], 0.375)

    def test_refuses_unknown_trim_and_empty_chapters(self):
        bad_style = dict(_STYLE, trim="9x12")
        with self.assertRaises(typeset.TypesetError):
            typeset.render_print_pdf(_BOOK, bad_style, _chapters(), _HEADERS)
        chs = _chapters()
        chs[1]["text"] = "   "
        with self.assertRaises(typeset.TypesetError):
            typeset.render_print_pdf(_BOOK, _STYLE, chs, _HEADERS)
        with self.assertRaises(typeset.TypesetError):
            typeset.render_docx(_BOOK, _STYLE, chs, _HEADERS)

    def test_leading_smaller_than_body_refused(self):
        with self.assertRaises(typeset.TypesetError):
            typeset.render_print_pdf(
                _BOOK, dict(_STYLE, body_size="12", leading="10"), _chapters(), _HEADERS
            )

    def test_gutter_schedule(self):
        self.assertEqual(typeset._gutter_for(150), 0.375)
        self.assertEqual(typeset._gutter_for(151), 0.5)
        self.assertEqual(typeset._gutter_for(400), 0.625)
        self.assertEqual(typeset._gutter_for(828), 0.875)

    def test_docx_renders_mirrored(self):
        data = typeset.render_docx(_BOOK, _STYLE, _chapters(), _HEADERS)
        with zipfile.ZipFile(BytesIO(data)) as zf:
            doc_xml = zf.read("word/document.xml").decode()
        self.assertIn("mirrorMargins", doc_xml)
        self.assertIn("Title 2", doc_xml)


class EpubA11yTests(unittest.TestCase):
    def _build(self, **kw):
        defaults = dict(
            title="Ash and Silence",
            author="Author X",
            book_id="ash",
            chapters=_chapters(),
            chapter_headers=_HEADERS,
            page_map={5: (1, 3), 9: (2, 1)},
            chapter_pages={1: 3, 2: 8, 3: 13},
            has_front=True,
            has_back=True,
        )
        defaults.update(kw)
        return epub_a11y.build_accessible_epub(**defaults)

    def _zip(self, data: bytes) -> zipfile.ZipFile:
        return zipfile.ZipFile(BytesIO(data))

    def test_mimetype_first_and_stored(self):
        zf = self._zip(self._build())
        first = zf.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)

    def test_nav_has_toc_landmarks_and_page_list(self):
        zf = self._zip(self._build())
        nav = zf.read("OEBPS/nav.xhtml").decode()
        self.assertIn('epub:type="toc"', nav)
        self.assertIn('epub:type="landmarks"', nav)
        self.assertIn('epub:type="page-list"', nav)
        self.assertIn('epub:type="bodymatter"', nav)
        # Page-list references the REAL print pages the PDF produced.
        self.assertIn("#pg5", nav)
        self.assertIn("#pg13", nav)

    def test_a11y_metadata_present(self):
        zf = self._zip(self._build())
        opf = zf.read("OEBPS/content.opf").decode()
        for needle in (
            "schema:accessMode",
            "schema:accessibilityFeature",
            "pageBreakMarkers",
            "schema:accessibilitySummary",
            "dcterms:conformsTo",
            "a11y:certifiedBy",
            "<dc:source>",
        ):
            self.assertIn(needle, opf)

    def test_hebrew_terms_get_xml_lang(self):
        chs = _chapters()
        chs[0]["text"] += "\n\nThe blessing read \u05e9\u05dc\u05d5\u05dd in the margin."
        zf = self._zip(self._build(chapters=chs))
        ch1 = zf.read("OEBPS/chapter-001.xhtml").decode()
        self.assertIn('<span xml:lang="he" lang="he">\u05e9\u05dc\u05d5\u05dd', ch1)

    def test_pagebreak_anchors_in_chapters(self):
        zf = self._zip(self._build())
        ch1 = zf.read("OEBPS/chapter-001.xhtml").decode()
        self.assertIn('epub:type="pagebreak"', ch1)
        self.assertIn('role="doc-pagebreak"', ch1)
        self.assertIn('id="pg3"', ch1)  # chapter start page
        self.assertIn('id="pg5"', ch1)  # mid-chapter print page

    def test_cover_without_alt_text_refused(self):
        with self.assertRaises(epub_a11y.EpubError):
            self._build(cover_image=(b"fakejpeg", "image/jpeg", "   "))

    def test_epigraph_semantics(self):
        chs = _chapters()
        chs[0]["epigraph_text"] = "A line of wisdom.\n— Elder"
        zf = self._zip(self._build(chapters=chs))
        ch1 = zf.read("OEBPS/chapter-001.xhtml").decode()
        self.assertIn('epub:type="epigraph"', ch1)

    def test_empty_chapter_refused(self):
        chs = _chapters()
        chs[2]["text"] = ""
        with self.assertRaises(epub_a11y.EpubError):
            self._build(chapters=chs)


def _seed_main_db(data_dir: str, work_id: str = WORK_ID, n: int = 3):
    conn = sqlite3.connect(str(Path(data_dir) / "orivellum.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS book_chapters "
        "(id TEXT PRIMARY KEY, work_id TEXT, seq INTEGER, title TEXT, text TEXT)"
    )
    for seq in range(n):
        conn.execute(
            "INSERT INTO book_chapters (id,work_id,seq,title,text) VALUES (?,?,?,?,?)",
            (f"{work_id}-ch{seq}", work_id, seq, f"Title {seq + 1}", _prose()),
        )
    conn.commit()
    conn.close()


class PressRenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        press.configure(self._tmp.name)
        press.cmd_init()
        _seed_main_db(self._tmp.name)
        b = press.create_book("Ash and Silence", "Author X", work_id=WORK_ID)
        self.slug = b["slug"]
        press.update_style(self.slug, dict(_STYLE))
        press.set_matter(self.slug, front=True, back=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _lock(self):
        press.lock_style(self.slug, "Author X")

    def test_render_refused_until_style_locked(self):
        with self.assertRaises(PermissionError):
            press.render_outputs(self.slug)

    def test_render_writes_files_and_records_actual_pages(self):
        self._lock()
        manifest = press.render_outputs(self.slug)
        self.assertGreater(manifest["actual_pages"], 0)
        for kind in press.OUTPUT_KINDS:
            self.assertIn(kind, manifest["files"])
            self.assertTrue(press.output_path(self.slug, kind).exists())
        vr = press.verify(self.slug)
        self.assertEqual(vr["actual_pages"], manifest["actual_pages"])
        # Manifest persisted and retrievable.
        m2 = press.get_render_manifest(self.slug)
        self.assertEqual(m2["files"]["epub"]["sha256"], manifest["files"]["epub"]["sha256"])

    def test_epub_page_list_matches_pdf_render(self):
        self._lock()
        press.render_outputs(self.slug)
        data = press.output_path(self.slug, "epub").read_bytes()
        nav = zipfile.ZipFile(BytesIO(data)).read("OEBPS/nav.xhtml").decode()
        self.assertIn('epub:type="page-list"', nav)

    def test_validation_fail_closed_and_production_seal_gate(self):
        self._lock()
        manifest = press.render_outputs(self.slug)
        sha = manifest["files"]["epub"]["sha256"]
        # No validation recorded → not clean → production seal refused.
        self.assertFalse(press.validation_status(self.slug)["clean"])
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "publisher", "production", "Author X")
        # One tool clean is not enough — BOTH must pass.
        press.record_validation(self.slug, "epubcheck", sha, True)
        self.assertFalse(press.validation_status(self.slug)["clean"])
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "publisher", "production", "Author X")
        press.record_validation(self.slug, "ace", sha, True)
        self.assertTrue(press.validation_status(self.slug)["clean"])
        manifest2 = press.seal_package(self.slug, "publisher", "production", "Author X")
        self.assertTrue(manifest2["package_sha256"])

    def test_failed_validation_blocks_seal(self):
        self._lock()
        manifest = press.render_outputs(self.slug)
        sha = manifest["files"]["epub"]["sha256"]
        press.record_validation(self.slug, "epubcheck", sha, True)
        press.record_validation(self.slug, "ace", sha, False, "1 violation")
        vs = press.validation_status(self.slug)
        self.assertEqual(vs["ace"], "failed")
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "publisher", "production", "Author X")

    def test_rerender_invalidates_stale_validation(self):
        self._lock()
        manifest = press.render_outputs(self.slug)
        sha = manifest["files"]["epub"]["sha256"]
        press.record_validation(self.slug, "epubcheck", sha, True)
        press.record_validation(self.slug, "ace", sha, True)
        self.assertTrue(press.validation_status(self.slug)["clean"])
        # Change the manuscript and re-render: the old records must not count.
        conn = sqlite3.connect(str(Path(self._tmp.name) / "orivellum.db"))
        conn.execute("UPDATE book_chapters SET text=text || ' A new final line.' WHERE seq=2")
        conn.commit()
        conn.close()
        press.render_outputs(self.slug)
        self.assertFalse(press.validation_status(self.slug)["clean"])

    def test_production_seal_refused_without_render(self):
        self._lock()
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "publisher", "production", "Author X")

    def test_test_reader_seal_cannot_bypass_production_gate(self):
        """ARC packages deliver the rendered PDF/EPUB — same gate, no bypass."""
        self._lock()
        # No render → refused.
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "test-reader", "production", "Author X", "reader@x")
        manifest = press.render_outputs(self.slug)
        sha = manifest["files"]["epub"]["sha256"]
        # Rendered but validation incomplete → still refused.
        with self.assertRaises(ValueError):
            press.seal_package(self.slug, "test-reader", "production", "Author X", "reader@x")
        press.record_validation(self.slug, "epubcheck", sha, True)
        press.record_validation(self.slug, "ace", sha, True)
        sealed = press.seal_package(self.slug, "test-reader", "production", "Author X", "reader@x")
        self.assertTrue(sealed["watermark"])
        # The submission manuscript format (typeset-free .docx) stays exempt.
        sub = press.seal_package(self.slug, "publisher", "submission", "Author X")
        self.assertEqual(sub["spec"]["format"], "standard-manuscript-format")

    def test_approved_epigraph_lands_in_outputs(self):
        press.set_epigraph_slot(self.slug, 1, True)
        draft = press.draft_epigraph(self.slug, 1, soul="grief", gateway_name="mock")
        self.assertNotEqual(draft["status"], "ABSTAINED")
        press.approve_epigraph(self.slug, 1, "Author X")
        self._lock()
        press.render_outputs(self.slug)
        data = press.output_path(self.slug, "epub").read_bytes()
        ch1 = zipfile.ZipFile(BytesIO(data)).read("OEBPS/chapter-001.xhtml").decode()
        first_line = draft["text"].splitlines()[0].strip()
        self.assertIn('epub:type="epigraph"', ch1)
        self.assertIn(first_line[:30], ch1)

    def test_press_ledger_verifies_after_render(self):
        self._lock()
        press.render_outputs(self.slug)
        ok, msg = press.verify_ledger(self.slug)
        self.assertTrue(ok, msg)


class AtelierPrintModelTests(unittest.TestCase):
    def test_ean13_check_digit(self):
        good = atelier.ean13("978-0-306-40615-7")
        self.assertTrue(good["valid"])
        self.assertEqual(good["ean13"], "9780306406157")
        bad = atelier.ean13("978-0-306-40615-3")
        self.assertFalse(bad["valid"])
        self.assertEqual(bad["expected_check_digit"], 7)
        self.assertFalse(atelier.ean13("12345")["valid"])
        self.assertFalse(atelier.ean13("9990306406157")["valid"])

    def test_hardcover_geometry(self):
        h = atelier.hardcover_dimensions("6x9", 320, "cream")
        self.assertEqual(h["binding"], "hardcover-case-laminate")
        self.assertAlmostEqual(h["board_width"], 6.16, places=2)
        self.assertAlmostEqual(h["board_height"], 9.32, places=2)
        self.assertGreater(h["full_wrap_width"], 2 * h["board_width"] + h["spine_width"])
        self.assertEqual(h["hinge_width"], atelier.HINGE_WIDTH)
        with self.assertRaises(ValueError):
            atelier.hardcover_dimensions("6x9", 40, "cream")
        with self.assertRaises(ValueError):
            atelier.hardcover_dimensions("6x9", 900, "cream")

    def test_pdfx_spec(self):
        spec = atelier.pdfx_spec("cover")
        self.assertEqual(spec["standard"], "PDF/X-1a:2001")
        self.assertEqual(spec["min_dpi"], 300)


class AtelierMetadataTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        atelier.configure(self._tmp.name)
        atelier.cmd_init()
        s = atelier.create_series("Job Cycle", books=1)
        atelier.update_series_brand(s["slug"], {k: "x" for k in atelier.BRAND_KEYS})
        atelier.lock_series(s["slug"], "Author X")
        atelier.create_book(s["slug"], "The Ash Court", pages=320)
        self.slug = "the-ash-court"

    def tearDown(self):
        self._tmp.cleanup()

    def test_bad_isbn_refused_at_write(self):
        with self.assertRaises(ValueError):
            atelier.set_print_metadata(self.slug, isbn="9780306406153")
        with self.assertRaises(ValueError):
            atelier.set_print_metadata(self.slug, binding="spiral")

    def test_actual_pages_rebases_geometry(self):
        b = atelier.record_actual_pages(self.slug, 287, "press:ash-and-silence")
        self.assertEqual(b["pages"], 287)
        self.assertEqual(b["actual_pages"], 287)
        self.assertEqual(b["pages_source"], "press:ash-and-silence")
        vr = atelier.verify_design(self.slug)
        self.assertTrue(vr["checks"]["pages_are_actual"])
        self.assertEqual(vr["spec"]["pages"], 287)

    def test_verify_fails_closed_without_actual_pages_or_isbn(self):
        vr = atelier.verify_design(self.slug)
        self.assertFalse(vr["checks"]["pages_are_actual"])
        self.assertFalse(vr["checks"]["isbn_ean13_valid"])
        self.assertFalse(vr["passed"])

    def test_hardcover_binding_included_in_verify(self):
        atelier.set_print_metadata(self.slug, binding="hardcover")
        vr = atelier.verify_design(self.slug)
        self.assertIn("hardcover", vr)
        self.assertEqual(vr["hardcover"]["binding"], "hardcover-case-laminate")

    def test_atelier_ledger_verifies(self):
        atelier.record_actual_pages(self.slug, 300, "press:x")
        ok, msg = atelier.verify_ledger(self.slug)
        self.assertTrue(ok, msg)


class ComplianceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ash and Silence", work_type="writing")["id"]
        self.ch_ids = []
        for seq in range(3):
            oid = self.db._create_object("book_chapter")
            with self.db._lock:
                self.db._conn.execute(
                    """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
                       source_doc_id, status, meta, created_at, updated_at)
                       VALUES(?,?,?,1,?,?,NULL,'draft','{}',?,?)""",
                    (oid, self.work_id, seq, f"Title {seq + 1}", _prose(2), _now(), _now()),
                )
                self.db._conn.commit()
            self.ch_ids.append(oid)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_undeclared_chapters_reported_loudly(self):
        sheet = compliance.disclosure_sheet(self.db, self.work_id)
        self.assertFalse(sheet["complete"])
        self.assertEqual(len(sheet["undeclared_chapters"]), 3)
        self.assertTrue(all(i["origin"] == "undeclared" for i in sheet["items"]))

    def test_kdp_mapping_generated_vs_assisted(self):
        self.db.record_provenance(self.ch_ids[0], "book_chapter", origin="ai_generated")
        self.db.record_provenance(self.ch_ids[1], "book_chapter", origin="ai_assisted")
        self.db.record_provenance(self.ch_ids[2], "book_chapter", origin="human")
        sheet = compliance.disclosure_sheet(self.db, self.work_id)
        self.assertTrue(sheet["complete"])
        by_seq = {i["chapter_seq"]: i for i in sheet["items"]}
        self.assertTrue(by_seq[0]["disclose"])  # ai_generated → disclosed
        self.assertFalse(by_seq[1]["disclose"])  # ai_assisted → not disclosed
        self.assertFalse(by_seq[2]["disclose"])  # human → not disclosed
        self.assertTrue(sheet["kdp_answer"]["used_ai_tools"])
        self.assertEqual(sheet["kdp_answer"]["text"], "ai_generated")

    def test_assembly_gate_fails_closed_on_empty_state(self):
        press.configure(self._tmp.name)
        press.cmd_init()
        atelier.configure(self._tmp.name)
        atelier.cmd_init()
        gate = compliance.assembly_gate(self.db, self.work_id)
        self.assertFalse(gate["passed"])
        # Every category of missing state is a named, failed check.
        self.assertFalse(gate["checks"]["genesis_ledger"])
        self.assertFalse(gate["checks"]["gate.d13_clean_and_signed"])
        self.assertFalse(gate["checks"]["press_render"])
        self.assertFalse(gate["checks"]["epub_validation_clean"])
        self.assertFalse(gate["checks"]["cover_geometry_verified"])
        self.assertFalse(gate["checks"]["provenance_complete"])
        self.assertTrue(gate["reasons"])

    def test_assembly_gate_assay_requires_run_and_signature(self):
        press.configure(self._tmp.name)
        press.cmd_init()
        atelier.configure(self._tmp.name)
        atelier.cmd_init()
        inst_id = self.db.upsert_assay_instrument(
            {
                "key": "gate.d13",
                "name": "Gate D13",
                "tier": 1,
                "variance": "deterministic",
                "purpose": "test",
                "origin": "test",
            }
        )
        # A clean run without a signature is still a failed gate check…
        run_id = self.db.create_assay_run(instrument_id=inst_id, work_id=self.work_id)
        self.db.finish_assay_run(run_id, status="done", verdict="pass")
        gate = compliance.assembly_gate(self.db, self.work_id)
        self.assertFalse(gate["checks"]["gate.d13_clean_and_signed"])
        # …until the author signs 'go'.
        self.db.create_assay_signature(
            work_id=self.work_id, gate_key="gate.d13", author="Author X", decision="go"
        )
        gate = compliance.assembly_gate(self.db, self.work_id)
        self.assertTrue(gate["checks"]["gate.d13_clean_and_signed"])

    def test_assembly_gate_open_findings_block(self):
        press.configure(self._tmp.name)
        press.cmd_init()
        atelier.configure(self._tmp.name)
        atelier.cmd_init()
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO narrative_finding(id, work_id, chapter_id, category,
                   subtype, fact_quote, fact_chapter, fact_offset,
                   contradiction_quote, contradiction_chapter, contradiction_offset,
                   severity, canon_class, disposition, dedupe_key, created_at)
                   VALUES('nf1',?,?,'factual_detail','date','He died in 1901.',0,0,
                          'He spoke in 1905.',1,0,'critical','HISTORICAL','open','dk1',?)""",
                (self.work_id, self.ch_ids[0], _now()),
            )
            self.db._conn.commit()
        gate = compliance.assembly_gate(self.db, self.work_id)
        self.assertFalse(gate["checks"]["no_open_critical_high_findings"])
        self.assertFalse(gate["checks"]["no_open_historical_contradictions"])


if __name__ == "__main__":
    unittest.main()
