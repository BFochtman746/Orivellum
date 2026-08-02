"""MCOS routes — /api/mcos/*

Benchmark listing/seeding, run orchestration (via FastAPI BackgroundTasks),
run history + detail, and LLM-gateway telemetry.  Auth is handled by the
global middleware in app.py, same as every other router.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from orivellum.api._deps import get_db, get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcos")


def _jload(s, default=None):
    if s is None:
        return default
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


def _prev_finished_avg(db, benchmark_id: str, before_started_at: str,
                       exclude_run_id: str) -> float | None:
    """Avg_score of the most recent finished run strictly before this one."""
    with db._lock:
        row = db._conn.execute(
            "SELECT avg_score FROM eval_runs WHERE benchmark_id=? AND status='done' "
            "AND id != ? AND avg_score IS NOT NULL AND started_at < ? "
            "ORDER BY finished_at DESC, started_at DESC LIMIT 1",
            (benchmark_id, exclude_run_id, before_started_at),
        ).fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _run_row_to_dict(db, row) -> dict:
    """Serialize an eval_runs row, computing meta.delta/regressed on the fly."""
    d = dict(row)
    meta = _jload(d.get("meta"), {}) or {}
    avg = d.get("avg_score")
    if avg is not None:
        prev = _prev_finished_avg(db, d["benchmark_id"], d.get("started_at") or "",
                                  d["id"])
        if prev is not None:
            delta = round(float(avg) - prev, 6)
            meta["delta"] = delta
            meta["regressed"] = delta < -0.15
        else:
            meta.setdefault("delta", None)
            meta.setdefault("regressed", False)
    return {
        "id": d["id"],
        "benchmark_id": d["benchmark_id"],
        "benchmark_name": d.get("benchmark_name"),
        "started_at": d.get("started_at"),
        "finished_at": d.get("finished_at"),
        "model": d.get("model"),
        "status": d.get("status"),
        "total_cases": d.get("total_cases"),
        "avg_score": avg,
        "meta": meta,
    }


@router.get("/benchmarks")
def list_benchmarks():
    db = get_db()
    with db._lock:
        benches = [dict(r) for r in db._conn.execute(
            "SELECT * FROM benchmarks ORDER BY category, name"
        ).fetchall()]
    out = []
    for b in benches:
        with db._lock:
            case_count = db._conn.execute(
                "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id=?", (b["id"],)
            ).fetchone()[0]
            last = db._conn.execute(
                "SELECT id, avg_score, status, finished_at FROM eval_runs "
                "WHERE benchmark_id=? ORDER BY started_at DESC LIMIT 1",
                (b["id"],),
            ).fetchone()
        out.append({
            "id": b["id"],
            "name": b["name"],
            "description": b["description"],
            "category": b["category"],
            "kind": b["kind"],
            "version": b["version"],
            "enabled": bool(b["enabled"]),
            "case_count": int(case_count),
            "last_run": ({
                "id": last["id"],
                "avg_score": last["avg_score"],
                "status": last["status"],
                "finished_at": last["finished_at"],
            } if last else None),
        })
    return {"benchmarks": out}


@router.post("/seed")
def seed():
    from orivellum.capabilities.mcos import seed_default_benchmarks
    db = get_db()
    return seed_default_benchmarks(db)


# A 'running' row older than this is assumed orphaned by a crashed worker and
# is reaped (marked failed) so it never blocks new runs forever.
_STALE_RUN_MINUTES = 30


def _reap_stale_runs(db, benchmark_id: str) -> None:
    """Mark any 'running' run for this benchmark older than the stale window
    as failed, so a crashed process can't permanently block new runs."""
    cutoff = f"-{_STALE_RUN_MINUTES} minutes"
    with db._lock:
        db._conn.execute(
            "UPDATE eval_runs SET status='failed', finished_at=datetime('now'), "
            "meta=json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
            "'$.error', 'stale run reaped') "
            "WHERE benchmark_id=? AND status='running' "
            "AND started_at < datetime('now', ?)",
            (benchmark_id, cutoff),
        )
        db._conn.commit()


def _has_running_run(db, benchmark_id: str) -> bool:
    _reap_stale_runs(db, benchmark_id)
    with db._lock:
        row = db._conn.execute(
            "SELECT 1 FROM eval_runs WHERE benchmark_id=? AND status='running' LIMIT 1",
            (benchmark_id,),
        ).fetchone()
    return row is not None


