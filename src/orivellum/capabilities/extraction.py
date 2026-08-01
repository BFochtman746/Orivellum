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
    # --- pdfplumber (primary: best for text-layer PDFs) ---
    try:
        import pdfplumber
        pages: list[PageSegment] = []
        headings: list[str] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = (page.extract_text() or "").strip()
                if not text:
                    continue
                seg = PageSegment(page=i, text=text)
                first_line = text.splitlines()[0].strip() if text else ""
                if first_line and len(first_line) < 120:
                    seg.heading = first_line
                    headings.append(first_line)
                pages.append(seg)
        if pages:
            full = "\n\n".join(p.text for p in pages)
            return ExtractionResult(
                kind="pdf",
                full_text=full,
                word_count=len(full.split()),
                pages=pages,
                headings=headings,
            )
        logger.info("pdfplumber found no text in %s — trying pypdf", path.name)
    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s — trying pypdf", path.name, exc)

    # --- pypdf (secondary: handles more edge cases / newer PDFs) ---
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages2: list[PageSegment] = []
        headings2: list[str] = []
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            seg = PageSegment(page=i, text=text)
            first_line = text.splitlines()[0].strip() if text else ""
            if first_line and len(first_line) < 120:
                seg.heading = first_line
                headings2.append(first_line)
            pages2.append(seg)
        if pages2:
            full2 = "\n\n".join(p.text for p in pages2)
            return ExtractionResult(
                kind="pdf",
                full_text=full2,
                word_count=len(full2.split()),
                pages=pages2,
                headings=headings2,
            )
        logger.info("pypdf also found no text in %s — falling back to markitdown", path.name)
    except Exception as exc:
        logger.warning("pypdf failed on %s: %s — falling back to markitdown", path.name, exc)

    # --- markitdown (final fallback: handles image-heavy / complex PDFs) ---
    return _extract_fallback(path, "pdf")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _extract_docx(path: Path) -> ExtractionResult:
    try:
        import docx as _docx  # python-docx
    except ImportError:
        return _extract_fallback(path, "docx")

    headings: list[str] = []
    blocks: list[str] = []
    try:
        doc = _docx.Document(str(path))

        # Preserve document order by iterating the XML body children
        from docx.oxml.ns import qn  # type: ignore
        body = doc.element.body
        for child in body.iterchildren():
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                # Paragraph
                para_text = "".join(n.text or "" for n in child.iter(qn("w:t"))).strip()
                if not para_text:
                    continue
                blocks.append(para_text)
                style_name = ""
                pPr = child.find(qn("w:pPr"))
                if pPr is not None:
                    pStyle = pPr.find(qn("w:pStyle"))
                    if pStyle is not None:
                        style_name = pStyle.get(qn("w:val"), "")
                if style_name.lower().startswith("heading"):
                    headings.append(para_text)

            elif tag == "tbl":
                # Table — extract all cell text as a TSV block
                rows_text: list[str] = []
                for tr in child.iter(qn("w:tr")):
                    cells = []
                    for tc in tr.iter(qn("w:tc")):
                        cell_text = "".join(n.text or "" for n in tc.iter(qn("w:t"))).strip()
                        cells.append(cell_text)
                    if any(cells):
                        rows_text.append("\t".join(cells))
                if rows_text:
                    blocks.append("[Table]\n" + "\n".join(rows_text))

    except Exception as exc:
        logger.warning("python-docx failed on %s: %s", path.name, exc)
        return _extract_fallback(path, "docx")

    full = "\n".join(blocks)
    # Group into ~40-block pages for the chunker
    chunk_size = 40
    pages = [
        PageSegment(page=i + 1, text="\n".join(blocks[i * chunk_size:(i + 1) * chunk_size]))
        for i in range(max(1, (len(blocks) + chunk_size - 1) // chunk_size))
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
                if line and line.replace("\t", ""):
                    rows_text.append(line)
            if rows_text:
                # Cap at 5000 rows per sheet (was 500); include row count note if truncated
                cap = 5000
                truncated = len(rows_text) > cap
                sheet_text = (
                    f"[Sheet: {sheet.title}]"
                    + (f" ({len(rows_text)} rows — showing first {cap})" if truncated else "")
                    + "\n"
                    + "\n".join(rows_text[:cap])
                )
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

def _probe_tesseract() -> None:
    """Ensure pytesseract can find the tesseract binary.

    On NixOS/Replit the binary lands in the nix store but may not be
    on the process PATH.  We probe common locations and set
    pytesseract.pytesseract.tesseract_cmd explicitly when needed.
    """
    import shutil
    import subprocess as _sp
    import pytesseract as _pt

    if shutil.which("tesseract"):
        return  # already on PATH

    import sys as _sys
    if _sys.platform != "win32":
        # On Unix/NixOS ask the login shell — it has a broader PATH
        try:
            result = _sp.run(
                ["bash", "-lc", "which tesseract"],
                capture_output=True, text=True, timeout=5,
            )
            candidate = result.stdout.strip()
            if candidate and Path(candidate).is_file():
                _pt.pytesseract.tesseract_cmd = candidate
                return
        except Exception:
            pass

        # Known nix store prefix pattern — walk /nix/store at depth-1 only
        nix_store = Path("/nix/store")
        if nix_store.exists():
            for pkg_dir in nix_store.iterdir():
                if "tesseract" in pkg_dir.name:
                    candidate = pkg_dir / "bin" / "tesseract"
                    if candidate.is_file():
                        _pt.pytesseract.tesseract_cmd = str(candidate)
                        return
    else:
        # Common Windows install location from the UB-Mannheim installer
        win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        if win_default.is_file():
            _pt.pytesseract.tesseract_cmd = str(win_default)
            return


def _extract_image_vision(path: Path) -> "ExtractionResult | None":
    """Use the configured vision LLM to describe image content.

    Returns an ExtractionResult when the vision model is configured and
    responds successfully, or None so the caller falls through to Tesseract.
    """
    try:
        from orivellum.configuration.config import load_config
        cfg = load_config()
        if not cfg.serving.vision_model:
            return None

        import base64
        import io
        import httpx
        from PIL import Image as _PIL

        img = _PIL.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()

        resp = httpx.post(
            f"{cfg.serving.base_url}/chat/completions",
            json={
                "model": cfg.serving.vision_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Describe everything in this image in detail. "
                                "Include all visible text, numbers, labels, "
                                "diagrams, charts, tables, and visual elements. "
                                "Be thorough — this description will be used for "
                                "search and knowledge extraction."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                    ],
                }],
                "stream": False,
            },
            timeout=cfg.serving.extraction_timeout_sec,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        if text.strip():
            logger.info("Vision model described %s (%d words)", path.name, len(text.split()))
            return ExtractionResult(
                kind="image",
                full_text=text,
                word_count=len(text.split()),
                pages=[PageSegment(page=1, text=text)],
            )
    except Exception as exc:
        logger.warning("Vision extraction failed on %s: %s", path.name, exc)
    return None


def _extract_image(path: Path) -> ExtractionResult:
    # --- vision LLM (primary when configured) ---
    vision_result = _extract_image_vision(path)
    if vision_result is not None:
        return vision_result

    # --- pytesseract (fallback: requires tesseract binary) ---
    try:
        from PIL import Image
        import pytesseract
        _probe_tesseract()
        img = Image.open(path)
        # Pre-process: convert to RGB so tesseract handles all modes
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        text = pytesseract.image_to_string(img, config="--psm 6")
        if text.strip():
            return ExtractionResult(
                kind="image",
                full_text=text,
                word_count=len(text.split()),
                pages=[PageSegment(page=1, text=text)],
            )
        logger.info("pytesseract returned no text for %s", path.name)
    except pytesseract.TesseractNotFoundError:
        logger.warning("tesseract not found — skipping OCR for %s", path.name)
    except Exception as exc:
        logger.warning("pytesseract OCR failed on %s: %s", path.name, exc)

    # --- markitdown fallback (handles some image formats) ---
    result = _extract_fallback(path, "image")
    if result.ok:
        return result

    # No text recovered — mark explicitly so callers can surface it to the user
    return ExtractionResult(kind="no_text", full_text="", word_count=0)


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
