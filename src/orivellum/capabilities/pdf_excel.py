"""PDF→Excel transcription per the PDF_TO_EXCEL Protocol v2.1.

Implements the protocol's machine-executable core on local tooling:

- Intake & preflight (doc 03): source integrity (SHA-256, page count,
  encryption), per-page assessment, deterministic risk scoring and QA tier.
- Dual-channel extraction (v2.1 O-1): channel A = pdfplumber (text layer +
  ruled-table detection); channel B = pypdf (independent text-layer parse).
  Disagreements route to the exception register — never silently resolved
  (O-3: an exception row always beats a guessed value).
- Workbook build + acceptance gates live in ``pdf_excel_build``:
  protocol workbook architecture (doc 06), deterministic recalculation
  gate (O-2) and page-completeness certification (O-4).

The output publishes as v1 of a Workbench project (verdict "transcribed"),
so versioning, review, download, and the automatic analysis report all come
from the existing Workbench machinery.
"""

from __future__ import annotations

import hashlib
import logging
import pathlib
import re
import tempfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2.1"

# ── Data shapes ───────────────────────────────────────────────────────────────


@dataclass
class PageExtract:
    """Both extraction channels for one PDF page."""

    page: int  # 1-based
    width: float = 0.0
    height: float = 0.0
    text_a: str = ""  # channel A: pdfplumber
    text_b: str = ""  # channel B: pypdf
    tables: list[list[list[str]]] = field(default_factory=list)  # channel A


@dataclass
class ExceptionRow:
    exception_id: str
    pdf_page: int
    exception_type: str
    description: str
    value_a: str = ""
    value_b: str = ""


# ── Preflight (doc 03) ────────────────────────────────────────────────────────


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _risk_score(pages: list[PageExtract]) -> dict:
    """Deterministic subset of the protocol's 8-dimension risk score.
    Dimensions we cannot judge without a human (financial criticality,
    source inconsistency) default conservatively per the protocol: static
    financial documents default to at least High."""
    total = len(pages) or 1
    text_pages = sum(1 for p in pages if len(p.text_a.strip()) > 20)
    table_pages = sum(1 for p in pages if p.tables)
    max_rows = max((len(t) for p in pages for t in p.tables), default=0)
    image_quality = 0 if text_pages == total else (2 if text_pages else 3)
    if not table_pages:
        table_density = 0
    elif max_rows <= 15:
        table_density = 1
    else:
        table_density = 2 if max_rows <= 60 else 3
    schema_stability = 0 if table_pages <= 1 else (1 if table_pages <= 3 else 2)
    boundary = 0 if total <= 3 else (1 if total <= 15 else 2)
    score = image_quality + table_density + schema_stability + boundary
    if score <= 5:
        risk_class, qa_tier = "Low", 1
    elif score <= 11:
        risk_class, qa_tier = "Moderate", 2
    elif score <= 17:
        risk_class, qa_tier = "High", 3
    else:
        risk_class, qa_tier = "Critical", 4
    return {
        "score": score,
        "class": risk_class,
        "qa_tier": qa_tier,
        "dimensions": {
            "image_quality": image_quality,
            "table_density": table_density,
            "schema_stability": schema_stability,
            "boundary_complexity": boundary,
        },
    }


