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

def _extract_pdf(path: Path, db=None) -> ExtractionResult:
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

    # --- VLM OCR (scanned / image-only pages — when vision model configured) ---
    vlm_result = _vlm_pdf_ocr(path, db=db)
    if vlm_result is not None and vlm_result.ok:
        return vlm_result

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
        # data_only=True reads cached values; formula-only files (never opened in Excel)
        # have no cached values → every cell is None → empty.  We detect that and retry
        # with data_only=False so formulas themselves are visible as text.
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        has_any_value = any(
            cell.value is not None
            for sheet in wb.worksheets
            for row in sheet.iter_rows()
            for cell in row
        )
        if not has_any_value:
            wb.close()
            logger.info("Excel %s has no cached values — retrying with data_only=False", path.name)
            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=False)

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
        # No artificial cap — extract everything; guard only at 500k rows
        cap = 500_000
        truncated = len(rows) > cap
        full = "\n".join(rows[:cap])
        if truncated:
            full += f"\n\n[Truncated: showing first {cap:,} of {len(rows):,} rows]"
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
# Image (OCR) + VLM PDF OCR
# ---------------------------------------------------------------------------

_VLM_OCR_PROMPT = (
    "Transcribe all text from this document image exactly, "
    "preserving structure, headings, and layout. "
    "Return only the text, with no commentary."
)


def _vlm_pdf_ocr(path: Path, db=None) -> "ExtractionResult | None":
    """Use the configured vision LLM to OCR a scanned (image-only) PDF.

    Renders each page with pdf2image at 150 DPI, encodes as JPEG base64,
    and POSTs to the vision model with a transcription prompt.
    Returns None when the vision model is not configured, pdf2image is
    unavailable, or the attempt fails entirely — so the caller falls through
    to markitdown cleanly.
    """
    try:
        from orivellum.configuration.config import load_config
        cfg = load_config()
        _vision_model: str = ""
        try:
            from orivellum.api._deps import get_db as _get_db
            _db = _get_db()
            _vision_model = _db.get_setting("vision_model", "") or cfg.serving.vision_model
        except Exception:
            _vision_model = cfg.serving.vision_model
        if not _vision_model:
            return None

        try:
            from pdf2image import convert_from_path  # type: ignore[import]
        except ImportError:
            logger.debug("pdf2image not installed — VLM PDF OCR unavailable")
            return None

        import base64
        import io
        from orivellum.capabilities.llm import llm_call

        pages_pil = convert_from_path(str(path), dpi=150)
        if not pages_pil:
            return None

        page_segs: list[PageSegment] = []
        page_headings: list[str] = []

        for i, page_img in enumerate(pages_pil, 1):
            try:
                if page_img.mode not in ("RGB", "L"):
                    page_img = page_img.convert("RGB")
                buf = io.BytesIO()
                page_img.save(buf, format="JPEG", quality=85)
                b64 = base64.b64encode(buf.getvalue()).decode()

                result = llm_call(
                    [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _VLM_OCR_PROMPT},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        ],
                    }],
                    base_url=cfg.serving.base_url,
                    model=_vision_model,
                    timeout=cfg.serving.extraction_timeout_sec,
                    purpose="extraction.ocr",
                    db=db,
                )
                text = (result.text or "").strip()
                if not text:
                    continue
                seg = PageSegment(page=i, text=text)
                first_line = text.splitlines()[0].strip()
                if first_line and len(first_line) < 120:
                    seg.heading = first_line
                    page_headings.append(first_line)
                page_segs.append(seg)
            except Exception as page_exc:
                logger.warning("VLM OCR failed on page %d of %s: %s", i, path.name, page_exc)

        if not page_segs:
            return None

        full = "\n\n".join(s.text for s in page_segs)
        logger.info(
            "VLM PDF OCR: %s — %d pages, %d words (model=%s)",
            path.name, len(page_segs), len(full.split()), _vision_model,
        )
        return ExtractionResult(
            kind="pdf",
            full_text=full,
            word_count=len(full.split()),
            pages=page_segs,
            headings=page_headings,
            meta={"ocr_engine": "vlm", "ocr_model": _vision_model},
        )
    except Exception as exc:
        logger.warning("VLM PDF OCR failed on %s: %s", path.name, exc)
    return None


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


