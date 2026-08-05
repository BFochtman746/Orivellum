"""Document generation capabilities — Excel, DOCX, PDF, PPTX.

Each generator:
  1. Pulls a Work's knowledge, documents, and tasks from the database.
  2. Produces a file in data/outputs/generate/{work_id}/
  3. Registers the output as a library document (tier=artifact) with
     provenance metadata so it is tracked, searchable, and recallable.
  4. Returns (file_path, doc_id) for the caller to serve.

No-pollution guarantee: generated outputs are always registered as
ARTIFACT tier — they are never promoted into a Work's canonical corpus.
"""
from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig

logger = logging.getLogger("orivellum.generate")

# ── Shared helpers ─────────────────────────────────────────────────────────────

def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")


def _ensure_dir(cfg: "OrivellumConfig", work_id: str) -> Path:
    """Return (and create) data/outputs/generate/{work_id}/"""
    d = Path(cfg.data_dir) / "outputs" / "generate" / work_id
    d.mkdir(parents=True, exist_ok=True)
    return d


_CHUNK_SIZE = 1_000  # characters per FTS chunk


def _register_output(
    doc_path: Path,
    work_id: "str | None",
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    format_label: str,
    title: str,
    text_content: str = "",
) -> str:
    """Register a generated file as a library document; return its doc_id.

    work_id may be None for unscoped outputs (e.g. tax packages that span all Works).

    Amendment-1 save/process/recall invariant: the full text is written to
    documents.extracted_text AND split into chunks that are inserted into
    chunks + chunks_fts so the document is findable via the application's
    document full-text search path (which queries chunks_fts, not extracted_text).
    """
    # Amendment-1 path fix: content_path must be relative to lib_root (data_dir/library)
    # because every Library endpoint resolves paths as lib_root / content_path.
    # _ensure_lib_symlink creates a zero-cost symlink under lib_root/generated/ for
    # files stored outside lib_root (outputs/generate/...) and returns the correct
    # lib-root-relative path so Library downloads, reprocess, and resolution all work.
    from orivellum.capabilities.persist import _ensure_lib_symlink
    lib_root = Path(cfg.data_dir) / "library"
    rel = _ensure_lib_symlink(doc_path, lib_root)
    sha = hashlib.sha256(doc_path.read_bytes()).hexdigest() if doc_path.exists() else None
    scope_label = work_id or "library"

    doc = db.create_document(
        title=title,
        source=f"generated/{format_label}/{scope_label}",
        sha256=sha,
        kind=doc_path.suffix.lstrip("."),
        work_id=work_id,
        content_path=rel,
        meta={
            "provenance": "generation",
            "origin_id": work_id,
            "format": format_label,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        tier="artifact",
    )
    doc_id = doc["id"]

    body = text_content or f"Generated {format_label} output for work {work_id}"

    # Mark the document ready and store the full text for direct lookup
    db.update_document_extracted(
        doc_id,
        extracted_text=body,
        word_count=max(len(body.split()), 1),
        readiness="ready",
    )

    # Split into overlapping chunks and index into chunks_fts so the document
    # is searchable via the application's standard document search path.
    words = body.split()
    # Build ~1 000-char chunks from the word list (no mid-word splits)
    chunk_texts: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        wl = len(word) + 1
        if current_len + wl > _CHUNK_SIZE and current:
            chunk_texts.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += wl
    if current:
        chunk_texts.append(" ".join(current))

    for page, chunk in enumerate(chunk_texts):
        try:
            db.add_chunk(doc_id, chunk, page=page)
        except Exception:
            logger.warning("_register_output: failed to add chunk %d for doc %s", page, doc_id)

    # Amendment-1: embed all chunks in the background so generated documents
    # are semantically searchable without waiting for the nightly backfill.
    # Wrapped in a try/except so thread failures are logged, never raised.
    import threading as _t
    from orivellum.capabilities.persist import record_provenance as _prov

    def _embed_bg(_doc_id=doc_id, _db=db) -> None:
        try:
            from orivellum.capabilities.embeddings import embed_chunks_for_doc
            embed_chunks_for_doc(_doc_id, _db)
        except Exception as _exc:
            logger.debug("_register_output background embed failed (non-fatal): %s", _exc)

    _t.Thread(target=_embed_bg, daemon=True).start()
    _prov(doc_id, "generation", db, work_id=work_id)

    return doc_id


def _format_knowledge_text(items: list[dict], max_items: int = 500) -> str:
    """Build a plain-text version of knowledge items for embedding."""
    lines = []
    for it in items[:max_items]:
        kind = it.get("kind", "note")
        text = it.get("text", "").strip()
        if text:
            lines.append(f"[{kind.upper()}] {text}")
    return "\n".join(lines)


def _slug(text: str, max_len: int = 40) -> str:
    import re
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return s[:max_len].strip("_")


# ── Excel ──────────────────────────────────────────────────────────────────────

def generate_excel(work_id: str, db: "OrivellumDB", cfg: "OrivellumConfig") -> tuple[Path, str]:
    """Generate an xlsx workbook summarising a Work; return (file_path, doc_id)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    work = db.get_work(work_id)
    if not work:
        raise ValueError(f"Work {work_id!r} not found")

    knowledge = db.list_knowledge(work_id=work_id, limit=500)
    docs = db.list_knowledge  # we'll use the raw query below
    tasks = db.list_tasks(work_id=work_id)
    # Documents via DB
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT id, title, kind, readiness, source, created_at FROM documents WHERE work_id=? ORDER BY created_at DESC",
            (work_id,),
        ).fetchall()
    doc_list = [dict(r) for r in doc_rows]

    wb = openpyxl.Workbook()

    # ── Styles ──
    hdr_fill = PatternFill("solid", fgColor="1E293B")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    title_font = Font(bold=True, size=14)
    sub_font = Font(size=10, color="64748B")
    thin = Side(style="thin", color="E2E8F0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def _apply_header(ws, headers: list[str], row: int = 1) -> None:
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = hdr_font
            cell.fill = hdr_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")

    def _autowidth(ws) -> None:
        for col in ws.columns:
            max_len = max((len(str(c.value or "")) for c in col), default=8)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 60)

    # ── Sheet 1: Summary ──
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = work.get("title", "Untitled Work")
    ws["A1"].font = title_font
    ws["A2"] = work.get("description") or ""
    ws["A2"].font = sub_font
    ws["A4"] = "Generated"
    ws["B4"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ws["A5"] = "Documents"
    ws["B5"] = len(doc_list)
    ws["A6"] = "Knowledge items"
    ws["B6"] = len(knowledge)
    ws["A7"] = "Open tasks"
    ws["B7"] = sum(1 for t in tasks if t.get("status") == "pending")
    for r in range(4, 8):
        ws[f"A{r}"].font = Font(bold=True, size=10)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 40

    # ── Sheet 2: Knowledge ──
    ws2 = wb.create_sheet("Knowledge")
    headers2 = ["#", "Kind", "Text", "Confidence", "Created"]
    _apply_header(ws2, headers2)
    for i, item in enumerate(knowledge, 1):
        text = (item.get("text") or "").replace("\n", " ").strip()[:300]
        ws2.append([
            i,
            item.get("kind", "note"),
            text,
            round(item.get("confidence", 0) * 100) if item.get("confidence") else "",
            (item.get("created_at") or "")[:10],
        ])
    for row in ws2.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
    _autowidth(ws2)

    # ── Sheet 3: Documents ──
    ws3 = wb.create_sheet("Documents")
    headers3 = ["#", "Title", "Kind", "Readiness", "Created"]
    _apply_header(ws3, headers3)
    for i, d in enumerate(doc_list, 1):
        ws3.append([
            i,
            (d.get("title") or "")[:80],
            d.get("kind", ""),
            d.get("readiness", ""),
            (d.get("created_at") or "")[:10],
        ])
    for row in ws3.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
    _autowidth(ws3)

    # ── Sheet 4: Tasks ──
    ws4 = wb.create_sheet("Tasks")
    headers4 = ["#", "Status", "Priority", "Text", "Created"]
    _apply_header(ws4, headers4)
    for i, t in enumerate(tasks, 1):
        ws4.append([
            i,
            t.get("status", ""),
            t.get("priority", 0),
            (t.get("text") or "")[:200],
            (t.get("created_at") or "")[:10],
        ])
    for row in ws4.iter_rows(min_row=2):
        for cell in row:
            cell.border = border
    _autowidth(ws4)

    # ── Save ──
    out_dir = _ensure_dir(cfg, work_id)
    slug = _slug(work.get("title", "work"))
    fname = f"{slug}_{_now_label()}.xlsx"
    fpath = out_dir / fname
    wb.save(str(fpath))

    text = _format_knowledge_text(knowledge)
    title_out = f"Research Workbook — {work.get('title', 'Work')}"
    doc_id = _register_output(fpath, work_id, db, cfg, "excel", title_out, text)
    logger.info("Generated Excel: %s → doc %s", fpath.name, doc_id)
    return fpath, doc_id


# ── DOCX ───────────────────────────────────────────────────────────────────────

def generate_docx_report(work_id: str, db: "OrivellumDB", cfg: "OrivellumConfig") -> tuple[Path, str]:
    """Generate a .docx research report from a Work; return (file_path, doc_id)."""
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    work = db.get_work(work_id)
    if not work:
        raise ValueError(f"Work {work_id!r} not found")

    knowledge = db.list_knowledge(work_id=work_id, limit=500)
    tasks = db.list_tasks(work_id=work_id)
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT title, kind, readiness FROM documents WHERE work_id=? ORDER BY created_at DESC",
            (work_id,),
        ).fetchall()
    doc_list = [dict(r) for r in doc_rows]

    document = Document()

    # ── Title ──
    h1 = document.add_heading(work.get("title", "Research Report"), 0)
    h1.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = h1.runs[0]
    run.font.color.rgb = RGBColor(0x0F, 0x17, 0x2A)

    desc = work.get("description") or ""
    if desc:
        p = document.add_paragraph(desc)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.runs[0].font.color.rgb = RGBColor(0x64, 0x74, 0x8B)
        p.runs[0].font.italic = True

    meta_p = document.add_paragraph(f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y')}")
    meta_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta_p.runs[0].font.size = Pt(9)
    meta_p.runs[0].font.color.rgb = RGBColor(0x94, 0xA3, 0xB8)
    document.add_page_break()

    # ── Stats ──
    document.add_heading("Overview", 1)
    stats_table = document.add_table(rows=1, cols=3)
    stats_table.style = "Table Grid"
    hdr_cells = stats_table.rows[0].cells
    hdr_cells[0].text = "Documents"
    hdr_cells[1].text = "Knowledge Items"
    hdr_cells[2].text = "Open Tasks"
    row_cells = stats_table.add_row().cells
    row_cells[0].text = str(len(doc_list))
    row_cells[1].text = str(len(knowledge))
    row_cells[2].text = str(sum(1 for t in tasks if t.get("status") == "pending"))
    document.add_paragraph("")

    # ── Knowledge by kind ──
    from collections import defaultdict
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for item in knowledge:
        by_kind[item.get("kind", "note")].append(item)

    kind_order = ["fact", "claim", "note", "question", "action", "entity", "summary"]
    all_kinds = kind_order + [k for k in by_kind if k not in kind_order]

    for kind in all_kinds:
        items = by_kind.get(kind, [])
        if not items:
            continue
        document.add_heading(kind.title() + "s", 2)
        for item in items[:80]:
            text = (item.get("text") or "").strip()
            if text:
                p = document.add_paragraph(style="List Bullet")
                p.add_run(text[:500])

    # ── Documents ──
    document.add_heading("Source Documents", 1)
    if doc_list:
        t = document.add_table(rows=1, cols=3)
        t.style = "Table Grid"
        hc = t.rows[0].cells
        hc[0].text = "Title"
        hc[1].text = "Kind"
        hc[2].text = "Status"
        for d in doc_list[:50]:
            rc = t.add_row().cells
            rc[0].text = (d.get("title") or "")[:80]
            rc[1].text = d.get("kind", "")
            rc[2].text = d.get("readiness", "")
    document.add_paragraph("")

    # ── Tasks ──
    pending = [t for t in tasks if t.get("status") == "pending"]
    if pending:
        document.add_heading("Open Tasks", 1)
        for task in pending[:50]:
            p = document.add_paragraph(style="List Bullet")
            p.add_run((task.get("text") or "")[:200])

    # ── Save ──
    out_dir = _ensure_dir(cfg, work_id)
    slug = _slug(work.get("title", "work"))
    fname = f"{slug}_{_now_label()}.docx"
    fpath = out_dir / fname
    document.save(str(fpath))

    text = _format_knowledge_text(knowledge)
    title_out = f"Research Report — {work.get('title', 'Work')}"
    doc_id = _register_output(fpath, work_id, db, cfg, "docx", title_out, text)
    logger.info("Generated DOCX: %s → doc %s", fpath.name, doc_id)
    return fpath, doc_id


# ── PDF ────────────────────────────────────────────────────────────────────────

def generate_pdf_report(work_id: str, db: "OrivellumDB", cfg: "OrivellumConfig") -> tuple[Path, str]:
    """Generate a PDF research report from a Work; return (file_path, doc_id)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        PageBreak, HRFlowable,
    )

    work = db.get_work(work_id)
    if not work:
        raise ValueError(f"Work {work_id!r} not found")

    knowledge = db.list_knowledge(work_id=work_id, limit=500)
    tasks = db.list_tasks(work_id=work_id)
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT title, kind, readiness FROM documents WHERE work_id=? ORDER BY created_at DESC",
            (work_id,),
        ).fetchall()
    doc_list = [dict(r) for r in doc_rows]

    out_dir = _ensure_dir(cfg, work_id)
    slug = _slug(work.get("title", "work"))
    fname = f"{slug}_{_now_label()}.pdf"
    fpath = out_dir / fname

    doc = SimpleDocTemplate(
        str(fpath),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle", parent=styles["Title"],
        fontSize=28, spaceAfter=12, textColor=colors.HexColor("#0F172A"),
    )
    h1_style = ParagraphStyle(
        "H1", parent=styles["Heading1"],
        fontSize=16, spaceBefore=18, spaceAfter=8,
        textColor=colors.HexColor("#1E293B"), borderPad=4,
    )
    h2_style = ParagraphStyle(
        "H2", parent=styles["Heading2"],
        fontSize=13, spaceBefore=14, spaceAfter=6,
        textColor=colors.HexColor("#334155"),
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=10, leading=15, spaceAfter=4,
        textColor=colors.HexColor("#374151"),
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"],
        fontSize=10, leading=14, leftIndent=20,
        bulletIndent=8, spaceAfter=3,
        textColor=colors.HexColor("#374151"),
    )
    meta_style = ParagraphStyle(
        "Meta", parent=styles["Normal"],
        fontSize=9, textColor=colors.HexColor("#94A3B8"), spaceAfter=6,
    )

    def _xml(s: str) -> str:
        """XML-escape a user-controlled string for use inside ReportLab Paragraph markup."""
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    story = []

    # Title — must be XML-escaped (title/description are user-controlled)
    title = _xml(work.get("title", "Research Report"))
    story.append(Paragraph(title, title_style))
    desc = work.get("description") or ""
    if desc:
        story.append(Paragraph(f"<i>{_xml(desc[:300])}</i>", meta_style))
    story.append(Paragraph(
        f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
        meta_style,
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#E2E8F0")))
    story.append(Spacer(1, 0.5 * cm))

    # Stats table
    story.append(Paragraph("Overview", h1_style))
    tbl_data = [
        ["Documents", "Knowledge Items", "Open Tasks"],
        [
            str(len(doc_list)),
            str(len(knowledge)),
            str(sum(1 for t in tasks if t.get("status") == "pending")),
        ],
    ]
    tbl = Table(tbl_data, colWidths=[5 * cm, 5 * cm, 5 * cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.5 * cm))

    # Knowledge by kind
    story.append(Paragraph("Knowledge Base", h1_style))
    from collections import defaultdict
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for item in knowledge:
        by_kind[item.get("kind", "note")].append(item)

    kind_order = ["fact", "claim", "note", "question", "action", "entity", "summary"]
    all_kinds = kind_order + [k for k in by_kind if k not in kind_order]

    for kind in all_kinds:
        items = by_kind.get(kind, [])
        if not items:
            continue
        story.append(Paragraph(f"{kind.title()}s ({len(items)})", h2_style))
        for item in items[:60]:
            text = (item.get("text") or "").strip()[:400]
            if text:
                # Escape XML special chars
                text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                story.append(Paragraph(f"• {text}", bullet_style))

    # Documents — all cell values through _xml() because ReportLab can
    # interpret strings containing &/</> as markup even in Table cells.
    if doc_list:
        story.append(PageBreak())
        story.append(Paragraph("Source Documents", h1_style))
        doc_data = [["Title", "Kind", "Status"]]
        for d in doc_list[:40]:
            doc_data.append([
                _xml((d.get("title") or "")[:60]),
                _xml(d.get("kind", "")),
                _xml(d.get("readiness", "")),
            ])
        doc_tbl = Table(doc_data, colWidths=[10 * cm, 3 * cm, 3 * cm])
        doc_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1E293B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(doc_tbl)

    # Tasks
    pending = [t for t in tasks if t.get("status") == "pending"]
    if pending:
        story.append(Paragraph("Open Tasks", h1_style))
        for task in pending[:40]:
            text = (task.get("text") or "").strip()[:200]
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(f"☐  {text}", bullet_style))

    doc.build(story)

    text = _format_knowledge_text(knowledge)
    title_out = f"Research Report — {work.get('title', 'Work')}"
    doc_id = _register_output(fpath, work_id, db, cfg, "pdf", title_out, text)
    logger.info("Generated PDF: %s → doc %s", fpath.name, doc_id)
    return fpath, doc_id


# ── PPTX ───────────────────────────────────────────────────────────────────────

def generate_pptx(work_id: str, db: "OrivellumDB", cfg: "OrivellumConfig") -> tuple[Path, str]:
    """Generate a PowerPoint deck from a Work; return (file_path, doc_id)."""
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    work = db.get_work(work_id)
    if not work:
        raise ValueError(f"Work {work_id!r} not found")

    knowledge = db.list_knowledge(work_id=work_id, limit=500)
    tasks = db.list_tasks(work_id=work_id)
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT title, kind FROM documents WHERE work_id=? ORDER BY created_at DESC LIMIT 30",
            (work_id,),
        ).fetchall()
    doc_list = [dict(r) for r in doc_rows]

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    # ── Color palette ──
    DARK_BG   = RGBColor(0x0F, 0x17, 0x2A)
    LIGHT_BG  = RGBColor(0xF8, 0xFA, 0xFC)
    ACCENT    = RGBColor(0x60, 0x81, 0xEB)
    WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
    GRAY      = RGBColor(0x64, 0x74, 0x8B)
    DARK_TEXT = RGBColor(0x1E, 0x29, 0x3B)

    slide_layouts = prs.slide_layouts

    def _bg_slide(slide, color: RGBColor) -> None:
        """Fill a slide's background with a solid color."""
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = color

    def _add_text_box(slide, text: str, left, top, width, height,
                      size: int = 14, bold: bool = False, color: RGBColor = WHITE,
                      align = PP_ALIGN.LEFT, wrap: bool = True) -> None:
        txBox = slide.shapes.add_textbox(left, top, width, height)
        tf = txBox.text_frame
        tf.word_wrap = wrap
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color

    inch = Inches(1)

    # ── Slide 1: Title ──
    slide1 = prs.slides.add_slide(slide_layouts[6])  # blank
    _bg_slide(slide1, DARK_BG)
    _add_text_box(
        slide1,
        work.get("title", "Research Report"),
        Inches(1), Inches(2.2), Inches(11.33), Inches(1.8),
        size=40, bold=True, color=WHITE, align=PP_ALIGN.CENTER,
    )
    desc = work.get("description") or ""
    if desc:
        _add_text_box(
            slide1,
            desc[:200],
            Inches(2), Inches(4.2), Inches(9.33), Inches(1),
            size=16, bold=False, color=ACCENT, align=PP_ALIGN.CENTER,
        )
    _add_text_box(
        slide1,
        f"Generated {datetime.now(timezone.utc).strftime('%B %d, %Y')}",
        Inches(1), Inches(6.6), Inches(11.33), Inches(0.5),
        size=11, bold=False, color=GRAY, align=PP_ALIGN.CENTER,
    )

    # ── Slide 2: Overview ──
    slide2 = prs.slides.add_slide(slide_layouts[6])
    _bg_slide(slide2, LIGHT_BG)
    _add_text_box(slide2, "Overview", Inches(1), Inches(0.5), Inches(11.33), Inches(0.8),
                  size=28, bold=True, color=DARK_TEXT)
    stats = [
        (str(len(doc_list)), "Documents"),
        (str(len(knowledge)), "Knowledge Items"),
        (str(sum(1 for t in tasks if t.get("status") == "pending")), "Open Tasks"),
    ]
    for i, (val, lbl) in enumerate(stats):
        left = Inches(1 + i * 4)
        # Value
        v_box = slide2.shapes.add_textbox(left, Inches(2), Inches(3.5), Inches(1.5))
        v_tf = v_box.text_frame
        vp = v_tf.paragraphs[0]
        vp.alignment = PP_ALIGN.CENTER
        vr = vp.add_run()
        vr.text = val
        vr.font.size = Pt(54)
        vr.font.bold = True
        vr.font.color.rgb = ACCENT
        # Label
        l_box = slide2.shapes.add_textbox(left, Inches(3.6), Inches(3.5), Inches(0.6))
        l_tf = l_box.text_frame
        lp = l_tf.paragraphs[0]
        lp.alignment = PP_ALIGN.CENTER
        lr = lp.add_run()
        lr.text = lbl
        lr.font.size = Pt(14)
        lr.font.color.rgb = GRAY

    # ── Slides 3+: Knowledge by kind ──
    from collections import defaultdict
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for item in knowledge:
        by_kind[item.get("kind", "note")].append(item)

    kind_order = ["fact", "claim", "note", "question", "action", "entity", "summary"]
    all_kinds = kind_order + [k for k in by_kind if k not in kind_order]

    for kind in all_kinds:
        items = by_kind.get(kind, [])
        if not items:
            continue
        # One slide per kind, up to 10 items
        slide = prs.slides.add_slide(slide_layouts[6])
        _bg_slide(slide, LIGHT_BG)
        _add_text_box(
            slide, f"{kind.title()}s",
            Inches(1), Inches(0.4), Inches(11.33), Inches(0.8),
            size=26, bold=True, color=DARK_TEXT,
        )
        # Bullet list
        txBox = slide.shapes.add_textbox(Inches(1), Inches(1.4), Inches(11.33), Inches(5.5))
        tf = txBox.text_frame
        tf.word_wrap = True
        first = True
        for item in items[:8]:
            text = (item.get("text") or "").strip()[:220]
            if not text:
                continue
            if first:
                p = tf.paragraphs[0]
                first = False
            else:
                p = tf.add_paragraph()
            p.space_before = Pt(4)
            run = p.add_run()
            run.text = f"• {text}"
            run.font.size = Pt(13)
            run.font.color.rgb = DARK_TEXT

    # ── Slide: Source Documents ──
    if doc_list:
        slide_d = prs.slides.add_slide(slide_layouts[6])
        _bg_slide(slide_d, DARK_BG)
        _add_text_box(
            slide_d, "Source Documents",
            Inches(1), Inches(0.4), Inches(11.33), Inches(0.8),
            size=26, bold=True, color=WHITE,
        )
        txBox = slide_d.shapes.add_textbox(Inches(1), Inches(1.4), Inches(11.33), Inches(5.5))
        tf = txBox.text_frame
        tf.word_wrap = True
        first = True
        for d in doc_list[:12]:
            title = (d.get("title") or "")[:80]
            kind = d.get("kind", "")
            if first:
                p = tf.paragraphs[0]
                first = False
            else:
                p = tf.add_paragraph()
            p.space_before = Pt(3)
            run = p.add_run()
            run.text = f"• {title}" + (f"  [{kind}]" if kind else "")
            run.font.size = Pt(12)
            run.font.color.rgb = RGBColor(0xCB, 0xD5, 0xE1)

    # ── Slide: Open Tasks ──
    pending = [t for t in tasks if t.get("status") == "pending"]
    if pending:
        slide_t = prs.slides.add_slide(slide_layouts[6])
        _bg_slide(slide_t, DARK_BG)
        _add_text_box(
            slide_t, "Open Tasks",
            Inches(1), Inches(0.4), Inches(11.33), Inches(0.8),
            size=26, bold=True, color=WHITE,
        )
        txBox = slide_t.shapes.add_textbox(Inches(1), Inches(1.4), Inches(11.33), Inches(5.5))
        tf = txBox.text_frame
        tf.word_wrap = True
        first = True
        for task in pending[:10]:
            text = (task.get("text") or "").strip()[:200]
            if first:
                p = tf.paragraphs[0]
                first = False
            else:
                p = tf.add_paragraph()
            run = p.add_run()
            run.text = f"☐  {text}"
            run.font.size = Pt(13)
            run.font.color.rgb = RGBColor(0xCB, 0xD5, 0xE1)

    # ── Save ──
    out_dir = _ensure_dir(cfg, work_id)
    slug = _slug(work.get("title", "work"))
    fname = f"{slug}_{_now_label()}.pptx"
    fpath = out_dir / fname
    prs.save(str(fpath))

    text = _format_knowledge_text(knowledge)
    title_out = f"Slide Deck — {work.get('title', 'Work')}"
    doc_id = _register_output(fpath, work_id, db, cfg, "pptx", title_out, text)
    logger.info("Generated PPTX: %s → doc %s", fpath.name, doc_id)
    return fpath, doc_id


