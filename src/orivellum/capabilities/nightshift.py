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
 11. MCOS benchmark evals  — run any due benchmark suites
 12. Outbox drain           — dispatch queued transactional events
 13. Audit-chain verify     — check governance audit chain integrity
 14. Version suggestions    — surface likely version pairs across each Work
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

# ── Testable helpers for the DB optimise pass ─────────────────────────────────

def _get_freelist_ratio(conn: "Any") -> "tuple[float, int, int]":
    """Return ``(ratio, freelist_count, page_count)`` for *conn*.

    Extracted so tests can monkeypatch this function to simulate arbitrary
    fragmentation without touching C-extension sqlite3.Connection attributes.
    """
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_cnt = conn.execute("PRAGMA page_count").fetchone()[0]
    ratio    = freelist / max(1, page_cnt)
    return ratio, freelist, page_cnt


def _run_vacuum(conn: "Any") -> None:
    """Execute VACUUM on *conn*.

    Extracted so tests can monkeypatch this function to simulate a slow
    VACUUM without touching C-extension sqlite3.Connection attributes.
    """
    conn.execute("VACUUM")


def _pass_db_optimise(db: "OrivellumDB", report: list[str]) -> None:
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
                ratio, freelist, page_cnt = 0.0, 0, 0
                do_vacuum = False

            # ── conditional VACUUM (still under db._lock) ──────────────────────
            # Holding db._lock ensures:
            #   - No other Python writer races with VACUUM on db._conn
            #   - Writers queue at the Python mutex (not SQLITE_BUSY) and
            #     proceed cleanly once the lock is released
            #   - Reads via db.read_conn() are never blocked (separate conn)
            if do_vacuum and db_path:
                size_before = (os.path.getsize(db_path)
                               if os.path.exists(db_path) else None)
                db._conn.commit()   # close any implicit read transaction first
                _run_vacuum(db._conn)

                if size_before is not None:
                    size_after = os.path.getsize(db_path)
                    saved_mb   = max(0, (size_before - size_after) / 1_048_576)
                    msg = (f"DB optimised — VACUUM saved {saved_mb:.1f} MB "
                           f"(freelist {ratio:.0%})")
                else:
                    msg = (f"DB optimised — VACUUM + checkpoint + ANALYZE "
                           f"(freelist {ratio:.0%})")
            else:
                reason = (f"< {_FREELIST_VACUUM_RATIO:.0%}, VACUUM skipped"
                          if not do_vacuum else "VACUUM skipped — no DB path")
                msg = (f"DB optimised — checkpoint + ANALYZE "
                       f"(freelist {ratio:.0%}, {reason})")

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
            # Track each type separately so cache bumps are precise.
            vk_deleted = 0   # orphaned knowledge-type vectors
            vc_deleted = 0   # orphaned chunk-type vectors
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
            logger.info("Nightshift orphan cleanup: k=%d c=%d v=%d",
                        k_deleted, c_deleted, v_deleted)
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
                                    doc_title=title, db=db,
                                    kind=doc_info.get("kind"))
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

            # Push notification — high gaps discovered (best-effort, daemon thread)
            try:
                from orivellum.capabilities.push import notify_push_best_effort as _push_gaps
                import threading as _push_thr_gaps
                _n = len(high_gaps)
                # e.g. "My Novel: Missing antagonist motivation"  → "My Novel"
                _first_work = high_gaps[0].split(":")[0].strip()[:30]
                _gap_body = (
                    f"{_n} new knowledge gap{'s' if _n != 1 else ''} found"
                    + (f" in {_first_work}" if _first_work else "")
                )
                _push_thr_gaps.Thread(
                    target=_push_gaps,
                    args=(db, "💡 Knowledge gaps found", _gap_body, {"screen": "governance"}),
                    daemon=True,
                ).start()
            except Exception:
                pass
        elif active_works:
            report.append(f"Research coverage: no critical gaps across "
                          f"{len(active_works)} work(s)")
    except Exception as exc:
        logger.warning("Gap analysis pass failed: %s", exc)


def _pass_evidence(db: "OrivellumDB", report: list[str]) -> None:
    """Rescore confidence and detect contradictions for stale Works.

    Only processes Works whose last rescore is >24 h old (tracked via the
    ``evidence_rescore:{work_id}`` settings key).  Capped at 5 Works per run
    so the pass stays bounded regardless of library size.
    """
    from datetime import datetime, timezone, timedelta

    _STALE_HOURS = 24
    _MAX_WORKS   = 5

    try:
        from orivellum.capabilities.evidence import rescore_work, detect_contradictions
        now = datetime.now(timezone.utc)
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
                rescored  += rescore_work(wid, db)
                conflicts += detect_contradictions(wid, db)
                db.set_setting(f"evidence_rescore:{wid}", now.isoformat())
                processed += 1
            except Exception as exc:
                logger.warning("Evidence pass failed for %s: %s", wid[:8], exc)

        if rescored:
            report.append(f"Evidence: re-scored {rescored} knowledge item(s) across {processed} work(s)")
        if conflicts:
            report.append(f"⚠ Contradictions: {conflicts} new conflict(s) — review in Governance")
        if skipped:
            logger.debug("Evidence pass: skipped %d recently-rescored work(s)", skipped)
    except Exception as exc:
        logger.warning("Evidence pass failed: %s", exc)