@router.post("/run/{benchmark_id}")
def run_one(benchmark_id: str, background_tasks: BackgroundTasks):
    from orivellum.capabilities.mcos import run_benchmark
    db = get_db()
    cfg = get_config()
    bench = None
    with db._lock:
        bench = db._conn.execute(
            "SELECT id FROM benchmarks WHERE id=?", (benchmark_id,)
        ).fetchone()
    if bench is None:
        raise HTTPException(status_code=404, detail="Benchmark not found")
    if _has_running_run(db, benchmark_id):
        raise HTTPException(status_code=409, detail="A run is already in progress")

    # Pre-create the running row synchronously so a concurrent request 409s.
    run_id = _start_run(db, cfg, benchmark_id, background_tasks)
    return {"run_id": run_id}


def _start_run(db, cfg, benchmark_id: str, background_tasks: BackgroundTasks) -> str:
    """Reserve a running row atomically then schedule the worker.

    The eval_runs row is created here (status='running') inside the lock so the
    409 guard in run_one is race-safe; the worker then executes the cases.
    """
    import uuid
    from datetime import datetime, timezone
    from orivellum.capabilities import mcos

    run_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    model = ""
    try:
        model = cfg.serving.workhorse_model or ""
    except Exception:
        model = ""
    # Reap orphaned runs before the race-safe re-check so a crashed worker
    # doesn't wrongly 409 a fresh request.
    _reap_stale_runs(db, benchmark_id)
    with db._lock:
        # Re-check under lock to close the race window.
        busy = db._conn.execute(
            "SELECT 1 FROM eval_runs WHERE benchmark_id=? AND status='running' LIMIT 1",
            (benchmark_id,),
        ).fetchone()
        if busy is not None:
            raise HTTPException(status_code=409, detail="A run is already in progress")
        n_cases = db._conn.execute(
            "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id=?", (benchmark_id,)
        ).fetchone()[0]
        db._conn.execute(
            "INSERT INTO eval_runs(id,benchmark_id,started_at,model,status,total_cases,"
            "meta) VALUES(?,?,?,?,'running',?,'{}')",
            (run_id, benchmark_id, started, model, int(n_cases)),
        )
        db._conn.commit()

    background_tasks.add_task(mcos._execute_run, db, cfg, benchmark_id, run_id)
    return run_id


@router.post("/run-all")
def run_all(background_tasks: BackgroundTasks):
    db = get_db()
    cfg = get_config()
    with db._lock:
        benches = [r["id"] for r in db._conn.execute(
            "SELECT id FROM benchmarks WHERE enabled=1 ORDER BY category, name"
        ).fetchall()]
    started: list[str] = []
    for bid in benches:
        if _has_running_run(db, bid):
            continue
        try:
            run_id = _start_run(db, cfg, bid, background_tasks)
            started.append(run_id)
        except HTTPException:
            continue
    return {"started": started}


@router.get("/runs")
def list_runs(benchmark_id: str | None = None, limit: int = Query(20, ge=1, le=200)):
    db = get_db()
    q = ("SELECT r.*, b.name AS benchmark_name FROM eval_runs r "
         "JOIN benchmarks b ON b.id = r.benchmark_id")
    args: list = []
    if benchmark_id:
        q += " WHERE r.benchmark_id=?"
        args.append(benchmark_id)
    q += " ORDER BY r.started_at DESC LIMIT ?"
    args.append(limit)
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()
    return {"runs": [_run_row_to_dict(db, r) for r in rows]}