def _extract_image_vision(path: Path, db=None) -> "ExtractionResult | None":
    """Use the configured vision LLM to describe image content.

    Returns an ExtractionResult when the vision model is configured and
    responds successfully, or None so the caller falls through to Tesseract.
    """
    try:
        from orivellum.configuration.config import load_config
        cfg = load_config()
        # Resolve vision model: DB setting overrides YAML config.
        # Import here (lazy) to avoid circular import at module load.
        _vision_model: str = ""
        try:
            from orivellum.api._deps import get_db as _get_db
            _db = _get_db()
            _vision_model = _db.get_setting("vision_model", "") or cfg.serving.vision_model
        except Exception:
            _vision_model = cfg.serving.vision_model
        if not _vision_model:
            return None

        import base64
        import io
        from PIL import Image as _PIL

        from orivellum.capabilities.llm import llm_call

        img = _PIL.open(path)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode()

        result = llm_call(
            [{
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
            base_url=cfg.serving.base_url,
            model=_vision_model,
            timeout=cfg.serving.extraction_timeout_sec,
            purpose="extraction.llm", db=db,
        )
        text = result.text
        if text and text.strip():
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


def _extract_image(path: Path, db=None) -> ExtractionResult:
    # --- vision LLM (primary when configured) ---
    vision_result = _extract_image_vision(path, db=db)
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
# HTML
# ---------------------------------------------------------------------------

def _extract_html(path: Path) -> ExtractionResult:
    """Strip tags and extract readable text from an HTML file."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self._skip = False
            self._parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: object) -> None:
            if tag in ("script", "style", "head", "nav", "footer"):
                self._skip = True

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style", "head", "nav", "footer"):
                self._skip = False
            if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
                self._parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self._skip and data.strip():
                self._parts.append(data)

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        stripper = _Stripper()
        stripper.feed(raw)
        full_text = " ".join(stripper._parts).strip()
        pages = [PageSegment(page=1, text=full_text)] if full_text else []
        return ExtractionResult(
            kind="html", full_text=full_text,
            word_count=len(full_text.split()), pages=pages,
        )
    except Exception as exc:
        logger.warning("HTML extraction failed for %s: %s", path.name, exc)
        return ExtractionResult(kind="html", full_text="", word_count=0)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def _extract_json(path: Path) -> ExtractionResult:
    """Format JSON as indented text for indexing."""
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        full_text = json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        # Malformed JSON — fall back to plain-text read
        full_text = path.read_text(encoding="utf-8", errors="replace")
    pages = [PageSegment(page=1, text=full_text)] if full_text.strip() else []
    return ExtractionResult(
        kind="json", full_text=full_text,
        word_count=len(full_text.split()), pages=pages,
    )


# ---------------------------------------------------------------------------
# ZIP archive
# ---------------------------------------------------------------------------

def _extract_zip(path: Path) -> ExtractionResult:
    """Extract text from every supported file inside a ZIP archive.

    Each member's content is concatenated under a ``=== filename ===`` header
    so the whole archive becomes one searchable document.

    Hardening:
    - Skips macOS metadata (``__MACOSX/`` entries and ``._*`` dotfiles)
    - Avoids basename collisions by prefixing each extracted file with its index
    - Records per-member failure reasons in the returned meta dict
    - Falls back to markitdown for formats not in the known list
    """
    import tempfile
    import zipfile

    # Extension → kind map (subset of _KIND_MAP; resolved at call-time)
    _EXT_KIND = {
        ".pdf": "pdf", ".docx": "docx", ".doc": "docx",
        ".xlsx": "excel", ".xls": "excel", ".csv": "csv",
        ".txt": "text", ".md": "markdown",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".html": "html", ".htm": "html",
        ".json": "json",
        ".py": "code", ".js": "code", ".ts": "code",
        ".pptx": "pptx", ".ppt": "pptx",
    }

    all_text: list[str] = []
    all_pages: list[PageSegment] = []
    all_headings: list[str] = []
    # Collect per-member results for informative error messages
    member_results: list[dict] = []   # {name, status, reason}

    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp = Path(tmpdir)
                for idx, name in enumerate(members):
                    basename = Path(name).name

                    # ── Skip macOS metadata ────────────────────────────────────
                    if name.startswith("__MACOSX/") or basename.startswith("._"):
                        continue

                    ext = Path(name).suffix.lower()
                    kind = _EXT_KIND.get(ext)
                    if not kind:
                        member_results.append({"name": name, "status": "skipped",
                                               "reason": f"unsupported type ({ext or 'no extension'})"})
                        continue

                    # ── Collision-safe extraction: prefix with index ────────────
                    safe_name = f"{idx:04d}_{basename}"
                    member_path = tmp / safe_name
                    try:
                        member_path.write_bytes(zf.read(name))
                    except Exception as exc:
                        logger.warning("ZIP: could not read member %s: %s", name, exc)
                        member_results.append({"name": name, "status": "error",
                                               "reason": f"read error: {exc}"})
                        continue

                    handler = _DISPATCH.get(kind, lambda p: _extract_fallback(p, kind))
                    try:
                        sub = handler(member_path)  # type: ignore[call-arg]
                    except Exception as exc:
                        logger.warning("ZIP: extraction failed for %s: %s", name, exc)
                        member_results.append({"name": name, "status": "error",
                                               "reason": f"extraction error: {exc}"})
                        continue

                    if not sub.full_text.strip():
                        member_results.append({"name": name, "status": "empty",
                                               "reason": "no readable text found"})
                        continue

                    header = f"=== {name} ==="
                    all_text.append(f"{header}\n{sub.full_text}")
                    all_headings.append(header)
                    offset = len(all_pages)
                    for seg in sub.pages:
                        all_pages.append(PageSegment(
                            page=offset + seg.page, text=seg.text, heading=name,
                        ))
                    member_results.append({"name": name, "status": "ok",
                                           "reason": f"{sub.word_count} words"})

    except zipfile.BadZipFile as exc:
        logger.error("Bad ZIP file %s: %s", path.name, exc)
        return ExtractionResult(kind="zip", full_text="", word_count=0,
                                meta={"error": f"Invalid ZIP file: {exc}"})

    full_text = "\n\n".join(all_text)
    ok_count   = sum(1 for r in member_results if r["status"] == "ok")
    fail_count = sum(1 for r in member_results if r["status"] in ("error", "empty"))
    skip_count = sum(1 for r in member_results if r["status"] == "skipped")

    meta: dict = {
        "zip_members": member_results,
        "zip_summary": f"{ok_count} extracted, {fail_count} empty/failed, {skip_count} skipped",
    }

    if not full_text.strip():
        # Build a human-readable explanation from member_results
        lines = []
        if not member_results:
            lines.append("ZIP archive is empty or contains no files.")
        else:
            tried = [r for r in member_results if r["status"] != "skipped"]
            if not tried:
                exts = list({Path(r["name"]).suffix.lower() for r in member_results})
                lines.append(
                    f"ZIP contains {len(member_results)} file(s) but none have a "
                    f"supported format. Found: {', '.join(exts) or 'unknown'}"
                )
            else:
                lines.append(f"ZIP contains {len(member_results)} file(s); none produced readable text:")
                for r in tried[:8]:
                    lines.append(f"  • {Path(r['name']).name}: {r['reason']}")
                if len(tried) > 8:
                    lines.append(f"  … and {len(tried) - 8} more")
        meta["user_message"] = " ".join(lines)

    return ExtractionResult(
        kind="zip", full_text=full_text,
        word_count=len(full_text.split()),
        pages=all_pages, headings=all_headings,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# faster-whisper — lazy singleton (loaded on first transcription call)
# ---------------------------------------------------------------------------
# Use threading.Lock to guard against concurrent first-call races.
# _fw_instance is:
#   None  — not yet attempted
#   False — attempted and failed (package missing or load error)
#   WhisperModel — successfully loaded

import threading as _threading

_fw_lock = _threading.Lock()
_fw_instance: object = None   # WhisperModel | False | None
_fw_loaded_size: str = ""     # model size string for the loaded singleton


def _get_faster_whisper(model_size: str = "base"):
    """Return a cached WhisperModel, loading it on first call.

    Returns None when the package is not installed or the model fails to load
    so callers can fall through to the metadata-only result without raising.
    """
    global _fw_instance, _fw_loaded_size
    if _fw_instance is not None:
        return None if _fw_instance is False else _fw_instance
    with _fw_lock:
        if _fw_instance is not None:
            return None if _fw_instance is False else _fw_instance
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
            logger.info("Loading faster-whisper model '%s' (first-run download may take a moment)…", model_size)
            model = WhisperModel(model_size, device="auto", compute_type="int8")
            _fw_instance = model
            _fw_loaded_size = model_size
            logger.info("faster-whisper model '%s' ready.", model_size)
        except ImportError:
            logger.info("faster-whisper not installed — local ASR fallback unavailable")
            _fw_instance = False
        except Exception as exc:
            logger.warning("faster-whisper failed to load model '%s': %s", model_size, exc)
            _fw_instance = False
    return None if _fw_instance is False else _fw_instance


def _is_faster_whisper_loaded() -> bool:
    """True only when the WhisperModel is actually in memory and ready to use."""
    return _fw_instance is not None and _fw_instance is not False


def _transcribe_faster_whisper(path: Path, model_size: str = "base") -> "ExtractionResult | None":
    """Transcribe *path* locally using faster-whisper.

    Returns None when the package is absent or transcription fails so the
    caller can fall through to the metadata-only result.
    """
    model = _get_faster_whisper(model_size)
    if model is None:
        return None
    try:
        segments, _info = model.transcribe(str(path))
        text = " ".join(seg.text for seg in segments).strip()
        if not text:
            logger.info("faster-whisper returned no text for %s", path.name)
            return None
        full = f"[Audio transcript: {path.name}]\n\n{text}"
        logger.info("faster-whisper transcription OK: %d words from %s", len(text.split()), path.name)
        return ExtractionResult(
            kind="audio",
            full_text=full,
            word_count=len(text.split()),
            pages=[PageSegment(page=1, text=text)],
            meta={
                "transcription": "faster_whisper",
                "model_size": model_size,
                "source": str(path.name),
            },
        )
    except Exception as exc:
        logger.warning("faster-whisper transcription failed for %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Audio extraction (AI server → faster-whisper → metadata-only)
# ---------------------------------------------------------------------------

def _extract_audio(path: Path, db=None) -> ExtractionResult:
    """Transcribe an audio file.

    Priority order:
      1. AI server /v1/audio/transcriptions (OpenAI-compatible Whisper API)
      2. faster-whisper local (CTranslate2 — ~4× faster than vanilla Whisper)
      3. Metadata-only placeholder so the file is still searchable/downloadable

    ``db`` (optional) is threaded from the public extract() entry point.
    """
    import urllib.request as _urlr
    import json as _json
    import mimetypes as _mt

    base_url: str = ""
    asr_model: str = "whisper-1"
    asr_local_model: str = "base"
    try:
        # Prefer the request-scoped config when running inside the FastAPI server.
        # Fall back to load_config() when called from a standalone script or test.
        try:
            from orivellum.api._deps import get_config as _get_cfg
            _cfg_obj = _get_cfg()
            base_url        = _cfg_obj.serving.base_url.rstrip("/")
            asr_model       = _cfg_obj.serving.asr_model
            asr_local_model = getattr(_cfg_obj.serving, "asr_local_model", "base")
        except Exception:
            from orivellum.configuration.config import load_config as _load_cfg
            _cfg_obj        = _load_cfg()
            base_url        = _cfg_obj.serving.base_url.rstrip("/")
            asr_model       = getattr(_cfg_obj.serving, "asr_model", "whisper-1")
            asr_local_model = getattr(_cfg_obj.serving, "asr_local_model", "base")
    except Exception:
        pass

    def _metadata_only(msg: str) -> ExtractionResult:
        text = (
            f"Audio file: {path.name}\n"
            f"Note: {msg}\n\n"
            "To enable transcription: (1) ensure your AI server exposes "
            "/v1/audio/transcriptions, or (2) install faster-whisper "
            "(`uv add faster-whisper`) for fully offline transcription."
        )
        return ExtractionResult(
            kind="audio", full_text=text, word_count=len(text.split()),
            meta={"transcription": None, "reason": msg},
        )

    # ── 0. Optional audio enhancement (DeepFilterNet3) ───────────────────────
    # Pre-process the audio before Whisper to remove background noise, room
    # reverb, and non-stationary interference.  Best-effort — any failure
    # silently falls back to the original file so transcription always runs.
    import tempfile as _tf_enh, shutil as _sh_enh  # noqa: E401
    _enh_tmp: str | None = None
    transcribe_path = path
    try:
        if db is not None and \
                db.get_setting("audio_enhance_enabled", "false").lower() == "true":
            from orivellum.capabilities.enhancement import (  # noqa: PLC0415
                enhance_audio as _enhance_audio,
                is_available as _dfn_available,
            )
            if _dfn_available():
                _enh_tmp = _tf_enh.mkdtemp(prefix="orivellum_dfn_")
                _ep = _enhance_audio(path, output_dir=Path(_enh_tmp))
                if _ep != path:
                    transcribe_path = _ep
                    logger.info(
                        "DeepFilterNet3 applied for %s; transcribing enhanced audio",
                        path.name,
                    )
    except Exception as _enh_err:
        logger.debug("Audio enhancement skipped: %s", _enh_err)

    try:
        # ── 1. AI server (Lemonade / any OpenAI-compatible Whisper endpoint) ─
        ai_server_exc: str = ""
        if base_url:
            try:
                mime_type, _ = _mt.guess_type(path.name)
                mime_type = mime_type or "application/octet-stream"

                import uuid as _uuid
                boundary = f"---{_uuid.uuid4().hex}"
                filename = path.name  # keep original filename in the multipart header

                with open(transcribe_path, "rb") as fh:
                    file_bytes = fh.read()

                body_parts: list[bytes] = []
                body_parts.append(
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                    f"Content-Type: {mime_type}\r\n\r\n"
                    .encode("utf-8") + file_bytes + b"\r\n"
                )
                body_parts.append(
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="model"\r\n\r\n'
                    f"{asr_model}\r\n"
                    .encode("utf-8")
                )
                body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
                body = b"".join(body_parts)

                req = _urlr.Request(
                    f"{base_url}/audio/transcriptions",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    method="POST",
                )
                with _urlr.urlopen(req, timeout=120) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                transcript = data.get("text", "").strip()
                if transcript:
                    logger.info("AI server transcription OK: %d words from %s",
                                len(transcript.split()), path.name)
                    return ExtractionResult(
                        kind="audio",
                        full_text=f"[Audio transcript: {path.name}]\n\n{transcript}",
                        word_count=len(transcript.split()),
                        pages=[PageSegment(page=1, text=transcript)],
                        meta={"transcription": "ai_server", "asr_model": asr_model,
                              "source": str(path.name),
                              "enhanced": transcribe_path != path},
                    )
                ai_server_exc = "AI server returned an empty transcription"
                logger.warning("AI server transcription: empty response for %s", path.name)
            except Exception as exc:
                ai_server_exc = str(exc)
                logger.info("AI server transcription unavailable for %s (%s) — trying faster-whisper",
                            path.name, exc)

        # ── 2. faster-whisper local ───────────────────────────────────────────
        fw_result = _transcribe_faster_whisper(transcribe_path, asr_local_model)
        if fw_result is not None:
            return fw_result

        # ── 3. Metadata-only ─────────────────────────────────────────────────
        reason = ai_server_exc or "AI server URL not configured"
        if not _is_faster_whisper_loaded():
            reason += " | faster-whisper not installed"
        return _metadata_only(reason)

    finally:
        # Always clean up the temporary enhanced audio file.
        if _enh_tmp:
            _sh_enh.rmtree(_enh_tmp, ignore_errors=True)


_DISPATCH: dict[str, object] = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "excel": _extract_excel,
    "csv": _extract_csv,
    "text": lambda p: _extract_text(p, "text"),
    "markdown": lambda p: _extract_text(p, "markdown"),
    "html": _extract_html,
    "json": _extract_json,
    "zip": _extract_zip,
    "image": _extract_image,
    "audio": _extract_audio,
}


def extract(path: str | Path, kind: str, db=None) -> ExtractionResult:
    """Extract text from *path* using the handler for *kind*.

    Falls back to markitdown for unknown kinds.  ``db`` (when supplied) is
    threaded to handlers that make LLM calls (image vision) so telemetry is
    recorded.
    """
    path = Path(path)
    handler = _DISPATCH.get(kind, lambda p: _extract_fallback(p, kind))
    try:
        if kind == "image":
            result = _extract_image(path, db=db)
        elif kind == "audio":
            result = _extract_audio(path, db=db)
        elif kind == "pdf":
            result = _extract_pdf(path, db=db)
        else:
            result = handler(path)  # type: ignore[call-arg]
    except Exception as exc:
        logger.error("Extraction error on %s (%s): %s", path.name, kind, exc)
        result = ExtractionResult(kind=kind, full_text="", word_count=0)
    logger.info("Extracted %s: %d words, %d pages", path.name, result.word_count, len(result.pages))
    return result
