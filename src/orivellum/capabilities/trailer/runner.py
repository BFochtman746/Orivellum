"""
runner.py — orchestrate the Trailer Architect pipeline inside Orivellum.

Called from the API route in a thread-pool executor so it doesn't block the
async event loop.  Progress is written back to the DB via status updates.

Supports format = 'full' | 'short' | 'both' (default: 'both').
  full  → standard 75 s 16:9 landscape package
  short → 30 s 9:16 vertical social clip (Reels / TikTok / Shorts)
  both  → package_json = {format:'both', full:{...}, short:{...}}
"""

from __future__ import annotations

import json
import logging
import traceback

from . import (
    analyze,
    package,
    validate,
)
from . import (
    concept as concept_mod,
)
from . import (
    method as method_mod,
)
from . import (
    plan as plan_mod,
)
from . import (
    plan_short as plan_short_mod,
)
from . import (
    plan_square as plan_square_mod,
)
from .config import build_trailer_config
from .io_orivellum import book_text_from_work
from .llm_adapter import OrivellumLLM

logger = logging.getLogger(__name__)

VALID_FORMATS = ("full", "short", "square", "both", "all")


def run_trailer_pipeline(
    db,
    work_id: str,
    trailer_id: str,
    fmt: str = "both",
) -> None:
    """Run the pipeline synchronously (call from a thread pool).

    fmt: 'full' | 'short' | 'both'
    Writes status/phase updates to the DB as each stage completes.
    """
    if fmt not in VALID_FORMATS:
        fmt = "both"

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
        # ── config ─────────────────────────────────────────────────────────
        cfg = build_trailer_config()
        llm = OrivellumLLM(cfg, offline=cfg.get("offline", False))

        # ── book content ───────────────────────────────────────────────────
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

        # ── stage 1: analyze (shared) ──────────────────────────────────────
        _update("running", "analyze")
        brief = analyze.run(llm, cfg, full_text, title_hint=title_hint)

        # ── stage 2: concepts (shared) ─────────────────────────────────────
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

        # ── stage 3: method (shared) ───────────────────────────────────────
        _update("running", "method")
        method = method_mod.build_method(cfg.get("registry"), chosen, cfg)

        # ── stage 4: plan ─────────────────────────────────────────────────
        _update("running", "plan")

        full_pkg: dict | None = None
        short_pkg: dict | None = None
        square_pkg: dict | None = None

        if fmt in ("full", "both", "all"):
            built_full = plan_mod.run(llm, cfg, brief, chosen, method)
            built_full["_all_concepts"] = cres["concepts"]
            val_full = validate.check(brief, chosen, method, built_full)
            full_pkg = package.build(
                brief=brief,
                concept=chosen,
                method=method,
                plan=built_full,
                validation=val_full,
            )

        if fmt in ("short", "both", "all"):
            _update("running", "plan_short")
            built_short = plan_short_mod.run(llm, cfg, brief, chosen, method)
            built_short["_all_concepts"] = cres["concepts"]
            val_short = validate.check(brief, chosen, method, built_short)
            short_pkg = package.build_short(
                brief=brief,
                concept=chosen,
                method=method,
                plan=built_short,
                validation=val_short,
            )

        if fmt in ("square", "all"):
            _update("running", "plan_square")
            built_square = plan_square_mod.run(llm, cfg, brief, chosen, method)
            built_square["_all_concepts"] = cres["concepts"]
            val_square = validate.check(brief, chosen, method, built_square)
            square_pkg = package.build_square(
                brief=brief,
                concept=chosen,
                method=method,
                plan=built_square,
                validation=val_square,
            )

        # ── stage 5/6: package ─────────────────────────────────────────────
        _update("running", "package")

        if fmt == "full":
            final_pkg = full_pkg
            val_status = full_pkg["validation"]["status"]  # type: ignore[index]
        elif fmt == "short":
            final_pkg = short_pkg
            val_status = short_pkg["validation"]["status"]  # type: ignore[index]
        elif fmt == "square":
            final_pkg = square_pkg
            val_status = square_pkg["validation"]["status"]  # type: ignore[index]
        elif fmt == "both":
            # Landscape + vertical — backward-compatible envelope
            full_ready = full_pkg["validation"]["status"] == "READY"  # type: ignore[index]
            short_ready = short_pkg["validation"]["status"] == "READY"  # type: ignore[index]
            val_status = "READY" if (full_ready and short_ready) else "BLOCKED"
            final_pkg = {
                "format": "both",
                "full": full_pkg,
                "short": short_pkg,
                # Convenience: shared fields promoted to top level so legacy
                # code that reads pkg.brief / pkg.concept / pkg.docs still works
                "brief": full_pkg["brief"],  # type: ignore[index]
                "concept": full_pkg["concept"],  # type: ignore[index]
                "method": full_pkg["method"],  # type: ignore[index]
                "generated": full_pkg["generated"],  # type: ignore[index]
                "docs": full_pkg["docs"],  # type: ignore[index]
                "plan": full_pkg["plan"],  # type: ignore[index]
                "validation": full_pkg["validation"],  # type: ignore[index]
                "shot_prompts": full_pkg["shot_prompts"],  # type: ignore[index]
                "status": val_status,
                "status_badge": "READY"
                if val_status == "READY"
                else f"BLOCKED (full={'✅' if full_ready else '⛔'} "
                f"short={'✅' if short_ready else '⛔'})",
            }
        else:
            # 'all' — all three formats in one envelope
            full_ready = full_pkg["validation"]["status"] == "READY"  # type: ignore[index]
            short_ready = short_pkg["validation"]["status"] == "READY"  # type: ignore[index]
            square_ready = square_pkg["validation"]["status"] == "READY"  # type: ignore[index]
            val_status = "READY" if (full_ready and short_ready and square_ready) else "BLOCKED"
            final_pkg = {
                "format": "all",
                "full": full_pkg,
                "short": short_pkg,
                "square": square_pkg,
                # Shared fields promoted from full package for legacy compatibility
                "brief": full_pkg["brief"],  # type: ignore[index]
                "concept": full_pkg["concept"],  # type: ignore[index]
                "method": full_pkg["method"],  # type: ignore[index]
                "generated": full_pkg["generated"],  # type: ignore[index]
                "docs": full_pkg["docs"],  # type: ignore[index]
                "plan": full_pkg["plan"],  # type: ignore[index]
                "validation": full_pkg["validation"],  # type: ignore[index]
                "shot_prompts": full_pkg["shot_prompts"],  # type: ignore[index]
                "status": val_status,
                "status_badge": (
                    "READY"
                    if val_status == "READY"
                    else f"BLOCKED (16:9={'✅' if full_ready else '⛔'} "
                    f"9:16={'✅' if short_ready else '⛔'} "
                    f"1:1={'✅' if square_ready else '⛔'})"
                ),
            }

        final_status = "ready" if val_status == "READY" else "blocked"
        _update(final_status, "done", pkg=final_pkg)
        logger.info("Trailer %s (%s) completed: %s", trailer_id, fmt, final_status)

    except Exception:
        err_msg = traceback.format_exc()
        logger.error("Trailer pipeline %s failed:\n%s", trailer_id, err_msg)
        _update("failed", "error", err=err_msg[-800:])
