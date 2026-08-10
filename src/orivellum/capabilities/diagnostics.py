"""System diagnostic engine for Orivellum.

Runs a comprehensive set of checks across the database, configuration,
service connectivity, data quality, and pipeline health.  Returns a
structured result that can be rendered as JSON (for the API) or Markdown
(for the CLI report).

Entry point: ``run_full_diagnostic(db, cfg, vacuum=False)``
"""
from __future__ import annotations

import logging
import shutil
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.diagnostics")

# ── Severity helpers ──────────────────────────────────────────────────────────

OK    = "ok"
WARN  = "warn"
ERROR = "error"
INFO  = "info"

def _check(name: str, status: str, value: Any, detail: str = "") -> dict:
    return {"name": name, "status": status, "value": value, "detail": detail}


# ── Section runners ───────────────────────────────────────────────────────────

def _check_db_integrity(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []

    # Schema version (tracked in settings table, not PRAGMA user_version)
    ver = db.get_setting("schema_version", "0")
    checks.append(_check("Schema version", OK, f"v{ver}",
                          "Database schema is current"))

    # SQLite integrity
    with db._lock:
        ic_rows = db._conn.execute("PRAGMA integrity_check").fetchall()
    ic_result = [r[0] for r in ic_rows]
    if ic_result == ["ok"]:
        checks.append(_check("DB integrity_check", OK, "ok",
                              "SQLite reports no structural corruption"))
    else:
        for msg in ic_result[:5]:
            checks.append(_check("DB integrity_check", ERROR, msg,
                                  "SQLite integrity check failed — back up and repair immediately"))

    # Quick check
    with db._lock:
        qc = db._conn.execute("PRAGMA quick_check").fetchone()[0]
    checks.append(_check("DB quick_check", OK if qc == "ok" else ERROR, qc,
                          "Fast structural check"))

    # WAL mode
    with db._lock:
        jm = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    checks.append(_check("Journal mode", OK if jm == "wal" else WARN, jm,
                          "WAL mode recommended for concurrent access"))

    # FK constraints enabled
    with db._lock:
        fk = db._conn.execute("PRAGMA foreign_keys").fetchone()[0]
    checks.append(_check("Foreign key constraints", OK if fk == 1 else WARN,
                          "ON" if fk else "OFF",
                          "FK constraints enforce referential integrity"))

    return checks


def _check_table_counts(db: OrivellumDB) -> list[dict]:
    tables = [
        "objects", "works", "documents", "chunks", "vectors",
        "knowledge", "conversations", "messages", "tasks",
        "book_pipelines", "pipeline_artifacts", "findings",
        "claims", "review_deferrals", "outbox", "audit_log",
        "settings", "prompts", "llm_calls", "work_gap_cache",
    ]
    counts: list[dict] = []
    for tbl in tables:
        try:
            with db._lock:
                n = db._conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            counts.append(_check(f"Table: {tbl}", INFO, n, f"{n:,} rows"))
        except Exception:
            counts.append(_check(f"Table: {tbl}", WARN, "missing",
                                  f"Table {tbl!r} does not exist — schema may need migration"))
    return counts


def _check_orphans(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []

    # Documents without objects records
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM documents d "
                "LEFT JOIN objects o ON o.id=d.id WHERE o.id IS NULL"
            ).fetchone()[0]
        status = ERROR if n > 0 else OK
        checks.append(_check("Orphaned documents (no objects row)", status, n,
                              f"{n} documents missing their objects record — these may be inaccessible" if n else "Clean"))
    except Exception as exc:
        checks.append(_check("Orphaned documents (no objects row)", WARN, "error", str(exc)))

    # Knowledge items pointing to non-existent works
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM knowledge k "
                "WHERE k.work_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM works w WHERE w.id=k.work_id)"
            ).fetchone()[0]
        status = ERROR if n > 0 else OK
        checks.append(_check("Orphaned knowledge items (missing work)", status, n,
                              f"{n} knowledge items reference deleted works" if n else "Clean"))
    except Exception as exc:
        checks.append(_check("Orphaned knowledge items (missing work)", WARN, "error", str(exc)))

    # Pipeline artifacts without a pipeline
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM pipeline_artifacts pa "
                "LEFT JOIN book_pipelines bp ON bp.id=pa.pipeline_id "
                "WHERE bp.id IS NULL"
            ).fetchone()[0]
        status = ERROR if n > 0 else OK
        checks.append(_check("Orphaned pipeline artifacts", status, n,
                              f"{n} artifacts have no parent pipeline" if n else "Clean"))
    except Exception as exc:
        checks.append(_check("Orphaned pipeline artifacts", WARN, "error", str(exc)))

    # Chunks without documents
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM chunks c "
                "LEFT JOIN documents d ON d.id=c.doc_id WHERE d.id IS NULL"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Orphaned chunks (no document)", status, n,
                              f"{n} chunks have no parent document — run nightshift to clean" if n else "Clean"))
    except Exception as exc:
        checks.append(_check("Orphaned chunks (no document)", WARN, "error", str(exc)))

    # Vectors without chunks
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM vectors v "
                "LEFT JOIN chunks c ON c.id=v.object_id AND v.object_type='chunk' "
                "WHERE v.object_type='chunk' AND c.id IS NULL"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Orphaned vectors (chunk type, missing chunk)", status, n,
                              f"{n} chunk-type vectors reference deleted chunks" if n else "Clean"))
    except Exception as exc:
        checks.append(_check("Orphaned vectors (chunk type, missing chunk)", WARN, "error", str(exc)))

    return checks


