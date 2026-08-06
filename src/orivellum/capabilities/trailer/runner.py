"""
runner.py — orchestrate the full Trailer Architect pipeline inside Orivellum.

Called from the API route in a thread-pool executor so it doesn't block the
async event loop.  Progress is written back to the DB via status updates.
"""
from __future__ import annotations
import json
import logging
import traceback

from . import analyze, concept as concept_mod, method as method_mod, plan as plan_mod, validate, package
from .config import build_trailer_config
from .llm_adapter import OrivellumLLM
from .io_orivellum import book_text_from_work

logger = logging.getLogger(__name__)


def run_trailer_pipeline(
    db,
    work_id: str,
    trailer_id: str,
) -> None:
    """Run the full pipeline synchronously (call from a thread pool).

    Writes status updates to db as it progresses:
      running → ready (or failed)
    """
    def _update(status: str, phase: str, pkg: dict | None = None, err: str | None = None) -> None:
        try:
            db.update_trailer(
                trailer_id,
                status=status,
                phase=phase,
                package_json=json.dumps(pkg) if pkg else None,
                error=err,
            )
        except Exception as exc:
            logger.warning("trailer status update failed: %s", exc)

    try:
        # -- config -------------------------------------------------------
        cfg = build_trailer_config()
        llm = OrivellumLLM(cfg, offline=cfg.get("offline", False))

        # -- pull book content from DB ------------------------------------
        _update("running", "loading")
        work = db.get_work(work_id)
        if not work:
            _update("failed", "loading", err=f"Work {work_id!r} not found")
            return
        title_hint = work.get("title", "")
        full_text = book_text_from_work(db, work_id)
        if not full_text:
            _update("failed", "loading", err="No extracted text found for this Work")
            return

        # -- stage 1: analyze  --------------------------------------------
        _update("running", "analyze")
        brief = analyze.run(llm, cfg, full_text, title_hint=title_hint)

        # -- stage 2: concepts  -------------------------------------------
        _update("running", "concept")
        cres = concept_mod.run(llm, cfg, brief)
        recommended = cres.get("recommended")
        if not recommended or not cres.get("concepts"):
            _update("failed", "concept", err="Concept stage returned no concepts")
            return
        chosen = next(
            (c for c in cres["concepts"] if c["name"] == recommended),
            cres["concepts"][0],
        )

        # -- stage 3: method  ---------------------------------------------
        _update("running", "method")
        method = method_mod.build_method(cfg.get("registry"), chosen, cfg)

        # -- stage 4: plan  -----------------------------------------------
        _update("running", "plan")
        built = plan_mod.run(llm, cfg, brief, chosen, method)
        built["_all_concepts"] = cres["concepts"]

        # -- stage 5: validate  -------------------------------------------
        _update("running", "validate")
        val = validate.check(brief, chosen, method, built)

        # -- stage 6: package  --------------------------------------------
        _update("running", "package")
        pkg = package.build(
            brief=brief,
            concept=chosen,
            method=method,
            plan=built,
            validation=val,
        )

        final_status = "ready" if val["status"] == "READY" else "blocked"
        _update(final_status, "done", pkg=pkg)
        logger.info("Trailer %s completed: %s", trailer_id, final_status)

    except Exception:
        err_msg = traceback.format_exc()
        logger.error("Trailer pipeline %s failed:\n%s", trailer_id, err_msg)
        _update("failed", "error", err=err_msg[-800:])