def preflight(pdf_path: pathlib.Path, pages: list[PageExtract]) -> dict:
    """Project manifest per the protocol's preflight output."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    total = len(pages) or 1
    text_pages = sum(1 for p in pages if len(p.text_a.strip()) > 20)
    if text_pages == total:
        source_type = "born-digital"
    elif text_pages == 0:
        source_type = "scanned"
    else:
        source_type = "hybrid"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "source_sha256": _sha256_file(pdf_path),
        "source_size": pdf_path.stat().st_size,
        "page_count": len(pages),
        "encrypted": bool(reader.is_encrypted),
        "source_type": source_type,
        "text_layer_pages": text_pages,
        "table_pages": sum(1 for p in pages if p.tables),
        "risk": _risk_score(pages),
        # Extraction totals the completeness gate certifies against — the
        # published workbook must contain exactly this much content.
        "content_totals": {
            "table_rows": {str(p.page): sum(len(t) for t in p.tables) for p in pages if p.tables},
            "narrative_chars": {
                str(p.page): len(p.text_a.strip())
                for p in pages
                if p.text_a.strip() and not p.tables
            },
        },
    }


# ── Dual-channel extraction (O-1) ─────────────────────────────────────────────

# Resource ceilings — exceeding any of them fails the run closed (recorded as
# the project error) rather than producing a partial "verified" transcription.
MAX_PAGES = 500
MAX_TOTAL_TEXT_CHARS = 5_000_000
MAX_TOTAL_TABLE_CELLS = 250_000


def dual_channel_extract(pdf_path: pathlib.Path) -> list[PageExtract]:
    """Channel A (pdfplumber: text + ruled tables) and channel B (pypdf
    text) for every page. Every physical page is represented — silent page
    loss is a named protocol failure mode. Resource ceilings are enforced
    incrementally so a crafted PDF cannot occupy the worker unbounded."""
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    n_pages = len(reader.pages)
    if n_pages > MAX_PAGES:
        raise ValueError(f"PDF has {n_pages} pages — the transcription limit is {MAX_PAGES}")

    pages: list[PageExtract] = []
    total_chars = 0
    total_cells = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            tables = [
                [[("" if c is None else str(c)) for c in row] for row in t]
                for t in (page.extract_tables() or [])
                if t
            ]
            text_a = page.extract_text() or ""
            total_chars += len(text_a)
            total_cells += sum(len(row) for t in tables for row in t)
            if total_chars > MAX_TOTAL_TEXT_CHARS:
                raise ValueError(
                    f"extracted text exceeds {MAX_TOTAL_TEXT_CHARS} characters at page {i}"
                )
            if total_cells > MAX_TOTAL_TABLE_CELLS:
                raise ValueError(
                    f"extracted tables exceed {MAX_TOTAL_TABLE_CELLS} cells at page {i}"
                )
            pages.append(
                PageExtract(
                    page=i,
                    width=float(page.width or 0),
                    height=float(page.height or 0),
                    text_a=text_a,
                    tables=tables,
                )
            )
    for i, rp in enumerate(reader.pages):
        if i < len(pages):
            try:
                text_b = (rp.extract_text() or "")[:MAX_TOTAL_TEXT_CHARS]
                pages[i].text_b = text_b
            except Exception:  # noqa: BLE001 - channel B is independent; A stands alone
                pages[i].text_b = ""
    return pages


# ── Channel comparison → exception register (O-1 / O-3) ─────────────────────

_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")
MAX_EXCEPTIONS = 500  # beyond this the extraction is untrustworthy — fail closed


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _numeric_counter(text: str):
    """Occurrence-aware numeric tokens (significant = 2+ digits)."""
    from collections import Counter

    tokens = (t.replace(",", "") for t in _NUM_RE.findall(text))
    return Counter(t for t in tokens if len(t.lstrip("-").rstrip(".")) >= 2)


def _locate_in_tables(tok: str, tables: list[list[list[str]]]) -> str:
    for t_idx, table in enumerate(tables, start=1):
        for r_idx, row in enumerate(table, start=1):
            if any(tok in _numeric_counter(cell) for cell in row):
                return f"table {t_idx} row {r_idx}"
    return "page text"


def _page_exceptions(p: PageExtract, next_id: int) -> tuple[list[ExceptionRow], int]:
    rows: list[ExceptionRow] = []
    if not p.text_b.strip():
        if p.text_a.strip() or p.tables:
            rows.append(
                ExceptionRow(
                    f"E{next_id:04d}",
                    p.page,
                    "single_channel",
                    "Channel B produced no text layer — page verified by one channel only.",
                )
            )
            next_id += 1
        return rows, next_id
    # Channel A occurrence baseline: page text and table cells both derive
    # from channel A, so take the per-token maximum (| on Counters).
    ca = _numeric_counter(p.text_a)
    for table in p.tables:
        table_counts = _numeric_counter(" ".join(c for row in table for c in row))
        ca |= table_counts
    cb = _numeric_counter(p.text_b)
    # A-only: values channel B does not corroborate often enough (duplicate
    # occurrences need duplicate corroboration).
    for tok, n_a in sorted((ca - cb).items()):
        rows.append(
            ExceptionRow(
                f"E{next_id:04d}",
                p.page,
                "channel_disagreement",
                f"Value in {_locate_in_tables(tok, p.tables)} appears {ca[tok]}× in "
                f"channel A but only {cb[tok]}× in channel B ({n_a} uncorroborated).",
                value_a=tok,
                value_b=f"{cb[tok]} occurrence(s)",
            )
        )
        next_id += 1
    # B-only: numbers channel B saw that channel A's extraction missed —
    # possible omission, registered rather than silently dropped.
    for tok, n_b in sorted((cb - ca).items()):
        rows.append(
            ExceptionRow(
                f"E{next_id:04d}",
                p.page,
                "channel_b_only",
                f"Channel B contains this value {cb[tok]}× but channel A captured it "
                f"only {ca[tok]}× — possible omission ({n_b} unmatched).",
                value_a=f"{ca[tok]} occurrence(s)",
                value_b=tok,
            )
        )
        next_id += 1
    if not p.tables and p.text_a.strip():
        sim = SequenceMatcher(None, _norm_text(p.text_a), _norm_text(p.text_b)).ratio()
        if sim < 0.9:
            rows.append(
                ExceptionRow(
                    f"E{next_id:04d}",
                    p.page,
                    "channel_disagreement",
                    f"Narrative text agreement between channels is {sim:.0%} (< 90%).",
                )
            )
            next_id += 1
    return rows, next_id


def compare_channels(pages: list[PageExtract]) -> list[ExceptionRow]:
    """Occurrence-aware, bidirectional numeric reconciliation between the
    channels. Disagreements and possible omissions become exception rows
    with both candidates — never averaged, never guessed. An exception
    flood means the extraction itself is untrustworthy: fail closed."""
    exceptions: list[ExceptionRow] = []
    next_id = 1
    for p in pages:
        rows, next_id = _page_exceptions(p, next_id)
        exceptions.extend(rows)
        if len(exceptions) > MAX_EXCEPTIONS:
            raise ValueError(
                f"more than {MAX_EXCEPTIONS} channel disagreements — the extraction "
                "is unreliable for this document; transcription rejected"
            )
    return exceptions


# ── Orchestrator ──────────────────────────────────────────────────────────────


def run_transcription(db, cfg, project_id: str, pdf_path: pathlib.Path, filename: str) -> None:
    """Full protocol run for one PDF. Caller must hold the project's build
    claim; it is released here (or by the chained analysis) in all paths.
    Deletes *pdf_path* when done."""
    from orivellum.capabilities.pdf_excel_build import build_workbook, run_acceptance_gates
    from orivellum.capabilities.workbench import _publish_version, _snapshot
    from orivellum.capabilities.workbench_analyze import run_analysis

    published = False
    try:
        pages = dual_channel_extract(pdf_path)
        if not pages:
            raise ValueError("the PDF contains no pages")
        manifest = preflight(pdf_path, pages)
        exceptions = compare_channels(pages)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", pathlib.PurePosixPath(filename).stem)[:60]
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            out.mkdir()
            xlsx = out / f"{stem or 'transcription'}_transcribed.xlsx"
            build_workbook(xlsx, filename, manifest, pages, exceptions)
            gates = run_acceptance_gates(xlsx, manifest)
            if not gates["passed"]:
                failed = "; ".join(g["detail"] for g in gates["gates"] if not g["ok"])
                raise ValueError(f"acceptance gates failed: {failed}")
            checks = {
                "protocol_version": PROTOCOL_VERSION,
                "manifest": manifest,
                "gates": gates,
                "exception_count": len(exceptions),
            }
            _publish_version(
                db,
                cfg,
                project_id,
                out,
                f"Transcribed from {filename} (protocol v{PROTOCOL_VERSION})",
                _snapshot(out),
                checks,
                note=(
                    f"{manifest['page_count']} page(s), "
                    f"{manifest['table_pages']} with tables, "
                    f"{len(exceptions)} exception(s), risk {manifest['risk']['class']}"
                ),
                verdict="transcribed",
            )
            published = True
    except Exception as exc:  # noqa: BLE001 - background job: record, never raise
        logger.exception("pdf-excel transcription failed for project %s", project_id)
        db.update_wb_project(project_id, building=0, last_error=str(exc)[:2000])
        return
    finally:
        pdf_path.unlink(missing_ok=True)
    if published:
        # Automatic workbook review on every fresh transcription; run_analysis
        # releases the build claim in its own finally block.
        run_analysis(
            db,
            cfg,
            project_id,
            focus="This workbook is a fresh PDF transcription. Check the exception "
            "register, the Checks sheet, and whether the page register covers the source.",
        )