# ── Bundle (ZIP) ───────────────────────────────────────────────────────────────

def bundle_files(
    file_paths: list[str],
    output_name: str,
    work_id: str,
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
) -> tuple[Path, str]:
    """Zip a list of file paths into a single archive; return (zip_path, doc_id)."""
    out_dir = _ensure_dir(cfg, work_id)
    safe_name = _slug(output_name or "bundle")
    fname = f"{safe_name}_{_now_label()}.zip"
    fpath = out_dir / fname

    data_dir = Path(cfg.data_dir)
    # Restrict bundle inputs to files already generated for this specific Work.
    # This prevents an authenticated caller from packaging unrelated application
    # data (e.g. the database or another Work's outputs).
    work_gen_root = (data_dir / "outputs" / "generate" / work_id).resolve()
    included_text_parts: list[str] = []
    rejected: list[str] = []

    with zipfile.ZipFile(str(fpath), "w", zipfile.ZIP_DEFLATED) as zf:
        for raw_path in file_paths:
            p = Path(raw_path)
            # Normalise relative paths (callers pass relative-to-data_dir paths)
            if not p.is_absolute():
                p = data_dir / p
            try:
                target = p.resolve()
                # Hard scope: must be under data/outputs/generate/{work_id}/
                target.relative_to(work_gen_root)
                if not (target.exists() and target.is_file()):
                    rejected.append(raw_path)
                    logger.warning("Bundle: path not found — %s", raw_path)
                    continue
                zf.write(str(target), target.name)
                if target.suffix.lower() in (".txt", ".md"):
                    try:
                        included_text_parts.append(target.read_text(errors="replace")[:500])
                    except Exception:
                        pass
            except ValueError:
                # Path escapes the Work's generation directory
                rejected.append(raw_path)
                logger.warning("Bundle: rejected out-of-scope path — %s", raw_path)
            except Exception as exc:
                rejected.append(raw_path)
                logger.warning("Bundle: skipping %s — %s", raw_path, exc)

    if rejected:
        logger.info("Bundle: %d path(s) rejected (out-of-scope or missing)", len(rejected))

    text = "\n\n".join(included_text_parts) or f"Bundle containing {len(file_paths)} files."
    work = db.get_work(work_id)
    work_title = (work.get("title") if work else None) or "Work"
    title_out = f"Bundle — {work_title}"
    doc_id = _register_output(fpath, work_id, db, cfg, "bundle", title_out, text)
    logger.info("Generated ZIP bundle: %s → doc %s", fpath.name, doc_id)
    return fpath, doc_id
