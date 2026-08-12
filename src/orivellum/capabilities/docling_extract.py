"""Docling layout-aware PDF extraction — the preferred first tier.

Docling (MIT, IBM Research) does layout-aware parsing: multi-column reading
order, table structure recognition, and clean Markdown export.  It is an
OPTIONAL dependency — install with ``uv sync --extra docling`` (or
``pip install docling``).  When it is missing, disabled, or fails on a
document, extraction silently proceeds through the existing tiers
(pdfplumber → pypdf → VLM OCR → markitdown) and document readiness is
unaffected.

Availability is probed once and cached (``probe(force=True)`` re-checks
without a restart).  The tier is additionally gated by the ``docling_enabled``
system setting, which defaults to enabled — so a successful install starts
working without any configuration step.

Windows note: Docling itself supports Windows; the probe is a pure import
check with no shell/bash dependency, so it is safe on every platform.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.capabilities.extraction import ExtractionResult

logger = logging.getLogger(__name__)

INSTALL_HINT = "uv sync --extra docling   (or: pip install docling)"

# ── availability probe (cached) ──────────────────────────────────────────────
_probe_lock = threading.Lock()
_probe_cache: dict | None = None


def probe(force: bool = False) -> dict:
    """Passive availability check — import-spec only, never loads models.

    Returns {"available": bool, "error": str | None, "install_hint": str | None}.
    Cached after the first call; ``force=True`` re-checks (e.g. after the user
    installs the package) without a server restart.  Never raises.
    """
    global _probe_cache
    with _probe_lock:
        if _probe_cache is not None and not force:
            return _probe_cache
        try:
            import importlib.util

            spec = importlib.util.find_spec("docling")
            if spec is None:
                _probe_cache = {
                    "available": False,
                    "error": "docling package is not installed",
                    "install_hint": INSTALL_HINT,
                }
            else:
                _probe_cache = {"available": True, "error": None, "install_hint": None}
        except Exception as exc:  # noqa: BLE001 — probe must never raise
            _probe_cache = {
                "available": False,
                "error": f"docling probe failed: {exc}",
                "install_hint": INSTALL_HINT,
            }
        return _probe_cache


def is_enabled(db=None) -> bool:
    """True when the Docling tier should be attempted.

    Requires BOTH the package to be importable and the ``docling_enabled``
    setting (default: enabled) not to be switched off.  With no db handle
    (direct ``extract()`` calls in scripts/tests) the setting defaults on.
    """
    if not probe()["available"]:
        return False
    if db is not None:
        try:
            if db.get_setting("docling_enabled", "true").lower() != "true":
                return False
        except Exception:  # noqa: BLE001 — a settings read error never blocks extraction
            pass
    return True


# ── converter (loaded once — model init is expensive) ────────────────────────
_converter_lock = threading.Lock()
_converter = None
# Docling's DocumentConverter caches a stateful PdfPipeline whose execute()
# is not safe for concurrent calls — two pipeline-worker threads converting
# at once can corrupt each other's run.  All convert() calls are serialized
# through this dedicated lock (separate from the init lock so a slow
# conversion never blocks a cheap availability check).
_convert_lock = threading.Lock()


def _get_converter():
    """Build (once) and return the shared DocumentConverter.

    Table-structure recognition on, Docling-internal OCR off — scanned pages
    stay the job of the existing VLM/tesseract tiers, per the design decision
    that Docling upgrades layout, not OCR.
    """
    global _converter
    with _converter_lock:
        if _converter is not None:
            return _converter
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = False
        opts.do_table_structure = True
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )
        return _converter


def _item_page(item) -> int:
    """Best-effort page number for a Docling document item (1-based)."""
    try:
        prov = getattr(item, "prov", None) or []
        if prov:
            return int(getattr(prov[0], "page_no", 1) or 1)
    except Exception:  # noqa: BLE001
        pass
    return 1


def _item_markdown(item, doc) -> str:
    """Serialize one Docling item to readable text (tables become Markdown)."""
    export = getattr(item, "export_to_markdown", None)
    if callable(export):
        # Newer docling-core wants the document for cell resolution; older
        # versions take no argument.  Try both.
        try:
            return (export(doc) or "").strip()
        except TypeError:
            try:
                return (export() or "").strip()
            except Exception:  # noqa: BLE001
                return ""
        except Exception:  # noqa: BLE001
            return ""
    return (getattr(item, "text", "") or "").strip()


def extract_pdf_docling(path: Path) -> ExtractionResult:
    """Run Docling on *path* and map its output to an ExtractionResult.

    Raises on any Docling failure — the caller (``docling_pdf_tier``) treats
    every exception as "this tier did not produce text" and falls through.
    """
    from orivellum.capabilities.extraction import ExtractionResult, PageSegment

    converter = _get_converter()
    with _convert_lock:
        conv = converter.convert(str(path))
    doc = conv.document

    # Group serialized items per page so downstream chunking keeps page
    # provenance.  If per-item iteration fails (docling-core API drift),
    # fall back to whole-document markdown as a single page.
    per_page: dict[int, list[str]] = {}
    headings: list[str] = []
    table_count = 0
    try:
        for item, _level in doc.iterate_items():
            text = _item_markdown(item, doc)
            if not text:
                continue
            if type(item).__name__ == "TableItem":
                table_count += 1
            label = str(getattr(item, "label", "") or "").lower()
            if "header" in label or "title" in label:
                first = text.splitlines()[0].strip()
                if first and len(first) < 120:
                    headings.append(first.lstrip("# ").strip())
            per_page.setdefault(_item_page(item), []).append(text)
    except Exception as exc:  # noqa: BLE001 — structure walk is best-effort
        logger.warning("Docling item walk failed on %s (%s) — using flat markdown", path.name, exc)
        per_page = {}

    if per_page:
        pages = [
            PageSegment(page=n, text="\n\n".join(parts)) for n, parts in sorted(per_page.items())
        ]
        for seg in pages:
            first_line = seg.text.splitlines()[0].strip()
            if first_line and len(first_line) < 120:
                seg.heading = first_line.lstrip("# ").strip()
        full = "\n\n".join(p.text for p in pages)
    else:
        full = (doc.export_to_markdown() or "").strip()
        pages = [PageSegment(page=1, text=full)] if full else []

    return ExtractionResult(
        kind="pdf",
        full_text=full,
        word_count=len(full.split()),
        pages=pages,
        headings=headings,
        meta={
            "extraction_method": "docling",
            "docling_tables": table_count,
        },
    )


def docling_pdf_tier(path: Path, db=None) -> ExtractionResult | None:
    """The pipeline-facing entry point: try Docling, or return None.

    Returns None when the tier is unavailable, disabled, produced no text, or
    failed for ANY reason — the caller then proceeds through the existing
    tiers.  This function never raises and never touches document state, so
    Docling being broken can never fail ingestion.
    """
    try:
        if not is_enabled(db):
            return None
        result = extract_pdf_docling(path)
        if not result.ok:
            logger.info("Docling produced no text for %s — falling through", path.name)
            return None
        logger.info(
            "Docling extracted %s: %d words, %d pages, %d tables",
            path.name,
            result.word_count,
            len(result.pages),
            result.meta.get("docling_tables", 0),
        )
        return result
    except Exception as exc:  # noqa: BLE001 — tier failure must never block extraction
        logger.warning("Docling tier failed on %s: %s — falling through", path.name, exc)
        return None
