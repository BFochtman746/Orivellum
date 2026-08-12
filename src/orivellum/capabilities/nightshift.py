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
  4b. Automation run pruning — cap terminal scheduled-run history per schedule
  5. Stuck-document retry   — re-queue imported/error/no_text docs (up to 20)
  6. Sparse-doc harvest     — re-harvest docs with < 3 knowledge items
  7. Gap analysis           — detect research gaps for every active Work
  8. Evidence rescoring     — update confidence + detect contradictions
  9. Embedding backfill     — embed up to 300 new knowledge items
 10. Work stats refresh     — recompute cached stats for every active Work
 11. MCOS benchmark evals  — run any due benchmark suites
 12. Outbox drain           — dispatch queued transactional events
 13. Audit-chain verify     — check governance audit chain integrity
 14. Version suggestions    — surface likely version pairs across each Work
 17b. Knowledge semantic dedup — cosine-similarity dedup of knowledge items
      across documents within each Work; gated by auto_dedup_enabled
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orivellum.api.executor import submit_bg

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.nightshift")

_MIN_KNOWLEDGE_ITEMS = 3
_MAX_DOCS_PER_RUN = 20

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
        _status["started_at"] = datetime.now(UTC).isoformat()
        _status["finished_at"] = None
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_docs_needing_work(db: OrivellumDB) -> list[dict]:
    """Return up to ``_MAX_DOCS_PER_RUN`` ready documents with too few knowledge items.

    Read-only query: selects 'ready' documents whose harvested knowledge count
    is below ``_MIN_KNOWLEDGE_ITEMS``, newest first. Used to size the sparse
    harvest and to record how much work remained. Returns a list of dicts (may
    be empty); does not mutate anything.
    """
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


def _get_stuck_docs(db: OrivellumDB, max_docs: int = 20) -> list[dict]:
    """Return up to ``max_docs`` documents stuck in a non-terminal state.

    Read-only query: selects non-zip documents whose readiness is
    imported/error/no_text/reprocessing and that are older than 10 minutes
    (so in-flight imports aren't disturbed), oldest first. Returns a list of
    dicts; does not mutate anything.
    """
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


def _get_stale_reset_docs(db: OrivellumDB, max_docs: int = 5) -> list[dict]:
    """Return documents whose FA-07 reset marker is stale (>10 min), oldest first.

    A ``meta['reset_in_progress']`` marker means a destructive multi-step reset
    (clear warnings → drop knowledge → reset → reprocess) began but a crash may
    have interrupted it before the ``finally`` that clears the marker ran. Such a
    document can be left with old knowledge deleted and nothing rebuilt, and its
    readiness may not reflect a terminal state — so the ordinary stuck-doc query
    could miss it. We detect a stale marker and re-drive reprocessing.

    Read-only: parses each candidate's meta JSON and compares
    ``reset_in_progress.started_at`` to now. Bounded to ``max_docs`` (recovery
    passes cap re-drives per run).
    """
    from orivellum.database.db import _jload  # local import: avoid cycle at import time

    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.work_id, d.title, d.source, d.content_path,
                      d.kind, d.readiness, d.meta
               FROM documents d
               WHERE d.kind != 'zip'
                 AND d.meta LIKE '%reset_in_progress%'
               ORDER BY d.created_at ASC""",
        ).fetchall()

    now = datetime.now(UTC)
    stale: list[dict] = []
    for r in rows:
        d = dict(r)
        meta = _jload(d.get("meta"), {}) or {}
        marker = meta.get("reset_in_progress")
        if not isinstance(marker, dict):
            continue
        started_at = marker.get("started_at")
        try:
            started = datetime.fromisoformat(started_at)
        except Exception:
            # Unparseable timestamp — treat as stale so it doesn't linger forever.
            stale.append(d)
        else:
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if (now - started).total_seconds() > 600:  # >10 minutes
                stale.append(d)
        if len(stale) >= max_docs:
            break
    return stale


def _record_run(
    db: OrivellumDB, docs_processed: int, items_added: int, report_path: str | None
) -> None:
    """Persist a summary row for the completed run and emit an audit entry.

    Side effects: inserts/replaces a ``nightshift_runs`` row (run id, timestamp,
    counts, report path) and writes a ``system.nightshift_run`` audit entry.

    Failure behaviour: logs-and-continues — a DB error while recording the run
    is logged at WARNING and the audit write is best-effort (swallowed on
    error). Never raises; a bookkeeping failure must not abort the run.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
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
        db.audit(
            "system.nightshift_run",
            object_id=run_id,
            object_type="nightshift_run",
            actor="system",
            detail=f"docs={docs_processed} items={items_added}",
        )
    except Exception:
        pass


def _write_report(data_dir: Path, date_str: str, items: list[str]) -> str:
    """Write the markdown Night Report for ``date_str`` and return its path.

    Side effects: creates ``<data_dir>/nightshift/`` if needed and writes
    ``<date_str>.md`` (overwriting any existing file for that date). An empty
    ``items`` list produces a "Nothing to report" body.

    Failure behaviour: raises on filesystem errors (mkdir / write) — the caller
    runs this at the end of the run, outside per-pass try/except.
    """
    report_dir = data_dir / "nightshift"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{date_str}.md"
    header = f"# Night Report — {date_str}\n\nGenerated by Orivellum Nightshift.\n\n"
    body = "\n".join(f"- {line}" for line in items) if items else "_Nothing to report._"
    path.write_text(header + body + "\n", encoding="utf-8")
    return str(path)


# ── Passes ────────────────────────────────────────────────────────────────────

# ── Testable helpers for the DB optimise pass ─────────────────────────────────


def _get_freelist_ratio(conn: Any) -> tuple[float, int, int]:
    """Return ``(ratio, freelist_count, page_count)`` for *conn*.

    Extracted so tests can monkeypatch this function to simulate arbitrary
    fragmentation without touching C-extension sqlite3.Connection attributes.
    """
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_cnt = conn.execute("PRAGMA page_count").fetchone()[0]
    ratio = freelist / max(1, page_cnt)
    return ratio, freelist, page_cnt


def _run_vacuum(conn: Any) -> None:
    """Execute VACUUM on *conn*.

    Extracted so tests can monkeypatch this function to simulate a slow
    VACUUM without touching C-extension sqlite3.Connection attributes.
    """
    conn.execute("VACUUM")


def _pass_db_optimise(db: OrivellumDB, report: list[str]) -> None:
    """WAL checkpoint + ANALYZE + conditional VACUUM + integrity check.

    All work runs inside a single ``with db._lock`` block so the implementation
    is correct regardless of how long VACUUM takes:

    Reads
        ``db.read_conn()`` uses a *separate per-thread connection* with
        ``PRAGMA query_only=ON``.  It never touches ``db._lock``, so read-heavy
        endpoints (settings, prompts, list queries) are completely unaffected
        by any maintenance work done here.

    Writes
        Calls that go through ``governed_write`` or ``db._lock`` queue at the
        Python mutex level until the lock is released.  They never see
        ``SQLITE_BUSY`` because only one SQLite connection (``db._conn``) ever
        writes, and VACUUM runs on that same connection under the same lock.

    Conditional VACUUM
        A full ``VACUUM`` is only triggered when
        ``freelist_count / page_count > 30 %`` (more than a third of the file
        is wasted space).  A routine ``PRAGMA wal_checkpoint(TRUNCATE)`` keeps
        fragmentation low enough that VACUUM is rarely needed.

    Nightshift runs at 03:00 local time when write activity is near-zero, so
    the lock hold during VACUUM (up to ~60 s on a very large DB) has minimal
    user-visible impact.  Reads remain unaffected throughout.
    """
    _FREELIST_VACUUM_RATIO = 0.30

    try:
        with db._lock:
            # ── integrity check ────────────────────────────────────────────────
            result = db._conn.execute("PRAGMA integrity_check(10)").fetchall()
            ok = len(result) == 1 and result[0][0] == "ok"
            if not ok:
                errors = [r[0] for r in result]
                logger.warning("DB integrity issues: %s", errors)
                report.append(f"⚠ DB integrity: {len(errors)} issue(s) — {errors[:3]}")
            else:
                logger.debug("DB integrity: ok")

            # ── WAL checkpoint + ANALYZE ───────────────────────────────────────
            db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db._conn.execute("ANALYZE")
            db._conn.commit()

            # ── freelist ratio (consistent snapshot while lock is held) ────────
            db_path: str | None = None
            try:
                row = db._conn.execute("PRAGMA database_list").fetchone()
                db_path = row[2] if row else None
            except Exception:
                pass

            try:
                ratio, freelist, page_cnt = _get_freelist_ratio(db._conn)
                do_vacuum = ratio > _FREELIST_VACUUM_RATIO
            except Exception:
                ratio, _freelist, _page_cnt = 0.0, 0, 0
                do_vacuum = False

            # ── conditional VACUUM (still under db._lock) ──────────────────────
            # Holding db._lock ensures:
            #   - No other Python writer races with VACUUM on db._conn
            #   - Writers queue at the Python mutex (not SQLITE_BUSY) and
            #     proceed cleanly once the lock is released
            #   - Reads via db.read_conn() are never blocked (separate conn)
            if do_vacuum and db_path:
                size_before = os.path.getsize(db_path) if os.path.exists(db_path) else None
                db._conn.commit()  # close any implicit read transaction first
                _run_vacuum(db._conn)

                if size_before is not None:
                    size_after = os.path.getsize(db_path)
                    saved_mb = max(0, (size_before - size_after) / 1_048_576)
                    msg = f"DB optimised — VACUUM saved {saved_mb:.1f} MB (freelist {ratio:.0%})"
                else:
                    msg = f"DB optimised — VACUUM + checkpoint + ANALYZE (freelist {ratio:.0%})"
            else:
                reason = (
                    f"< {_FREELIST_VACUUM_RATIO:.0%}, VACUUM skipped"
                    if not do_vacuum
                    else "VACUUM skipped — no DB path"
                )
                msg = f"DB optimised — checkpoint + ANALYZE (freelist {ratio:.0%}, {reason})"

        report.append(msg)
        logger.info(msg)

    except Exception as exc:
        logger.warning("DB optimise pass failed: %s", exc)
        report.append(f"⚠ DB optimise: {exc}")


