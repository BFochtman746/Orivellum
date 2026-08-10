"""Document generation API routes — /api/generate/*

POST /api/generate/excel              → generate xlsx workbook from a Work
POST /api/generate/report             → generate pdf or docx report from a Work
POST /api/generate/slides             → generate pptx deck from a Work
POST /api/generate/bundle             → zip a list of output file paths
GET  /api/generate/download           → stream a generated file for download
POST /api/generate/workshop/plan      → clarifying-question planner (self-prompting)
POST /api/generate/workshop/execute   → AI code-gen → safe sandbox → critique loop
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.errors import internal_error

logger = logging.getLogger("orivellum.api.generate")

router = APIRouter(prefix="/api", tags=["generate"], dependencies=[Depends(require_auth)])
# ── Pydantic request models ────────────────────────────────────────────────────


class GenerateWorkRequest(BaseModel):
    work_id: str


class GenerateReportRequest(BaseModel):
    work_id: str
    format: str = "pdf"  # "pdf" | "docx"


class BundleRequest(BaseModel):
    work_id: str
    paths: list[str]
    name: str | None = None


# ── Workshop request models ────────────────────────────────────────────────────


class WorkshopPlanRequest(BaseModel):
    request: str
    format: str | None = None  # xlsx | docx | pdf | pptx
    work_id: str | None = None


class WorkshopExecuteRequest(BaseModel):
    session_id: str | None = None
    request: str
    format: str = "docx"
    work_id: str | None = None
    answers: dict[str, str] = {}


# ── Response shape ─────────────────────────────────────────────────────────────


def _generation_result(file_path: Path, doc_id: str, cfg) -> dict:
    """Build the standard response payload for a successful generation."""
    data_dir = Path(cfg.data_dir)
    rel = str(file_path.relative_to(data_dir))
    return {
        "ok": True,
        "doc_id": doc_id,
        "filename": file_path.name,
        "path": rel,
        # Clients can download via GET /api/generate/download?path=<rel>
        "download_url": f"/api/generate/download?path={rel}",
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
    }


# ── Excel ──────────────────────────────────────────────────────────────────────


@router.post("/generate/excel")
def generate_excel_endpoint(body: GenerateWorkRequest):
    """Generate an xlsx workbook summarising a Work's knowledge, docs, and tasks."""
    db = get_db()
    cfg = get_config()

    if not db.get_work(body.work_id):
        raise HTTPException(status_code=404, detail=f"Work {body.work_id!r} not found")

    try:
        from orivellum.capabilities.generate import generate_excel

        fpath, doc_id = generate_excel(body.work_id, db, cfg)
    except Exception as exc:
        raise internal_error(logger, exc, f"excel generation for work {body.work_id!r}") from exc

    return _generation_result(fpath, doc_id, cfg)


# ── Report (PDF / DOCX) ────────────────────────────────────────────────────────


@router.post("/generate/report")
def generate_report_endpoint(body: GenerateReportRequest):
    """Generate a PDF or DOCX research report from a Work's knowledge base."""
    db = get_db()
    cfg = get_config()

    if not db.get_work(body.work_id):
        raise HTTPException(status_code=404, detail=f"Work {body.work_id!r} not found")

    fmt = body.format.lower()
    if fmt not in ("pdf", "docx"):
        raise HTTPException(status_code=422, detail="format must be 'pdf' or 'docx'")

    try:
        if fmt == "pdf":
            from orivellum.capabilities.generate import generate_pdf_report

            fpath, doc_id = generate_pdf_report(body.work_id, db, cfg)
        else:
            from orivellum.capabilities.generate import generate_docx_report

            fpath, doc_id = generate_docx_report(body.work_id, db, cfg)
    except Exception as exc:
        raise internal_error(
            logger, exc, f"report generation ({fmt}) for work {body.work_id!r}"
        ) from exc

    return _generation_result(fpath, doc_id, cfg)


# ── Slides (PPTX) ─────────────────────────────────────────────────────────────


@router.post("/generate/slides")
def generate_slides_endpoint(body: GenerateWorkRequest):
    """Generate a PowerPoint slide deck from a Work's knowledge base."""
    db = get_db()
    cfg = get_config()

    if not db.get_work(body.work_id):
        raise HTTPException(status_code=404, detail=f"Work {body.work_id!r} not found")

    try:
        from orivellum.capabilities.generate import generate_pptx

        fpath, doc_id = generate_pptx(body.work_id, db, cfg)
    except Exception as exc:
        raise internal_error(logger, exc, f"pptx generation for work {body.work_id!r}") from exc

    return _generation_result(fpath, doc_id, cfg)


# ── Bundle (ZIP) ───────────────────────────────────────────────────────────────