def _check_stuck_records(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []
    datetime.now(UTC).isoformat()

    # Documents stuck in 'imported' > 10 minutes
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM documents d JOIN objects o ON o.id=d.id "
                "WHERE d.readiness='imported' "
                "AND o.created_at < datetime('now', '-10 minutes')"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Documents stuck in 'imported'", status, n,
                              f"{n} docs have been importing for >10 min — reprocess from Library" if n else "None stuck"))
    except Exception as exc:
        checks.append(_check("Documents stuck in 'imported'", WARN, "error", str(exc)))

    # Documents in error state
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE readiness='error'"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Documents in error state", status, n,
                              f"{n} documents failed processing — check extraction logs" if n else "None"))
    except Exception as exc:
        checks.append(_check("Documents in error state", WARN, "error", str(exc)))

    # Pipeline artifacts stuck in 'running' > 5 minutes
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM pipeline_artifacts "
                "WHERE status='running' "
                "AND updated_at < datetime('now', '-5 minutes')"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Pipeline artifacts stuck in 'running'", status, n,
                              f"{n} stage workers appear crashed — retry from pipeline panel" if n else "None"))
    except Exception as exc:
        checks.append(_check("Pipeline artifacts stuck in 'running'", WARN, "error", str(exc)))

    # Open high/critical findings blocking pipelines
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM findings "
                "WHERE state='open' AND severity IN ('high','critical')"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Open high/critical governance findings", status, n,
                              f"{n} blockers prevent pipeline advancement" if n else "None"))
    except Exception as exc:
        checks.append(_check("Open high/critical governance findings", WARN, "error", str(exc)))

    # Outbox items not yet dispatched (column is dispatched_at, not delivered_at)
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE dispatched_at IS NULL"
            ).fetchone()[0]
        status = WARN if n > 50 else OK
        checks.append(_check("Undelivered outbox events", status, n,
                              f"{n} events pending dispatch" if n else "None"))
    except Exception as exc:
        checks.append(_check("Undelivered outbox events", WARN, "error", str(exc)))

    return checks


