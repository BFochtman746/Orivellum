#!/usr/bin/env python3
"""Generate the Orivellum Ultimate Training Manual as a PDF.

Usage:  uv run python scripts/generate_training_manual.py
Output: orivellum_training_manual.pdf
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle, KeepTogether,
)
from reportlab.platypus.tableofcontents import TableOfContents

# ── Palette ────────────────────────────────────────────────────────────────────
NAVY   = colors.HexColor("#0F1B2D")
GOLD   = colors.HexColor("#C9A84C")
TEAL   = colors.HexColor("#2A7B7B")
RUST   = colors.HexColor("#8B3A2A")
OFFWHT = colors.HexColor("#F8F5EF")
LTGREY = colors.HexColor("#E8E3D8")
MDGREY = colors.HexColor("#9E9687")
DKGREY = colors.HexColor("#3D3830")
GREEN  = colors.HexColor("#2A6B3A")
AMBER  = colors.HexColor("#C27D1A")
RED    = colors.HexColor("#8B1F1F")

W, H = letter

# ── Style sheet ────────────────────────────────────────────────────────────────

def make_styles():
    base = getSampleStyleSheet()
    s = {}

    s["title_page_title"] = ParagraphStyle(
        "title_page_title", fontName="Helvetica-Bold", fontSize=36,
        textColor=GOLD, alignment=TA_CENTER, leading=44, spaceAfter=8,
    )
    s["title_page_sub"] = ParagraphStyle(
        "title_page_sub", fontName="Helvetica", fontSize=14,
        textColor=OFFWHT, alignment=TA_CENTER, leading=20, spaceAfter=4,
    )
    s["title_page_ver"] = ParagraphStyle(
        "title_page_ver", fontName="Helvetica", fontSize=10,
        textColor=MDGREY, alignment=TA_CENTER, spaceAfter=2,
    )

    s["h1"] = ParagraphStyle(
        "h1", fontName="Helvetica-Bold", fontSize=22,
        textColor=NAVY, spaceBefore=18, spaceAfter=8, leading=28,
    )
    s["h2"] = ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=16,
        textColor=TEAL, spaceBefore=14, spaceAfter=6, leading=22,
    )
    s["h3"] = ParagraphStyle(
        "h3", fontName="Helvetica-Bold", fontSize=13,
        textColor=DKGREY, spaceBefore=10, spaceAfter=4, leading=18,
    )
    s["h4"] = ParagraphStyle(
        "h4", fontName="Helvetica-BoldOblique", fontSize=11,
        textColor=TEAL, spaceBefore=8, spaceAfter=3, leading=16,
    )
    s["body"] = ParagraphStyle(
        "body", fontName="Helvetica", fontSize=10,
        textColor=DKGREY, leading=15, spaceAfter=6, alignment=TA_JUSTIFY,
    )
    s["body_sm"] = ParagraphStyle(
        "body_sm", fontName="Helvetica", fontSize=9,
        textColor=DKGREY, leading=13, spaceAfter=4,
    )
    s["bullet"] = ParagraphStyle(
        "bullet", fontName="Helvetica", fontSize=10,
        textColor=DKGREY, leading=15, spaceAfter=3,
        leftIndent=16, bulletIndent=4,
    )
    s["subbullet"] = ParagraphStyle(
        "subbullet", fontName="Helvetica", fontSize=9,
        textColor=DKGREY, leading=13, spaceAfter=2,
        leftIndent=32, bulletIndent=20,
    )
    s["code"] = ParagraphStyle(
        "code", fontName="Courier", fontSize=8.5,
        textColor=NAVY, backColor=colors.HexColor("#EEF2F7"),
        leading=13, spaceAfter=4, leftIndent=8, rightIndent=8,
        spaceBefore=2,
    )
    s["callout_title"] = ParagraphStyle(
        "callout_title", fontName="Helvetica-Bold", fontSize=10,
        textColor=NAVY, leading=14, spaceAfter=2,
    )
    s["callout_body"] = ParagraphStyle(
        "callout_body", fontName="Helvetica", fontSize=9.5,
        textColor=DKGREY, leading=14, spaceAfter=0,
    )
    s["toc_h1"] = ParagraphStyle(
        "toc_h1", fontName="Helvetica-Bold", fontSize=11,
        textColor=NAVY, leading=16, spaceAfter=2,
    )
    s["toc_h2"] = ParagraphStyle(
        "toc_h2", fontName="Helvetica", fontSize=10,
        textColor=DKGREY, leading=14, leftIndent=16, spaceAfter=1,
    )
    s["page_label"] = ParagraphStyle(
        "page_label", fontName="Helvetica", fontSize=8,
        textColor=MDGREY,
    )
    s["table_header"] = ParagraphStyle(
        "table_header", fontName="Helvetica-Bold", fontSize=9,
        textColor=OFFWHT, leading=13, alignment=TA_CENTER,
    )
    s["table_cell"] = ParagraphStyle(
        "table_cell", fontName="Helvetica", fontSize=9,
        textColor=DKGREY, leading=13,
    )
    s["table_cell_code"] = ParagraphStyle(
        "table_cell_code", fontName="Courier", fontSize=8,
        textColor=NAVY, leading=12,
    )
    s["warning"] = ParagraphStyle(
        "warning", fontName="Helvetica-Bold", fontSize=9.5,
        textColor=RED, leading=14,
    )
    s["note"] = ParagraphStyle(
        "note", fontName="Helvetica-Oblique", fontSize=9.5,
        textColor=TEAL, leading=14,
    )
    s["chapter_label"] = ParagraphStyle(
        "chapter_label", fontName="Helvetica", fontSize=10,
        textColor=GOLD, spaceBefore=0, spaceAfter=2, alignment=TA_LEFT,
    )
    return s

S = make_styles()

# ── Helpers ────────────────────────────────────────────────────────────────────

def p(text, style="body"): return Paragraph(text, S[style])
def h1(text):              return Paragraph(text, S["h1"])
def h2(text):              return Paragraph(text, S["h2"])
def h3(text):              return Paragraph(text, S["h3"])
def h4(text):              return Paragraph(text, S["h4"])
def sp(n=6):               return Spacer(1, n)
def hr():                  return HRFlowable(width="100%", thickness=0.5, color=LTGREY, spaceAfter=6, spaceBefore=6)
def thin_hr():             return HRFlowable(width="100%", thickness=0.25, color=LTGREY, spaceAfter=4, spaceBefore=4)

def bullet(text, level=0):
    style = "subbullet" if level > 0 else "bullet"
    marker = "◦" if level > 0 else "•"
    return Paragraph(f"{marker}&nbsp;&nbsp;{text}", S[style])

def code(text):
    escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Paragraph(escaped, S["code"])

def callout(title, body, bg=None, border=TEAL):
    bg = bg or colors.HexColor("#EDF6F6")
    content = [
        [
            Paragraph(f"▸ {title}", S["callout_title"]),
            Paragraph(body, S["callout_body"]),
        ]
    ]
    t = Table(content, colWidths=["100%"])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), bg),
        ("LEFTPADDING",  (0,0), (-1,-1), 10),
        ("RIGHTPADDING", (0,0), (-1,-1), 10),
        ("TOPPADDING",   (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0), (-1,-1), 8),
        ("LINEBEFORE",   (0,0), (0,-1), 3, border),
        ("ROWBACKGROUNDS",(0,0),(-1,-1),[bg]),
    ]))
    return t

def warning_box(text):
    return callout("⚠ Important", text, bg=colors.HexColor("#FDF2F2"), border=RED)

def tip_box(text):
    return callout("💡 Tip", text, bg=colors.HexColor("#F0F7F0"), border=GREEN)

def info_box(title, text):
    return callout(title, text, bg=colors.HexColor("#FFF8EC"), border=AMBER)

def section_box(title, rows):
    """A shaded section header box, then rows of [label, value]."""
    data = [[Paragraph(title, S["table_header"]), ""]]
    for lbl, val in rows:
        data.append([
            Paragraph(lbl, ParagraphStyle("tbl_lbl", fontName="Helvetica-Bold",
                      fontSize=9, textColor=DKGREY, leading=13)),
            Paragraph(val, S["table_cell"]),
        ])
    t = Table(data, colWidths=[1.8*inch, 4.7*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), NAVY),
        ("SPAN",         (0,0), (-1,0)),
        ("ALIGN",        (0,0), (-1,0), "CENTER"),
        ("BACKGROUND",   (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",         (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
    ]))
    return t

def two_col_table(headers, rows, col_widths=None):
    col_widths = col_widths or [2.2*inch, 4.3*inch]
    data = [[Paragraph(h, S["table_header"]) for h in headers]]
    for row in rows:
        data.append([Paragraph(str(c), S["table_cell"]) for c in row])
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0), NAVY),
        ("BACKGROUND",   (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",         (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
    ]))
    return t

# ── Page layout ────────────────────────────────────────────────────────────────

def header_footer(canvas, doc):
    canvas.saveState()
    # Header bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, H - 0.55*inch, W, 0.55*inch, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(0.5*inch, H - 0.35*inch, "ORIVELLUM  ·  ULTIMATE TRAINING MANUAL")
    canvas.setFillColor(OFFWHT)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(W - 0.5*inch, H - 0.35*inch, doc.title if hasattr(doc, "title") else "")
    # Footer bar
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, W, 0.4*inch, fill=1, stroke=0)
    canvas.setFillColor(MDGREY)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(0.5*inch, 0.14*inch, "Confidential — Internal Use Only")
    canvas.setFillColor(GOLD)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawCentredString(W/2, 0.14*inch, str(doc.page))
    canvas.setFillColor(MDGREY)
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(W - 0.5*inch, 0.14*inch, "Orivellum Intelligence Platform")
    # Gold accent line under header
    canvas.setFillColor(GOLD)
    canvas.rect(0, H - 0.58*inch, W, 0.03*inch, fill=1, stroke=0)
    canvas.restoreState()

def cover_page_template(canvas, doc):
    canvas.saveState()
    # Full dark background
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    # Gold top bar
    canvas.setFillColor(GOLD)
    canvas.rect(0, H - 0.08*inch, W, 0.08*inch, fill=1, stroke=0)
    # Teal bottom bar
    canvas.setFillColor(TEAL)
    canvas.rect(0, 0, W, 0.06*inch, fill=1, stroke=0)
    # Large background watermark text
    canvas.setFillColor(colors.HexColor("#1A2D47"))
    canvas.setFont("Helvetica-Bold", 160)
    canvas.drawCentredString(W/2, H/2 - 80, "OV")
    canvas.restoreState()


# ── Document builder ───────────────────────────────────────────────────────────

class OrivellumDoc(BaseDocTemplate):
    def __init__(self, filename):
        super().__init__(
            filename,
            pagesize=letter,
            rightMargin=0.65*inch, leftMargin=0.65*inch,
            topMargin=0.85*inch, bottomMargin=0.65*inch,
            title="Orivellum Training Manual",
        )
        self.title = ""   # updated per section in header_footer
        body_frame  = Frame(0.65*inch, 0.65*inch,
                            W - 1.3*inch, H - 1.5*inch, id="body")
        cover_frame = Frame(0.65*inch, 1.0*inch,
                            W - 1.3*inch, H - 2.0*inch, id="cover")
        self.addPageTemplates([
            PageTemplate("cover", [cover_frame], onPage=cover_page_template),
            PageTemplate("normal", [body_frame], onPage=header_footer),
        ])
        self.toc = TableOfContents()
        self.toc.levelStyles = [S["toc_h1"], S["toc_h2"]]

    def afterFlowable(self, flowable):
        """Register headings for TOC."""
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            text  = flowable.getPlainText()
            if style == "h1":
                self.notify("TOCEntry", (0, text, self.page))
            elif style == "h2":
                self.notify("TOCEntry", (1, text, self.page))


# ── Content ────────────────────────────────────────────────────────────────────

def build_story():
    story = []

    # ══════════════════════════════════════════════════════════════════════
    #  COVER
    # ══════════════════════════════════════════════════════════════════════
    story.append(sp(80))
    story.append(p("ORIVELLUM", "title_page_title"))
    story.append(sp(4))
    story.append(p("ULTIMATE TRAINING MANUAL", "title_page_sub"))
    story.append(sp(16))
    story.append(HRFlowable(width="60%", thickness=1, color=GOLD,
                             spaceAfter=16, spaceBefore=0, hAlign="CENTER"))
    story.append(p("Intelligence Platform for Research, Writing &amp; Knowledge Management", "title_page_sub"))
    story.append(sp(60))
    story.append(p("Installation  ·  LLM Configuration  ·  Database Maintenance  ·  Complete Page Guide", "title_page_ver"))
    story.append(sp(8))
    story.append(p("Version 1.0  ·  August 2026  ·  Internal Use Only", "title_page_ver"))

    # ══════════════════════════════════════════════════════════════════════
    #  TABLE OF CONTENTS
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(h1("Table of Contents"))
    story.append(sp(4))
    toc = TableOfContents()
    toc.levelStyles = [S["toc_h1"], S["toc_h2"]]
    story.append(toc)

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 1 — SYSTEM OVERVIEW
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 1", "chapter_label"))
    story.append(h1("System Overview"))
    story.append(hr())
    story.append(p(
        "Orivellum is a self-hosted, AI-powered intelligence platform built for authors, "
        "researchers, and knowledge workers who need to manage large bodies of source material, "
        "extract structured knowledge from it, and produce high-quality written work from that "
        "knowledge — all with full local-first privacy. Every AI feature runs against a local "
        "LLM server you control; no data is sent to external cloud providers."
    ))
    story.append(sp(6))

    story.append(h2("Architecture at a Glance"))
    arch_data = [
        ["Layer", "Technology", "Role"],
        ["Backend API", "Python 3.12+ · FastAPI · uvicorn", "REST + streaming SSE endpoints, background pipeline, nightshift daemon"],
        ["Database", "SQLite (WAL mode) · FTS5 · custom migrations", "All documents, knowledge, conversations, embeddings, settings"],
        ["Document pipeline", "pypdf / pdfplumber · Tesseract OCR · python-docx · openpyxl · markitdown", "Extract, chunk, embed, harvest knowledge from every file type"],
        ["AI gateway", "Local OpenAI-compatible server (Lemonade or Ollama)", "Chat, extraction, embeddings, TTS, ASR — all local"],
        ["Web frontend", "React 19 · Vite 7 · TanStack Query · Tailwind CSS 4 · Wouter", "Full workspace UI — every page in this manual"],
        ["Mobile app", "Expo / React Native", "Read-aloud, chat, library, learning — on iOS &amp; Android"],
        ["Search", "SQLite FTS5 (lexical) + local embeddings (semantic) + BM25 hybrid", "Retrieval-augmented generation for chat context"],
        ["Web research", "Tavily API · YouTube Transcript API · RRF + BM25 pipeline", "Multi-mode internet research with citation assembly"],
    ]
    t = Table([[Paragraph(c, S["table_header"] if i == 0 else S["table_cell"])
                for c in row] for i, row in enumerate(arch_data)],
              colWidths=[1.4*inch, 2.2*inch, 2.9*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t)
    story.append(sp(8))

    story.append(h2("Key Concepts"))
    concepts = [
        ("<b>Work</b>", "A research or writing project. The core organizational unit. A Work links documents, knowledge items, conversations, tasks, chapters, and book pipeline state."),
        ("<b>Document</b>", "Any uploaded file — PDF, DOCX, XLSX, CSV, Markdown, audio, image, ZIP archive. Documents are processed through the extraction pipeline to produce text, chunks, and knowledge items."),
        ("<b>Knowledge Item</b>", "A structured fact or claim extracted from a document by rule-based and/or LLM-based harvesting. Knowledge items have confidence scores, source citations, and review status."),
        ("<b>Conversation</b>", "An AI chat session. Can be linked to a Work so the AI draws context from that Work's knowledge when answering."),
        ("<b>Nightshift</b>", "A background daemon that runs nightly (default 3 AM). It vacuums the database, processes stuck documents, refreshes embeddings, detects knowledge gaps, and runs 20+ maintenance passes."),
        ("<b>MCOS</b>", "Model Calibration &amp; Observability System. Tracks every LLM call (model, latency, tokens, success/failure), runs benchmark suites, and maintains a prompt health registry for regression detection."),
        ("<b>Embeddings</b>", "Vector representations of text used for semantic search. Stored locally in SQLite. Requires an embedding model running on your local LLM server."),
        ("<b>Readiness</b>", "A document's processing state: <i>imported</i> (queued), <i>ready</i> (processed), <i>error</i> (extraction failed), <i>no_text</i> (nothing readable), <i>draft</i> (manually created)."),
    ]
    for term, desc in concepts:
        story.append(KeepTogether([
            p(f"{term} — {desc}", "bullet"),
            sp(2),
        ]))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 2 — INSTALLATION
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 2", "chapter_label"))
    story.append(h1("Installation &amp; Setup"))
    story.append(hr())

    story.append(h2("Prerequisites"))
    story.append(p(
        "Orivellum has two categories of dependency: managed (installed automatically "
        "by the setup scripts) and system (must be installed by your OS package manager "
        "or the Windows setup script before running the application)."
    ))
    story.append(sp(4))

    story.append(h3("Runtime Requirements"))
    req_data = [
        ["Component", "Minimum Version", "Notes"],
        ["Python", "3.12+", "3.13 works; 3.12 is the pinned version in .python-version"],
        ["Node.js", "20+", "Required for the web frontend and mobile app"],
        ["pnpm", "9+", "Workspace package manager — do not substitute npm or yarn"],
        ["uv", "0.4+", "Python package manager and virtual-environment tool"],
    ]
    story.append(two_col_table(["Component", "Version", "Notes"],
                               [r[0:] for r in req_data[1:]],
                               col_widths=[1.5*inch, 1.3*inch, 3.7*inch]))
    story.append(sp(8))

    story.append(h3("System Tools (OS-level)"))
    sys_data = [
        ["Tool", "Purpose", "Ubuntu/Debian", "macOS"],
        ["Tesseract OCR", "OCR for scanned PDFs and images", "sudo apt-get install tesseract-ocr", "brew install tesseract"],
        ["Poppler", "PDF-to-image conversion (pdf2image)", "sudo apt-get install poppler-utils", "brew install poppler"],
        ["FFmpeg", "Audio conversion for TTS/ASR pipeline", "sudo apt-get install ffmpeg", "brew install ffmpeg"],
        ["espeak-ng", "Fallback text-to-speech (optional)", "sudo apt-get install espeak-ng", "brew install espeak"],
    ]
    t2 = Table([[Paragraph(c, S["table_header"] if i == 0 else S["table_cell"])
                 for c in row] for i, row in enumerate(sys_data)],
               colWidths=[1.1*inch, 1.5*inch, 2.0*inch, 1.9*inch])
    t2.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("FONTNAME",      (2,1), (-1,-1), "Courier"),
        ("FONTSIZE",      (2,1), (-1,-1), 7.5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t2)
    story.append(sp(6))
    story.append(warning_box(
        "uv cannot install system tools. Tesseract and Poppler must be installed via your "
        "OS package manager (apt, brew, or the Windows setup script) BEFORE running uv sync. "
        "The diagnostics page will tell you if either is missing."
    ))
    story.append(sp(8))

    story.append(h2("Linux / Replit — Step-by-Step Setup"))
    linux_steps = [
        ("Install system tools", "sudo apt-get install -y tesseract-ocr poppler-utils ffmpeg espeak-ng"),
        ("Clone or open the project", "git clone <repo-url> orivellum && cd orivellum"),
        ("Install Python packages", "uv sync"),
        ("Install Node packages", "pnpm install"),
        ("Start everything", "./start.sh   # waits for API health, then starts Vite"),
        ("Or start manually", "uv run python -m orivellum.api.main &\nPORT=5173 BASE_PATH=/ ORIVELLUM_API_URL=http://127.0.0.1:8080 pnpm --filter @workspace/orivellum-ui run dev"),
    ]
    for label, cmd in linux_steps:
        story.append(p(f"<b>{label}</b>"))
        story.append(code(cmd))
        story.append(sp(2))

    story.append(sp(4))
    story.append(h2("Windows — Step-by-Step Setup"))
    story.append(p(
        "Windows uses two PowerShell scripts. The setup script installs all dependencies "
        "automatically (Python 3.12, Node LTS, pnpm, uv, Tesseract 5.5.0, Poppler 24.08, "
        "FFmpeg, espeak-ng 1.51.1). The start script builds the UI and launches the API server."
    ))
    win_steps = [
        ("Allow scripts (one-time)", "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned"),
        ("Run full setup", ".\\scripts\\setup-windows.ps1"),
        ("Start the application", ".\\scripts\\start.ps1"),
        ("Open in browser", "http://localhost:8080/orivellum-ui/"),
        ("Start with mobile app", ".\\scripts\\start.ps1 -Mobile"),
        ("Skip UI rebuild (faster)", ".\\scripts\\start.ps1 -SkipBuild"),
    ]
    for label, cmd in win_steps:
        story.append(p(f"<b>{label}</b>"))
        story.append(code(cmd))
        story.append(sp(2))

    story.append(sp(4))
    story.append(h2("Environment Variables"))
    story.append(p("All variables are optional — defaults work for a local single-user installation."))
    story.append(sp(4))
    env_vars = [
        ["Variable", "Default", "Description"],
        ["ORIVELLUM_AI_URL", "http://127.0.0.1:13305/api/v1", "Base URL of your local LLM server"],
        ["ORIVELLUM_DATA_DIR", "./data", "Where the database and library files are stored"],
        ["ORIVELLUM_LOG_LEVEL", "INFO", "Logging verbosity: DEBUG / INFO / WARNING / ERROR"],
        ["ORIVELLUM_API_KEY", "(generated)", "Bearer token for API authentication"],
        ["ORIVELLUM_PORT", "8080", "Port the API server listens on"],
        ["ORIVELLUM_HOST", "0.0.0.0", "Network interface the API binds to"],
        ["ORIVELLUM_DB_PATH", "(data_dir/orivellum.db)", "Explicit database file path"],
        ["ORIVELLUM_CONTEXT_WINDOW", "32768", "Token context window for chat"],
        ["ORIVELLUM_EXTRACTION_TIMEOUT", "30", "Seconds allowed per LLM extraction call"],
        ["ORIVELLUM_EMBEDDER_MODEL", "(config.yaml value)", "Name of the embedding model"],
        ["ORIVELLUM_WORKERS", "8", "Thread pool size for background pipeline work"],
        ["PORT", "8080", "Alternate port override (same as ORIVELLUM_PORT)"],
        ["TAVILY_API_KEY", "(none)", "Required only for internet research / web search features"],
    ]
    story.append(two_col_table(["Variable", "Default", "Description"],
                               [r[0:] for r in env_vars[1:]],
                               col_widths=[2.1*inch, 1.5*inch, 2.9*inch]))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 3 — LLM CONFIGURATION
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 3", "chapter_label"))
    story.append(h1("LLM Configuration"))
    story.append(hr())

    story.append(p(
        "Orivellum does not connect to any external AI API. Every AI feature — "
        "chat, document extraction, knowledge harvesting, embeddings, text-to-speech, "
        "speech-to-text, web research synthesis — uses a local OpenAI-compatible server "
        "that you run and control. Your data never leaves your machine."
    ))
    story.append(sp(6))

    story.append(h2("Choosing a Local LLM Server"))
    story.append(p("Two servers are officially tested and supported:"))
    story.append(sp(4))

    server_data = [
        ["Server", "Install", "Start Command", "API URL", "Best For"],
        ["Lemonade Server\n(Primary / Recommended)", "pip install lemonade-server", "lemonade-server --port 13305", "http://127.0.0.1:13305/api/v1", "AMD Ryzen AI / NPU acceleration; native Windows; recommended for the hardware this system was designed for"],
        ["Ollama", "ollama.com/download", "ollama serve", "http://127.0.0.1:11434/v1", "Easy cross-platform setup; wide model library; Nvidia/CPU fallback"],
        ["LM Studio", "lmstudio.ai", "(GUI app)", "http://127.0.0.1:1234/v1", "GUI-first; good for exploring models before committing"],
        ["vLLM / llama.cpp server", "(various)", "varies", "any /v1 path", "Linux servers, high-throughput production use"],
    ]
    t3 = Table([[Paragraph(c, S["table_header"] if i == 0 else S["table_cell"])
                 for c in row] for i, row in enumerate(server_data)],
               colWidths=[1.2*inch, 1.2*inch, 1.5*inch, 1.3*inch, 1.3*inch])
    t3.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t3)
    story.append(sp(8))

    story.append(h2("Model Roles and Recommendations"))
    story.append(p(
        "Orivellum uses up to six named model slots. Each is configured in <b>config.yaml</b> "
        "in the project root under the <code>serving:</code> key. Model names must match "
        "exactly what your LLM server reports — check <code>GET /v1/models</code> on your "
        "server if unsure."
    ))
    story.append(sp(6))

    model_data = [
        ["Slot", "config.yaml key", "Primary Role", "Recommended Models"],
        ["Workhorse", "workhorse_model", "Chat, summarisation, general reasoning, web research synthesis", "Qwen3.6-35B-A3B-GGUF (fast MoE, vision built in)\nQwen3-30B-A3B-Instruct-2507-GGUF"],
        ["Reasoner", "reasoner_model", "Complex multi-step reasoning tasks, verification, deep analysis", "gpt-oss-120b-mxfp-GGUF (best local, needs 128 GB RAM)\ngpt-oss-20b-mxfp4-GGUF (smaller machines)"],
        ["Coder", "coder_model", "Code generation, document workshop, structured output", "Qwen3-Coder-30B-A3B-Instruct-GGUF (256K context)\nQwen3-Coder-Next-GGUF (~48 GB)"],
        ["Embedder", "embedder_model", "Semantic search vectors for all documents and knowledge", "Qwen3-Embedding-8B-GGUF (recommended)\nnomic-embed-text-v2-moe-GGUF (lightweight)"],
        ["Vision", "vision_model", "OCR of scanned documents and images (fallback to Tesseract)", "Qwen3.6-35B-A3B-GGUF (same as Workhorse)\n(leave empty to use Tesseract)"],
        ["Reranker", "reranker_model", "Re-ranks search results for better retrieval quality (optional)", "bge-reranker-v2-m3-GGUF (optional)"],
    ]
    t4 = Table([[Paragraph(c, S["table_header"] if i == 0 else S["table_cell"])
                 for c in row] for i, row in enumerate(model_data)],
               colWidths=[0.8*inch, 1.3*inch, 1.8*inch, 2.6*inch])
    t4.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t4)
    story.append(sp(8))

    story.append(h2("config.yaml — Complete Reference"))
    story.append(p("Create or edit <b>config.yaml</b> in the project root. Everything is optional — "
                   "comment out any line to use the default."))
    story.append(code(
"""serving:
  base_url: "http://127.0.0.1:13305/api/v1"  # Your LLM server URL
  workhorse_model: "Qwen3.6-35B-A3B-GGUF"              # General-purpose model
  reasoner_model:  "gpt-oss-120b-mxfp-GGUF"            # Complex reasoning
  coder_model:     "Qwen3-Coder-30B-A3B-Instruct-GGUF" # Code & structured output
  embedder_model:  "Qwen3-Embedding-8B-GGUF"           # Semantic embeddings
  vision_model:    "Qwen3.6-35B-A3B-GGUF"              # OCR (same as workhorse)
  reranker_model:  ""                         # Leave empty → no reranking
  context_window:  131072                     # Token context limit
  timeout_sec:     300                        # LLM call timeout (seconds)
  extraction_timeout_sec: 120                 # Per-document extraction timeout