def _pass_context_prefix_backfill(db: "OrivellumDB", report: list[str]) -> None:
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
                    doc_id, db,
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
                logger.debug("Context-prefix backfill failed for doc %s: %s",
                             doc_id[:8], exc)

        if total_generated:
            report.append(
                f"Context prefixes: generated {total_generated} prefix(es), "
                f"re-embedded {total_reembedded} chunk(s)"
            )
    except Exception as exc:
        logger.warning("Context-prefix backfill pass failed: %s", exc)


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


def _pass_dispatch_outbox(db: "OrivellumDB", report: list[str]) -> None:
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


def _pass_verify_audit_chain(db: "OrivellumDB", report: list[str]) -> None:
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


def _pass_version_suggestions(db: "OrivellumDB", report: list[str]) -> None:
    """Cross-check document pairs in every Work for similar filename stems and
    create version-relationship suggestions for any new matches found.

    Mirrors the same logic run at upload time (_maybe_suggest_version in library.py)
    so that documents imported before the feature was introduced also get covered.
    """
    import re as _re
    import json as _json
    import uuid as _uuid_mod
    import datetime as _dt
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
            for doc_b in docs[i + 1:]:
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
                    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
                    meta_payload = _json.dumps({
                        "doc_a_id": doc_a["id"],
                        "doc_b_id": doc_b["id"],
                        "doc_a_title": doc_a.get("title", ""),
                        "doc_b_title": doc_b.get("title", ""),
                        "similarity_basis": "filename_stem",
                    })
                    text_label = (
                        f'"{doc_a.get("title") or stem_a}" and '
                        f'"{doc_b.get("title") or stem_b}" '
                        f"may be versions of the same document"
                    )
                    db._conn.execute(
                        """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            str(_uuid_mod.uuid4()), wid, "version_relationship",
                            text_label, meta_payload, now_iso,
                        ),
                    )
                    db._conn.commit()
                    created += 1

    if created:
        report.append(f"Version suggestions: created {created} new pair suggestion(s)")
    else:
        report.append("Version suggestions: no new version pairs found")


def _pass_zip_provenance_backfill(db: "OrivellumDB", report: list[str]) -> None:
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
                _rp(doc_id, "zip_extract", db,
                    origin_id=parent_id, work_id=work_id)
                backfilled += 1
            except Exception as exc:
                logger.debug("ZIP provenance backfill: failed for %s: %s", doc_id, exc)

        if backfilled:
            report.append(f"ZIP provenance backfill: {backfilled} document(s) recorded")
            logger.info("Nightshift ZIP provenance backfill: %d rows written", backfilled)
    except Exception as exc:
        logger.warning("ZIP provenance backfill pass failed: %s", exc)
        report.append(f"ZIP provenance backfill: failed — {exc}")


def _pass_clustering(db: "OrivellumDB", report: list[str]) -> None:
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


def _run_nightshift_passes(db: "OrivellumDB", cfg: "OrivellumConfig") -> None:
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

    # 5 — Retry stuck documents
    logger.info("Nightshift pass 5/13: stuck document recovery")
    _pass_stuck_docs(db, cfg, report)

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

    # 15 — ZIP child provenance back-fill
    logger.info("Nightshift pass 15/17: ZIP provenance backfill")
    _pass_zip_provenance_backfill(db, report)

    # 16 — Topic clustering
    logger.info("Nightshift pass 16/17: topic clustering")
    _pass_clustering(db, report)

    # 16 — Proactive custodian: staleness nudges
    logger.info("Nightshift pass 16/16: proactive custodian nudges")
    try:
        from orivellum.capabilities.custodian import run_custodian
        custodian_result = run_custodian(db)
        written = custodian_result.get("nudges_written", 0)
        pruned  = custodian_result.get("pruned", 0)
        report.append(
            f"Custodian: {written} nudge(s) written, {pruned} old nudge(s) pruned"
        )
    except Exception as _cex:
        logger.warning("Custodian pass failed (non-fatal): %s", _cex)
        report.append(f"Custodian: skipped ({_cex})")

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
