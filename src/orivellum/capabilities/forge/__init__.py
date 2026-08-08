"""Forge Website Factory — integrated into Orivellum as a first-class capability.

Pipeline: PLAN → DESIGN (human selects direction) → BUILD → VERIFY → REVIEW → RELEASE

All LLM calls route through orivellum.capabilities.llm.llm_call() so they appear
in the llm_calls audit table and in MCOS governance.  Build artefacts land in
{data_dir}/forge-builds/{project_id}/{job_id}/ — a plain directory (no git worktree
requirement) seeded from the static-site template bundled here.

Public API
----------
run_forge_job(db, cfg, project_id, job_id) -> None
    Entry point called by the background executor.  Updates job status, appends
    events, and saves artefacts via db.*_forge_* helpers.
"""

from .pipeline import run_forge_job  # noqa: F401

__all__ = ["run_forge_job"]
