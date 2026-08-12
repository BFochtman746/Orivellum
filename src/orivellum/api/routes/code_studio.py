"""Code Studio API routes.

POST /api/code/generate   — full plan→generate→test→package pipeline
POST /api/code/plan       — planning step only (returns file tree, no generation)
POST /api/code/analyze    — analyze an uploaded code zip (proposal-only)
GET  /api/code/download   — download a generated zip (delegates to generate routes)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.errors import internal_error

logger = logging.getLogger("orivellum.api.code_studio")

router = APIRouter(
    prefix="/api",
    tags=["code-studio"],
    dependencies=[Depends(require_auth)],
)


# ── Request / response models ──────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    description: str
    language: str | None = None   # python | javascript | typescript
    max_fix_retries: int = 2


class PlanRequest(BaseModel):
    description: str
    language: str | None = None


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/code/plan")
def plan_endpoint(body: PlanRequest):
    """Return the project plan (file tree + test strategy) without generating code.

    Fast — single LLM call.  Use this to preview the structure before committing
    to a full generation run.
    """
    if not body.description.strip():
        raise HTTPException(422, "description must not be empty")

    db = get_db()
    cfg = get_config()

    try:
        from orivellum.capabilities.code_studio import plan_project

        plan = plan_project(
            description=body.description.strip(),
            language=body.language,
            cfg=cfg,
            db=db,
        )
    except Exception as exc:
        raise internal_error(logger, exc, "code studio plan") from exc

    return {
        "ok": True,
        "title": plan.title,
        "description": plan.description,
        "language": plan.language,
        "entry_point": plan.entry_point,
        "test_command": plan.test_command,
        "dependencies": plan.dependencies,
        "files": [
            {"path": f.path, "description": f.description, "is_test": f.is_test}
            for f in plan.files
        ],
    }


@router.post("/code/generate")
def generate_endpoint(body: GenerateRequest):
    """Run the full pipeline: plan → generate → test → fix → package.

    Returns a download URL for the generated zip on success.
    Tests must pass for ok=True; if all fix retries are exhausted the result
    still returns the generated files but ok=False and test details.

    Long-running (~1–3 min for a typical Python project).
    """
    if not body.description.strip():
        raise HTTPException(422, "description must not be empty")

    db = get_db()
    cfg = get_config()

    try:
        from orivellum.capabilities.code_studio import run_pipeline

        result = run_pipeline(
            description=body.description.strip(),
            language=body.language,
            max_fix_retries=max(0, min(body.max_fix_retries, 3)),
            cfg=cfg,
            db=db,
        )
    except Exception as exc:
        raise internal_error(logger, exc, "code studio generate") from exc

    resp: dict = {
        "ok": result.ok,
        "title": result.title,
        "language": result.language,
        "error": result.error,
        "files": [
            {"path": f.path, "size": len(f.content), "is_test": f.path.split("/")[-1].startswith("test_")}
            for f in result.files
        ],
        "download_url": result.download_url,
    }

    if result.test_result:
        resp["tests"] = {
            "passed": result.test_result.passed,
            "output": result.test_result.output,
            "error": result.test_result.error,
            "tests_found": result.test_result.tests_found,
        }

    if result.plan:
        resp["plan"] = {
            "entry_point": result.plan.entry_point,
            "test_command": result.plan.test_command,
            "dependencies": result.plan.dependencies,
        }

    return resp


@router.post("/code/generate/file-content")
def get_file_content_endpoint(body: dict):
    """Return the full content of a specific generated file by path.

    Used by the Studio UI to display file contents in the code viewer.
    Pass { zip_path: '...', file_path: '...' } or
         { files: [{path, content}], file_path: '...' }.

    This is a lightweight helper — the heavy lifting is done by /code/generate.
    """
    # This endpoint is a stub; the UI holds file content in-memory from the
    # generate response; this exists for future persistence integration.
    return {"ok": False, "detail": "Use the generate response files array directly"}


@router.post("/code/analyze")
async def analyze_endpoint(file: UploadFile = File(...)):
    """Analyze an uploaded code zip and return findings + improvement proposals.

    Accepts a .zip file containing source code.  Returns:
    - summary: what the code does
    - issues: list of problems with severity
    - proposals: prioritized improvement suggestions

    PROPOSAL-ONLY — never modifies the uploaded code.
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(422, "Only .zip files are accepted")

    max_size = 10 * 1024 * 1024  # 10 MB
    content = await file.read()
    if len(content) > max_size:
        raise HTTPException(413, f"File too large — maximum {max_size // (1024*1024)} MB")

    db = get_db()
    cfg = get_config()

    try:
        from orivellum.capabilities.code_studio import analyze_project_zip

        result = analyze_project_zip(zip_bytes=content, cfg=cfg, db=db)
    except Exception as exc:
        raise internal_error(logger, exc, "code studio analyze") from exc

    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "Analysis failed"))

    return result