server:
  port: 8080
  host: "0.0.0.0"
  api_key: ""                                 # Leave empty → auto-generated

data:
  dir: "./data"                               # Database and library storage"""))
    story.append(sp(6))

    story.append(h2("Pulling Models (Ollama Example)"))
    story.append(p("Run these commands after <code>ollama serve</code> is running:"))
    story.append(code(
"""# Workhorse (MoE models are dramatically faster on unified-memory machines)
ollama pull qwen3:30b-a3b

# Reasoner (best local reasoning — needs 128 GB RAM)
ollama pull gpt-oss:120b

# Embeddings (required for semantic search)
ollama pull qwen3-embedding:8b

# Coder (optional — for document workshop)
ollama pull qwen3-coder:30b"""))
    story.append(sp(6))

    story.append(h2("Verifying LLM Connectivity"))
    story.append(p("Go to <b>System</b> → the status cards at the top of the page will show:"))
    story.append(bullet("<b>LLM Status</b> — green circle = connected, red = unreachable"))
    story.append(bullet("<b>Semantic Search</b> — shows embedding model status and circuit breaker state"))
    story.append(bullet("<b>TTS / ASR</b> — text-to-speech and speech-to-text availability"))
    story.append(sp(4))
    story.append(tip_box(
        "If the System page shows the LLM as unreachable, check that your LLM server "
        "is running and that ORIVELLUM_AI_URL (or config.yaml serving.base_url) matches "
        "the URL exactly, including the /v1 path suffix."
    ))
    story.append(sp(6))

    story.append(h2("MCOS — Model Calibration &amp; Observability System"))
    story.append(p(
        "MCOS is Orivellum's internal telemetry and quality-assurance system for LLM calls. "
        "It runs transparently in the background — you don't need to configure it."
    ))
    story.append(bullet("<b>Telemetry</b> — every LLM call is logged: model name, purpose, latency, input/output tokens, success/failure, error"))
    story.append(bullet("<b>Prompt registry</b> — system prompts are versioned; regressions are detected when a prompt's quality drops"))
    story.append(bullet("<b>Benchmark suites</b> — nightly runs test model quality against known-correct Q&amp;A pairs"))
    story.append(bullet("<b>MCOS page</b> — view call history, model health, benchmark results, and prompt health at /mcos"))
    story.append(sp(4))
    story.append(info_box("Why MCOS matters",
        "When you switch models or update your LLM server, MCOS will immediately surface "
        "any quality regressions in extraction, chat, or search. Check the MCOS page after "
        "any major configuration change."))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 4 — DATABASE MAINTENANCE
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 4", "chapter_label"))
    story.append(h1("Database Maintenance &amp; Troubleshooting"))
    story.append(hr())

    story.append(p(
        "Orivellum uses a single SQLite database file at <b>data/orivellum.db</b> (default). "
        "SQLite is reliable in WAL (Write-Ahead Logging) mode, which is how Orivellum runs it. "
        "This chapter covers routine maintenance, reprocessing stuck documents, fixing common "
        "errors, and restoring a healthy state."
    ))
    story.append(sp(6))

    story.append(h2("Schema Versions and Migrations"))
    story.append(p(
        "The database schema is managed by an append-only migration system. "
        "Every migration is numbered, idempotent, and runs automatically when the "
        "application starts. The current schema is <b>v101</b>. You never need to run "
        "migrations manually — just start the application and they apply."
    ))
    story.append(sp(4))
    story.append(tip_box(
        "To check the current schema version: go to System → Diagnostics, or run: "
        "uv run python scripts/run_diagnostics.py — the report shows schema_version."
    ))
    story.append(sp(6))

    story.append(h2("The Nightshift Daemon"))
    story.append(p(
        "Nightshift is an automatic background maintenance daemon that runs every night "
        "at 3 AM local time (configurable in Settings). It is the primary tool for keeping "
        "the database healthy — think of it as an automated DBA running nightly maintenance."
    ))
    story.append(sp(4))
    story.append(h3("What Nightshift Does (in order)"))
    nightshift_passes = [
        ("DB Optimisation", "Runs SQLite integrity_check, WAL checkpoint, ANALYZE, and conditional VACUUM (only if >30% of pages are free)"),
        ("Temp cleanup", "Removes zero-byte output files and orphaned .part upload temp files"),
        ("Report pruning", "Keeps the last 30 nightly reports in data/nightshift/"),
        ("Orphan cleanup", "Deletes knowledge items, chunks, and vectors whose parent documents were deleted"),
        ("Stuck-document recovery", "Retries up to 5 documents per run that have been stuck in 'imported' or 'error' state for more than 10 minutes"),
        ("VLM re-extraction", "Re-processes scanned PDFs and images that landed in no_text using the vision model (if configured)"),
        ("Audio retranscription", "Re-runs speech-to-text on audio files that failed or have outdated transcripts"),
        ("Sparse doc harvest", "Runs LLM knowledge harvest on documents that have fewer than 3 knowledge items"),
        ("Gap analysis", "Detects knowledge gaps in active Works and writes results to the gap cache"),
        ("Evidence rescore", "Re-scores knowledge item confidence and detects contradictions"),
        ("Chunk-prefix backfill", "Adds contextual prefixes to chunks missing them, then re-embeds"),
        ("Embedding backfill", "Generates embeddings for chunks and knowledge items that are missing vectors"),
        ("Stats refresh", "Updates work-level aggregate statistics (document count, knowledge count, etc.)"),
        ("MCOS benchmarks", "Runs model quality benchmarks and checks prompt health for regressions"),
        ("Outbox drain", "Delivers up to 500 queued background notifications"),
        ("Audit chain verification", "Verifies the integrity of the governance audit log"),
        ("Version suggestions", "Suggests version relationships between similar documents in the same Work"),
        ("Topic clustering", "Groups knowledge items into topic clusters for the Topics page"),
        ("Memory management", "Expires old working memory, deduplicates, promotes episodic→semantic memory"),
    ]
    for name, desc in nightshift_passes:
        story.append(KeepTogether([
            p(f"<b>{name}</b> — {desc}", "bullet"),
            sp(2),
        ]))

    story.append(sp(6))
    story.append(h3("Triggering Nightshift Manually"))
    story.append(p("You can trigger a full nightshift run immediately without waiting for 3 AM:"))
    story.append(bullet("Go to <b>System</b> → Nightshift card → click <b>Run Now</b>"))
    story.append(bullet("Or via API: <code>POST /api/system/nightshift/run-now</code>"))
    story.append(bullet("Check the result in System → Nightshift → last report"))
    story.append(sp(8))

    story.append(h2("Document Readiness States"))
    story.append(p("Every document has a <b>readiness</b> field that shows where it is in the pipeline:"))
    story.append(sp(4))
    readiness_data = [
        ["State", "Meaning", "What to Do"],
        ["imported", "Queued for extraction — waiting for the pipeline to pick it up", "Wait up to 2 minutes. If it stays 'imported' for more than 10 minutes, the pipeline may be stuck — use Reprocess."],
        ["ready", "Fully extracted, chunked, embedded, and knowledge-harvested", "Nothing — this is the healthy terminal state."],
        ["error", "Extraction failed with an error message", "Click the document → see the error detail → fix the root cause (missing tool, corrupt file) → click Reprocess."],
        ["no_text", "File processed but no readable text found", "For scanned PDFs: configure a vision model. For images: check Tesseract is installed. Then Reprocess."],
        ["draft", "Manually created document placeholder", "Add content or link a real file."],
    ]
    t5 = Table([[Paragraph(c, S["table_header"] if i == 0 else S["table_cell"])
                 for c in row] for i, row in enumerate(readiness_data)],
               colWidths=[0.8*inch, 2.5*inch, 3.2*inch])
    t5.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("RIGHTPADDING",  (0,0), (-1,-1), 6),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t5)
    story.append(sp(8))

    story.append(h2("Reprocessing Documents"))
    story.append(h3("Single Document"))
    story.append(bullet("Go to <b>Library</b> → click the document → click <b>Reprocess</b> in the document detail header"))
    story.append(bullet("Or via API: <code>POST /api/library/{doc_id}/reprocess</code>"))
    story.append(bullet("Use <code>?force=true</code> to reprocess even if the document is already ready"))
    story.append(sp(4))

    story.append(h3("Bulk Reprocess (All Stuck Documents)"))
    story.append(p("The reprocess-all endpoint fixes every document that is stuck, in error, or has no text in one call:"))
    story.append(code("POST /api/library/reprocess-all"))
    story.append(p("This endpoint:"))
    story.append(bullet("Resets all 'imported', 'error', and 'no_text' documents back to the 'imported' state"))
    story.append(bullet("Handles ZIP archives that haven't been exploded yet"))
    story.append(bullet("Re-queues everything for the extraction pipeline"))
    story.append(bullet("Returns a count of how many documents were re-queued"))
    story.append(sp(4))

    story.append(h3("From the Command Line"))
    story.append(code(
"""# Run the full diagnostics report (identifies every problem)
uv run python scripts/run_diagnostics.py

# Run diagnostics + SQLite VACUUM (compacts the database)
uv run python scripts/run_diagnostics.py --vacuum"""))
    story.append(sp(8))

    story.append(h2("Repairing Embeddings"))
    story.append(p(
        "Embeddings must be rebuilt any time you change the embedding model or its "
        "output dimension. Running a mismatched model produces wrong semantic search results "
        "without any obvious error."
    ))
    story.append(bullet("Go to <b>System</b> → Semantic Search card → click <b>Probe Embeddings</b>"))
    story.append(bullet("If the probe succeeds but dimensions don't match: click <b>Reindex Embeddings</b>"))
    story.append(bullet("Reindex deletes all stored vectors, verifies the embedder is live, then rebuilds from scratch"))
    story.append(bullet("Large libraries may take several hours — search falls back to BM25 lexical matching during this time"))
    story.append(sp(6))
    story.append(warning_box(
        "Do not run a VACUUM on a database that has failed an integrity_check. "
        "A corrupted database may appear to compact but the corruption is preserved. "
        "Restore from a backup first, then vacuum the restored copy."
    ))
    story.append(sp(8))

    story.append(h2("Common Problems and Fixes"))
    problems = [
        ("Document stuck in 'imported' forever",
         "The pipeline thread may have crashed. Trigger Reprocess on the document, "
         "or use reprocess-all. Check the API server logs for exceptions. Nightshift "
         "will automatically retry stuck documents (>10 min) each night."),
        ("All AI features fail (chat won't respond, extraction fails)",
         "Your LLM server is not running or the URL is wrong. Check "
         "ORIVELLUM_AI_URL / config.yaml serving.base_url. Verify the server is "
         "running: curl http://127.0.0.1:13305/api/v1/models (Lemonade) or "
         "curl http://127.0.0.1:11434/v1/models (Ollama)."),
        ("Semantic search returns poor results or nothing",
         "Embeddings may be missing or dimension-mismatched. Go to System → "
         "Semantic Search → Probe → Reindex. Check that your embedder model name "
         "in config.yaml exactly matches what the server reports."),
        ("PDFs come back as no_text (scanned documents)",
         "Tesseract is either not installed or the PDF is image-only. Install "
         "tesseract-ocr and poppler-utils, then Reprocess. For better quality, "
         "configure a vision_model in config.yaml."),
        ("Database grows very large (>1 GB)",
         "Run nightshift (which does a conditional VACUUM) or: "
         "uv run python scripts/run_diagnostics.py --vacuum"),
        ("Schema migration fails on startup",
         "The database file may be from a newer version or be corrupt. Check the "
         "error message in the API logs. If corrupt, restore from the most recent "
         "backup in data/backups/."),
        ("Knowledge harvest produces no items",
         "AI extraction may be disabled. Go to Settings → check 'AI Extraction "
         "Enabled'. Also verify the workhorse_model is set and reachable."),
        ("Nightshift hasn't run in more than 2 days",
         "Go to System → Nightshift card → Run Now. Check if the last run had an "
         "error. API server must be running at the nightshift hour (default 3 AM)."),
    ]
    for problem, fix in problems:
        story.append(KeepTogether([
            h4(f"Problem: {problem}"),
            p(f"<b>Fix:</b> {fix}", "body_sm"),
            thin_hr(),
        ]))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 5 — PAGE-BY-PAGE GUIDE
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 5", "chapter_label"))
    story.append(h1("Page-by-Page User Guide"))
    story.append(hr())
    story.append(p(
        "This chapter covers every page in the Orivellum web application. "
        "The navigation sidebar is always visible on the left. Pages are grouped "
        "by workflow: Knowledge → Creation → Learning → Administration."
    ))
    story.append(sp(8))

    # ── PAGE: Dashboard ────────────────────────────────────────────────────
    story.append(h2("Dashboard  (/)")  )
    story.append(p(
        "The Dashboard is the command centre of your Orivellum workspace. It gives you a "
        "complete picture of your active projects, recent activity, and what needs attention — "
        "all on one screen."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Custodian Header</b> — time-of-day greeting, pending task count, and quick-action buttons"))
    story.append(bullet("<b>Suggestions Panel</b> — AI-generated suggestions for your active Work (tasks, research questions, gaps to fill)"))
    story.append(bullet("<b>Gap Analysis Panel</b> — knowledge gaps detected across your Works, with suggested actions"))
    story.append(bullet("<b>Scorecard</b> — aggregate health metrics: total documents, knowledge items, pending reviews, active conversations"))
    story.append(bullet("<b>Active Works Cards</b> — your in-progress Works with progress bars and quick navigation"))
    story.append(bullet("<b>Recent Documents</b> — last-uploaded or last-modified documents with readiness indicators"))
    story.append(bullet("<b>Recent Conversations</b> — your most recent AI chat sessions with model and timestamp"))
    story.append(bullet("<b>Activity Feed</b> — timestamped log of everything that has happened across the system"))
    story.append(h3("How to Use"))
    story.append(bullet("Start here every session — the gap panel and suggestions tell you what to work on next"))
    story.append(bullet("Click any Work card to jump directly to that work's detail page"))
    story.append(bullet("The scorecard turning yellow or red on any metric means something needs attention"))
    story.append(sp(8))

    # ── PAGE: Works List ───────────────────────────────────────────────────
    story.append(h2("Works  (/works)"))
    story.append(p(
        "Works are your top-level research or writing projects. Every document, knowledge item, "
        "conversation, and task belongs to a Work. Think of a Work as a book project or major "
        "research topic."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Page Header</b> — Create Work button, search bar, type/status filters"))
    story.append(bullet("<b>Works Grid</b> — cards showing title, type, status, document count, knowledge count, and a completion progress bar"))
    story.append(h3("Work Types"))
    story.append(bullet("<b>Research</b> — for academic or investigative projects"))
    story.append(bullet("<b>Fiction / Narrative</b> — for novels and long-form creative writing"))
    story.append(bullet("<b>Non-fiction</b> — for books, essays, journalism"))
    story.append(bullet("<b>Reference</b> — for knowledge bases, wikis, training material"))
    story.append(h3("How to Use"))
    story.append(bullet("Click <b>Create Work</b> → fill in title, type, and optional description → Save"))
    story.append(bullet("Click any Work card to open the full Work workspace"))
    story.append(bullet("Use the filter chips to show only Fiction, Research, or a specific status"))
    story.append(sp(8))

    # ── PAGE: Work Detail ──────────────────────────────────────────────────
    story.append(h2("Work Detail  (/works/:workId)"))
    story.append(p(
        "The Work Detail page is the main workspace for a single project. It combines every "
        "aspect of managing a Work — documents, knowledge, chapters, tasks, conversations, "
        "health monitoring, and AI tools — all in a tabbed interface."
    ))
    story.append(h3("Tabs"))
    story.append(bullet("<b>Overview</b> — Work metadata, description, statistics bar (document count, knowledge count, conversations), book health card, and quick-chat button"))
    story.append(bullet("<b>Documents</b> — all documents linked to this Work with readiness states, upload button, and unlink option (hover a doc to reveal the X button)"))
    story.append(bullet("<b>Knowledge</b> — all extracted knowledge items with confidence scores, review status (AI-auto vs rule-based), approve/reject thumbs, and search"))
    story.append(bullet("<b>Book</b> — the book production pipeline: chapter ordering, pipeline state machine (Draft → Research → Writing → Editing → Final), and the book intelligence view"))
    story.append(bullet("<b>Brainstorm</b> — AI brainstorming canvas for generating ideas, outlines, and research questions linked to this Work"))
    story.append(bullet("<b>Tasks</b> — task list linked to this Work; create, complete, and prioritise tasks"))
    story.append(bullet("<b>Intelligence</b> — knowledge quality dashboard: gaps, contradictions, confidence distribution, chapter coverage"))
    story.append(h3("Book Health Card"))
    story.append(p(
        "The Overview tab shows a 'Book Health' card that polls automatically as documents "
        "are processed. It shows overall health score, gaps, confidence, and alerts for "
        "contradictions or missing evidence. Green = healthy, amber = needs attention, red = "
        "significant issues."
    ))
    story.append(h3("How to Use"))
    story.append(bullet("Upload documents via the Documents tab → they are automatically linked to this Work"))
    story.append(bullet("Review AI-extracted knowledge in the Knowledge tab — approve good items, reject bad ones"))
    story.append(bullet("Use Quick Chat (Overview tab) to open a conversation pre-linked to this Work"))
    story.append(bullet("Advance the book pipeline (Book tab) as you complete each production stage"))
    story.append(sp(8))

    # ── PAGE: Work Intelligence ────────────────────────────────────────────
    story.append(h2("Work Intelligence  (/works/:workId/intelligence)"))
    story.append(p(
        "A dedicated analytics view for one Work, showing the quality and completeness "
        "of its knowledge base in depth."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Confidence Summary</b> — distribution of knowledge item confidence scores (high / medium / low)"))
    story.append(bullet("<b>Health Score</b> — aggregate health: completeness, evidence quality, contradiction rate"))
    story.append(bullet("<b>Knowledge Gaps</b> — topics that are mentioned but not well-evidenced, with suggested research questions"))
    story.append(bullet("<b>Contradictions</b> — pairs of knowledge items that say conflicting things"))
    story.append(bullet("<b>Chapter Coverage</b> — for books: which chapters have strong knowledge backing vs thin coverage"))
    story.append(bullet("<b>Knowledge Item List</b> — all items filterable by confidence band, source, and review status"))
    story.append(h3("How to Use"))
    story.append(bullet("Run 'Rescore Knowledge' when you've added new documents — updates all confidence scores"))
    story.append(bullet("Use the Gaps list to decide what to research next"))
    story.append(bullet("Resolve contradictions by reviewing source documents and approving/rejecting competing claims"))
    story.append(sp(8))

    # ── PAGE: Chat ─────────────────────────────────────────────────────────
    story.append(h2("Chat  (/chat)"))
    story.append(p(
        "The Chat page is a full AI conversation workspace. Conversations can be standalone "
        "or linked to a specific Work, in which case the AI automatically draws context from "
        "that Work's knowledge base when answering."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Conversations Sidebar</b> — list of all conversations with timestamps, archived state, and rename/archive actions"))
    story.append(bullet("<b>Model &amp; Mode Controls</b> — choose the active model, toggle Deep Mode (uses the Reasoner model), connectivity indicator"))
    story.append(bullet("<b>Message Thread</b> — streaming AI responses with markdown rendering, code syntax highlighting, and copy-to-clipboard"))
    story.append(bullet("<b>Composer</b> — message input with file attachment, internet search toggle, and send button"))
    story.append(bullet("<b>Sources Panel</b> — knowledge items and web sources that were injected as context for the current response"))
    story.append(bullet("<b>Work Badge</b> — when the conversation is linked to a Work, a badge links back to that Work's detail page"))
    story.append(bullet("<b>Files Panel</b> — documents attached to or referenced in the conversation"))
    story.append(bullet("<b>Project Compass</b> — shows active Work metadata and a summary of the most relevant knowledge items"))
    story.append(h3("Linking a Conversation to a Work"))
    story.append(bullet("Open a conversation → click the Work Badge selector at the top → choose a Work"))
    story.append(bullet("The AI will automatically inject the top 8 most relevant knowledge items from that Work with every response"))
    story.append(bullet("The fastest way: in the Work Detail page, click 'Quick Chat' — creates a conversation already linked"))
    story.append(h3("Internet Search"))
    story.append(bullet("Toggle the globe icon in the composer to enable web search mode"))
    story.append(bullet("The system automatically detects biblical/theological queries and routes them to curated sources"))
    story.append(bullet("YouTube video transcripts, academic papers, and Facebook public pages are all searchable"))
    story.append(bullet("Requires TAVILY_API_KEY to be set in environment"))
    story.append(h3("Deep Mode"))
    story.append(bullet("Toggle the brain icon to switch from the Workhorse model to the Reasoner model"))
    story.append(bullet("Use for complex analysis, multi-step reasoning, and evaluation tasks"))
    story.append(bullet("Slower but significantly more capable for hard questions"))
    story.append(sp(8))

    # ── PAGE: Library ──────────────────────────────────────────────────────
    story.append(h2("Library  (/library)"))
    story.append(p(
        "The Library is the central document store. Every file you upload lives here, "
        "regardless of which Work it belongs to. The Library gives you full visibility "
        "into extraction status, knowledge, and document relationships."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Header</b> — Upload button, search bar, filter chips (by readiness, Work, file type)"))
    story.append(bullet("<b>Document Cards / Table</b> — shows filename, type icon, readiness badge, knowledge count, linked Work, and upload date"))
    story.append(bullet("<b>Work Filter Chip</b> — click to filter to only documents in a specific Work"))
    story.append(h3("Supported File Types"))
    story.append(bullet("<b>PDF</b> — 3-tier extraction: pdfplumber (text) → pdf2image+Tesseract (OCR) → vision model"))
    story.append(bullet("<b>DOCX</b> — full text + tables extraction via python-docx"))
    story.append(bullet("<b>XLSX / CSV</b> — up to 5,000 rows per sheet via openpyxl"))
    story.append(bullet("<b>PPTX</b> — slide text and speaker notes"))
    story.append(bullet("<b>Markdown / Text / HTML / JSON / Code</b> — direct text extraction"))
    story.append(bullet("<b>MP3 / WAV / M4A / FLAC</b> — speech-to-text via Whisper / faster-whisper"))
    story.append(bullet("<b>PNG / JPG / WEBP</b> — Tesseract OCR or vision model"))
    story.append(bullet("<b>ZIP</b> — exploded into individual child documents automatically"))
    story.append(h3("How to Upload"))
    story.append(bullet("Click <b>Upload</b> → drag files or click to browse → optionally assign to a Work before uploading"))
    story.append(bullet("ZIP files are automatically exploded — all contained files become individual library documents"))
    story.append(bullet("Duplicate detection: re-uploading the same file navigates to the existing document (SHA-256 dedup)"))
    story.append(h3("After Uploading"))
    story.append(bullet("Readiness starts at 'imported' → moves to 'ready' in 30–120 seconds depending on file type and size"))
    story.append(bullet("Poll for status: Library cards auto-refresh every 4 seconds while any document is in 'imported' state"))
    story.append(sp(8))

    # ── PAGE: Library Detail ───────────────────────────────────────────────
    story.append(h2("Document Detail  (/library/:docId)"))
    story.append(p("The full inspector for a single document. Everything extracted from a document is visible and actionable here."))
    story.append(h3("Sections"))
    story.append(bullet("<b>Document Header</b> — title, readiness badge, Reprocess button, Download button, Read Aloud button"))
    story.append(bullet("<b>Extraction Status</b> — warning messages from the pipeline if extraction had any issues"))
    story.append(bullet("<b>Overview Tab</b> — metadata (file type, size, sha256, upload date, linked Work selector, lifecycle state)"))
    story.append(bullet("<b>Knowledge Tab</b> — all AI-extracted and rule-based knowledge items with approve/reject review controls"))
    story.append(bullet("<b>Chunks Tab</b> — raw text chunks used for search and retrieval (with their embeddings status)"))
    story.append(bullet("<b>Chapters Tab</b> — for books processed with chapter detection: individual chapter records"))
    story.append(bullet("<b>Read Aloud Panel</b> — text-to-speech playback of the document's extracted text"))
    story.append(bullet("<b>Version Snapshots</b> — if multiple versions of this document exist, navigate between them"))
    story.append(bullet("<b>Related Documents</b> — near-duplicate detection results and version suggestions"))
    story.append(h3("Reviewing Knowledge Items"))
    story.append(bullet("AI-extracted items (from the LLM) show a purple Sparkles badge — review carefully"))
    story.append(bullet("Rule-based items (from regex/pattern matching) are reliable and rarely need review"))
    story.append(bullet("Click 👍 to approve an item — it is then included in chat context"))
    story.append(bullet("Click 👎 to reject — the item fades to 50% opacity and is excluded from chat context"))
    story.append(bullet("Rejected items can be un-rejected by clicking 👍 again"))
    story.append(sp(8))

    # ── PAGE: Projects ─────────────────────────────────────────────────────
    story.append(h2("Projects  (/projects)"))
    story.append(p(
        "Projects are higher-level containers that group related Works together. A Project might "
        "be a book series, a research programme, or a client engagement."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Header</b> — Create Project button"))
    story.append(bullet("<b>Project Cards</b> — title, description, Work count, document count, concept count"))
    story.append(h3("Project Detail  (/projects/:projectId)"))
    story.append(bullet("<b>Header</b> — project title, description, aggregate metrics"))
    story.append(bullet("<b>Summary Cards</b> — Works in project, total documents, concepts, mastery progress"))
    story.append(bullet("<b>Concepts List</b> — learning concepts associated with this project with mastery labels"))
    story.append(sp(8))

    # ── PAGE: Books ────────────────────────────────────────────────────────
    story.append(h2("Books  (/books)"))
    story.append(p(
        "The Books page groups your Works by their book-production status. "
        "It is the high-level production overview when you're running multiple book projects simultaneously."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Active Books</b> — Works with an active book pipeline in progress, showing stage, chapter count, and progress"))
    story.append(bullet("<b>In Progress</b> — Works that have started the book pipeline but are at an early stage"))
    story.append(bullet("<b>Go to Works CTA</b> — when no books exist yet, prompts you to create a Work"))
    story.append(sp(8))

    # ── PAGE: Finishing ────────────────────────────────────────────────────
    story.append(h2("Finishing  (/finishing)"))
    story.append(p(
        "The Finishing page is the final production stage — where a completed manuscript "
        "gets prepared for distribution. It handles pre-flight checks, chapter ordering, "
        "export packages, cover versioning, and brand tokens."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Book Selector</b> — choose which Work/book you're finishing"))
    story.append(bullet("<b>Pre-flight Card</b> — automated checklist that must pass before export (all chapters present, no missing content, health score above threshold)"))
    story.append(bullet("<b>Book Metadata Card</b> — title, subtitle, author, ISBN, publication date"))
    story.append(bullet("<b>Chapters Card</b> — final chapter ordering table with drag-to-reorder and word count per chapter"))
    story.append(bullet("<b>Export Card</b> — generate DOCX or PDF export packages for test readers or publishers; recipient management and sign-off tracking"))
    story.append(bullet("<b>Product Spec</b> — physical or digital product specifications (trim size, paper type, format)"))
    story.append(bullet("<b>Cover Versions</b> — manage cover artwork versions"))
    story.append(bullet("<b>Seal Cover</b> — lock a cover version as the final canonical cover"))
    story.append(bullet("<b>Brand Tokens</b> — typography, colour palette, and design tokens for the book's visual identity"))
    story.append(sp(8))

    # ── PAGE: Learn ────────────────────────────────────────────────────────
    story.append(h2("Learn  (/learn)"))
    story.append(p(
        "The Learn page is a guided study environment. It uses Socratic questioning and "
        "spaced repetition to help you deeply understand the content in your library — "
        "not just store it."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Learning Header</b> — current streak, session progress, daily goal"))
    story.append(bullet("<b>Topic / Course Controls</b> — filter by Work, concept, or search for a specific topic"))
    story.append(bullet("<b>Prerequisite Cards</b> — learning concepts you should understand before tackling others, shown with mastery level"))
    story.append(bullet("<b>Study Panel</b> — current question or concept with Socratic follow-ups; session limited to 5 questions by default"))
    story.append(bullet("<b>Progress Cards</b> — mastery bars per concept (novice → developing → proficient → mastery)"))
    story.append(h3("How Socratic Learning Works"))
    story.append(bullet("The system asks you an open-ended question about a concept in your library"))
    story.append(bullet("Your answer is evaluated by the AI against known evidence from your knowledge base"))
    story.append(bullet("A follow-up question probes deeper or corrects a misunderstanding"))
    story.append(bullet("After 5 questions, the session ends and mastery is updated"))
    story.append(bullet("Concepts with low mastery appear at the top of your queue next session"))
    story.append(sp(8))

    # ── PAGE: Intake ───────────────────────────────────────────────────────
    story.append(h2("Intake  (/intake)"))
    story.append(p(
        "The Intake page is the structured starting point for a new Work. "
        "Instead of uploading raw files directly, Intake lets you define the Work's "
        "profile, source material, and extraction options before processing begins."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Profile Card</b> — Work title, type, description, and writing goals"))
    story.append(bullet("<b>Source Selection</b> — file upload area specifically for the intake batch"))
    story.append(bullet("<b>Metadata Fields</b> — tags, author, publication date, subject area"))
    story.append(bullet("<b>Extraction Options</b> — choose extraction depth, enable/disable AI harvest, select relevant knowledge domains"))
    story.append(bullet("<b>Submit Area</b> — submit the intake for processing; status shows extraction progress"))
    story.append(sp(8))

    # ── PAGE: Topics / The Web ─────────────────────────────────────────────
    story.append(h2("Topics / The Web  (/topics)"))
    story.append(p(
        "The Topics page visualises the connected web of topics that have been extracted "
        "across all your knowledge items. It reveals thematic relationships between Works "
        "and documents you might not have noticed."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Search / Filter</b> — find a specific topic or filter by Work"))
    story.append(bullet("<b>Topic Cards</b> — each cluster of related knowledge items is grouped into a topic card with a name and summary"))
    story.append(bullet("<b>Related Connections</b> — clicking a topic shows which Works, documents, and other topics it connects to"))
    story.append(sp(8))

    # ── PAGE: Knowledge Graph ──────────────────────────────────────────────
    story.append(h2("Knowledge Graph  (/graph)"))
    story.append(p(
        "An interactive visual graph showing entities (people, places, organisations, concepts, "
        "events) extracted from your documents and the relationships between them."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Graph Canvas</b> — force-directed node-link diagram; drag nodes to rearrange, scroll to zoom"))
    story.append(bullet("<b>Entity Kind Filter Chips</b> — show/hide People, Places, Organisations, Events, Concepts by type"))
    story.append(bullet("<b>Graph Legend</b> — colour key for entity kinds and relationship types"))
    story.append(bullet("<b>Search</b> — find and highlight a specific entity in the graph"))
    story.append(bullet("<b>Selection Panel</b> — click a node to see all knowledge items connected to that entity"))
    story.append(h3("How to Use"))
    story.append(bullet("Use filter chips to reduce visual noise when the graph is dense"))
    story.append(bullet("Click a node → the selection panel shows all facts linked to that entity"))
    story.append(bullet("Filters don't move nodes — layout stays stable between filter changes"))
    story.append(sp(8))

    # ── PAGE: Actions ──────────────────────────────────────────────────────
    story.append(h2("Actions  (/actions)"))
    story.append(p(
        "The Actions page is a general-purpose AI task runner. Actions are parameterised "
        "AI operations — you select a Work, fill in the action's form fields, and execute it."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Action Selector</b> — dropdown of available actions (summarise, gap analysis, generate outline, etc.)"))
    story.append(bullet("<b>Work Selector</b> — the Work the action should run against"))
    story.append(bullet("<b>Dynamic Input Form</b> — each action has its own schema of required inputs"))
    story.append(bullet("<b>Execution Controls</b> — Run button; status indicator while running"))
    story.append(bullet("<b>Output Panel</b> — result of the action, rendered as markdown"))
    story.append(bullet("<b>Action History</b> — previous runs with their outputs"))
    story.append(sp(8))

    # ── PAGE: MCOS ─────────────────────────────────────────────────────────
    story.append(h2("MCOS  (/mcos)"))
    story.append(p(
        "The MCOS (Model Calibration &amp; Observability System) page is the administration "
        "panel for monitoring AI quality. Use it after changing models or to investigate "
        "unexpected AI behaviour."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Overview / Status Header</b> — total LLM calls, average latency, error rate, most recent benchmark result"))
    story.append(bullet("<b>LLM Call Log</b> — searchable table of every AI call: timestamp, purpose, model, tokens in/out, latency, success/failure"))
    story.append(bullet("<b>Model Health Cards</b> — per-model health summary: uptime, p50/p95 latency, token throughput"))
    story.append(bullet("<b>Benchmark Results</b> — nightly quality tests with pass/fail and regression indicators"))
    story.append(bullet("<b>Prompt Registry</b> — view and health-check system prompts; prompts that regressed are flagged"))
    story.append(bullet("<b>Policy / Settings</b> — enable/disable MCOS features, set benchmark schedule"))
    story.append(sp(8))

    # ── PAGE: Governance ──────────────────────────────────────────────────
    story.append(h2("Governance  (/governance)"))
    story.append(p(
        "The Governance page is the quality-control centre for knowledge extracted by the AI. "
        "Everything the LLM extracts goes into a review queue here before it influences "
        "your research or chat context — unless you've approved automatic acceptance in Settings."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Review Queue Tabs</b> — Knowledge Items, Document Findings, Benchmark Regressions — each with a count badge"))
    story.append(bullet("<b>Unverified Items List</b> — AI-extracted claims waiting for review, sorted by confidence (lowest first by default)"))
    story.append(bullet("<b>Item Detail</b> — full text, source document, extraction method, confidence score, and provenance chain"))
    story.append(bullet("<b>Confidence Explanation</b> — hover any confidence score to see what drove it up or down"))
    story.append(bullet("<b>Approve / Reject Controls</b> — accept a claim into the knowledge base, or reject it with an optional note"))
    story.append(bullet("<b>Edit</b> — correct the claim text before approving"))
    story.append(h3("Workflow"))
    story.append(bullet("Items appear here automatically after LLM extraction — you don't need to trigger this"))
    story.append(bullet("Approve high-confidence items in batch; review low-confidence items carefully"))
    story.append(bullet("Rejected items are soft-deleted — they remain in the database for audit purposes"))
    story.append(sp(8))

    # ── PAGE: Review ──────────────────────────────────────────────────────
    story.append(h2("Review  (/review)"))
    story.append(p(
        "The Review page is a simplified review queue focused on document findings and "
        "knowledge items that need human verification. It is similar to Governance but "
        "presents items in a card-based workflow optimised for fast review."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Queue Header</b> — total pending count, filter by Work or document"))
    story.append(bullet("<b>Filter Controls</b> — filter by knowledge type, confidence band, or source document"))
    story.append(bullet("<b>Pending Knowledge Cards</b> — one card per item showing claim, source, confidence, and Accept/Reject buttons"))
    story.append(bullet("<b>Item Detail</b> — expand a card to see full provenance including which chunk of which document generated the claim"))
    story.append(sp(8))

    # ── PAGE: System ───────────────────────────────────────────────────────
    story.append(h2("System  (/system)"))
    story.append(p(
        "The System page is the administration console for the Orivellum installation. "
        "This is where you check connectivity, run diagnostics, manage embeddings, "
        "configure nightshift, and review system health."
    ))
    story.append(h3("Key Cards"))
    story.append(bullet("<b>LLM Status</b> — live connectivity check against your LLM server; shows model name and latency"))
    story.append(bullet("<b>Semantic Search / Embeddings</b> — embedding model health, circuit breaker state, Probe and Reindex buttons"))
    story.append(bullet("<b>Diagnostics</b> — run the full diagnostic suite: schema, integrity, orphans, connectivity, OCR/FFmpeg, data quality — results in categorised OK/WARN/ERROR format"))
    story.append(bullet("<b>Nightshift</b> — last run time and result, Run Now button, next scheduled time"))
    story.append(bullet("<b>Audio Enhancement</b> — enable/disable DeepFilterNet3 noise reduction for audio files"))
    story.append(bullet("<b>Settings</b> — enable/disable AI extraction, nightshift, deep-mode defaults, and other system-wide toggles"))
    story.append(bullet("<b>API Key</b> — view or regenerate the bearer token used by external API clients and the mobile app"))
    story.append(h3("When to Visit"))
    story.append(bullet("After installing: verify LLM connectivity and embeddings are green"))
    story.append(bullet("After changing models: run Diagnostics and Probe Embeddings"))
    story.append(bullet("When documents stop processing: check Diagnostics for 'stuck documents' warning"))
    story.append(bullet("Monthly: trigger Nightshift Run Now and check the report"))
    story.append(sp(8))

    # ── PAGE: Studio ───────────────────────────────────────────────────────
    story.append(h2("Studio  (/studio — in Works Detail)"))
    story.append(p(
        "The Studio is an AI document generation workspace embedded inside the Work Detail page. "
        "It lets you instruct the AI to generate new documents — chapters, outlines, summaries, "
        "reports — using your knowledge base as the source of truth."
    ))
    story.append(h3("Sections"))
    story.append(bullet("<b>Plan Panel</b> — describe what you want to generate; the AI creates a structured writing plan"))
    story.append(bullet("<b>Execute Panel</b> — approve the plan and run generation; streams the document in real time"))
    story.append(bullet("<b>Critique Loop</b> — the AI critiques its own output against your knowledge base, then revises"))
    story.append(bullet("<b>Output Panel</b> — final document with export to DOCX, Markdown, or direct library import"))
    story.append(bullet("<b>Read Aloud</b> — listen to the generated document via TTS"))
    story.append(sp(8))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 6 — MOBILE APP
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 6", "chapter_label"))
    story.append(h1("Mobile App"))
    story.append(hr())
    story.append(p(
        "The Orivellum mobile app (iOS and Android) is an Expo/React Native companion to "
        "the web platform. It focuses on the most portable workflows: reading documents "
        "aloud, chatting with your AI, reviewing knowledge, and learning on the go. "
        "It connects to the same local API server as the web app."
    ))
    story.append(sp(4))

    story.append(h2("Connecting the Mobile App"))
    story.append(bullet("Open the mobile app → Settings → API URL → enter your server's URL"))
    story.append(bullet("On the same Wi-Fi: use your machine's local IP address (e.g. http://192.168.1.100:8080)"))
    story.append(bullet("Remotely: use Tailscale or a similar VPN — the API supports CORS for the 100.64–127.x.x Tailscale range"))
    story.append(bullet("The API key is shown on the System page — enter it in the mobile app's Settings → API Key field"))
    story.append(sp(6))

    story.append(h2("Mobile Pages"))
    mobile_pages = [
        ("Library", "Browse, search, and upload documents. Tap a document to open the detail view with knowledge review and Work linking."),
        ("Read Aloud", "Listen to any document in chunked TTS. Full playback controls, silence mode support. Downloads the audio file to device storage."),
        ("Chat", "Full AI conversation with streaming responses. Linked to any Work — same context injection as the web app."),
        ("Works Detail", "Overview, Documents, Knowledge, and Brainstorm tabs for a Work — same content as the web but optimised for touch."),
        ("Learn", "Socratic learning sessions on the go. Same question-answer-feedback loop as the web."),
        ("Knowledge Graph", "Touch-interactive entity graph with the same filter chips and entity colours as the web."),
    ]
    for page, desc in mobile_pages:
        story.append(p(f"<b>{page}</b> — {desc}", "bullet"))
        story.append(sp(2))

    story.append(sp(6))
    story.append(warning_box(
        "The 'Save to Files' button for downloaded audiobooks is only shown on iOS, "
        "not on Android — Android saves to a system-accessible location automatically."
    ))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 7 — WORKFLOWS AND BEST PRACTICES
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 7", "chapter_label"))
    story.append(h1("Workflows &amp; Best Practices"))
    story.append(hr())

    story.append(h2("Starting a New Book Project — Full Workflow"))
    steps = [
        ("Create a Work", "Works → Create Work → set title, type (Fiction/Non-fiction), description → Save"),
        ("Upload source material", "In the Work Detail → Documents tab → Upload. Add all research PDFs, DOCX, notes, and reference files. ZIPs are automatically exploded."),
        ("Wait for extraction", "Documents tab shows readiness progress. All documents should reach 'ready' within a few minutes. Reprocess any that error."),
        ("Review AI knowledge", "Work Detail → Knowledge tab → review AI-extracted items (purple Sparkles badge). Approve good ones, reject bad ones. Also check Governance for the full queue."),
        ("Check the intelligence report", "Work Detail → Intelligence tab. Note any gaps or contradictions. Add missing research documents."),
        ("Start writing", "Use the Brainstorm tab to generate outlines and ideas. Use Studio (inside Book tab) to generate chapter drafts."),
        ("Use Chat for research questions", "Quick Chat → ask questions about your topic. The AI draws from your approved knowledge items."),
        ("Advance the book pipeline", "Work Detail → Book tab → advance the state machine (Draft → Research → Writing → Editing → Final) as each stage completes."),
        ("Finishing", "When writing is complete: Finishing page → run pre-flight → order chapters → export DOCX for your editor."),
    ]
    for i, (title, desc) in enumerate(steps, 1):
        story.append(KeepTogether([
            p(f"<b>Step {i}: {title}</b>"),
            p(desc, "body_sm"),
            sp(4),
        ]))

    story.append(sp(6))
    story.append(h2("Research Workflow (Non-book)"))
    story.append(bullet("Create a Work with type 'Research'"))
    story.append(bullet("Upload all source PDFs and documents"))
    story.append(bullet("Enable AI extraction in Settings (if not already on)"))
    story.append(bullet("Use the Knowledge Graph to spot entity relationships you hadn't noticed"))
    story.append(bullet("Use the Topics page to see which themes cluster across your sources"))
    story.append(bullet("Chat with your sources: 'What does the literature say about X?' draws from your approved knowledge"))
    story.append(bullet("Use internet search in Chat for gaps not covered by your documents"))
    story.append(sp(6))

    story.append(h2("Daily Maintenance Checklist"))
    story.append(bullet("Dashboard → check gap analysis for new gaps detected overnight"))
    story.append(bullet("Governance → review any new AI-extracted items in the queue"))
    story.append(bullet("Library → check for any documents stuck in 'error' or 'no_text' state"))
    story.append(bullet("System → confirm LLM status is green"))
    story.append(sp(6))

    story.append(h2("Monthly Maintenance Checklist"))
    story.append(bullet("System → Nightshift → Run Now — verify the report shows no critical errors"))
    story.append(bullet("System → Diagnostics → Run — review any WARN or ERROR items"))
    story.append(bullet("System → Embeddings → Probe — confirm embeddings are healthy"))
    story.append(bullet("If the database is over 500 MB: System → Diagnostics → Run with --vacuum flag"))
    story.append(sp(6))

    story.append(h2("Search Tips"))
    story.append(bullet("Library search uses hybrid FTS5 + semantic search — short queries (&lt;3 words) use lexical only"))
    story.append(bullet("For best semantic results: phrase your search as a complete question, not just keywords"))
    story.append(bullet("In Chat: link the conversation to a Work before asking — the AI searches that Work's knowledge base"))
    story.append(bullet("Knowledge items tagged as 'rejected' are excluded from search and chat context"))
    story.append(sp(6))

    story.append(h2("Internet Research — Search Modes"))
    story.append(p("When internet search is enabled in Chat, the system automatically selects the best search mode:"))
    story.append(bullet("<b>WEB</b> — always included; general web results"))
    story.append(bullet("<b>BIBLICAL</b> — auto-detected from scripture/theology keywords; searches biblegateway, biblehub, blueletterbible, ccel, thegospelcoalition, and 17 other curated sources"))
    story.append(bullet("<b>NEWS</b> — auto-detected from words like 'today', 'latest', '2026'; uses Tavily news lane"))
    story.append(bullet("<b>YOUTUBE</b> — searches YouTube and pulls full video transcripts (not just titles)"))
    story.append(bullet("<b>FACEBOOK</b> — searches publicly-indexed Facebook pages only"))
    story.append(bullet("<b>ACADEMIC</b> — restricts to arxiv, PubMed, JSTOR, Semantic Scholar, and similar scholarly sources"))

    # ══════════════════════════════════════════════════════════════════════
    #  CHAPTER 8 — QUICK REFERENCE
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(p("Chapter 8", "chapter_label"))
    story.append(h1("Quick Reference"))
    story.append(hr())

    story.append(h2("API Endpoints — Most Useful"))
    api_endpoints = [
        ["Endpoint", "Method", "Description"],
        ["/api/library/upload", "POST", "Upload a file (multipart/form-data). Fields: file, work_id (optional)"],
        ["/api/library/reprocess-all", "POST", "Bulk reprocess all stuck/error/no_text documents"],
        ["/api/library/{doc_id}/reprocess", "POST", "Reprocess a single document. Add ?force=true to force even if ready"],
        ["/api/system/nightshift/run-now", "POST", "Trigger a full nightshift maintenance run immediately"],
        ["/api/system/nightshift/status", "GET", "Last run time, result, and next scheduled time"],
        ["/api/system/embeddings/status", "GET", "Embedding model health, dimension, vector counts"],
        ["/api/system/embeddings/probe", "POST", "Live-test the embedder and reset circuit breaker on success"],
        ["/api/system/diagnostics", "GET", "Full system health report (schema, integrity, tools, LLM)"],
        ["/api/works", "GET", "List all Works. Query: ?work_type=&status="],
        ["/api/works/{id}/stats", "GET", "Document count, knowledge count, conversation count for a Work"],
        ["/api/library", "GET", "List documents. Query: ?work_id=&readiness=&kind="],
        ["/api/conversations", "GET", "List conversations. Query: ?work_id=&archived=false"],
    ]
    t_api = Table([[Paragraph(c, S["table_header"] if i == 0 else
                   (S["table_cell_code"] if j == 0 else S["table_cell"]))
                   for j, c in enumerate(row)] for i, row in enumerate(api_endpoints)],
                  colWidths=[2.4*inch, 0.6*inch, 3.5*inch])
    t_api.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), NAVY),
        ("BACKGROUND",    (0,1), (-1,-1), OFFWHT),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[OFFWHT, colors.HexColor("#EEE9DF")]),
        ("GRID",          (0,0), (-1,-1), 0.3, LTGREY),
        ("LEFTPADDING",   (0,0), (-1,-1), 5),
        ("RIGHTPADDING",  (0,0), (-1,-1), 5),
        ("TOPPADDING",    (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("VALIGN",        (0,0), (-1,-1), "TOP"),
    ]))
    story.append(t_api)
    story.append(sp(10))

    story.append(h2("Keyboard Shortcuts (Web App)"))
    shortcuts = [
        ["Shortcut", "Action"],
        ["Ctrl + Enter", "Send chat message"],
        ["Ctrl + K", "Open global search"],
        ["Ctrl + Shift + A", "Toggle archived conversations"],
        ["Escape", "Close any open dialog or panel"],
        ["Ctrl + C (on AI message)", "Copy message to clipboard (click copy button on the message)"],
    ]
    story.append(two_col_table(["Shortcut", "Action"], shortcuts[1:],
                               col_widths=[2.0*inch, 4.5*inch]))
    story.append(sp(10))

    story.append(h2("Useful Scripts"))
    scripts = [
        ("uv run python scripts/run_diagnostics.py", "Full system health report"),
        ("uv run python scripts/run_diagnostics.py --vacuum", "Health report + SQLite VACUUM"),
        ("uv run python scripts/import_excel_vault.py", "Re-import the Excel Training Vault"),
        ("uv run python -m orivellum.api.main", "Start the API server manually"),
        ("pnpm --filter @workspace/orivellum-ui run dev", "Start the web frontend (dev mode)"),
        ("pnpm --filter @workspace/mobile run dev", "Start the mobile app (Expo)"),
        ("uv run pytest tests/", "Run all Python tests"),
    ]
    for cmd, desc in scripts:
        story.append(p(f"<b>{desc}</b>"))
        story.append(code(cmd))
        story.append(sp(3))

    story.append(sp(8))
    story.append(h2("Glossary"))
    terms = [
        ("ASR", "Automatic Speech Recognition — speech-to-text using Whisper or faster-whisper"),
        ("BM25", "Best Match 25 — a lexical relevance scoring algorithm used for passage ranking"),
        ("Circuit Breaker", "A fault-tolerance pattern that stops retrying a failing service after N failures and retries automatically after a cooldown"),
        ("Embeddings", "Dense numerical vectors representing text meaning, used for semantic similarity search"),
        ("FTS5", "SQLite's built-in full-text search engine used for fast keyword search"),
        ("LLM", "Large Language Model — the AI model that powers chat, extraction, and synthesis"),
        ("MCOS", "Model Calibration &amp; Observability System — Orivellum's LLM telemetry and quality framework"),
        ("Nightshift", "The nightly maintenance daemon that keeps the database healthy"),
        ("PKLOS", "Policy Knowledge and Lifecycle Operating System — Orivellum's governance and audit framework"),
        ("RAG", "Retrieval-Augmented Generation — injecting retrieved knowledge items as context before asking the LLM to answer"),
        ("RRF", "Reciprocal Rank Fusion — combines ranked lists from multiple searches into one fused ranking"),
        ("SSE", "Server-Sent Events — how the streaming chat responses are delivered to the browser"),
        ("TTS", "Text-to-Speech — converts document text to audio using the configured TTS model"),
        ("WAL", "Write-Ahead Logging — SQLite mode that allows concurrent reads while a write is in progress"),
        ("Work", "The top-level project container that groups documents, knowledge, conversations, and tasks"),
    ]
    for term, definition in terms:
        story.append(p(f"<b>{term}</b> — {definition}", "body_sm"))
        story.append(sp(1))

    # ── Final page ─────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(sp(100))
    story.append(HRFlowable(width="100%", thickness=1, color=GOLD,
                             spaceAfter=16, spaceBefore=0))
    story.append(p("Orivellum Intelligence Platform", "title_page_sub"))
    story.append(p("Ultimate Training Manual — Version 1.0 — August 2026", "title_page_ver"))
    story.append(p("Confidential — Internal Use Only", "title_page_ver"))
    story.append(sp(8))
    story.append(p("This document was generated automatically from the live Orivellum codebase.", "title_page_ver"))
    story.append(p("Re-run scripts/generate_training_manual.py to get a current version.", "title_page_ver"))

    return story


# ── Build ──────────────────────────────────────────────────────────────────────

def main():
    out_path = ROOT / "orivellum_training_manual.pdf"
    print(f"Building {out_path} …")

    doc = OrivellumDoc(str(out_path))
    story = build_story()

    from reportlab.platypus import NextPageTemplate
    # Wrap: cover template for page 1, then normal for all subsequent pages.
    # The cover content sits before the first PageBreak; we splice the
    # template switches around it.
    story.insert(0, NextPageTemplate("cover"))
    # Find the first PageBreak and inject the switch to "normal" just before it.
    for i, item in enumerate(story):
        if isinstance(item, PageBreak):
            story.insert(i, NextPageTemplate("normal"))
            break

    doc.multiBuild(story)
    size_kb = out_path.stat().st_size // 1024
    print(f"Done — {out_path} ({size_kb} KB)")
    return out_path


if __name__ == "__main__":
    main()