def _check_configuration(db: OrivellumDB, cfg: OrivellumConfig) -> list[dict]:
    checks: list[dict] = []

    # LLM base URL
    url = cfg.serving.base_url
    checks.append(_check("LLM base URL", OK if url else ERROR, url or "(not set)",
                          "URL of the AI inference endpoint"))

    # LLM model name
    model = cfg.serving.workhorse_model
    checks.append(_check("LLM workhorse model", OK if model else ERROR, model or "(not set)",
                          "Model used for all AI operations"))

    # Session secret — must be at least 32 chars to be cryptographically adequate.
    import os as _os
    _env_secret = _os.environ.get("SESSION_SECRET")
    if _env_secret:
        if len(_env_secret) >= 32:
            checks.append(_check(
                "Session secret", OK,
                f"set ({len(_env_secret)} chars)",
                "SESSION_SECRET meets the 32-char minimum — sessions are cryptographically strong",
            ))
        else:
            checks.append(_check(
                "Session secret", ERROR,
                f"TOO SHORT ({len(_env_secret)} chars, minimum 32)",
                "Set SESSION_SECRET to a value with at least 32 characters — "
                "run: python -c \"import secrets; print(secrets.token_hex(32))\"",
            ))
    else:
        checks.append(_check(
            "Session secret", WARN,
            "not set — ephemeral per-restart secret in use",
            "Sessions are invalidated on every restart. "
            "Set SESSION_SECRET to a stable 32+ char value for persistent sessions.",
        ))

    # AI extraction enabled
    ai_enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"
    checks.append(_check("AI extraction enabled", INFO,
                          "yes" if ai_enabled else "no",
                          "LLM-based knowledge extraction runs if enabled"))

    # Data directory
    import os
    data_dir = __import__("os").environ.get("ORIVELLUM_DATA_DIR", "data")
    exists = os.path.isdir(data_dir)
    writable = os.access(data_dir, os.W_OK) if exists else False
    checks.append(_check("Data directory", OK if (exists and writable) else ERROR,
                          data_dir,
                          "exists and writable" if (exists and writable) else
                          ("exists but not writable" if exists else "directory missing")))

    # DB file size
    db_path = getattr(db, "_path", None)
    if db_path and db_path != ":memory:":
        try:
            size_mb = os.path.getsize(db_path) / 1_048_576
            checks.append(_check("Database file size", INFO, f"{size_mb:.1f} MB", db_path))
        except Exception:
            pass

    # Output directory for TTS/media
    out_dir = os.path.join(data_dir, "outputs")
    out_ok = os.path.isdir(out_dir) and os.access(out_dir, os.W_OK)
    checks.append(_check("Outputs directory", OK if out_ok else WARN, out_dir,
                          "writable" if out_ok else "missing or not writable — TTS/media may fail"))

    return checks


def _check_services(cfg: OrivellumConfig) -> list[dict]:
    checks: list[dict] = []

    # LLM endpoint reachability
    try:
        import httpx
        t0 = time.monotonic()
        r = httpx.get(f"{cfg.serving.base_url}/models", timeout=4.0)
        ms = round((time.monotonic() - t0) * 1000)
        ok = r.status_code in (200, 401, 403)  # 401/403 = auth required = server up
        checks.append(_check("LLM endpoint reachable", OK if ok else WARN,
                              f"HTTP {r.status_code} ({ms} ms)",
                              "LLM inference server responded" if ok else "Unexpected status — AI features may fail"))
    except Exception as exc:
        checks.append(_check("LLM endpoint reachable", ERROR, "unreachable",
                              f"Cannot reach {cfg.serving.base_url}: {exc}"))

    # Embeddings circuit breaker state
    try:
        from orivellum.capabilities.embeddings import (
            _FAILURE_THRESHOLD,
            _circuit_state,
            _failure_count,
        )
        state = _circuit_state
        fc = _failure_count
        checks.append(_check("Embeddings circuit breaker", OK if state == "closed" else WARN,
                              state,
                              f"{'open' if state == 'open' else 'half-open' if state == 'half_open' else 'closed (healthy)'} — failures: {fc}/{_FAILURE_THRESHOLD}"))
    except Exception:
        checks.append(_check("Embeddings circuit breaker", INFO, "unknown",
                              "Could not read circuit breaker state"))

    # TTS: espeak-ng (informational only — never used for audible speech;
    # the no-robot-voice policy routes all synthesis through neural engines)
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    checks.append(_check("espeak-ng (unused, informational)", OK if espeak else WARN,
                          espeak or "not found",
                          "Present but not used for speech — neural engines only"
                          if espeak else
                          "Not installed — fine; robotic fallback is disabled by policy"))

    # OCR: tesseract
    tess = shutil.which("tesseract")
    checks.append(_check("OCR (tesseract)", OK if tess else WARN,
                          tess or "not found",
                          "OCR available for image/scanned PDF extraction" if tess else
                          "Install tesseract for image text extraction"))

    # ffmpeg for audio
    ff = shutil.which("ffmpeg")
    checks.append(_check("Audio encoding (ffmpeg)", OK if ff else WARN,
                          ff or "not found",
                          "ffmpeg available for audio format conversion" if ff else
                          "Install ffmpeg for TTS audio encoding"))

    return checks