@router.get("/runs/{run_id}")
def run_detail(run_id: str):
    db = get_db()
    with db._lock:
        row = db._conn.execute(
            "SELECT r.*, b.name AS benchmark_name FROM eval_runs r "
            "JOIN benchmarks b ON b.id = r.benchmark_id WHERE r.id=?",
            (run_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    with db._lock:
        results = db._conn.execute(
            "SELECT e.case_id, e.score, e.judge_scores, e.response, e.latency_ms, "
            "e.error, c.question FROM eval_results e "
            "LEFT JOIN benchmark_cases c ON c.id = e.case_id "
            "WHERE e.run_id=? ORDER BY e.id",
            (run_id,),
        ).fetchall()
    out_results = [{
        "case_id": r["case_id"],
        "question": r["question"],
        "score": r["score"],
        "judge_scores": _jload(r["judge_scores"], {}),
        "response": r["response"],
        "latency_ms": r["latency_ms"],
        "error": r["error"],
    } for r in results]
    return {"run": _run_row_to_dict(db, row), "results": out_results}


@router.get("/telemetry")
def telemetry(days: int = Query(7, ge=1, le=365)):
    db = get_db()
    since = f"-{int(days)} days"
    with db._lock:
        by_purpose = db._conn.execute(
            "SELECT purpose, COUNT(*) AS calls, AVG(latency_ms) AS avg_latency_ms, "
            "SUM(COALESCE(prompt_tokens,0)) AS total_prompt_tokens, "
            "SUM(COALESCE(completion_tokens,0)) AS total_completion_tokens, "
            "AVG(CASE WHEN ok=0 THEN 1.0 ELSE 0.0 END) AS error_rate "
            "FROM llm_calls WHERE ts >= datetime('now', ?) "
            "GROUP BY purpose ORDER BY calls DESC",
            (since,),
        ).fetchall()
        daily = db._conn.execute(
            "SELECT date(ts) AS day, COUNT(*) AS calls, "
            "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS errors, "
            "AVG(latency_ms) AS avg_latency_ms "
            "FROM llm_calls WHERE ts >= datetime('now', ?) "
            "GROUP BY date(ts) ORDER BY day",
            (since,),
        ).fetchall()
    return {
        "by_purpose": [{
            "purpose": r["purpose"],
            "calls": r["calls"],
            "avg_latency_ms": r["avg_latency_ms"],
            "total_prompt_tokens": r["total_prompt_tokens"],
            "total_completion_tokens": r["total_completion_tokens"],
            "error_rate": r["error_rate"],
        } for r in by_purpose],
        "daily": [{
            "day": r["day"],
            "calls": r["calls"],
            "errors": r["errors"],
            "avg_latency_ms": r["avg_latency_ms"],
        } for r in daily],
    }


@router.get("/regressions")
def list_regressions(limit: int = Query(20, ge=1, le=200)):
    """List finished runs flagged as regressions (meta.regressed === true)."""
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            "SELECT r.id, r.benchmark_id, r.finished_at, r.avg_score, r.meta, "
            "b.name AS benchmark_name FROM eval_runs r "
            "JOIN benchmarks b ON b.id = r.benchmark_id "
            "WHERE r.status='done' "
            "AND json_valid(r.meta) AND json_extract(r.meta, '$.regressed')=1 "
            "ORDER BY r.finished_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        meta = _jload(r["meta"], {}) or {}
        out.append({
            "run_id": r["id"],
            "benchmark_id": r["benchmark_id"],
            "benchmark_name": r["benchmark_name"],
            "finished_at": r["finished_at"],
            "avg_score": r["avg_score"],
            "delta": meta.get("delta"),
            "acknowledged": meta.get("ack") is True,
        })
    return {"regressions": out}


@router.post("/regressions/{run_id}/ack")
def ack_regression(run_id: str):
    """Acknowledge a regression by setting meta.ack=true.

    404 if the run does not exist or is not flagged as a regression.
    """
    db = get_db()
    # Atomic read-modify-write: a single UPDATE using json_set adds meta.ack
    # without touching any other keys, so a concurrent _finalize_run write can
    # neither be lost nor erase meta.regressed/delta. The WHERE clause enforces
    # the "must be a regression" predicate; rowcount distinguishes 404s.
    with db._lock:
        cur = db._conn.execute(
            "UPDATE eval_runs "
            "SET meta = json_set("
            "  CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, '$.ack', json('true')"
            ") "
            "WHERE id=? AND json_valid(meta) "
            "AND json_extract(meta, '$.regressed')=1",
            (run_id,),
        )
        updated = cur.rowcount
        db._conn.commit()
        if updated == 0:
            # Disambiguate: does the run exist at all?
            exists = db._conn.execute(
                "SELECT 1 FROM eval_runs WHERE id=?", (run_id,)
            ).fetchone()
    if updated == 0:
        if exists is None:
            raise HTTPException(status_code=404, detail="Run not found")
        raise HTTPException(status_code=404, detail="Run is not a regression")
    return {"run_id": run_id, "acknowledged": True}
