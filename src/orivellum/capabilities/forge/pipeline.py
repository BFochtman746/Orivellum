"""Forge pipeline orchestrator — called from the background executor."""
from __future__ import annotations

import json
import logging
import pathlib
from datetime import UTC, datetime

from .agent import run_builder
from .gates import run_quality_gates
from .planner import create_plan
from .visual import create_visual_design

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _build_work_context(db: object, work_id: str | None) -> str:
    """Pull the top knowledge items from the linked Work to enrich the brief."""
    if not work_id:
        return ""
    try:
        items = db.list_knowledge(work_id=work_id, limit=40)
        if not items:
            return ""
        lines = []
        for item in items:
            kind = item.get("kind", "fact")
            text = item.get("text", "")
            subj = item.get("subject", "")
            line = f"[{kind}] {subj + ': ' if subj else ''}{text}"
            lines.append(line)
        return "\n".join(lines[:30])
    except Exception as exc:
        logger.warning("Could not fetch work context: %s", exc)
        return ""


def _build_dir_for(cfg: object, project_id: str, job_id: str) -> pathlib.Path:
    data_dir = pathlib.Path(getattr(cfg, "data_dir", "data"))
    return data_dir / "forge-builds" / project_id / job_id


def _emit(db: object, job_id: str, phase: str, message: str, data: dict | None = None):
    """Append a Forge event — used as the on_event callback."""
    try:
        db.append_forge_event(job_id, phase, message, data)
    except Exception as exc:
        logger.warning("Could not append forge event: %s", exc)


def run_forge_job(db: object, cfg: object, project_id: str, job_id: str) -> None:
    """Entry point — runs in background executor thread."""
    job = db.get_forge_job(job_id)
    if not job:
        logger.error("Forge job %s not found", job_id)
        return

    project = db.get_forge_project(project_id)
    if not project:
        logger.error("Forge project %s not found", project_id)
        return

    job_type = job.get("type", "")
    db.update_forge_job(job_id, status="running", started_at=_now())

    def event(phase: str, message: str, data: dict | None = None):
        _emit(db, job_id, phase, message, data)

    try:
        if job_type == "PLAN":
            _run_plan(db, cfg, project, job, event)

        elif job_type == "DESIGN":
            _run_design(db, cfg, project, job, event)

        elif job_type == "BUILD":
            _run_build(db, cfg, project, job, event)

        elif job_type == "VERIFY":
            _run_verify(db, cfg, project, job, event)

        elif job_type == "REPAIR":
            _run_repair(db, cfg, project, job, event)

        else:
            raise ValueError(f"Unknown job type: {job_type}")

    except Exception as exc:
        logger.exception("Forge job %s failed: %s", job_id, exc)
        db.update_forge_job(job_id, status="failed", completed_at=_now(),
                            meta=json.dumps({"error": str(exc)}))
        event("job_error", f"Job failed: {exc}")


# ── Phase runners ──────────────────────────────────────────────────────────────

def _run_plan(db, cfg, project, job, event):
    work_context = _build_work_context(db, project.get("work_id"))
    plan = create_plan(
        cfg=cfg, db=db,
        brief=project.get("brief", ""),
        work_context=work_context,
        instruction=job.get("instruction") or "",
        on_event=event,
    )
    db.save_forge_artifact(job["id"], "site-plan", plan)
    db.update_forge_project(project["id"], config_update={"plan_job_id": job["id"]})
    db.update_forge_job(job["id"], status="awaiting_approval", completed_at=_now())
    event("plan_complete", "Plan ready — review and approve to continue.")


def _run_design(db, cfg, project, job, event):
    # Resolve the approved plan
    plan_job_id = job.get("plan_job_id") or project.get("config_data", {}).get("plan_job_id")
    if not plan_job_id:
        raise ValueError("A plan_job_id is required for DESIGN.")
    plan_art = db.get_forge_artifact(plan_job_id, "site-plan")
    if not plan_art:
        raise ValueError("Approved site-plan artifact not found.")
    plan = json.loads(plan_art["content_json"])

    design = create_visual_design(
        cfg=cfg, db=db, plan=plan,
        instruction=job.get("instruction") or "",
        on_event=event,
    )
    db.save_forge_artifact(job["id"], "visual-design", design)
    db.update_forge_project(project["id"], config_update={"design_job_id": job["id"]})
    db.update_forge_job(job["id"],
                        status="awaiting_approval",
                        plan_job_id=plan_job_id,
                        completed_at=_now())
    event("design_complete", "Three visual directions ready — select one to approve.")