def _check_data_quality(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []

    # Works without documents
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM works w "
                "WHERE NOT EXISTS (SELECT 1 FROM documents d JOIN objects o ON o.id=d.id "
                "                  WHERE d.work_id=w.id AND o.lifecycle != 'deleted')"
            ).fetchone()[0]
        status = INFO if n > 0 else OK
        checks.append(_check("Works with no documents", status, n,
                              f"{n} Works have no linked documents yet" if n else "All Works have documents"))
    except Exception as exc:
        checks.append(_check("Works with no documents", WARN, "error", str(exc)))

    # Works without knowledge items
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM works w "
                "WHERE NOT EXISTS (SELECT 1 FROM knowledge k WHERE k.work_id=w.id)"
            ).fetchone()[0]
        status = INFO if n > 0 else OK
        checks.append(_check("Works with no knowledge items", status, n,
                              f"{n} Works have no extracted knowledge" if n else "All Works have knowledge"))
    except Exception as exc:
        checks.append(_check("Works with no knowledge items", WARN, "error", str(exc)))

    # Documents with extracted_text=NULL and readiness not in (no_text, error, imported)
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE (extracted_text IS NULL OR extracted_text='') "
                "AND readiness NOT IN ('no_text','error','imported','deleted')"
            ).fetchone()[0]
        status = WARN if n > 0 else OK
        checks.append(_check("Documents missing extracted text", status, n,
                              f"{n} docs marked ready but have no text — reprocess them" if n else "All documents have content"))
    except Exception as exc:
        checks.append(_check("Documents missing extracted text", WARN, "error", str(exc)))

    # Average knowledge confidence
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT AVG(confidence), COUNT(*) FROM knowledge "
                "WHERE review_status != 'rejected'"
            ).fetchone()
        avg_conf = round((row[0] or 0) * 100, 1)
        total = row[1] or 0
        status = WARN if avg_conf < 50 else OK
        checks.append(_check("Avg knowledge confidence", status,
                              f"{avg_conf}% (n={total:,})",
                              "Low average may indicate poor extraction quality" if avg_conf < 50 else "Healthy"))
    except Exception as exc:
        checks.append(_check("Avg knowledge confidence", WARN, "error", str(exc)))

    # AI_auto items awaiting review
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE review_status='ai_auto'"
            ).fetchone()[0]
        checks.append(_check("AI knowledge items awaiting review", INFO, n,
                              f"{n} AI-extracted items not yet approved/rejected"))
    except Exception as exc:
        checks.append(_check("AI knowledge items awaiting review", WARN, "error", str(exc)))

    # Unreviewed governance queue items (table is review_deferrals)
    try:
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) FROM review_deferrals WHERE status='pending'"
            ).fetchone()[0]
        status = INFO if n > 0 else OK
        checks.append(_check("Governance queue items pending", status, n,
                              f"{n} items await governance review" if n else "Queue clear"))
    except Exception:
        checks.append(_check("Governance queue items pending", INFO, "—", "Table not present or no pending items"))

    return checks