def _pass_cleanup_outputs(cfg: OrivellumConfig, report: list[str]) -> None:
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


def _pass_prune_old_reports(cfg: OrivellumConfig, keep: int = 30) -> None:
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


def _pass_orphan_cleanup(db: OrivellumDB, report: list[str]) -> None:
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
            # Track each type separately so cache bumps are precise.
            vk_deleted = 0  # orphaned knowledge-type vectors
            vc_deleted = 0  # orphaned chunk-type vectors
            try:
                vk_deleted = db._conn.execute(
                    """DELETE FROM vectors
                       WHERE object_type = 'knowledge'
                         AND object_id NOT IN (SELECT id FROM knowledge)"""
                ).rowcount
                vc_deleted = db._conn.execute(
                    """DELETE FROM vectors
                       WHERE object_type = 'chunk'
                         AND object_id NOT IN (SELECT id FROM chunks)"""
                ).rowcount
            except Exception:
                pass
            v_deleted = vk_deleted + vc_deleted
            db._conn.commit()

        removed = k_deleted + c_deleted + v_deleted
        if removed:
            report.append(
                f"Orphan cleanup: removed {k_deleted} knowledge, "
                f"{c_deleted} chunks, {v_deleted} vectors"
            )
            logger.info(
                "Nightshift orphan cleanup: k=%d c=%d v=%d", k_deleted, c_deleted, v_deleted
            )
            # Bump the in-process vector cache for every type that was touched
            # so semantic_search does not return deleted entries on the next
            # call. The cache rebuilds lazily on the first subsequent query.
            # Note: vectors can become orphaned even when k_deleted/c_deleted
            # are zero (e.g. chunks are cascade-deleted via FK when their
            # parent document is removed, leaving vectors with no referent).
            try:
                from orivellum.capabilities.embeddings import bump_vector_cache_version

                if k_deleted or vk_deleted:
                    bump_vector_cache_version(db._path, "knowledge")
                if c_deleted or vc_deleted:
                    bump_vector_cache_version(db._path, "chunk")
            except Exception:
                pass
    except Exception as exc:
        logger.warning("Orphan cleanup pass failed: %s", exc)


def _pass_prune_schedule_runs(db: OrivellumDB, report: list[str]) -> None:
    """Prune old terminal scheduled runs so automation history never grows forever.

    A nightly automation adds ~365 operations rows a year per schedule. This
    delegates to ``store.prune_finished_schedule_runs`` (keep the newest 50
    per schedule; drop runs older than 90 days beyond the newest 5; never
    touch active runs, manual operations, or un-alerted failures) and reports
    the count. Idempotent — a second run finds nothing.
    """
    try:
        from orivellum.capabilities.operations.store import prune_finished_schedule_runs

        deleted = prune_finished_schedule_runs(db)
        if deleted:
            report.append(f"Pruned {deleted} old automation run(s) from history")
            logger.info("Nightshift: pruned %d old scheduled operation rows", deleted)
    except Exception as exc:
        logger.warning("Schedule-run pruning pass failed: %s", exc)
        report.append(f"⚠ Automation history pruning: {exc}")


def _pass_stuck_docs(db: OrivellumDB, cfg: OrivellumConfig, report: list[str]) -> None:
    """Re-queue all stuck documents (imported/error/no_text) that have a file.

    When a vision model is configured, no_text PDF/image documents are
    excluded here because pass 5b (_pass_no_text_reextract) owns those rows.
    This prevents both passes from spawning workers for the same document
    concurrently and producing duplicate chunks/knowledge items.
    """
    try:
        from orivellum.capabilities.pipeline import process_document as _proc

        lib_root = Path(cfg.data_dir) / "library"
        stuck = _get_stuck_docs(db, max_docs=20)

        # Exclude no_text PDF/image docs when VLM is configured — pass 5b owns them.
        _vlm = db.get_setting("vision_model", "").strip() or cfg.serving.vision_model
        if _vlm:
            stuck = [
                d
                for d in stuck
                if not (d.get("readiness") == "no_text" and d.get("kind") in ("pdf", "image"))
            ]

        # FA-07 — fold in documents with a stale reset marker (>10 min). A crash
        # mid-reset may leave a document that the readiness-based query above
        # misses; re-drive it here (max 5/run) and clear its marker so it is not
        # picked up again on the next pass unless it re-fails.
        try:
            stale_reset = _get_stale_reset_docs(db, max_docs=5)
        except Exception as _sr_exc:
            logger.warning("Stale-reset detection failed: %s", _sr_exc)
            stale_reset = []
        _seen_ids = {d["id"] for d in stuck}
        for d in stale_reset:
            if d["id"] not in _seen_ids:
                stuck.append(d)
                _seen_ids.add(d["id"])
                logger.info("Nightshift: re-driving doc %s with stale reset marker", d["id"])
            try:
                db.clear_reset_marker(d["id"])
            except Exception:
                pass

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
                    db.update_document_extracted(
                        it["id"], "", 0, readiness="imported", error_message=None
                    )
                    _proc(
                        doc_id=it["id"],
                        file_path=it["_file_path"],
                        kind=it.get("kind") or "text",
                        work_id=it.get("work_id"),
                        title=it.get("title") or it["id"],
                        db=db,
                    )
                except Exception as rec_exc:
                    logger.warning("Recovery failed for %s: %s", it["id"], rec_exc)

        submit_bg(_worker, queue, kind="nightshift", label="recovery")
        report.append(f"Recovery: re-queued {len(queue)} stuck document(s) (sequential)")
        logger.info("Nightshift: queued %d stuck docs for sequential recovery", len(queue))
    except Exception as exc:
        logger.warning("Stuck-doc pass failed: %s", exc)


def _pass_no_text_reextract(db: OrivellumDB, cfg: OrivellumConfig, report: list[str]) -> None:
    """Re-extract PDF/image docs stuck in no_text when a vision model is now configured.

    Tesseract sometimes returns nothing for scanned PDFs (rotated pages, unusual
    fonts, handwriting, etc.).  When the user later enables a VLM (vision_model),
    those documents can be recovered automatically on the next nightshift run.
    """
    try:
        vision_model = db.get_setting("vision_model", "").strip() or cfg.serving.vision_model
        if not vision_model:
            # No VLM configured — nothing to do; Tesseract already ran.
            return

        with db._lock:
            rows = db._conn.execute(
                """SELECT d.id, d.kind, d.work_id, d.title,
                          d.content_path, d.source
                   FROM documents d
                   WHERE d.readiness = 'no_text'
                     AND d.kind IN ('pdf', 'image')
                   ORDER BY d.created_at DESC
                   LIMIT 30""",
            ).fetchall()
        docs = [dict(r) for r in rows]

        if not docs:
            return

        lib_root = Path(cfg.data_dir) / "library"
        queue: list[dict] = []
        for doc in docs:
            content_path = doc.get("content_path")
            file_path: Path | None = None
            if content_path:
                file_path = lib_root / content_path
            elif doc.get("source"):
                file_path = Path(doc["source"])
            if not file_path or not file_path.exists():
                continue
            queue.append({**doc, "_file_path": str(file_path)})

        if not queue:
            return

        from orivellum.capabilities.pipeline import process_document as _proc

        def _worker(items: list[dict]) -> None:
            for it in items:
                try:
                    db.update_document_extracted(
                        it["id"], "", 0, readiness="imported", error_message=None
                    )
                    _proc(
                        doc_id=it["id"],
                        file_path=it["_file_path"],
                        kind=it.get("kind") or "pdf",
                        work_id=it.get("work_id"),
                        title=it.get("title") or it["id"],
                        db=db,
                    )
                except Exception as exc:
                    logger.warning("VLM re-extract failed for %s: %s", it["id"], exc)

        submit_bg(_worker, queue, kind="nightshift", label="vlm-reextract")
        report.append(
            f"VLM re-extract: queued {len(queue)} no_text PDF/image doc(s) "
            f"(vision_model={vision_model})"
        )
        logger.info(
            "Nightshift VLM re-extract: queued %d no_text docs via %s", len(queue), vision_model
        )
    except Exception as exc:
        logger.warning("VLM re-extract pass failed (non-fatal): %s", exc)