def _run_build(db, cfg, project, job, event):
    plan_job_id   = job.get("plan_job_id") or project.get("config_data", {}).get("plan_job_id")
    design_job_id = job.get("design_job_id") or project.get("config_data", {}).get("design_job_id")
    if not plan_job_id:
        raise ValueError("plan_job_id required for BUILD.")
    if not design_job_id:
        raise ValueError("design_job_id required for BUILD — complete DESIGN first.")

    plan_art   = db.get_forge_artifact(plan_job_id, "site-plan")
    design_art = db.get_forge_artifact(design_job_id, "visual-design")
    if not plan_art or not design_art:
        raise ValueError("Plan or design artifact missing.")

    plan   = json.loads(plan_art["content_json"])
    design = json.loads(design_art["content_json"])

    # Pick the selected concept or fall back to the first
    selected_id = job.get("meta", {}) and json.loads(job.get("meta", "{}")).get("selected_concept_id")
    concepts = design.get("concepts", [])
    concept = next((c for c in concepts if c.get("id") == selected_id), None) or (concepts[0] if concepts else {})

    build_dir = _build_dir_for(cfg, project["id"], job["id"])
    db.update_forge_job(job["id"], build_dir=str(build_dir))

    summary = run_builder(
        cfg=cfg, db=db, build_dir=build_dir,
        plan=plan, concept=concept,
        instruction=job.get("instruction") or "",
        on_event=event,
    )

    # Run gates immediately after build
    gate_summary = run_quality_gates(build_dir, on_event=event)
    db.save_forge_artifact(job["id"], "gate-report", gate_summary)

    status = "passed" if gate_summary["status"] == "passed" else \
             "conditional" if gate_summary["status"] == "conditional" else "blocked"

    db.update_forge_job(job["id"],
                        status=status,
                        plan_job_id=plan_job_id,
                        design_job_id=design_job_id,
                        completed_at=_now(),
                        meta=json.dumps({"build_summary": summary,
                                         "gate_status": gate_summary["status"],
                                         "selected_concept_id": selected_id}))
    event("build_complete", f"Build finished — gate status: {gate_summary['status'].upper()}.",
          gate_summary)


def _run_verify(db, cfg, project, job, event):
    """Re-run gates on the latest build's directory."""
    target_job_id = job.get("target_job_id")
    if not target_job_id:
        raise ValueError("target_job_id required for VERIFY.")
    target_job = db.get_forge_job(target_job_id)
    if not target_job or not target_job.get("build_dir"):
        raise ValueError("Target build job has no build_dir.")

    build_dir = pathlib.Path(target_job["build_dir"])
    if not build_dir.exists():
        raise FileNotFoundError(f"Build directory not found: {build_dir}")

    event("verify_start", f"Re-running quality gates on build {target_job_id[:8]}…")
    gate_summary = run_quality_gates(build_dir, on_event=event)
    db.save_forge_artifact(job["id"], "gate-report", gate_summary)

    status = "passed" if gate_summary["status"] == "passed" else \
             "conditional" if gate_summary["status"] == "conditional" else "blocked"
    db.update_forge_job(job["id"],
                        status=status,
                        target_job_id=target_job_id,
                        completed_at=_now())
    event("verify_complete", f"Verification complete — {gate_summary['status'].upper()}.",
          gate_summary)


def _run_repair(db, cfg, project, job, event):
    """Re-run the build agent on an existing build directory."""
    target_job_id = job.get("target_job_id")
    if not target_job_id:
        raise ValueError("target_job_id required for REPAIR.")
    target_job = db.get_forge_job(target_job_id)
    if not target_job or not target_job.get("build_dir"):
        raise ValueError("Target build has no build_dir.")

    plan_job_id   = target_job.get("plan_job_id")
    design_job_id = target_job.get("design_job_id")
    plan_art   = db.get_forge_artifact(plan_job_id, "site-plan")   if plan_job_id   else None
    design_art = db.get_forge_artifact(design_job_id, "visual-design") if design_job_id else None

    plan    = json.loads(plan_art["content_json"])   if plan_art   else {}
    design  = json.loads(design_art["content_json"]) if design_art else {}
    concepts = design.get("concepts", [])
    target_meta = json.loads(target_job.get("meta", "{}"))
    selected_id = target_meta.get("selected_concept_id")
    concept = next((c for c in concepts if c.get("id") == selected_id), None) or \
              (concepts[0] if concepts else {})

    build_dir = pathlib.Path(target_job["build_dir"])
    db.update_forge_job(job["id"], build_dir=str(build_dir))

    max_rounds = int(json.loads(project.get("config", "{}")).get("max_repair_rounds", 12))
    summary = run_builder(
        cfg=cfg, db=db, build_dir=build_dir,
        plan=plan, concept=concept,
        instruction=(job.get("instruction") or
                     "Fix quality-gate failures from the previous build. Do not add unrelated features."),
        on_event=event,
        max_rounds=max_rounds,
    )

    gate_summary = run_quality_gates(build_dir, on_event=event)
    db.save_forge_artifact(job["id"], "gate-report", gate_summary)

    status = "passed" if gate_summary["status"] == "passed" else \
             "conditional" if gate_summary["status"] == "conditional" else "blocked"
    db.update_forge_job(job["id"],
                        status=status,
                        target_job_id=target_job_id,
                        completed_at=_now(),
                        meta=json.dumps({"repair_summary": summary,
                                         "gate_status": gate_summary["status"]}))
    event("repair_complete", f"Repair finished — {gate_summary['status'].upper()}.", gate_summary)