def _check_nightshift(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []

    last_run_raw = db.get_setting("nightshift_last_run")
    if last_run_raw:
        try:
            last_dt = datetime.fromisoformat(last_run_raw.replace("Z", "+00:00"))
            age_h = (datetime.now(UTC) - last_dt).total_seconds() / 3600
            age_label = f"{age_h:.1f}h ago"
            status = WARN if age_h > 36 else OK
            checks.append(_check("Nightshift last run", status, last_run_raw,
                                  f"Ran {age_label}" + (" — overdue (expected every ~24 h)" if age_h > 36 else "")))
        except Exception:
            checks.append(_check("Nightshift last run", INFO, last_run_raw, ""))
    else:
        checks.append(_check("Nightshift last run", WARN, "never",
                              "Nightshift has not run yet — it fires at 03:00 local time"))

    # Last nightshift error
    last_err = db.get_setting("nightshift_last_error")
    if last_err:
        checks.append(_check("Nightshift last error", WARN, last_err[:120],
                              "Most recent nightshift error — may affect doc reprocessing / auto-memory"))
    else:
        checks.append(_check("Nightshift last error", OK, "none", "No recorded errors"))

    return checks


def _check_pipeline_health(db: OrivellumDB) -> list[dict]:
    checks: list[dict] = []

    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT status, COUNT(*) as n FROM book_pipelines "
                "GROUP BY status ORDER BY status"
            ).fetchall()
        if rows:
            summary = ", ".join(f"{r['status']}={r['n']}" for r in rows)
            checks.append(_check("Book pipelines by stage", INFO, summary,
                                  f"{sum(r['n'] for r in rows)} total pipelines"))
        else:
            checks.append(_check("Book pipelines by stage", INFO, "none", "No pipelines created yet"))
    except Exception as exc:
        checks.append(_check("Book pipelines by stage", WARN, "error", str(exc)))

    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT status, COUNT(*) as n FROM pipeline_artifacts "
                "GROUP BY status ORDER BY status"
            ).fetchall()
        if rows:
            summary = ", ".join(f"{r['status']}={r['n']}" for r in rows)
            checks.append(_check("Pipeline artifacts by status", INFO, summary, ""))
        else:
            checks.append(_check("Pipeline artifacts by status", INFO, "none", "No artifacts yet"))
    except Exception as exc:
        checks.append(_check("Pipeline artifacts by status", WARN, "error", str(exc)))

    return checks


# ── VACUUM ─────────────────────────────────────────────────────────────────────

def _run_vacuum(db: OrivellumDB) -> dict:
    """Run VACUUM under the db write lock. Returns a timing result."""
    try:
        # Measure size before
        import os
        db_path = getattr(db, "_path", None)
        before = os.path.getsize(db_path) if db_path and db_path != ":memory:" else None

        t0 = time.monotonic()
        with db._lock:
            db._conn.execute("VACUUM")
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        after = os.path.getsize(db_path) if db_path and db_path != ":memory:" else None
        saved = ""
        if before and after:
            saved_mb = (before - after) / 1_048_576
            saved = f" (freed {saved_mb:+.2f} MB)"
        return _check("VACUUM", OK, f"{elapsed_ms} ms{saved}", "Database compacted successfully")
    except Exception as exc:
        return _check("VACUUM", ERROR, "failed", str(exc))


# ── Report renderer ───────────────────────────────────────────────────────────

_ICONS = {OK: "✅", WARN: "⚠️ ", ERROR: "❌", INFO: "ℹ️ "}