def _pass_audio_reextract(db: OrivellumDB, cfg: OrivellumConfig, report: list[str]) -> None:
    """Re-transcribe audio docs that previously got only a metadata-only placeholder.

    When faster-whisper is installed, documents that landed in the library as
    "Audio file: …  Note: Transcription unavailable…" can be silently re-transcribed
    overnight.  We identify them by the exact marker string injected by
    _metadata_only() in extraction.py — "To enable transcription" — which appears
    in extracted_text only when both the AI server AND faster-whisper failed.
    This ensures legitimate short recordings (voice notes, brief clips) are never
    accidentally requeued.

    Skips when faster-whisper is not installed (no-op, no import needed to check).
    """
    try:
        import importlib.util as _ilu

        if _ilu.find_spec("faster_whisper") is None:
            # Package not installed — nothing to do
            return

        with db._lock:
            rows = db._conn.execute(
                """SELECT d.id, d.kind, d.work_id, d.title,
                          d.content_path, d.source
                   FROM documents d
                   WHERE d.kind = 'audio'
                     AND d.readiness = 'ready'
                     AND d.extracted_text LIKE '%To enable transcription%'
                   ORDER BY d.created_at DESC
                   LIMIT 10""",
            ).fetchall()
        docs = [dict(r) for r in rows]

        if not docs:
            return

        lib_root = Path(cfg.data_dir) / "library"
        queue: list[dict] = []
        for doc in docs:
            content_path = doc.get("content_path")
            file_path: Path | None = None
            if content_path:
                file_path = lib_root / content_path
            elif doc.get("source"):
                file_path = Path(doc["source"])
            if not file_path or not file_path.exists():
                continue
            queue.append({**doc, "_file_path": str(file_path)})

        if not queue:
            return

        from orivellum.capabilities.pipeline import process_document as _proc

        def _worker(items: list[dict]) -> None:
            for it in items:
                try:
                    db.update_document_extracted(
                        it["id"], "", 0, readiness="imported", error_message=None
                    )
                    _proc(
                        doc_id=it["id"],
                        file_path=it["_file_path"],
                        kind="audio",
                        work_id=it.get("work_id"),
                        title=it.get("title") or it["id"],
                        db=db,
                    )
                except Exception as exc:
                    logger.warning("Audio re-transcription failed for %s: %s", it["id"], exc)

        submit_bg(_worker, queue, kind="nightshift", label="audio-reextract")
        from orivellum.capabilities.extraction import _resolve_asr_local_model

        asr_size = _resolve_asr_local_model(
            db, getattr(cfg.serving, "asr_local_model", "large-v3-turbo")
        )
        report.append(
            f"Audio re-transcription: queued {len(queue)} metadata-only audio doc(s) "
            f"(faster-whisper {asr_size})"
        )
        logger.info(
            "Nightshift audio re-transcription: queued %d docs via faster-whisper %s",
            len(queue),
            asr_size,
        )
    except Exception as exc:
        logger.warning("Audio re-transcription pass failed (non-fatal): %s", exc)


def _pass_sparse_harvest(db: OrivellumDB, report: list[str]) -> int:
    """Re-harvest documents with few knowledge items; returns items added."""
    items_added = 0
    docs = _get_docs_needing_work(db)
    if not docs:
        return 0
    try:
        from orivellum.capabilities.extraction import ExtractionResult, PageSegment
        from orivellum.capabilities.knowledge_harvest import harvest

        ai_enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"

        for doc in docs:
            doc_id = doc["id"]
            work_id = doc.get("work_id")
            title = doc.get("title") or doc.get("source") or doc_id
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
                pages = [PageSegment(page=i + 1, text=r["text"]) for i, r in enumerate(chunks_row)]
                result = ExtractionResult(
                    kind=doc_info.get("kind") or "text",
                    full_text=full_text,
                    word_count=len(full_text.split()),
                    pages=pages,
                )

                before = len(db.list_knowledge(work_id=work_id, limit=500))
                harvest(result, doc_id=doc_id, work_id=work_id, doc_title=title, db=db)

                if ai_enabled:
                    try:
                        from orivellum.capabilities.knowledge_harvest import llm_harvest

                        llm_harvest(
                            result,
                            doc_id=doc_id,
                            work_id=work_id,
                            doc_title=title,
                            db=db,
                            kind=doc_info.get("kind"),
                        )
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


def _pass_gap_analysis(db: OrivellumDB, report: list[str]) -> None:
    """Detect research gaps for every active Work and cache results."""
    try:
        from orivellum.capabilities.corpus_hygiene import detect_hygiene

        active_works = db.list_works(status="active")
        high_gaps: list[str] = []
        for work in active_works[:20]:
            try:
                gr = detect_hygiene(work["id"], db)
                try:
                    gap_dicts = [
                        {
                            "kind": g.kind,
                            "title": g.title,
                            "description": g.description,
                            "severity": g.severity,
                            "metadata": g.metadata,
                            "finding_key": g.finding_key,
                        }
                        for g in gr.findings
                    ]
                    db.cache_work_gaps(
                        work["id"],
                        gap_dicts,
                        gr.coverage_pct,
                        suggested_queries=gr.suggested_queries,
                    )
                except Exception:
                    pass
                for g in gr.findings:
                    if g.severity == "high":
                        wtitle = work.get("title", work["id"][:12])
                        high_gaps.append(f"{wtitle}: {g.title}")
            except Exception:
                pass

        if high_gaps:
            report.append(
                f"Research gaps — {len(high_gaps)} critical item(s) across "
                f"{len(active_works)} work(s):"
            )
            report.extend(f"  ⚠ {line}" for line in high_gaps[:10])
        elif active_works:
            report.append(f"Research coverage: no critical gaps across {len(active_works)} work(s)")
    except Exception as exc:
        logger.warning("Gap analysis pass failed: %s", exc)


def _pass_evidence(db: OrivellumDB, report: list[str]) -> None:
    """Rescore confidence and detect contradictions for stale Works.

    Only processes Works whose last rescore is >24 h old (tracked via the
    ``evidence_rescore:{work_id}`` settings key).  Capped at 5 Works per run
    so the pass stays bounded regardless of library size.
    """
    from datetime import datetime, timedelta

    _STALE_HOURS = 24
    _MAX_WORKS = 5

    try:
        from orivellum.capabilities.evidence import detect_contradictions, rescore_work

        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=_STALE_HOURS)

        rescored = conflicts = skipped = 0
        processed = 0
        for work in db.list_works(status="active"):
            if processed >= _MAX_WORKS:
                break
            wid = work.get("id", "")
            # Skip if rescored recently
            last_str = db.get_setting(f"evidence_rescore:{wid}", "")
            if last_str:
                try:
                    last_dt = datetime.fromisoformat(last_str)
                    if last_dt > cutoff:
                        skipped += 1
                        continue
                except ValueError:
                    pass  # malformed timestamp — treat as stale
            try:
                rescored += rescore_work(wid, db)
                conflicts += detect_contradictions(wid, db)
                db.set_setting(f"evidence_rescore:{wid}", now.isoformat())
                processed += 1
            except Exception as exc:
                logger.warning("Evidence pass failed for %s: %s", wid[:8], exc)

        if rescored:
            report.append(
                f"Evidence: re-scored {rescored} knowledge item(s) across {processed} work(s)"
            )
        if conflicts:
            report.append(f"⚠ Contradictions: {conflicts} new conflict(s) — review in Governance")
        if skipped:
            logger.debug("Evidence pass: skipped %d recently-rescored work(s)", skipped)
    except Exception as exc:
        logger.warning("Evidence pass failed: %s", exc)


def _pass_context_prefix_backfill(db: OrivellumDB, report: list[str]) -> None:
    """Generate AI context prefixes for up to 100 un-prefixed chunks.

    Implements the Anthropic Contextual Retrieval technique: each chunk
    receives a short 1-2 sentence description of the document it came from
    and the broader topic it covers.  The prefix is stored in
    ``chunks.context_prefix`` and prepended to the raw chunk text when
    computing embeddings at query time.

    Gated by ``ai_extraction_enabled``.  After generating prefixes,
    the updated chunks are re-embedded so vectors reflect the enriched text.
    Only runs when new chunks without prefixes exist — a no-op once the
    library is fully enriched.
    """
    try:
        if db.get_setting("ai_extraction_enabled", "false") != "true":
            return

        from orivellum.capabilities.chunking import (
            CTX_BACKFILL_MAX,
            generate_context_prefixes_for_doc,
        )
        from orivellum.capabilities.embeddings import embed_chunks_for_doc

        # Find up to CTX_BACKFILL_MAX distinct documents that have un-prefixed chunks,
        # ordered by most recently created so fresh imports are enriched first.
        with db._lock:
            doc_rows = db._conn.execute(
                """SELECT DISTINCT c.doc_id, d.title, d.extracted_text
                   FROM chunks c
                   JOIN documents d ON d.id = c.doc_id
                   WHERE c.context_prefix IS NULL AND length(c.text) > 40
                     AND COALESCE(d.quarantined, 0) = 0
                   ORDER BY d.created_at DESC
                   LIMIT 20""",
            ).fetchall()

        if not doc_rows:
            return

        total_generated = 0
        total_reembedded = 0
        remaining = CTX_BACKFILL_MAX

        for row in doc_rows:
            if remaining <= 0:
                break
            doc_id = row["doc_id"]
            doc_title = row["title"] or ""
            # Use the stored extracted text as the document excerpt for context.
            doc_excerpt = (row["extracted_text"] or "")[:2000]
            try:
                n = generate_context_prefixes_for_doc(
                    doc_id,
                    db,
                    doc_title=doc_title,
                    doc_text_excerpt=doc_excerpt,
                )
                if n:
                    total_generated += n
                    remaining -= n
                    # Re-embed the chunks that now have a prefix so vectors are
                    # consistent with what will be shown at retrieval time.
                    # Delete existing vectors first so embed_chunks_for_doc picks
                    # them up (it only embeds un-vectorised chunks).
                    with db._lock:
                        prefixed_ids = db._conn.execute(
                            """SELECT id FROM chunks
                               WHERE doc_id=? AND context_prefix IS NOT NULL""",
                            (doc_id,),
                        ).fetchall()
                    if prefixed_ids:
                        ids = [r["id"] for r in prefixed_ids]
                        placeholders = ",".join("?" * len(ids))
                        with db._lock:
                            db._conn.execute(
                                f"DELETE FROM vectors WHERE object_id IN ({placeholders})"
                                f" AND object_type='chunk'",
                                ids,
                            )
                            db._conn.commit()
                        total_reembedded += embed_chunks_for_doc(doc_id, db)
            except Exception as exc:
                logger.debug("Context-prefix backfill failed for doc %s: %s", doc_id[:8], exc)

        if total_generated:
            report.append(
                f"Context prefixes: generated {total_generated} prefix(es), "
                f"re-embedded {total_reembedded} chunk(s)"
            )
    except Exception as exc:
        logger.warning("Context-prefix backfill pass failed: %s", exc)


