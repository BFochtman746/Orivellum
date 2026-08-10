"""Actions API — typed, grounded, reversible actions.

Routes
------
GET  /api/actions                    → list registered actions
GET  /api/actions/runs               → recent action_runs
GET  /api/actions/runs/{run_id}      → single run status
POST /api/actions/{name}/preview     → confirm_message (no side effects)
POST /api/actions/{name}/execute     → execute + return download URL
POST /api/actions/template-fill      → multipart: template file + data → execute
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.errors import internal_error

logger = logging.getLogger("orivellum.api.actions")
router = APIRouter(prefix="/api/actions", tags=["actions"], dependencies=[Depends(require_auth)])
# ── Lazy registry loader ───────────────────────────────────────────────────────

def _registry():
    from orivellum.capabilities.actions import get_registry
    return get_registry()


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("")
def list_actions():
    """Return all registered actions with their schemas."""
    reg = _registry()
    return {"actions": [a.to_dict() for a in reg.values()]}


@router.get("/runs")
def list_runs(
    limit: int = Query(30, ge=1, le=200),
    work_id: str | None = Query(None),
):
    """Return recent action runs, newest first."""
    db = get_db()
    from orivellum.capabilities.actions import list_runs as _list_runs
    runs = _list_runs(db, limit=limit, work_id=work_id)
    return {"runs": runs, "count": len(runs)}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    """Return a single action run by ID."""
    db = get_db()
    from orivellum.capabilities.actions import get_run as _get_run
    run = _get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/{run_id}/log")
def get_run_log(run_id: str):
    """Return structured log lines synthesised from run metadata.

    The action_runs table stores outcome fields (error, output_label, etc.)
    but not a verbatim log stream.  This endpoint assembles those fields into
    a chronological list of {ts, level, msg} entries suitable for display.
    """
    db = get_db()
    from orivellum.capabilities.actions import get_run as _get_run
    run = _get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    lines: list[dict] = []

    # Start event
    lines.append({
        "ts": run["created_at"],
        "level": "info",
        "msg": f"Action '{run['action_name']}' started",
    })

    # Inputs summary
    try:
        inputs = json.loads(run.get("inputs") or "{}")
    except Exception:
        inputs = {}
    if inputs:
        lines.append({
            "ts": run["created_at"],
            "level": "info",
            "msg": "Inputs: " + json.dumps(inputs, ensure_ascii=False),
        })

    # Work scope
    if run.get("work_id"):
        lines.append({
            "ts": run["created_at"],
            "level": "info",
            "msg": f"Scoped to work: {run['work_id']}",
        })

    # Outcome
    status = run.get("status", "running")
    completed_at = run.get("completed_at") or run["created_at"]

    if status == "done":
        label = run.get("output_label") or "output ready"
        lines.append({"ts": completed_at, "level": "info", "msg": f"Completed: {label}"})
        if run.get("output_path"):
            lines.append({"ts": completed_at, "level": "info", "msg": f"Output: {run['output_path']}"})
    elif status == "error":
        lines.append({
            "ts": completed_at,
            "level": "error",
            "msg": run.get("error") or "Action failed with an unknown error",
        })
    else:
        lines.append({"ts": run["created_at"], "level": "info", "msg": "Action is still running…"})

    # Duration
    if run.get("completed_at") and run.get("created_at"):
        try:
            from datetime import datetime
            start = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            end   = datetime.fromisoformat(run["completed_at"].replace("Z", "+00:00"))
            secs  = round((end - start).total_seconds(), 1)
            lines.append({"ts": completed_at, "level": "info", "msg": f"Duration: {secs}s"})
        except Exception:
            pass

    return {"run_id": run_id, "status": status, "lines": lines}


@router.post("/runs/{run_id}/retry")
async def retry_run(run_id: str):
    """Re-execute a run (typically a failed one) with the same original inputs.

    Creates a new action_runs row and returns its result immediately
    (actions are synchronous for now).
    """
    db = get_db()
    cfg = get_config()
    from orivellum.capabilities.actions import get_run as _get_run

    run = _get_run(db, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    reg = _registry()
    action = reg.get(run["action_name"])
    if not action:
        raise HTTPException(
            status_code=404,
            detail=f"Action '{run['action_name']}' is no longer registered",
        )

    try:
        inputs = json.loads(run.get("inputs") or "{}")
    except Exception:
        inputs = {}

    try:
        result = action.execute(inputs, db, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise internal_error(
            logger, exc, f"retry of action {run['action_name']!r}"
        ) from exc

    download_url: str | None = None
    if result.get("output_path"):
        base = (cfg.serving.base_url or "").rstrip("/")
        download_url = f"{base}/api/studio/outputs/serve?path={result['output_path']}"

    return {**result, "download_url": download_url}


@router.post("/{name}/preview")
async def preview_action(name: str, request: Request):
    """Return the confirmation message for an action without side effects.

    Body (JSON): action inputs dict.
    """
    reg = _registry()
    action = reg.get(name)
    if not action:
        raise HTTPException(status_code=404, detail=f"Action '{name}' not found")

    try:
        body = await request.json()
    except Exception:
        body = {}

    confirm = action.confirm_message(body)
    return {
        "action": name,
        "confirm_message": confirm,
        "description": action.description,
        "category": action.category,
    }


@router.post("/template-fill")
async def template_fill_upload(
    template: UploadFile = File(...),
    data: str = Form("{}"),
    output_name: str = Form("filled"),
    work_id: str = Form(""),
):
    """Template fill with a directly uploaded template file.

    Saves the uploaded file to a temp path in data/uploads/templates/,
    registers it as a library document, then runs TemplateFillAction.
    """
    import hashlib
    import uuid
    from pathlib import Path

    db = get_db()
    cfg = get_config()
    data_dir = Path(cfg.data_dir)

    # Save uploaded template
    tpl_dir = data_dir / "uploads" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(template.filename or "template.docx").suffix.lower() or ".docx"
    tpl_path = tpl_dir / f"{uuid.uuid4()}{suffix}"

    content = await template.read()
    tpl_path.write_bytes(content)

    # Register template as a library document
    sha = hashlib.sha256(content).hexdigest()
    rel = str(tpl_path.relative_to(data_dir))
    try:
        tpl_doc = db.create_document(
            title=f"Template: {template.filename or 'uploaded'}",
            source="upload/template",
            sha256=sha,
            kind=suffix.lstrip("."),
            work_id=work_id or None,
            content_path=rel,
            meta={"is_template": True},
            tier="artifact",
        )
        tpl_doc_id = tpl_doc["id"]
    except Exception as exc:
        # sha conflict — reuse existing doc
        with db._lock:
            existing = db._conn.execute(
                "SELECT id FROM documents WHERE sha256=? LIMIT 1", (sha,)
            ).fetchone()
        if existing:
            tpl_doc_id = existing["id"]
        else:
            raise internal_error(logger, exc, "register uploaded template") from exc

    # Parse data
    try:
        data_dict = json.loads(data) if data else {}
    except Exception:
        data_dict = {}

    # Run template fill
    reg = _registry()
    action = reg.get("template_fill")
    if not action:
        raise HTTPException(status_code=500, detail="template_fill action not registered")

    fill_inputs = {
        "template_doc_id": tpl_doc_id,
        "data": data_dict,
        "output_name": output_name,
        "work_id": work_id or None,
    }

    try:
        result = action.execute(fill_inputs, db, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise internal_error(logger, exc, "template_fill execution") from exc

    download_url: str | None = None
    if result.get("output_path"):
        base = (cfg.serving.base_url or "").rstrip("/")
        download_url = f"{base}/api/studio/outputs/serve?path={result['output_path']}"

    return {**result, "download_url": download_url}


@router.post("/{name}/execute")
async def execute_action(name: str, request: Request):
    """Execute a registered action.

    Body (JSON): action inputs dict (must satisfy input_schema).
    Returns: run_id, output_path, output_label, output_doc_id, download_url, summary.
    """
    reg = _registry()
    action = reg.get(name)
    if not action:
        raise HTTPException(status_code=404, detail=f"Action '{name}' not found")

    try:
        inputs = await request.json()
    except Exception:
        inputs = {}

    # ── Input validation: check required fields are present ────────────────────
    input_schema = action.input_schema or {}
    required_fields: list[str] = input_schema.get("required", [])
    schema_props: dict = input_schema.get("properties", {})
    missing = [f for f in required_fields if inputs.get(f) in (None, "", [])]
    if missing:
        descriptions = {k: v.get("description", k) for k, v in schema_props.items()}
        detail = "Missing required input fields: " + ", ".join(
            f"{f} ({descriptions.get(f, '')})" for f in missing
        )
        raise HTTPException(status_code=422, detail=detail)

    db = get_db()
    cfg = get_config()

    try:
        result = action.execute(inputs, db, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise internal_error(logger, exc, f"action {name!r} execution") from exc

    # Build a download URL if there is an output path
    download_url: str | None = None
    if result.get("output_path"):
        base = (cfg.serving.base_url or "").rstrip("/")
        download_url = f"{base}/api/studio/outputs/serve?path={result['output_path']}"

    return {**result, "download_url": download_url}