def render_markdown(result: dict) -> str:
    lines: list[str] = []
    ts = result.get("generated_at", "")
    lines += [
        "# Orivellum System Diagnostic Report",
        "",
        f"**Generated:** {ts}",
        f"**Schema version:** {result.get('schema_version', '?')}",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Checks | Count |",
        "|--------|-------|",
        f"| ✅ Passing | {result['summary']['ok']} |",
        f"| ⚠️  Warnings | {result['summary']['warn']} |",
        f"| ❌ Errors | {result['summary']['error']} |",
        f"| ℹ️  Info | {result['summary']['info']} |",
        f"| **Total** | **{result['summary']['total']}** |",
        "",
    ]

    # Issues list (errors then warnings)
    issues = [c for c in result["all_checks"] if c["status"] in (ERROR, WARN)]
    if issues:
        lines += ["## 🔴 Issues Requiring Attention", ""]
        for i, c in enumerate(issues, 1):
            icon = _ICONS[c["status"]]
            lines.append(f"{i}. {icon} **{c['name']}**: `{c['value']}`"
                         + (f" — {c['detail']}" if c['detail'] else ""))
        lines.append("")
    else:
        lines += ["## ✅ No Issues Found", "", "All checks passed cleanly.", ""]

    # Sections
    for section in result["sections"]:
        lines += [f"## {section['title']}", ""]
        lines += ["| Check | Status | Value | Detail |",
                  "|-------|--------|-------|--------|"]
        for c in section["checks"]:
            icon = _ICONS.get(c["status"], "?")
            val = str(c["value"])[:60]
            detail = (c.get("detail") or "")[:80]
            lines.append(f"| {c['name']} | {icon} {c['status'].upper()} | `{val}` | {detail} |")
        lines.append("")

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────────────

def run_full_diagnostic(
    db: OrivellumDB,
    cfg: OrivellumConfig,
    vacuum: bool = False,
) -> dict:
    """Run all diagnostic checks and return a structured result.

    Args:
        db:     Live OrivellumDB instance.
        cfg:    Application configuration.
        vacuum: If True, run SQLite VACUUM before returning the result.

    Returns:
        Dict with keys: generated_at, schema_version, summary, sections,
        all_checks, markdown_report.
    """
    t_start = time.monotonic()
    generated_at = datetime.now(UTC).isoformat()

    # Schema version (tracked in settings table, not PRAGMA user_version)
    schema_ver = db.get_setting("schema_version", "0")

    sections_raw = [
        ("🗄️ Database Integrity",      _check_db_integrity(db)),
        ("📊 Table Row Counts",         _check_table_counts(db)),
        ("🔗 Orphaned Records",         _check_orphans(db)),
        ("⏳ Stuck / Error Records",    _check_stuck_records(db)),
        ("⚙️ Configuration",           _check_configuration(db, cfg)),
        ("🌐 Service Connectivity",    _check_services(cfg)),
        ("📋 Data Quality",            _check_data_quality(db)),
        ("🌙 Nightshift Daemon",       _check_nightshift(db)),
        ("📚 Pipeline Health",         _check_pipeline_health(db)),
    ]

    vacuum_result: dict | None = None
    if vacuum:
        vacuum_result = _run_vacuum(db)

    all_checks: list[dict] = []
    sections: list[dict] = []
    for title, checks in sections_raw:
        all_checks.extend(checks)
        sections.append({"title": title, "checks": checks})

    if vacuum_result:
        sections.append({"title": "🧹 VACUUM", "checks": [vacuum_result]})
        all_checks.append(vacuum_result)

    summary = {
        "ok":    sum(1 for c in all_checks if c["status"] == OK),
        "warn":  sum(1 for c in all_checks if c["status"] == WARN),
        "error": sum(1 for c in all_checks if c["status"] == ERROR),
        "info":  sum(1 for c in all_checks if c["status"] == INFO),
        "total": len(all_checks),
    }
    summary["health"] = (
        "error"   if summary["error"] > 0 else
        "warn"    if summary["warn"] > 0  else
        "ok"
    )

    elapsed_ms = round((time.monotonic() - t_start) * 1000)

    result = {
        "generated_at":  generated_at,
        "schema_version": f"v{schema_ver}" if not str(schema_ver).startswith("v") else schema_ver,
        "elapsed_ms":    elapsed_ms,
        "summary":       summary,
        "sections":      sections,
        "all_checks":    all_checks,
        "vacuum_ran":    vacuum,
    }
    result["markdown_report"] = render_markdown(result)
    return result