def _pass_embeddings(db: OrivellumDB, report: list[str]) -> None:
    """Embed up to 300 unembedded knowledge items."""
    try:
        from orivellum.capabilities.embeddings import backfill_embeddings

        embedded = backfill_embeddings(db, max_items=300)
        if embedded:
            report.append(f"Semantic index: embedded {embedded} new item(s)")
    except Exception as exc:
        logger.warning("Embedding pass failed: %s", exc)


def _pass_work_stats(db: OrivellumDB, report: list[str]) -> None:
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


def _pass_mcos(db: OrivellumDB, cfg: OrivellumConfig, report: list[str]) -> None:
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
            is_ai_reachable,
            run_benchmark,
            seed_default_benchmarks,
        )

        seed_default_benchmarks(db)

        with db._lock:
            benches = [
                dict(r)
                for r in db._conn.execute(
                    "SELECT id, name, kind FROM benchmarks WHERE enabled=1 ORDER BY name"
                ).fetchall()
            ]

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
            report.append(
                f"MCOS benchmarks — {len(ran_lines)} run"
                f"{'' if ai_ok else ' (retrieval only — AI unreachable)'}:"
            )
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

                health_results = run_prompt_health(db, cfg)  # returns list[dict]
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
                            f"{ok_flag} {reason} (not benchmarkable)"
                        )
                        if not hr.get("ok"):
                            # Broken template/empty prompt — flag prominently.
                            report.append(f"⚠ Prompt content issue in '{hr.get('slot')}': {reason}")
                    elif not hr.get("ok"):
                        report.append(
                            f"Prompt health — '{hr.get('slot_label', hr.get('slot'))}': "
                            f"skipped ({hr.get('reason', 'unknown')})"
                        )
                    else:
                        cur = hr.get("current_agg")
                        cur_str = f"{cur:.2f}" if cur is not None else "n/a"
                        report.append(
                            f"Prompt health — '{label}'{ver_str}: "
                            f"{cur_str} ({len(hr['runs'])} suite run(s))"
                        )
                        if hr.get("regressed"):
                            report.append(
                                f"⚠ Prompt health regression: '{label}'{ver_str} Δ{hr.get('delta')}"
                            )
            else:
                report.append("Prompt health: skipped (AI unreachable)")
        except Exception as exc:
            logger.warning("MCOS prompt-health pass failed: %s", exc)
            report.append(f"⚠ Prompt health: {exc}")
    except Exception as exc:
        logger.warning("MCOS pass failed: %s", exc)


# ── Main runner ───────────────────────────────────────────────────────────────


def run_nightshift(db: OrivellumDB, cfg: OrivellumConfig, _preacquired: bool = False) -> None:
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
            _status["finished_at"] = datetime.now(UTC).isoformat()


def _pass_dispatch_outbox(db: OrivellumDB, report: list[str]) -> None:
    """Drain the transactional outbox by marking all pending events dispatched.

    The outbox (schema v56) records every governed write atomically.  Until a
    real consumer (SSE push, webhook, search-index sync) is wired, nightshift
    acts as the null dispatcher: it logs what it saw and marks events processed
    so the table does not grow unboundedly.

    When a real consumer exists, replace the mark-dispatched call here with
    the actual delivery logic and only mark dispatched on confirmed delivery.
    """
    try:
        pending = db.list_outbox(pending_only=True, limit=500)
        if not pending:
            report.append("outbox: 0 pending events")
            return
        # Group by event_type for the report line.
        from collections import Counter

        counts: Counter = Counter(e["event_type"] for e in pending)
        for event in pending:
            db.dispatch_outbox_event(event["id"])
        summary = ", ".join(f"{t}×{n}" for t, n in counts.most_common(10))
        report.append(f"outbox: dispatched {len(pending)} events ({summary})")
        logger.info("Nightshift outbox: drained %d events", len(pending))
    except Exception as exc:
        report.append(f"outbox: drain failed — {exc}")
        logger.warning("Nightshift outbox drain failed: %s", exc)


def _pass_verify_audit_chain(db: OrivellumDB, report: list[str]) -> None:
    """Verify the hash-chained audit ledger is intact.

    Calls ``db.verify_audit_chain()`` and appends a ✓ or ✗ line to the
    nightshift report so any tampering surfaces in the nightly report before
    a human checks the governance page.
    """
    try:
        ok, reason = db.verify_audit_chain()
        if ok:
            report.append("audit-chain: ✓ intact")
        else:
            report.append(f"audit-chain: ✗ BROKEN — {reason}")
            logger.error("Nightshift audit-chain verification FAILED: %s", reason)
    except Exception as exc:
        report.append(f"audit-chain: check failed — {exc}")
        logger.warning("Nightshift audit-chain check error: %s", exc)


def _pass_reseed_concepts(db: OrivellumDB, cfg: OrivellumConfig, report: list[str]) -> None:
    """Incrementally re-seed learning concepts for Works with fresh knowledge.

    A Work qualifies when its newest question-safe knowledge item is newer
    than its newest concept (or it has knowledge but no concepts yet).
    Bounded to a few Works per night; seed_concepts is idempotent and ends
    with a prerequisite cycle check, so repeated runs converge.
    """
    _MAX_WORKS = 5
    try:
        with db._lock:
            rows = db._conn.execute(
                """SELECT k.work_id
                   FROM knowledge k
                   WHERE k.work_id IS NOT NULL
                     AND k.review_status IN ('auto','ai_auto','approved')
                   GROUP BY k.work_id
                   HAVING MAX(k.created_at) > COALESCE(
                       (SELECT MAX(c.created_at) FROM work_concepts c
                        WHERE c.work_id = k.work_id), '')
                   ORDER BY MAX(k.created_at) DESC
                   LIMIT ?""",
                (_MAX_WORKS,),
            ).fetchall()
        work_ids = [r["work_id"] for r in rows]
        if not work_ids:
            return
        from orivellum.capabilities.learning import seed_concepts

        seeded = 0
        for wid in work_ids:
            try:
                seed_concepts(db, wid, cfg.serving.base_url, cfg.serving.workhorse_model)
                seeded += 1
            except Exception as exc:
                logger.warning("Concept re-seed failed for work %s: %s", wid, exc)
        if seeded:
            report.append(f"Concept re-seed: {seeded} Work(s) refreshed")
    except Exception as exc:
        logger.warning("Concept re-seed pass failed (non-fatal): %s", exc)
        report.append(f"Concept re-seed: failed — {exc}")


