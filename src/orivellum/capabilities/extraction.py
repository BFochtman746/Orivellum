"""Text extraction from documents of various types.

Supports: PDF (pdfplumber), DOCX (python-docx), Excel (openpyxl),
CSV, plain text, Markdown, images (pytesseract OCR), and a
markitdown fallback for everything else.

Returns an ExtractionResult with full text, per-page segments, and
file metadata needed for chunking and knowledge harvest.
"""
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass
class PageSegment:
    """A logical page or section of text."""
    page: int
    text: str
    heading: str = ""


@dataclass
class ExtractionResult:
    kind: str
    full_text: str
    word_count: int
    pages: list[PageSegment] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.full_text.strip())


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        import pdfplumber
    except ImportError:
        return _extract_fallback(path, "pdf")

    pages: list[PageSegment] = []
    headings: list[str] = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                seg = PageSegment(page=i, text=text)
                # Very rough heading detection: short lines at start of page
                first_line = text.splitlines()[0].strip() if text else ""
                if first_line and len(first_line) < 120:
                    seg.heading = first_line
                    headings.append(first_line)
                pages.append(seg)
    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s", path.name, exc)
        return _extract_fallback(path, "pdf")

    full = "\n\n".join(p.text for p in pages)
    return ExtractionResult(
        kind="pdf",
        full_text=full,
        word_count=len(full.split()),
        pages=pages,
        headings=headings,
    )


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _extract_docx(path: Path) -> ExtractionResult:
    try:
        import docx as _docx  # python-docx
    except ImportError:
        return _extract_fallback(path, "docx")

    headings: list[str] = []
    paragraphs: list[str] = []
    try:
        doc = _docx.Document(str(path))
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            paragraphs.append(text)
            # python-docx style names start with "Heading"
            if para.style and para.style.name.startswith("Heading"):
                headings.append(text)
    except Exception as exc:
        logger.warning("python-docx failed on %s: %s", path.name, exc)
        return _extract_fallback(path, "docx")

    full = "\n".join(paragraphs)
    # Group into ~40-paragraph pages for the chunker
    chunk_size = 40
    pages = [
        PageSegment(page=i + 1, text="\n".join(paragraphs[i * chunk_size:(i + 1) * chunk_size]))
        for i in range(max(1, (len(paragraphs) + chunk_size - 1) // chunk_size))
    ]
    return ExtractionResult(
        kind="docx",
        full_text=full,
        word_count=len(full.split()),
        pages=pages,
        headings=headings,
    )


# ---------------------------------------------------------------------------
# Excel / CSV
# ---------------------------------------------------------------------------

def _extract_excel(path: Path) -> ExtractionResult:
    try:
        import openpyxl
    except ImportError:
        return _extract_fallback(path, "excel")

    parts: list[str] = []
    headings: list[str] = []
    pages: list[PageSegment] = []
    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        for idx, sheet in enumerate(wb.worksheets, 1):
            rows_text: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                line = "\t".join(cells).strip()
                if line and line != "\t" * len(cells):
                    rows_text.append(line)
            if rows_text:
                sheet_text = f"[Sheet: {sheet.title}]\n" + "\n".join(rows_text[:500])
                parts.append(sheet_text)
                headings.append(sheet.title)
                pages.append(PageSegment(page=idx, text=sheet_text, heading=sheet.title))
    except Exception as exc:
        logger.warning("openpyxl failed on %s: %s", path.name, exc)
        return _extract_fallback(path, "excel")

    full = "\n\n".join(parts)
    return ExtractionResult(
        kind="excel",
        full_text=full,
        word_count=len(full.split()),
        pages=pages,
        headings=headings,
    )


def _extract_csv(path: Path) -> ExtractionResult:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        rows = ["\t".join(r) for r in reader]
        full = "\n".join(rows[:2000])  # cap at 2000 rows
        return ExtractionResult(
            kind="csv",
            full_text=full,
            word_count=len(full.split()),
            pages=[PageSegment(page=1, text=full)],
        )
    except Exception as exc:
        logger.warning("CSV extraction failed on %s: %s", path.name, exc)
        return ExtractionResult(kind="csv", full_text="", word_count=0)


# ---------------------------------------------------------------------------
# Plain text / Markdown
# ---------------------------------------------------------------------------

def _extract_text(path: Path, kind: str = "text") -> ExtractionResult:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Text read failed on %s: %s", path.name, exc)
        return ExtractionResult(kind=kind, full_text="", word_count=0)

    # Split into ~100-line pages
    lines = text.splitlines()
    page_size = 100
    pages = [
        PageSegment(page=i + 1, text="\n".join(lines[i * page_size:(i + 1) * page_size]))
        for i in range(max(1, (len(lines) + page_size - 1) // page_size))
    ]

    # Extract markdown headings
    headings = [
        line.lstrip("#").strip()
        for line in lines
        if line.startswith("#") and len(line) < 120
    ]

    return ExtractionResult(
        kind=kind,
        full_text=text,
        word_count=len(text.split()),
        pages=pages,
        headings=headings,
    )


# ---------------------------------------------------------------------------
# Image (OCR)
# ---------------------------------------------------------------------------

def _extract_image(path: Path) -> ExtractionResult:
    try:
        from PIL import Image
        import pytesseract
        img = Image.open(path)
        text = pytesseract.image_to_string(img)
        return ExtractionResult(
            kind="image",
            full_text=text,
            word_count=len(text.split()),
            pages=[PageSegment(page=1, text=text)],
        )
    except Exception as exc:
        logger.warning("OCR failed on %s: %s", path.name, exc)
        return ExtractionResult(kind="image", full_text="", word_count=0)


# ---------------------------------------------------------------------------
# Markitdown fallback
# ---------------------------------------------------------------------------

def _extract_fallback(path: Path, kind: str) -> ExtractionResult:
    try:
        from markitdown import MarkItDown
        md = MarkItDown()
        result = md.convert(str(path))
        text = result.text_content or ""
        return ExtractionResult(
            kind=kind,
            full_text=text,
            word_count=len(text.split()),
            pages=[PageSegment(page=1, text=text)],
        )
    except Exception as exc:
        logger.warning("markitdown fallback failed on %s: %s", path.name, exc)
        return ExtractionResult(kind=kind, full_text="", word_count=0)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, object] = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "excel": _extract_excel,
    "csv": _extract_csv,
    "text": lambda p: _extract_text(p, "text"),
    "markdown": lambda p: _extract_text(p, "markdown"),
    "image": _extract_image,
}


def extract(path: str | Path, kind: str) -> ExtractionResult:
    """Extract text from *path* using the handler for *kind*.

    Falls back to markitdown for unknown kinds.
    """
    path = Path(path)
    handler = _DISPATCH.get(kind, lambda p: _extract_fallback(p, kind))
    try:
        result = handler(path)  # type: ignore[call-arg]
    except Exception as exc:
        logger.error("Extraction error on %s (%s): %s", path.name, kind, exc)
        result = ExtractionResult(kind=kind, full_text="", word_count=0)
    logger.info("Extracted %s: %d words, %d pages", path.name, result.word_count, len(result.pages))
    return result