@router.post("/generate/bundle")
def generate_bundle_endpoint(body: BundleRequest):
    """Zip a list of output file paths (relative to data_dir) into a single archive."""
    db = get_db()
    cfg = get_config()

    if not db.get_work(body.work_id):
        raise HTTPException(status_code=404, detail=f"Work {body.work_id!r} not found")

    if not body.paths:
        raise HTTPException(status_code=422, detail="paths list must not be empty")

    try:
        from orivellum.capabilities.generate import bundle_files

        fpath, doc_id = bundle_files(body.paths, body.name or "bundle", body.work_id, db, cfg)
    except Exception as exc:
        raise internal_error(logger, exc, f"bundle generation for work {body.work_id!r}") from exc

    return _generation_result(fpath, doc_id, cfg)


# ── Download ───────────────────────────────────────────────────────────────────


@router.get("/generate/download")
def download_generated_file(path: str):
    """Stream a generated file for download.

    `path` is relative to the data_dir (e.g. ``outputs/generate/{work_id}/report.pdf``).
    The file must exist under data/outputs/generate/ to prevent path traversal.
    """
    cfg = get_config()
    data_dir = Path(cfg.data_dir)
    generate_root = data_dir / "outputs" / "generate"

    try:
        target = (data_dir / path).resolve()
        target.relative_to(generate_root.resolve())  # path-traversal guard
    except (ValueError, Exception):
        raise HTTPException(status_code=400, detail="Invalid path")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    suffix_mime = {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }
    media_type = suffix_mime.get(target.suffix.lower(), "application/octet-stream")

    return FileResponse(
        str(target),
        media_type=media_type,
        filename=target.name,
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


# ── Prompt-driven generation (chat-native) ────────────────────────────────────


class GenerateFromPromptRequest(BaseModel):
    prompt: str
    format: str = "docx"  # docx | pdf | pptx | xlsx
    filename: str | None = None
    work_id: str | None = None
    conversation_id: str | None = None  # informational only (not used server-side)


@router.post("/generate/from-prompt")
async def generate_from_prompt_endpoint(body: GenerateFromPromptRequest):
    """Generate a DOCX, PDF, PPTX, or XLSX from any free-form text prompt.

    Unlike the work-scoped endpoints, this does not require a work_id — it works
    from any chat conversation.  The generated file is stored in
    ``data/outputs/generate/chat/`` (or the work's folder when work_id is supplied)
    and registered as an ARTIFACT-tier library document.
    """
    db = get_db()
    cfg = get_config()

    fmt = body.format.lower().strip(".")
    if fmt not in ("docx", "pdf", "pptx", "xlsx"):
        raise HTTPException(status_code=422, detail="format must be docx, pdf, pptx, or xlsx")

    if not body.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt must not be empty")

    # Validate work_id when supplied
    if body.work_id and not db.get_work(body.work_id):
        raise HTTPException(status_code=404, detail=f"Work {body.work_id!r} not found")

    try:
        from orivellum.capabilities.generate import generate_from_prompt

        fpath, doc_id = generate_from_prompt(
            prompt=body.prompt,
            format=fmt,
            filename=body.filename,
            work_id=body.work_id,
            db=db,
            cfg=cfg,
        )
    except Exception as exc:
        raise internal_error(logger, exc, "from-prompt generation") from exc

    return _generation_result(fpath, doc_id, cfg)


# ── Workshop — self-prompting, AI code-generated, critique-looped ──────────────


@router.post("/generate/workshop/plan")
def workshop_plan(body: WorkshopPlanRequest):
    """Step 1: LLM generates clarifying questions for the document request.

    Returns session_id + questions + detected_format + detected_intent.
    Call /generate/workshop/execute with the session_id + answers to produce the file.
    """
    if not body.request or not body.request.strip():
        raise HTTPException(status_code=422, detail="request must not be empty")

    db = get_db()
    cfg = get_config()

    try:
        from orivellum.capabilities.workshop import plan_document

        session = plan_document(
            request=body.request.strip(),
            format_hint=body.format,
            work_id=body.work_id,
            db=db,
            cfg=cfg,
        )
    except Exception as exc:
        raise internal_error(logger, exc, "workshop plan") from exc

    return session


@router.post("/generate/workshop/execute")
def workshop_execute(body: WorkshopExecuteRequest):
    """Step 2: Generate the document — AI writes code → safe sandbox → critique.

    Pass session_id from /plan (optional but recommended for context),
    plus answers dict keyed by question id.

    Returns ok, download_url, critique, doc_id.
    """
    if not body.request or not body.request.strip():
        raise HTTPException(status_code=422, detail="request must not be empty")

    db = get_db()
    cfg = get_config()

    try:
        from orivellum.capabilities.workshop import execute_workshop

        result = execute_workshop(
            session_id=body.session_id,
            request=body.request.strip(),
            format=body.format,
            work_id=body.work_id,
            answers=body.answers or {},
            db=db,
            cfg=cfg,
        )
    except Exception as exc:
        raise internal_error(logger, exc, "workshop execute") from exc

    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))

    return result