def _pass_version_suggestions(db: OrivellumDB, report: list[str]) -> None:
    """Cross-check document pairs in every Work for similar filename stems and
    create version-relationship suggestions for any new matches found.

    Mirrors the same logic run at upload time (_maybe_suggest_version in library.py)
    so that documents imported before the feature was introduced also get covered.
    """
    import datetime as _dt
    import json as _json
    import re as _re
    import uuid as _uuid_mod
    from difflib import SequenceMatcher
    from pathlib import Path as _Path

    _VER = _re.compile(
        r"[_\s\-]*(v\d+[\d.]*|draft\d*|rev\d*|copy\d*|\d+|final|interim|updated?)$",
        _re.I,
    )

    def _similar(a: str, b: str) -> bool:
        if not a or not b:
            return False
        a = a.lower().strip()
        b = b.lower().strip()
        if a == b:
            return True
        a_base = _VER.sub("", a).strip()
        b_base = _VER.sub("", b).strip()
        if a_base and b_base and a_base == b_base:
            return True
        return SequenceMatcher(None, a, b).ratio() >= 0.75

    try:
        works = db.list_works()
    except Exception as exc:
        logger.debug("Version suggestions pass: could not list works: %s", exc)
        return

    created = 0
    for work in works:
        wid = work["id"]
        try:
            docs = db.list_documents(work_id=wid, limit=500)
        except Exception:
            continue
        if len(docs) < 2:
            continue
        for i, doc_a in enumerate(docs):
            stem_a = _Path(doc_a.get("title") or "").stem
            if not stem_a:
                continue
            for doc_b in docs[i + 1 :]:
                stem_b = _Path(doc_b.get("title") or "").stem
                if not stem_b:
                    continue
                if not _similar(stem_a, stem_b):
                    continue
                with db._lock:
                    already = db._conn.execute(
                        """SELECT id FROM suggestions
                           WHERE work_id=? AND kind='version_relationship'
                           AND (
                               (json_extract(meta,'$.doc_a_id')=? AND json_extract(meta,'$.doc_b_id')=?)
                            OR (json_extract(meta,'$.doc_a_id')=? AND json_extract(meta,'$.doc_b_id')=?)
                           )""",
                        (wid, doc_a["id"], doc_b["id"], doc_b["id"], doc_a["id"]),
                    ).fetchone()
                    if already:
                        continue
                    now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                    meta_payload = _json.dumps(
                        {
                            "doc_a_id": doc_a["id"],
                            "doc_b_id": doc_b["id"],
                            "doc_a_title": doc_a.get("title", ""),
                            "doc_b_title": doc_b.get("title", ""),
                            "similarity_basis": "filename_stem",
                        }
                    )
                    text_label = (
                        f'"{doc_a.get("title") or stem_a}" and '
                        f'"{doc_b.get("title") or stem_b}" '
                        f"may be versions of the same document"
                    )
                    db._conn.execute(
                        """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            str(_uuid_mod.uuid4()),
                            wid,
                            "version_relationship",
                            text_label,
                            meta_payload,
                            now_iso,
                        ),
                    )
                    db._conn.commit()
                    created += 1

    if created:
        report.append(f"Version suggestions: created {created} new pair suggestion(s)")
    else:
        report.append("Version suggestions: no new version pairs found")


def _pass_zip_provenance_backfill(db: OrivellumDB, report: list[str]) -> None:
    """Back-fill object_provenance rows for ZIP child documents that were created
    before this feature was introduced (or on runs where record_provenance failed).

    A document is a ZIP child when its ``meta`` JSON contains a ``"from_zip"``
    key (written by ``_explode_zip_into_documents``).  For each such document
    that has no existing provenance row we insert one with source='zip_extract'
    and origin_id=<parent_zip_doc_id>.

    Capped at 500 documents per run to stay bounded; a second run catches the
    rest if the library is very large.
    """
    try:
        from orivellum.capabilities.persist import record_provenance as _rp

        # Find ZIP child docs missing provenance rows.  Uses json_extract so only
        # documents with a well-formed from_zip value are candidates.
        with db._lock:
            candidates = db._conn.execute(
                """SELECT d.id, d.work_id, json_extract(d.meta, '$.from_zip') AS parent_id
                   FROM documents d
                   WHERE json_extract(d.meta, '$.from_zip') IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM object_provenance op
                         WHERE op.object_id = d.id
                           AND op.source = 'zip_extract'
                     )
                   LIMIT 500""",
            ).fetchall()

        if not candidates:
            logger.debug("ZIP provenance backfill: nothing to do")
            return

        backfilled = 0
        for row in candidates:
            doc_id = row["id"]
            parent_id = row["parent_id"]
            work_id = row["work_id"]
            try:
                _rp(doc_id, "zip_extract", db, origin_id=parent_id, work_id=work_id)
                backfilled += 1
            except Exception as exc:
                logger.debug("ZIP provenance backfill: failed for %s: %s", doc_id, exc)

        if backfilled:
            report.append(f"ZIP provenance backfill: {backfilled} document(s) recorded")
            logger.info("Nightshift ZIP provenance backfill: %d rows written", backfilled)
    except Exception as exc:
        logger.warning("ZIP provenance backfill pass failed: %s", exc)
        report.append(f"ZIP provenance backfill: failed — {exc}")


def _pass_clustering(db: OrivellumDB, report: list[str]) -> None:
    """Rebuild topic clusters over all vectorised documents.

    Skips gracefully if there are no vectors (embeddings not yet generated or
    the embedding endpoint is unavailable).
    """
    try:
        from orivellum.capabilities.cluster import run_clustering

        result = run_clustering(db)
        status = result.get("status", "?")
        if status == "skipped":
            report.append(f"Clustering: skipped — {result.get('reason', '')}")
        else:
            report.append(
                f"Clustering: {result['topics']} topics from {result['docs_clustered']} docs, "
                f"{result['doc_links']} doc-links (k={result.get('k', '?')})"
            )
    except Exception as exc:
        report.append(f"Clustering: failed — {exc}")
        logger.warning("Nightshift clustering pass failed: %s", exc, exc_info=True)


def _pass_knowledge_semantic_dedup(db: OrivellumDB, report: list[str]) -> None:
    """Find near-duplicate knowledge items across different source documents within
    each Work by comparing their stored embedding vectors (no new LLM calls).

    Thresholds
    ----------
    cosine ≥ 0.88  — auto-retire the older item via
                     ``review_status = 'superseded_duplicate'`` so it is
                     excluded from chat context injection and semantic search.
    0.75 ≤ cosine < 0.88 — insert a ``semantic_duplicate`` governance suggestion
                     so a human can decide which item to keep.

    Gates
    -----
    - ``auto_dedup_enabled`` setting must be ``"true"`` (same gate as the
      MinHash file-dedup pass).
    - Skips silently when no knowledge vectors exist yet (embeddings offline or
      backfill not yet run).

    Complexity
    ----------
    Capped at _MAX_PER_WORK items per Work (400) so the O(n²) comparison
    stays bounded.  Works with fewer than 2 embedded items are skipped.
    """
    import datetime as _dt
    import json as _json
    import uuid as _uuid_mod

    _HIGH = 0.88  # auto-retire threshold
    _LOW = 0.75  # suggest-for-review threshold
    _MAX_PER_WORK = 400  # keep O(n²) loop bounded

    # Gate 1: setting
    if db.get_setting("auto_dedup_enabled", "false").lower() != "true":
        logger.debug("Nightshift: knowledge semantic dedup skipped (auto_dedup_enabled=false)")
        return

    # Gate 2: check that at least some knowledge vectors exist — proxy for
    # "the embeddings service has been running at some point".  Avoids
    # importing private circuit-breaker state from embeddings.py.
    try:
        with db._lock:
            vec_count: int = db._conn.execute(
                "SELECT COUNT(*) FROM vectors WHERE object_type='knowledge'"
            ).fetchone()[0]
        if vec_count == 0:
            report.append("Knowledge semantic dedup: skipped (no knowledge vectors yet)")
            return
    except Exception as exc:
        logger.debug("Knowledge semantic dedup: vector count check failed: %s", exc)
        return

    from orivellum.capabilities.embeddings import bump_vector_cache_version, unpack_vector
    from orivellum.capabilities.embeddings import cosine as _cosine

    try:
        works = db.list_works()
    except Exception as exc:
        logger.warning("Knowledge semantic dedup: could not list works: %s", exc)
        return

    total_superseded = 0
    total_suggested = 0

    for work in works:
        wid = work["id"]
        try:
            # Load knowledge items that have vectors AND come from a named
            # source document — items without source_doc_id cannot be
            # cross-document-compared so we skip them.
            with db._lock:
                rows = db._conn.execute(
                    """SELECT k.id, k.created_at, k.source_doc_id,
                              v.embedding, v.dim
                       FROM knowledge k
                       JOIN vectors v
                         ON v.object_id = k.id AND v.object_type = 'knowledge'
                       WHERE k.work_id = ?
                         AND k.source_doc_id IS NOT NULL
                         AND k.review_status NOT IN ('rejected', 'superseded_duplicate')
                       ORDER BY k.created_at ASC
                       LIMIT ?""",
                    (wid, _MAX_PER_WORK),
                ).fetchall()

            if len(rows) < 2:
                continue

            # Pre-load vectors — items that fail to unpack are skipped silently.
            items: list[dict] = []
            for r in rows:
                try:
                    vec = unpack_vector(bytes(r["embedding"]), r["dim"])
                    items.append(
                        {
                            "id": r["id"],
                            "created_at": r["created_at"],
                            "source_doc_id": r["source_doc_id"],
                            "vec": vec,
                        }
                    )
                except Exception:
                    continue

            if len(items) < 2:
                continue

            superseded_ids: set[str] = set()
            now_iso = _dt.datetime.now(_dt.UTC).isoformat()

            for i in range(len(items)):
                if items[i]["id"] in superseded_ids:
                    continue
                for j in range(i + 1, len(items)):
                    if items[j]["id"] in superseded_ids:
                        continue
                    # Only compare items from *different* source documents.
                    if items[i]["source_doc_id"] == items[j]["source_doc_id"]:
                        continue

                    sim = _cosine(items[i]["vec"], items[j]["vec"])

                    if sim >= _HIGH:
                        # items are sorted by created_at ASC → items[i] is older
                        older_id = items[i]["id"]
                        with db._lock:
                            db._conn.execute(
                                """UPDATE knowledge
                                   SET review_status = 'superseded_duplicate'
                                   WHERE id = ?""",
                                (older_id,),
                            )
                            db._conn.commit()
                        superseded_ids.add(older_id)
                        total_superseded += 1
                        # Invalidate vector cache so next search excludes the
                        # retired item without waiting for the next cache eviction.
                        try:
                            bump_vector_cache_version(db._path, "knowledge")
                        except Exception:
                            pass

                    elif sim >= _LOW:
                        id_a, id_b = items[i]["id"], items[j]["id"]
                        with db._lock:
                            already = db._conn.execute(
                                """SELECT id FROM suggestions
                                   WHERE work_id = ? AND kind = 'semantic_duplicate'
                                   AND (
                                       (json_extract(meta,'$.item_a_id') = ?
                                        AND json_extract(meta,'$.item_b_id') = ?)
                                    OR (json_extract(meta,'$.item_a_id') = ?
                                        AND json_extract(meta,'$.item_b_id') = ?)
                                   )""",
                                (wid, id_a, id_b, id_b, id_a),
                            ).fetchone()
                            if not already:
                                meta_payload = _json.dumps(
                                    {
                                        "item_a_id": id_a,
                                        "item_b_id": id_b,
                                        "similarity": round(float(sim), 4),
                                        "similarity_basis": "cosine_embedding",
                                    }
                                )
                                db._conn.execute(
                                    """INSERT INTO suggestions
                                       (id, work_id, kind, text, meta, created_at)
                                       VALUES (?, ?, ?, ?, ?, ?)""",
                                    (
                                        str(_uuid_mod.uuid4()),
                                        wid,
                                        "semantic_duplicate",
                                        (
                                            "Two knowledge items may express the same fact "
                                            f"(similarity {sim:.0%})"
                                        ),
                                        meta_payload,
                                        now_iso,
                                    ),
                                )
                                db._conn.commit()
                                total_suggested += 1

        except Exception as exc:
            logger.warning("Knowledge semantic dedup: failed for work %s: %s", wid, exc)

    parts: list[str] = []
    if total_superseded:
        parts.append(f"{total_superseded} item(s) retired")
    if total_suggested:
        parts.append(f"{total_suggested} pair(s) flagged for review")
    report.append(
        f"Knowledge semantic dedup: {', '.join(parts)}"
        if parts
        else "Knowledge semantic dedup: no near-duplicates found"
    )
    if total_superseded or total_suggested:
        logger.info(
            "Nightshift knowledge semantic dedup: superseded=%d suggested=%d",
            total_superseded,
            total_suggested,
        )


def _pass_cold_item_detection(db: OrivellumDB, report: list[str]) -> None:
    """Surface knowledge items that have never been injected into chat, or not
    injected in the last 60 days, as ``cold_knowledge_item`` governance
    suggestions so the user can decide whether to keep, archive, or delete them.

    Cold criteria
    -------------
    A knowledge item is considered cold when ALL of the following hold:
    - ``review_status`` is not ``rejected`` or ``superseded_duplicate``
      (already-retired items are already out of the active index).
    - It was created more than 30 days ago (newly imported items get a grace
      period before they can be flagged cold).
    - It has *no row* in ``knowledge_retrievals`` at all (never injected), OR
      its most-recent retrieval row is older than 60 days.

    Does **not** auto-delete or change ``review_status`` — only inserts a
    suggestion of kind ``cold_knowledge_item``.  Existing suggestions for the
    same item are skipped (idempotent).

    Gates
    -----
    - Silently no-ops when the ``knowledge_retrievals`` table does not exist
      yet (old schema before v102).  This prevents nightshift from crashing on
      instances that have not yet run the migration.
    """
    import datetime as _dt
    import json as _json
    import uuid as _uuid_mod

    _COLD_THRESHOLD_DAYS = 60  # days since last retrieval → cold
    _NEW_ITEM_GRACE_DAYS = 30  # items younger than this are never flagged
    _MAX_SUGGESTIONS_PER_RUN = 200  # cap to avoid suggestion flood on first run

    # Gate: knowledge_retrievals table must exist (schema v102+)
    try:
        with db._lock:
            db._conn.execute("SELECT 1 FROM knowledge_retrievals LIMIT 1").fetchone()
    except Exception as _exc:
        logger.debug("Cold-item detection: knowledge_retrievals table not available (%s)", _exc)
        report.append("Cold-item detection: skipped (schema v102+ not yet applied)")
        return

    try:
        now_iso = _dt.datetime.now(_dt.UTC).isoformat()
        cutoff_new = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=_NEW_ITEM_GRACE_DAYS)
        ).isoformat()
        cutoff_cold = (
            _dt.datetime.now(_dt.UTC) - _dt.timedelta(days=_COLD_THRESHOLD_DAYS)
        ).isoformat()

        with db._lock:
            # Find cold items: created before grace-period cutoff; either never
            # retrieved or last retrieved before the cold threshold.
            cold_rows = db._conn.execute(
                """SELECT k.id, k.work_id, k.text, k.kind,
                          MAX(kr.retrieved_at) AS last_retrieved
                   FROM knowledge k
                   LEFT JOIN knowledge_retrievals kr ON kr.knowledge_id = k.id
                   WHERE k.review_status NOT IN ('rejected', 'superseded_duplicate')
                     AND k.created_at < ?
                   GROUP BY k.id
                   HAVING last_retrieved IS NULL OR last_retrieved < ?
                   ORDER BY last_retrieved ASC NULLS FIRST, k.created_at ASC
                   LIMIT ?""",
                (cutoff_new, cutoff_cold, _MAX_SUGGESTIONS_PER_RUN * 4),
            ).fetchall()

        if not cold_rows:
            report.append("Cold-item detection: no cold knowledge items found")
            return

        # Build set of items that already have a pending cold suggestion so we
        # don't duplicate-insert (idempotent).
        with db._lock:
            existing_raw = db._conn.execute(
                """SELECT json_extract(meta, '$.knowledge_id') AS kid
                   FROM suggestions
                   WHERE kind = 'cold_knowledge_item'"""
            ).fetchall()
        existing_kids: set[str] = {r["kid"] for r in existing_raw if r["kid"]}

        inserts: list[tuple] = []
        count = 0
        for row in cold_rows:
            if count >= _MAX_SUGGESTIONS_PER_RUN:
                break
            kid = row["id"]
            if kid in existing_kids:
                continue
            last_ret = row["last_retrieved"]
            if last_ret:
                age_label = f"last used {last_ret[:10]}"
            else:
                age_label = "never used in chat"
            snippet = (row["text"] or "")[:120].strip()
            meta_payload = _json.dumps(
                {
                    "knowledge_id": kid,
                    "last_retrieved": last_ret,
                    "kind": row["kind"] or "note",
                    "snippet": snippet,
                }
            )
            inserts.append(
                (
                    str(_uuid_mod.uuid4()),
                    row["work_id"],
                    "cold_knowledge_item",
                    (
                        f"Knowledge item has not been used in chat ({age_label}). "
                        f'Consider keeping, archiving, or deleting: "{snippet}"'
                        if snippet
                        else f"Knowledge item has not been used in chat ({age_label})."
                    ),
                    meta_payload,
                    now_iso,
                )
            )
            count += 1

        if inserts:
            with db._lock:
                db._conn.executemany(
                    """INSERT INTO suggestions
                       (id, work_id, kind, text, meta, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    inserts,
                )
                db._conn.commit()

        report.append(
            f"Cold-item detection: {len(inserts)} new cold item(s) flagged for review"
            if inserts
            else "Cold-item detection: all cold items already have suggestions"
        )
        if inserts:
            logger.info(
                "Nightshift cold-item detection: %d new suggestion(s) inserted", len(inserts)
            )

    except Exception as exc:
        logger.warning("Cold-item detection pass failed (non-fatal): %s", exc)
        report.append(f"Cold-item detection: failed — {exc}")


