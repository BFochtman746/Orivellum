"""Nightshift — nightly background maintenance, processing, and auto-memory.

Runs as a daemon thread started in the FastAPI lifespan.  Wakes at the
configured hour (default 03:00 local time) and executes every maintenance
pass in order, each in its own try/except so one failure never blocks the
rest.  Writes a markdown Night Report to data/nightshift/.

Passes (in order):
  1. Database optimisation  — VACUUM, ANALYZE, integrity check
  2. Temp-file cleanup      — delete zero-byte files from outputs/
  3. Old report pruning     — keep last 30 night reports
  4. Orphan cleanup         — knowledge / chunks with no parent document
  5. Stuck-document retry   — re-queue imported/error/no_text docs (up to 20)
  6. Sparse-doc harvest     — re-harvest docs with < 3 knowledge items
  7. Gap analysis           — detect research gaps for every active Work
  8. Evidence rescoring     — update confidence + detect contradictions
  9. Embedding backfill     — embed up to 300 new knowledge items
 10. Work stats refresh     — recompute cached stats for every active Work
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig

logger = logging.getLogger("orivellum.nightshift")

_MIN_KNOWLEDGE_ITEMS = 3
_MAX_DOCS_PER_RUN    = 20

# ── Run-status tracker ──────────────────────────────────────────────────────
# Module-level snapshot of the current/last in-process nightshift run, guarded
# by its own lock so the API can report progress without touching the DB.
_status_lock = threading.Lock()
_status: dict = {"running": False, "started_at": None, "finished_at": None}


def get_status() -> dict:
    """Return a copy of the current nightshift run status."""
    with _status_lock:
        return dict(_status)


def is_running() -> bool:
    with _status_lock:
        return bool(_status.get("running"))


def try_start() -> bool:
    """Atomically reserve the run slot.

    Under ``_status_lock``: if a run is already in flight, return False;
    otherwise mark it running (set started_at, clear finished_at) and return
    True.  The caller that receives True MUST eventually run ``run_nightshift``
    (which clears the flag in its ``finally``) so the reservation is released.
    """
    with _status_lock:
        if _status.get("running"):
            return False
        _status["running"] = True
        _status["started_at"] = datetime.now(timezone.utc).isoformat()
        _status["finished_at"] = None
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_docs_needing_work(db: "OrivellumDB") -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.work_id, d.title, d.source,
                      COUNT(k.id) AS kcount
               FROM documents d
               LEFT JOIN knowledge k ON k.source_doc_id = d.id
               WHERE d.readiness = 'ready'
               GROUP BY d.id
               HAVING kcount < ?
               ORDER BY d.created_at DESC
               LIMIT ?""",
            (_MIN_KNOWLEDGE_ITEMS, _MAX_DOCS_PER_RUN),
        ).fetchall()
    return [dict(r) for r in rows]


