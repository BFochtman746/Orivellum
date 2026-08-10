"""Book packaging — readiness reporting and EPUB/ZIP assembly."""
import io
import json
import zipfile

from orivellum.capabilities.book_package import (
    build_book_export,
    package_readiness,
)

PIPELINE = {"id": "pl-1", "work_id": "w-1", "title": "The Test Book", "status": "B5"}


def _chapter(seq, title, text):
    return {"id": f"ch-{seq}", "pipeline_id": "pl-1", "work_id": "w-1",
            "seq": seq, "title": title, "text": text, "status": "drafted"}


class TestReadiness:
    def test_no_chapters(self):
        r = package_readiness(PIPELINE, [])
        assert r["ready"] is False
        assert r["chapters_total"] == 0
        assert any("No chapters" in reason for reason in r["reasons"])

    def test_all_empty_chapters(self):
        r = package_readiness(PIPELINE, [_chapter(1, "One", ""), _chapter(2, "Two", "   ")])
        assert r["ready"] is False
        assert r["chapters_with_text"] == 0
        assert any("empty" in reason for reason in r["reasons"])

    def test_ready_with_mixed_chapters(self):
        r = package_readiness(
            PIPELINE, [_chapter(1, "One", "Some prose."), _chapter(2, "Two", "")]
        )
        assert r["ready"] is True
        assert r["chapters_with_text"] == 1
        assert r["chapters_empty"] == 1
        assert r["reasons"] == []


class TestExport:
    def test_unready_raises_value_error(self):
        try:
            build_book_export(PIPELINE, [])
            raise AssertionError("should have raised")
        except ValueError as e:
            assert "No chapters" in str(e)

    def test_zip_contents(self):
        chapters = [
            _chapter(1, "Beginnings", "First paragraph.\n\nSecond paragraph."),
            _chapter(2, "Middles", "More prose here."),
            _chapter(3, "Empty", ""),  # skipped, recorded in manifest
        ]
        filename, payload = build_book_export(PIPELINE, chapters, {"title": "X"})
        assert filename == "the-test-book-package.zip"

        zf = zipfile.ZipFile(io.BytesIO(payload))
        names = zf.namelist()
        assert "the-test-book.epub" in names
        assert "manifest.json" in names
        assert "markdown/001-beginnings.md" in names
        assert "markdown/002-middles.md" in names
        assert not any("empty" in n for n in names)

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["chapters_included"] == 2
        assert manifest["chapters_skipped"][0]["seq"] == 3
        assert manifest["pipeline_stage"] == "B5"

    def test_epub_is_valid_structure(self):
        chapters = [_chapter(1, "Only <Chapter> & Co", "Text with <angle> & amp.")]
        _, payload = build_book_export(PIPELINE, chapters)
        outer = zipfile.ZipFile(io.BytesIO(payload))
        epub = zipfile.ZipFile(io.BytesIO(outer.read("the-test-book.epub")))

        # Spec: mimetype first, stored uncompressed
        infos = epub.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert epub.read("mimetype") == b"application/epub+zip"

        assert "META-INF/container.xml" in epub.namelist()
        opf = epub.read("OEBPS/content.opf").decode()
        assert "The Test Book" in opf
        assert 'idref="ch1"' in opf

        # HTML-escaping — raw angle brackets from the title/text must not leak
        ch = epub.read("OEBPS/chapter-001.xhtml").decode()
        assert "&lt;angle&gt;" in ch
        assert "&lt;Chapter&gt;" in ch

        nav = epub.read("OEBPS/nav.xhtml").decode()
        assert "chapter-001.xhtml" in nav