def _run_nightshift_passes(db: OrivellumDB, cfg: OrivellumConfig) -> None:
    """Run every maintenance pass in order and write the Night Report.

    Executes the numbered passes sequentially, appending a line to ``report``
    for each. Every pass is wrapped in its own try/except (either inside the
    pass function or here at the call site) so one pass failing never blocks
    the rest — failures are logged and noted in the report.

    Side effects: mutates the database (each pass), spawns background workers
    for re-extraction passes, writes the markdown report, and records the run
    via ``_record_run``. Returns None; only an unexpected error in the trailing
    report/record step (outside a per-pass guard) can propagate.
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    start_ts = time.time()
    logger.info("Nightshift starting for %s", date_str)
    report: list[str] = []

    # 1 — Database maintenance
    logger.info("Nightshift pass 1/13: database optimisation")
    _pass_db_optimise(db, report)

    # 2 — Zero-byte temp file cleanup
    logger.info("Nightshift pass 2/13: output temp-file cleanup")
    _pass_cleanup_outputs(cfg, report)

    # 3 — Prune old night reports
    logger.info("Nightshift pass 3/13: prune old reports")
    _pass_prune_old_reports(cfg)

    # 4 — Orphaned knowledge / chunks / vectors
    logger.info("Nightshift pass 4/13: orphan cleanup")
    _pass_orphan_cleanup(db, report)

    # 4b — Automation run-history retention
    logger.info("Nightshift pass 4b: prune old automation runs")
    _pass_prune_schedule_runs(db, report)

    # 5 — Retry stuck documents
    logger.info("Nightshift pass 5/13: stuck document recovery")
    _pass_stuck_docs(db, cfg, report)

    # 5b — VLM re-extraction of no_text PDFs/images (when vision model now configured)
    logger.info("Nightshift pass 5b: VLM no_text re-extraction")
    _pass_no_text_reextract(db, cfg, report)

    # 5c — faster-whisper re-transcription of metadata-only audio docs
    logger.info("Nightshift pass 5c: audio re-transcription via faster-whisper")
    _pass_audio_reextract(db, cfg, report)

    # 6 — Harvest sparse documents
    logger.info("Nightshift pass 6/13: sparse document harvest")
    items_added = _pass_sparse_harvest(db, report)

    # 7 — Gap analysis
    logger.info("Nightshift pass 7/13: gap analysis")
    _pass_gap_analysis(db, report)

    # 8 — Evidence rescoring + contradiction detection
    logger.info("Nightshift pass 8/13: evidence rescoring")
    _pass_evidence(db, report)

    # 9 — Contextual chunk prefix generation (Anthropic Contextual Retrieval)
    logger.info("Nightshift pass 9/18: context-prefix backfill")
    _pass_context_prefix_backfill(db, report)

    # 10 — Semantic embedding backfill
    logger.info("Nightshift pass 10/18: embedding backfill")
    _pass_embeddings(db, report)

    # 11 — Work stats refresh
    logger.info("Nightshift pass 11/18: work stats refresh")
    _pass_work_stats(db, report)

    # 11 — MCOS benchmark evaluations
    logger.info("Nightshift pass 11/13: MCOS benchmark evaluations")
    _pass_mcos(db, cfg, report)

    # 12 — Drain transactional outbox
    logger.info("Nightshift pass 12/13: outbox drain")
    _pass_dispatch_outbox(db, report)

    # 13 — Verify audit-chain integrity
    logger.info("Nightshift pass 13/14: audit-chain verification")
    _pass_verify_audit_chain(db, report)

    # 14 — Version relationship suggestions
    logger.info("Nightshift pass 14/15: version-relationship suggestions")
    _pass_version_suggestions(db, report)

    # 14a — Autonomy: unattended draft-check-revise runs (opt-in, M12)
    logger.info("Nightshift pass 14a: autonomy runs")
    try:
        from orivellum.capabilities.autonomy import run_nightshift_pass as _autonomy_pass

        _autonomy_pass(db, cfg, report)
    except Exception as _auto_exc:
        logger.warning("Autonomy pass failed (non-fatal): %s", _auto_exc)
        report.append(f"Autonomy: failed — {_auto_exc}")

    # 14b — Mail Steward delta sync (fires only when connected)
    logger.info("Nightshift pass 14b: mail steward delta sync")
    try:
        if db.get_setting("mail_steward.connected", "false") == "true":
            from orivellum.capabilities.mail.steward import sync_mail

            mail_result = sync_mail(db, cfg)
            if not mail_result.get("skipped"):
                new_msgs = mail_result.get("new", 0)
                errors = mail_result.get("errors", 0)
                report.append(
                    f"Mail sync: +{new_msgs} new" + (f", {errors} errors" if errors else "")
                )
    except Exception as _mail_exc:
        logger.warning("Mail sync pass failed (non-fatal): %s", _mail_exc)
        report.append(f"Mail sync: failed — {_mail_exc}")

    # 15 — ZIP child provenance back-fill
    logger.info("Nightshift pass 15/17: ZIP provenance backfill")
    _pass_zip_provenance_backfill(db, report)

    # 16 — Topic clustering
    logger.info("Nightshift pass 16/17: topic clustering")
    _pass_clustering(db, report)

    # 16b — Learning concept re-seed for Works with fresh knowledge
    logger.info("Nightshift pass 16b: learning concept re-seed")
    _pass_reseed_concepts(db, cfg, report)

    # 17 — Topic profiles (LLM-generated plain-English summaries per cluster)
    logger.info("Nightshift pass 17/19: topic profile generation")
    try:
        if db.get_setting("ai_extraction_enabled", "false").lower() == "true":
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            tp_result = generate_topic_profiles(db, cfg)
            if tp_result["generated"]:
                report.append(
                    f"Topic profiles: generated {tp_result['generated']} profile(s)"
                    + (f", {tp_result['skipped']} skipped" if tp_result["skipped"] else "")
                    + (f", {tp_result['errors']} error(s)" if tp_result["errors"] else "")
                )
        else:
            logger.debug("Nightshift: topic profiles skipped (ai_extraction_enabled=false)")
    except Exception as _tpex:
        logger.warning("Topic profile pass failed (non-fatal): %s", _tpex)
        report.append(f"Topic profiles: failed — {_tpex}")

    # Proactive custodian: staleness nudges
    logger.info("Nightshift pass 18/19: proactive custodian nudges")
    try:
        from orivellum.capabilities.custodian import run_custodian

        custodian_result = run_custodian(db)
        written = custodian_result.get("nudges_written", 0)
        pruned = custodian_result.get("pruned", 0)
        report.append(f"Custodian: {written} nudge(s) written, {pruned} old nudge(s) pruned")
    except Exception as _cex:
        logger.warning("Custodian pass failed (non-fatal): %s", _cex)
        report.append(f"Custodian: skipped ({_cex})")

    # 18b — Working memory TTL expiry
    logger.info("Nightshift pass 18b: working-memory TTL expiry")
    try:
        expired = db.cleanup_working_memory_ttl()
        if expired:
            report.append(f"Working memory TTL: expired {expired} row(s)")
    except Exception as _wex:
        logger.debug("Working memory TTL pass failed (non-fatal): %s", _wex)

    # 19a — Memory deduplication
    logger.info("Nightshift pass 19a: memory deduplication")
    _pass_memory_dedup(db, report)

    # 19b — Episodic-to-semantic promotion
    logger.info("Nightshift pass 19b: episodic memory promotion")
    _pass_memory_promote(db, report)

    # 17 — Automatic near-duplicate resolution
    # Gated by auto_dedup_enabled=true; silently skips when disabled so the
    # nightshift run time is not affected for users who prefer manual review.
    logger.info("Nightshift pass 17/19: auto near-duplicate resolution")
    try:
        _auto_dedup_enabled = db.get_setting("auto_dedup_enabled", "false").lower() == "true"
        if _auto_dedup_enabled:
            from orivellum.capabilities.auto_dedup import auto_resolve_duplicates

            _ad = auto_resolve_duplicates(db)
            report.append(
                f"Auto-dedup: {_ad['processed']} pair(s) examined — "
                f"{_ad['superseded']} superseded, {_ad['versioned']} versioned, "
                f"{_ad['skipped']} skipped, {_ad['errors']} error(s)"
            )
            logger.info(
                "Auto-dedup pass: %d processed, %d superseded, %d versioned",
                _ad["processed"],
                _ad["superseded"],
                _ad["versioned"],
            )
        else:
            report.append("Auto-dedup: disabled (auto_dedup_enabled=false)")
    except Exception as _adex:
        logger.warning("Auto-dedup pass failed (non-fatal): %s", _adex)
        report.append(f"Auto-dedup: failed — {_adex}")

    # 17b — Knowledge semantic dedup (cross-document, embedding-based)
    # Uses stored vectors so no new LLM calls.  Gated by auto_dedup_enabled.
    logger.info("Nightshift pass 17b: knowledge semantic dedup")
    try:
        _pass_knowledge_semantic_dedup(db, report)
    except Exception as _ksdex:
        logger.warning("Knowledge semantic dedup pass failed (non-fatal): %s", _ksdex)
        report.append(f"Knowledge semantic dedup: failed — {_ksdex}")

    # 18 — Cold-item detection (knowledge items never used or unused ≥ 60 days)
    # Reads knowledge_retrievals (schema v102) to find dead facts and surfaces
    # them as governance suggestions.  Does NOT auto-delete.
    logger.info("Nightshift pass 18: cold-item detection")
    try:
        _pass_cold_item_detection(db, report)
    except Exception as _cidex:
        logger.warning("Cold-item detection pass failed (non-fatal): %s", _cidex)
        report.append(f"Cold-item detection: failed — {_cidex}")

    # Commonplace notes: classify captured inbox blocks into filing proposals
    # (surfaced in the review inbox) and refresh yesterday's + today's reports.
    logger.info("Nightshift pass 19: commonplace note processing")
    try:
        from orivellum.capabilities import notes as _notes_cap

        _nrec = _notes_cap.resume_approved(db, cfg)
        if _nrec:
            report.append(f"Notes: recovered {_nrec} interrupted filing(s)")
        _nres = _notes_cap.process_inbox(db, cfg)
        if _nres["scanned"]:
            report.append(
                f"Notes: {_nres['proposed']} of {_nres['scanned']} inbox note(s) "
                f"classified for review"
                + (f", {_nres['failed']} failed (will retry)" if _nres["failed"] else "")
            )
        for _nday in (_notes_cap.yesterday_str(), _notes_cap.today_str()):
            try:
                _notes_cap.build_daily_report(db, cfg, _nday)
            except Exception as _nrex:
                logger.warning("Notes report for %s failed (non-fatal): %s", _nday, _nrex)
    except Exception as _nex:
        logger.warning("Notes pass failed (non-fatal): %s", _nex)
        report.append(f"Notes: failed — {_nex}")

    elapsed = time.time() - start_ts
    report.append(f"Completed in {elapsed:.0f}s")

    report_path = _write_report(Path(cfg.data_dir), date_str, report)
    _record_run(db, len(_get_docs_needing_work(db)), items_added, report_path)
    logger.info("Nightshift complete in %.0fs — %d report lines", elapsed, len(report))


def _memory_text_similarity(a: str, b: str) -> float:
    """Word-set Jaccard similarity for short memory fact values.

    Uses a character-normalised word-tokenisation so punctuation differences
    don't inflate dissimilarity.  Fast enough for pairwise comparison of the
    ~100 memory rows typical in a single user session.
    """
    import re as _re

    def _tokens(s: str) -> set[str]:
        return set(_re.sub(r"[^\w\s]", "", s.lower()).split())

    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _pass_memory_dedup(db: OrivellumDB, report: list[str]) -> None:
    """Deduplicate current memory facts; flag contradictions in memory_conflicts.

    Algorithm
    ---------
    1. Load all *current* user_memory rows (valid_to IS NULL).
    2. Group by key:
       a. Exact-duplicate values (sim ≥ 0.95) within the same key → keep the
          most recently created row; soft-delete the others (set valid_to=now()).
       b. Contradictory values (sim < 0.50) within the same key → record a
          conflict in memory_conflicts without touching either row.
    3. Cross-key near-duplicates (sim ≥ 0.85 on *value*, different keys) →
       soft-delete the older row; keep the newer.  No conflict is recorded
       because these are phrasing variants, not contradictions.

    Idempotency
    -----------
    - Soft-deleted rows are excluded from the next load (valid_to IS NULL only).
    - Conflict pairs use INSERT OR IGNORE on UNIQUE(memory_id_a, memory_id_b).
    Running twice produces the same result.
    """
    _EXACT_THRESH = 0.95  # same-key same-value → dedup
    _CONFLICT_THRESH = 0.50  # same-key different-value → flag as conflict
    _CROSS_NEAR_THRESH = 0.85  # cross-key near-dup → merge

    try:
        now = datetime.now(UTC).isoformat()

        with db._lock:
            rows = db._conn.execute(
                """SELECT id, key, value, memory_type, created_at
                   FROM user_memory
                   WHERE valid_to IS NULL
                   ORDER BY created_at ASC"""
            ).fetchall()

        if not rows:
            return

        facts = [dict(r) for r in rows]

        # ── Stage 1: same-key dedup / conflict detection ───────────────────
        from collections import defaultdict as _dd

        by_key: dict[str, list[dict]] = _dd(list)
        for f in facts:
            by_key[f["key"]].append(f)

        merged = 0
        conflicts = 0

        for key, group in by_key.items():
            if len(group) < 2:
                continue
            # Sort newest-first so the newest row is always memory_id_a (the
            # "preferred" side when the user resolves a conflict via "Keep A").
            group_sorted = sorted(group, key=lambda x: x["created_at"], reverse=True)
            keep = group_sorted[0]  # newest = memory_id_a
            for old in group_sorted[1:]:  # older = memory_id_b
                sim = _memory_text_similarity(keep["value"], old["value"])
                if sim >= _EXACT_THRESH:
                    # Near-identical: soft-delete the older row — safe because
                    # the two rows are essentially the same fact.
                    try:
                        with db._lock:
                            db._conn.execute(
                                "UPDATE user_memory SET valid_to=? WHERE id=? AND valid_to IS NULL",
                                (now, old["id"]),
                            )
                            db._conn.commit()
                        merged += 1
                        logger.debug(
                            "Memory dedup: merged duplicate key=%s id=%s→%s",
                            key,
                            old["id"][:8],
                            keep["id"][:8],
                        )
                    except Exception as exc:
                        logger.debug("Memory dedup merge failed: %s", exc)
                elif sim < _CONFLICT_THRESH:
                    # Contradictory values: register (newer first = memory_id_a)
                    # without touching either row.  The user resolves via the UI.
                    db.record_memory_conflict(keep["id"], old["id"])
                    conflicts += 1
                    logger.debug(
                        "Memory conflict: key=%s newer=%s older=%s (sim=%.2f)",
                        key,
                        keep["id"][:8],
                        old["id"][:8],
                        sim,
                    )
                # sim in [_CONFLICT_THRESH, _EXACT_THRESH) → benign restatement;
                # keep both silently.

        # ── Stage 2: cross-key value near-duplicates ───────────────────────
        # Different keys may encode distinct facts even when their text looks
        # similar — auto-deletion would silently erase valid beliefs.  Instead,
        # register each near-duplicate pair as a conflict for user review.
        # The newer row is stored as memory_id_a (the "keep" default in the UI).
        with db._lock:
            current_rows = db._conn.execute(
                "SELECT id, key, value, created_at FROM user_memory WHERE valid_to IS NULL"
            ).fetchall()
        current = [dict(r) for r in current_rows]

        cross_conflicts = 0
        # Compare each pair once (upper-triangle)
        for i in range(len(current)):
            for j in range(i + 1, len(current)):
                fa, fb = current[i], current[j]
                if fa["key"] == fb["key"]:
                    continue  # already handled in Stage 1
                sim = _memory_text_similarity(fa["value"], fb["value"])
                if sim >= _CROSS_NEAR_THRESH:
                    # Newer = memory_id_a, older = memory_id_b for meaningful
                    # A/B labeling in the conflict UI.
                    newer = fa if fa["created_at"] >= fb["created_at"] else fb
                    older = fb if newer is fa else fa
                    cid = db.record_memory_conflict(newer["id"], older["id"])
                    if cid:
                        cross_conflicts += 1
                        logger.debug(
                            "Cross-key near-dup flagged for review: %s vs %s (sim=%.2f)",
                            fa["key"],
                            fb["key"],
                            sim,
                        )

        total_merged = merged
        total_conflicts = conflicts + cross_conflicts
        msg = (
            f"Memory dedup: {total_merged} exact duplicate(s) merged, "
            f"{total_conflicts} conflict(s) flagged for review "
            f"({conflicts} same-key, {cross_conflicts} cross-key)"
        )
        if total_merged or total_conflicts:
            report.append(msg)
            logger.info(msg)
        else:
            logger.debug("Memory dedup: nothing to do (%d fact(s) checked)", len(facts))

    except Exception as exc:
        logger.warning("Memory dedup pass failed: %s", exc)
        report.append(f"⚠ Memory dedup: {exc}")


def _pass_memory_promote(db: OrivellumDB, report: list[str]) -> None:
    """Promote episodic memories to semantic after ≥ 3 observations.

    An episodic memory key that has been observed 3 or more times (counting
    all historical rows, not just the current one) represents a recurring
    pattern and should be promoted to ``memory_type='semantic'`` — a more
    durable, preference-level belief.

    Promotion uses the bi-temporal soft-delete + insert pattern: the current
    episodic row is soft-deleted (valid_to = now) and a new semantic row is
    inserted in the same transaction.  This is done directly via SQL rather
    than through ``upsert_memory_fact`` because that helper is a no-op when
    the value is unchanged — it does not check memory_type.

    Idempotency
    -----------
    Once the current row is promoted to semantic, the key no longer appears
    in the "episodic with ≥ 3 occurrences" query on the next run (the current
    row's memory_type is now 'semantic', so the HAVING clause is not met),
    making the pass naturally idempotent.
    """
    _MIN_OBSERVATIONS = 3

    try:
        with db._lock:
            rows = db._conn.execute(
                """SELECT key,
                          SUM(CASE WHEN memory_type = 'episodic' THEN 1 ELSE 0 END) AS episodic_count
                   FROM user_memory
                   GROUP BY key
                   HAVING episodic_count >= ?""",
                (_MIN_OBSERVATIONS,),
            ).fetchall()

        if not rows:
            return

        now = datetime.now(UTC).isoformat()
        promoted = 0

        for row in rows:
            key = row["key"]
            episodic_count = row["episodic_count"]

            # Only promote if the *current* row is still episodic.
            # Fetch all provenance fields so the promoted semantic row
            # preserves its evidence chain (v99 Evidence-Before-Belief contract).
            with db._lock:
                current = db._conn.execute(
                    """SELECT id, value, source_conv_id, source_evidence_id
                       FROM user_memory
                       WHERE key=? AND valid_to IS NULL
                         AND memory_type='episodic'""",
                    (key,),
                ).fetchone()

            if not current:
                continue  # already promoted or no current row

            current_id = current["id"]
            value = current["value"]
            source_conv_id = current["source_conv_id"]
            source_evidence_id = current["source_evidence_id"]

            try:
                with db._lock:
                    # Soft-delete the current episodic row
                    db._conn.execute(
                        "UPDATE user_memory SET valid_to=? WHERE id=? AND valid_to IS NULL",
                        (now, current_id),
                    )
                    # Insert a new semantic row — same key, value, and provenance,
                    # but memory_type upgraded to 'semantic'.  Preserving
                    # source_conv_id and source_evidence_id keeps the Evidence-
                    # Before-Belief guarantee intact after promotion.
                    promoted_id = str(uuid.uuid4())
                    db._conn.execute(
                        """INSERT INTO user_memory
                               (id, key, value, memory_type,
                                valid_from, valid_to, txn_time, created_at,
                                source_conv_id, source_evidence_id)
                           VALUES (?,?,?,?,?,NULL,?,?,?,?)""",
                        (
                            promoted_id,
                            key,
                            value,
                            "semantic",
                            now,
                            now,
                            now,
                            source_conv_id,
                            source_evidence_id,
                        ),
                    )
                    db._conn.commit()
                # Sync the new promoted row into user_memory_fts (v101+).
                # Called outside the lock because _sync_memory_fts acquires
                # its own lock internally.
                db._sync_memory_fts(promoted_id, key, value)
                promoted += 1
                logger.debug(
                    "Memory promote: key=%s episodic_count=%d → semantic",
                    key,
                    episodic_count,
                )
            except Exception as exc:
                logger.debug("Memory promote failed for key=%s: %s", key, exc)

        if promoted:
            msg = f"Memory promote: {promoted} episodic fact(s) promoted to semantic"
            report.append(msg)
            logger.info(msg)
        else:
            logger.debug("Memory promote: no keys met the threshold")

    except Exception as exc:
        logger.warning("Memory promote pass failed: %s", exc)
        report.append(f"⚠ Memory promote: {exc}")


def start_nightshift_daemon(db: OrivellumDB, cfg: OrivellumConfig) -> threading.Thread:
    """Start the nightshift daemon thread.  Returns the thread (daemon=True).

    Reads the configured ``nightshift_hour`` (default 3) and launches a daemon
    thread running ``_loop``: it sleeps until the next occurrence of that hour,
    then — if ``nightshift_enabled`` is true — invokes ``run_nightshift`` and
    repeats forever. A crashing run is caught and logged at ERROR so the loop
    survives and fires again the next night; the thread never blocks shutdown.
    """
    nightshift_hour = int(db.get_setting("nightshift_hour", "3"))

    def _loop() -> None:
        logger.info("Nightshift daemon ready (fires at %02d:00 local time)", nightshift_hour)
        while True:
            now = datetime.now()
            target = now.replace(hour=nightshift_hour, minute=0, second=0, microsecond=0)
            if target <= now:
                # Already past today's window — aim for tomorrow
                from datetime import timedelta

                target += timedelta(days=1)
            wait_secs = (target - now).total_seconds()
            logger.debug("Nightshift sleeping %.0f s until %s", wait_secs, target.isoformat())
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