def _get_stuck_docs(db: "OrivellumDB", max_docs: int = 20) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.work_id, d.title, d.source, d.content_path,
                      d.kind, d.readiness
               FROM documents d
               WHERE d.readiness IN ('imported', 'error', 'no_text', 'reprocessing')
                 AND d.kind != 'zip'
                 AND datetime(d.created_at) < datetime('now', '-10 minutes')
               ORDER BY d.created_at ASC
               LIMIT ?""",
            (max_docs,),
        ).fetchall()
    return [dict(r) for r in rows]


def _record_run(db: "OrivellumDB", docs_processed: int, items_added: int,
                report_path: str | None) -> None:
    run_id = str(uuid.uuid4())
    now    = datetime.now(timezone.utc).isoformat()
    with db._lock:
        try:
            db._conn.execute(
                "INSERT OR REPLACE INTO nightshift_runs"
                "(id,ran_at,docs_processed,items_added,report_path) VALUES(?,?,?,?,?)",
                (run_id, now, docs_processed, items_added, report_path),
            )
            db._conn.commit()
        except Exception as exc:
            logger.warning("Could not record nightshift run: %s", exc)
    try:
        db.audit("system.nightshift_run", object_id=run_id,
                 object_type="nightshift_run", actor="system",
                 detail=f"docs={docs_processed} items={items_added}")
    except Exception:
        pass


def _write_report(data_dir: Path, date_str: str, items: list[str]) -> str:
    report_dir = data_dir / "nightshift"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{date_str}.md"
    header = f"# Night Report — {date_str}\n\nGenerated by Orivellum Nightshift.\n\n"
    body   = "\n".join(f"- {line}" for line in items) if items else "_Nothing to report._"
    path.write_text(header + body + "\n", encoding="utf-8")
    return str(path)


# ── Passes ────────────────────────────────────────────────────────────────────

def _pass_db_optimise(db: "OrivellumDB", report: list[str]) -> None:
    """VACUUM + ANALYZE + integrity check.  Keeps SQLite fast and healthy."""
    try:
        with db._lock:
            # integrity_check returns list of rows; "ok" means clean
            result = db._conn.execute("PRAGMA integrity_check(10)").fetchall()
            ok = len(result) == 1 and result[0][0] == "ok"
            if not ok:
                errors = [r[0] for r in result]
                logger.warning("DB integrity issues: %s", errors)
                report.append(f"⚠ DB integrity: {len(errors)} issue(s) — {errors[:3]}")
            else:
                logger.debug("DB integrity: ok")

            # WAL checkpoint — flush WAL to main DB file
            db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            # ANALYZE updates query-planner statistics
            db._conn.execute("ANALYZE")
            db._conn.commit()

        # VACUUM — run on the main serialized connection while holding the
        # app lock so our own writers cannot race it (avoids SQLITE_BUSY).
        # VACUUM cannot run inside a transaction, so commit first.
        with db._lock:
            db_path = None
            try:
                row = db._conn.execute("PRAGMA database_list").fetchone()
                if row:
                    db_path = row[2]  # filename column
            except Exception:
                pass

            size_before = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else None
            db._conn.commit()
            db._conn.execute("VACUUM")

        if size_before is not None:
            size_after = os.path.getsize(db_path)
            saved_mb = max(0, (size_before - size_after) / 1_048_576)
            msg = f"DB optimised — VACUUM saved {saved_mb:.1f} MB" if saved_mb > 0.05 \
                  else "DB optimised — VACUUM (no size change)"
        else:
            msg = "DB optimised — VACUUM + ANALYZE + WAL checkpoint"
        report.append(msg)
        logger.info(msg)

    except Exception as exc:
        logger.warning("DB optimise pass failed: %s", exc)
        report.append(f"⚠ DB optimise: {exc}")


def _pass_cleanup_outputs(cfg: "OrivellumConfig", report: list[str]) -> None:
    """Delete zero-byte temp files from the outputs directory."""
    try:
        out_dir = Path(cfg.data_dir) / "outputs"
        if not out_dir.exists():
            return
        deleted = 0
        for f in out_dir.rglob("*"):
            if f.is_file() and f.stat().st_size == 0:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass
        # Also remove empty subdirectories
        for d in sorted(out_dir.rglob("*"), reverse=True):
            if d.is_dir() and d != out_dir:
                try:
                    if not any(d.iterdir()):
                        d.rmdir()
                except Exception:
                    pass
        if deleted:
            report.append(f"Cleaned up {deleted} zero-byte temp file(s) from outputs/")
            logger.info("Nightshift: removed %d empty temp files", deleted)
    except Exception as exc:
        logger.warning("Output cleanup pass failed: %s", exc)


def _pass_prune_old_reports(cfg: "OrivellumConfig", keep: int = 30) -> None:
    """Keep only the most recent `keep` night reports."""
    try:
        report_dir = Path(cfg.data_dir) / "nightshift"
        if not report_dir.exists():
            return
        reports = sorted(report_dir.glob("*.md"), reverse=True)
        for old in reports[keep:]:
            try:
                old.unlink()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Report pruning failed: %s", exc)


def _pass_orphan_cleanup(db: "OrivellumDB", report: list[str]) -> None:
    """Remove knowledge items and chunks whose parent document no longer exists."""
    try:
        with db._lock:
            # Knowledge items with no document
            k_deleted = db._conn.execute(
                """DELETE FROM knowledge
                   WHERE source_doc_id IS NOT NULL
                     AND source_doc_id NOT IN (SELECT id FROM documents)"""
            ).rowcount
            # Chunks with no document
            c_deleted = db._conn.execute(
                """DELETE FROM chunks
                   WHERE doc_id NOT IN (SELECT id FROM documents)"""
            ).rowcount
            # Orphaned embeddings — type-aware: knowledge vectors checked
            # against knowledge, chunk vectors against chunks.  Never touch
            # vectors of other/unknown object types.
            v_deleted = 0
            try:
                v_deleted += db._conn.execute(
                    """DELETE FROM vectors
                       WHERE object_type = 'knowledge'
                         AND object_id NOT IN (SELECT id FROM knowledge)"""
                ).rowcount
                v_deleted += db._conn.execute(
                    """DELETE FROM vectors
                       WHERE object_type = 'chunk'
                         AND object_id NOT IN (SELECT id FROM chunks)"""
                ).rowcount
            except Exception:
                pass
            db._conn.commit()

        removed = k_deleted + c_deleted + v_deleted
        if removed:
            report.append(
                f"Orphan cleanup: removed {k_deleted} knowledge, "
                f"{c_deleted} chunks, {v_deleted} vectors"
            )
            logger.info("Nightshift orphan cleanup: k=%d c=%d v=%d",
                        k_deleted, c_deleted, v_deleted)
    except Exception as exc:
        logger.warning("Orphan cleanup pass failed: %s", exc)


def _pass_stuck_docs(db: "OrivellumDB", cfg: "OrivellumConfig",
                     report: list[str]) -> None:
    """Re-queue all stuck documents (imported/error/no_text) that have a file."""
    try:
        from orivellum.capabilities.pipeline import process_document as _proc
        lib_root = Path(cfg.data_dir) / "library"
        stuck = _get_stuck_docs(db, max_docs=20)
        queue: list[dict] = []
        for sdoc in stuck:
            content_path = sdoc.get("content_path")
            file_path: Path | None = None
            if content_path:
                file_path = lib_root / content_path
            elif sdoc.get("source"):
                file_path = Path(sdoc["source"])
            if not file_path or not file_path.exists():
                continue
            queue.append({**sdoc, "_file_path": str(file_path)})

        if not queue:
            return

        # Process the queue sequentially in ONE worker thread — 20 parallel
        # extraction pipelines would contend on the shared SQLite connection
        # and saturate CPU / the LLM endpoint on a single-user machine.
        def _worker(items: list[dict]) -> None:
            for it in items:
                try:
                    db.update_document_extracted(it["id"], "", 0,
                                                 readiness="imported",
                                                 error_message=None)
                    _proc(doc_id=it["id"], file_path=it["_file_path"],
                          kind=it.get("kind") or "text",
                          work_id=it.get("work_id"),
                          title=it.get("title") or it["id"],
                          db=db)
                except Exception as rec_exc:
                    logger.warning("Recovery failed for %s: %s", it["id"], rec_exc)

        threading.Thread(target=_worker, args=(queue,),
                         name="nightshift-recovery", daemon=True).start()
        report.append(f"Recovery: re-queued {len(queue)} stuck document(s) (sequential)")
        logger.info("Nightshift: queued %d stuck docs for sequential recovery", len(queue))
    except Exception as exc:
        logger.warning("Stuck-doc pass failed: %s", exc)


def _pass_sparse_harvest(db: "OrivellumDB", report: list[str]) -> int:
    """Re-harvest documents with few knowledge items; returns items added."""
    items_added = 0
    docs = _get_docs_needing_work(db)
    if not docs:
        return 0
    try:
        from orivellum.capabilities.knowledge_harvest import harvest
        from orivellum.capabilities.extraction import ExtractionResult, PageSegment
        ai_enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"

        for doc in docs:
            doc_id  = doc["id"]
            work_id = doc.get("work_id")
            title   = doc.get("title") or doc.get("source") or doc_id
            try:
                with db._lock:
                    chunks_row = db._conn.execute(
                        "SELECT text FROM chunks WHERE doc_id=? ORDER BY page LIMIT 30",
                        (doc_id,),
                    ).fetchall()
                full_text = "\n".join(r["text"] for r in chunks_row)
                if not full_text.strip():
                    continue

                doc_info = db.get_document(doc_id) or {}
                pages = [PageSegment(page=i + 1, text=r["text"])
                         for i, r in enumerate(chunks_row)]
                result = ExtractionResult(
                    kind=doc_info.get("kind") or "text",
                    full_text=full_text,
                    word_count=len(full_text.split()),
                    pages=pages,
                )

                before = len(db.list_knowledge(work_id=work_id, limit=500))
                harvest(result, doc_id=doc_id, work_id=work_id,
                        doc_title=title, db=db)

                if ai_enabled:
                    try:
                        from orivellum.capabilities.knowledge_harvest import llm_harvest
                        llm_harvest(result, doc_id=doc_id, work_id=work_id,
                                    doc_title=title, db=db)
                    except Exception as ai_exc:
                        logger.warning("LLM harvest failed for %s: %s", doc_id, ai_exc)

                after = len(db.list_knowledge(work_id=work_id, limit=500))
                added = max(0, after - before)
                items_added += added
                if added:
                    report.append(f"Harvest: {title[:60]} +{added} item(s)")
            except Exception as exc:
                logger.warning("Sparse harvest failed for %s: %s", doc_id, exc)

    except Exception as exc:
        logger.warning("Sparse harvest pass failed: %s", exc)

    return items_added


def _pass_gap_analysis(db: "OrivellumDB", report: list[str]) -> None:
    """Detect research gaps for every active Work and cache results."""
    try:
        from orivellum.capabilities.gaps import detect_gaps
        active_works = db.list_works(status="active")
        high_gaps: list[str] = []
        for work in active_works[:20]:
            try:
                gr = detect_gaps(work["id"], db)
                try:
                    gap_dicts = [
                        {"kind": g.kind, "title": g.title,
                         "description": g.description,
                         "severity": g.severity, "metadata": g.metadata}
                        for g in gr.gaps
                    ]
                    db.cache_work_gaps(work["id"], gap_dicts, gr.coverage_pct)
                except Exception:
                    pass
                for g in gr.gaps:
                    if g.severity == "high":
                        wtitle = work.get("title", work["id"][:12])
                        high_gaps.append(f"{wtitle}: {g.title}")
            except Exception:
                pass

        if high_gaps:
            report.append(f"Research gaps — {len(high_gaps)} critical item(s) across "
                          f"{len(active_works)} work(s):")
            report.extend(f"  ⚠ {line}" for line in high_gaps[:10])
        elif active_works:
            report.append(f"Research coverage: no critical gaps across "
                          f"{len(active_works)} work(s)")
    except Exception as exc:
        logger.warning("Gap analysis pass failed: %s", exc)


def _pass_evidence(db: "OrivellumDB", report: list[str]) -> None:
    """Rescore confidence and detect contradictions for every active Work."""
    try:
        from orivellum.capabilities.evidence import rescore_work, detect_contradictions
        rescored = conflicts = 0
        for work in db.list_works(status="active")[:20]:
            try:
                rescored  += rescore_work(work["id"], db)
                conflicts += detect_contradictions(work["id"], db)
            except Exception as exc:
                logger.warning("Evidence pass failed for %s: %s",
                               work.get("id", "?")[:8], exc)
        if rescored:
            report.append(f"Evidence: re-scored {rescored} knowledge item(s)")
        if conflicts:
            report.append(f"⚠ Contradictions: {conflicts} new conflict(s) — review in Governance")
    except Exception as exc:
        logger.warning("Evidence pass failed: %s", exc)


def _pass_embeddings(db: "OrivellumDB", report: list[str]) -> None:
    """Embed up to 300 unembedded knowledge items."""
    try:
        from orivellum.capabilities.embeddings import backfill_embeddings
        embedded = backfill_embeddings(db, max_items=300)
        if embedded:
            report.append(f"Semantic index: embedded {embedded} new item(s)")
    except Exception as exc:
        logger.warning("Embedding pass failed: %s", exc)


def _pass_work_stats(db: "OrivellumDB", report: list[str]) -> None:
    """Refresh cached stats for every active Work so the UI is always current."""
    try:
        works = db.list_works(status="active")
        refreshed = 0
        for work in works:
            try:
                # get_work_stats rebuilds counts from scratch
                db.get_work_stats(work["id"])
                refreshed += 1
            except Exception:
                pass
        if refreshed:
            logger.debug("Nightshift: refreshed stats for %d works", refreshed)
    except Exception as exc:
        logger.warning("Work stats pass failed: %s", exc)


def _pass_mcos(db: "OrivellumDB", cfg: "OrivellumConfig", report: list[str]) -> None:
    """Seed benchmarks and run MCOS evaluations.

    Gated by the ``mcos_nightly_enabled`` setting (default on).  Retrieval-kind
    suites always run (no LLM needed); LLM-kind suites run only when the model
    endpoint is reachable.  Appends per-benchmark average scores and flags any
    regressions.
    """
    try:
        if db.get_setting("mcos_nightly_enabled", "true").lower() != "true":
            return
        from orivellum.capabilities.mcos import (
            seed_default_benchmarks, run_benchmark, is_ai_reachable,
        )
        seed_default_benchmarks(db)

        with db._lock:
            benches = [dict(r) for r in db._conn.execute(
                "SELECT id, name, kind FROM benchmarks WHERE enabled=1 ORDER BY name"
            ).fetchall()]

        ai_ok = is_ai_reachable(cfg)
        ran_lines: list[str] = []
        regressions: list[str] = []
        for b in benches:
            if b["kind"] != "retrieval" and not ai_ok:
                continue
            try:
                run_id = run_benchmark(db, cfg, b["id"])
                with db._lock:
                    row = db._conn.execute(
                        "SELECT avg_score, meta FROM eval_runs WHERE id=?", (run_id,)
                    ).fetchone()
                avg = row["avg_score"] if row else None
                meta = {}
                try:
                    import json as _json
                    meta = _json.loads(row["meta"]) if row and row["meta"] else {}
                except Exception:
                    meta = {}
                avg_str = f"{avg:.2f}" if avg is not None else "n/a"
                ran_lines.append(f"  {b['name']}: {avg_str}")
                if meta.get("regressed"):
                    regressions.append(f"{b['name']} (Δ{meta.get('delta')})")
            except Exception as exc:
                logger.warning("MCOS benchmark %s failed: %s", b.get("id"), exc)

        if ran_lines:
            report.append(f"MCOS benchmarks — {len(ran_lines)} run"
                          f"{'' if ai_ok else ' (retrieval only — AI unreachable)'}:")
            report.extend(ran_lines)
        if regressions:
            report.append("⚠ MCOS regressions: " + "; ".join(regressions))

        # ── Nightly prompt health (all registered slots) ─────────────────────
        # run_prompt_health(db, cfg) with no slot argument returns a list of
        # per-slot result dicts — one entry per PROMPT_SLOTS entry.
        # Benchmarkable slots (chat.base) run suites and detect regressions.
        # Non-benchmarkable slots (harvest.extract, mcos.judge) get structural
        # validation only and are reported as "skipped (not benchmarkable)".
        # Wrapped independently so a health-pass failure never breaks the rest
        # of the MCOS pass or nightshift; a report line is appended either way.
        try:
            if ai_ok:
                from orivellum.capabilities.mcos import run_prompt_health
                health_results = run_prompt_health(db, cfg)   # returns list[dict]
                for hr in health_results:
                    label = hr.get("prompt_name") or hr.get("slot_label") or hr.get("slot", "?")
                    ver = hr.get("prompt_version")
                    ver_str = f" v{ver}" if ver is not None else ""
                    if hr.get("skipped"):
                        # Non-benchmarkable slot — structural validation result.
                        reason = hr.get("reason", "not benchmarkable")
                        ok_flag = "✓" if hr.get("ok") else "⚠"
                        report.append(
                            f"Prompt health — '{label}'{ver_str}: "
                            f"{ok_flag} {reason} (not benchmarkable)")
                        if not hr.get("ok"):
                            # Broken template/empty prompt — flag prominently.
                            report.append(
                                f"⚠ Prompt content issue in '{hr.get('slot')}': "
                                f"{reason}")
                    elif not hr.get("ok"):
                        report.append(
                            f"Prompt health — '{hr.get('slot_label', hr.get('slot'))}': "
                            f"skipped ({hr.get('reason', 'unknown')})")
                    else:
                        cur = hr.get("current_agg")
                        cur_str = f"{cur:.2f}" if cur is not None else "n/a"
                        report.append(
                            f"Prompt health — '{label}'{ver_str}: "
                            f"{cur_str} ({len(hr['runs'])} suite run(s))")
                        if hr.get("regressed"):
                            report.append(
                                f"⚠ Prompt health regression: "
                                f"'{label}'{ver_str} Δ{hr.get('delta')}")
            else:
                report.append("Prompt health: skipped (AI unreachable)")
        except Exception as exc:
            logger.warning("MCOS prompt-health pass failed: %s", exc)
            report.append(f"⚠ Prompt health: {exc}")
    except Exception as exc:
        logger.warning("MCOS pass failed: %s", exc)


# ── Main runner ───────────────────────────────────────────────────────────────

def run_nightshift(db: "OrivellumDB", cfg: "OrivellumConfig",
                   _preacquired: bool = False) -> None:
    """Execute one complete nightshift pass synchronously.

    Reservation is atomic: unless ``_preacquired`` is True (meaning the caller
    already won the slot via ``try_start()``), this acquires the slot itself and
    silently no-ops if a run is already in flight — so two concurrent callers
    can never overlap.  Either way, the ``finally`` clears the flag exactly once.
    """
    if not _preacquired:
        if not try_start():
            logger.info("Nightshift already running — skipping overlapping run")
            return
    try:
        _run_nightshift_passes(db, cfg)
    finally:
        with _status_lock:
            _status["running"] = False
            _status["finished_at"] = datetime.now(timezone.utc).isoformat()


def _run_nightshift_passes(db: "OrivellumDB", cfg: "OrivellumConfig") -> None:
    date_str = datetime.now().strftime("%Y-%m-%d")
    start_ts = time.time()
    logger.info("Nightshift starting for %s", date_str)
    report: list[str] = []

    # 1 — Database maintenance
    logger.info("Nightshift pass 1/11: database optimisation")
    _pass_db_optimise(db, report)

    # 2 — Zero-byte temp file cleanup
    logger.info("Nightshift pass 2/11: output temp-file cleanup")
    _pass_cleanup_outputs(cfg, report)

    # 3 — Prune old night reports
    logger.info("Nightshift pass 3/11: prune old reports")
    _pass_prune_old_reports(cfg)

    # 4 — Orphaned knowledge / chunks / vectors
    logger.info("Nightshift pass 4/11: orphan cleanup")
    _pass_orphan_cleanup(db, report)

    # 5 — Retry stuck documents
    logger.info("Nightshift pass 5/11: stuck document recovery")
    _pass_stuck_docs(db, cfg, report)

    # 6 — Harvest sparse documents
    logger.info("Nightshift pass 6/11: sparse document harvest")
    items_added = _pass_sparse_harvest(db, report)

    # 7 — Gap analysis
    logger.info("Nightshift pass 7/11: gap analysis")
    _pass_gap_analysis(db, report)

    # 8 — Evidence rescoring + contradiction detection
    logger.info("Nightshift pass 8/11: evidence rescoring")
    _pass_evidence(db, report)

    # 9 — Semantic embedding backfill
    logger.info("Nightshift pass 9/11: embedding backfill")
    _pass_embeddings(db, report)

    # 10 — Work stats refresh
    logger.info("Nightshift pass 10/11: work stats refresh")
    _pass_work_stats(db, report)

    # 11 — MCOS benchmark evaluations
    logger.info("Nightshift pass 11/11: MCOS benchmark evaluations")
    _pass_mcos(db, cfg, report)

    elapsed = time.time() - start_ts
    report.append(f"Completed in {elapsed:.0f}s")

    report_path = _write_report(Path(cfg.data_dir), date_str, report)
    _record_run(db, len(_get_docs_needing_work(db)), items_added, report_path)
    logger.info("Nightshift complete in %.0fs — %d report lines", elapsed, len(report))


def start_nightshift_daemon(db: "OrivellumDB",
                            cfg: "OrivellumConfig") -> threading.Thread:
    """Start the nightshift daemon thread.  Returns the thread (daemon=True)."""
    nightshift_hour = int(db.get_setting("nightshift_hour", "3"))

    def _loop() -> None:
        logger.info("Nightshift daemon ready (fires at %02d:00 local time)",
                    nightshift_hour)
        while True:
            now    = datetime.now()
            target = now.replace(hour=nightshift_hour, minute=0, second=0,
                                 microsecond=0)
            if target <= now:
                # Already past today's window — aim for tomorrow
                from datetime import timedelta
                target += timedelta(days=1)
            wait_secs = (target - now).total_seconds()
            logger.debug("Nightshift sleeping %.0f s until %s",
                         wait_secs, target.isoformat())
            time.sleep(max(wait_secs, 1))

            enabled = db.get_setting("nightshift_enabled", "true").lower() == "true"
            if enabled:
                try:
                    run_nightshift(db, cfg)
                except Exception as exc:
                    logger.error("Nightshift run crashed: %s", exc, exc_info=True)
            else:
                logger.info("Nightshift disabled — skipping run")

    t = threading.Thread(target=_loop, name="orivellum-nightshift", daemon=True)
    t.start()
    return t
