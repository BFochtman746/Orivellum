"""Pacing & immersion engine — API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities.pacing import (
    PacingError,
    PROFILES,
    analyze_scene,
    extract_scenes,
    get_pacing_profile,
    get_pacing_run,
    get_scene,
    list_pacing_findings,
    list_pacing_runs,
    list_scenes,
    resolve_pacing_finding,
    run_pacing_diagnostics,
    update_pacing_profile,
    update_scene,
)
from orivellum.api.executor import submit_bg

router = APIRouter(prefix="/api", tags=["pacing"], dependencies=[Depends(require_auth)])


def _not_found(msg: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg)


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=msg)


# ── Scene extraction ──────────────────────────────────────────────────────────

@router.post("/works/{work_id}/pacing/scenes/extract")
def route_extract_scenes(
    work_id: str,
    chapter_id: Optional[str] = Body(default=None, embed=True),
    db=Depends(get_db), cfg=Depends(get_config),
):
    """Propose scene segmentation from chapter text.

    Runs synchronously for single-chapter extractions; for full-book extraction
    the same endpoint is used (caller should expect a brief delay).
    """
    try:
        scenes = extract_scenes(db, cfg, work_id, chapter_id=chapter_id)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    return {"scenes": scenes, "count": len(scenes)}


@router.get("/works/{work_id}/pacing/scenes")
def route_list_scenes(
    work_id: str,
    chapter_id: Optional[str] = Query(default=None),
    db=Depends(get_db),
):
    return {"scenes": list_scenes(db, work_id, chapter_id=chapter_id)}


@router.patch("/pacing/scenes/{scene_id}")
def route_update_scene(
    scene_id: str,
    body: dict = Body(default={}),
    db=Depends(get_db),
):
    """Author correction of a proposed scene field."""
    try:
        scene = update_scene(db, scene_id, **body)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    return {"scene": scene}


# ── Scene analysis ────────────────────────────────────────────────────────────

@router.post("/pacing/scenes/{scene_id}/analyze")
def route_analyze_scene(
    scene_id: str,
    db=Depends(get_db), cfg=Depends(get_config),
):
    """Extract pacing metrics for one scene via LLM."""
    try:
        metrics = analyze_scene(db, cfg, scene_id)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    return {"metrics": metrics}


@router.post("/works/{work_id}/pacing/analyze-all")
def route_analyze_all_scenes(
    work_id: str,
    db=Depends(get_db), cfg=Depends(get_config),
):
    """Kick off background analysis of all scenes for this work."""
    scenes = list_scenes(db, work_id)
    if not scenes:
        raise _bad("No scenes found — extract scenes first")

    def _run():
        for sc in scenes:
            try:
                analyze_scene(db, cfg, sc["id"])
            except Exception as exc:
                import logging
                logging.getLogger("orivellum.pacing").warning(
                    "Scene analysis failed for %s: %s", sc["id"], exc
                )

    submit_bg(_run)
    return {"queued": len(scenes)}


# ── Diagnostics runs ──────────────────────────────────────────────────────────

@router.post("/works/{work_id}/pacing/run")
def route_run_diagnostics(
    work_id: str,
    body: dict = Body(default={}),
    db=Depends(get_db), cfg=Depends(get_config),
):
    """Run all seven pacing detectors for a work."""
    profile = body.get("profile")
    try:
        run = run_pacing_diagnostics(db, cfg, work_id, profile=profile)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    findings = list_pacing_findings(db, run["id"])
    return {"run": run, "findings": findings}


@router.get("/works/{work_id}/pacing/runs")
def route_list_runs(work_id: str, db=Depends(get_db)):
    return {"runs": list_pacing_runs(db, work_id)}


@router.get("/pacing/runs/{run_id}")
def route_get_run(run_id: str, db=Depends(get_db)):
    try:
        run = get_pacing_run(db, run_id)
    except PacingError:
        raise _not_found(f"Pacing run {run_id!r} not found")
    findings = list_pacing_findings(db, run_id)
    return {"run": run, "findings": findings}


# ── Findings ──────────────────────────────────────────────────────────────────

@router.get("/pacing/runs/{run_id}/findings")
def route_list_findings(run_id: str, db=Depends(get_db)):
    return {"findings": list_pacing_findings(db, run_id)}


@router.patch("/pacing/findings/{finding_id}")
def route_resolve_finding(
    finding_id: str,
    body: dict = Body(default={}),
    db=Depends(get_db),
):
    status = body.get("status", "open")
    note = body.get("resolution_note", "")
    try:
        finding = resolve_pacing_finding(db, finding_id, status, note)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    return {"finding": finding}


# ── Profile ───────────────────────────────────────────────────────────────────

@router.get("/works/{work_id}/pacing/profile")
def route_get_profile(work_id: str, db=Depends(get_db)):
    return {"profile": get_pacing_profile(db, work_id)}


@router.put("/works/{work_id}/pacing/profile")
def route_update_profile(
    work_id: str,
    body: dict = Body(default={}),
    db=Depends(get_db),
):
    profile_name = body.get("profile_name", "deep_immersive")
    thresholds = body.get("thresholds")
    try:
        profile = update_pacing_profile(db, work_id, profile_name, thresholds)
    except PacingError as exc:
        raise _bad(str(exc)) from exc
    return {"profile": profile}


# ── Profiles catalog ──────────────────────────────────────────────────────────

@router.get("/pacing/profiles")
def route_list_profiles():
    return {
        "profiles": [
            {"name": k, "label": v["label"]}
            for k, v in PROFILES.items()
        ]
    }
