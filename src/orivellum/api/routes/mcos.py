"""MCOS routes — /api/mcos/*

Benchmark listing/seeding, run orchestration (via FastAPI BackgroundTasks),
run history + detail, and LLM-gateway telemetry.  Auth is handled by the
global middleware in app.py, same as every other router.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel

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
    """Avg_score of the most recent finished NORMAL run strictly before this one.

    Prompt A/B runs (meta.prompt_id set) are excluded so they never become a
    regression baseline nor are compared against normal runs.
    """
    with db._lock:
        row = db._conn.execute(
            "SELECT avg_score FROM eval_runs WHERE benchmark_id=? AND status='done' "
            "AND id != ? AND avg_score IS NOT NULL AND started_at < ? "
            "AND (NOT json_valid(meta) OR json_extract(meta, '$.prompt_id') IS NULL) "
            "ORDER BY finished_at DESC, started_at DESC LIMIT 1",
            (benchmark_id, exclude_run_id, before_started_at),
        ).fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _run_row_to_dict(db, row) -> dict:
    """Serialize an eval_runs row, computing meta.delta/regressed on the fly."""
    d = dict(row)
    meta = _jload(d.get("meta"), {}) or {}
    avg = d.get("avg_score")
    # Prompt A/B runs are attribution-only: they are never compared against
    # normal-run baselines, so keep their stored meta as-is (delta=None /
    # regressed=false) rather than recomputing a spurious regression.
    if meta.get("prompt_id"):
        meta.setdefault("delta", None)
        meta.setdefault("regressed", False)
    elif avg is not None:
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
    """List finished runs flagged as regressions.

    Two kinds: normal benchmark regressions (meta.regressed=true, non-prompt
    runs) and nightly prompt-health regressions
    (meta.prompt_health_regressed=true).  Prompt rows carry a ``kind='prompt'``
    plus the prompt's name/version.
    """
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            "SELECT r.id, r.benchmark_id, r.finished_at, r.avg_score, r.meta, "
            "b.name AS benchmark_name FROM eval_runs r "
            "JOIN benchmarks b ON b.id = r.benchmark_id "
            "WHERE r.status='done' AND json_valid(r.meta) AND ("
            "  (json_extract(r.meta, '$.regressed')=1 "
            "     AND json_extract(r.meta, '$.prompt_id') IS NULL) "
            "  OR json_extract(r.meta, '$.prompt_health_regressed')=1) "
            "ORDER BY r.finished_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out = []
    prompt_name_cache: dict[str, dict] = {}
    for r in rows:
        meta = _jload(r["meta"], {}) or {}
        is_prompt = meta.get("prompt_health_regressed") is True
        entry = {
            "run_id": r["id"],
            "benchmark_id": r["benchmark_id"],
            "benchmark_name": r["benchmark_name"],
            "finished_at": r["finished_at"],
            "avg_score": r["avg_score"],
            "delta": meta.get("delta"),
            "acknowledged": meta.get("ack") is True,
            "kind": "prompt" if is_prompt else "benchmark",
        }
        if is_prompt:
            pid = meta.get("prompt_id")
            pinfo = prompt_name_cache.get(pid)
            if pinfo is None and pid:
                with db._lock:
                    prow = db._conn.execute(
                        "SELECT name, version FROM prompts WHERE id=?", (pid,)
                    ).fetchone()
                pinfo = dict(prow) if prow else {}
                prompt_name_cache[pid] = pinfo
            entry["prompt_name"] = (pinfo or {}).get("name")
            # Prefer the version recorded on the run (may differ from current).
            entry["prompt_version"] = meta.get("prompt_version") or \
                (pinfo or {}).get("version")
            # Slot lets governance tell apart chat.base vs harvest.extract, etc.
            entry["prompt_slot"] = meta.get("prompt_slot")
        out.append(entry)
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
            "AND (json_extract(meta, '$.regressed')=1 "
            "     OR json_extract(meta, '$.prompt_health_regressed')=1)",
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


# ── Phase 4: prompt registry ─────────────────────────────────────────────────

class PromptCreate(BaseModel):
    slot: str
    name: str
    content: str
    notes: str | None = None


class RagApply(BaseModel):
    target_words: int
    overlap_words: int
    reprocess_library: bool = False


def _prompt_dict(db, row) -> dict:
    """Serialize a prompts row, attaching last_benchmark aggregate if present."""
    d = dict(row)
    return {
        "id": d["id"],
        "slot": d["slot"],
        "name": d["name"],
        "content": d["content"],
        "version": d["version"],
        "active": bool(d["active"]),
        "created_at": d["created_at"],
        "notes": d.get("notes"),
        "last_benchmark": _prompt_benchmark_status(db, d["id"], quiet=True),
    }


@router.get("/prompts")
def list_prompts(slot: str | None = None):
    db = get_db()
    q = "SELECT * FROM prompts"
    args: list = []
    if slot:
        q += " WHERE slot=?"
        args.append(slot)
    q += " ORDER BY slot, version DESC"
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()
    return {"prompts": [_prompt_dict(db, r) for r in rows]}


@router.get("/prompts/slots")
def list_prompt_slots():
    """Enumerate the known prompt slots with label + benchmarkability + the
    active prompt's name/version and total prompt count per slot."""
    from orivellum.capabilities.mcos import PROMPT_SLOTS
    db = get_db()
    out = []
    for slot, meta in PROMPT_SLOTS.items():
        with db._lock:
            count = db._conn.execute(
                "SELECT COUNT(*) FROM prompts WHERE slot=?", (slot,)
            ).fetchone()[0]
            active = db._conn.execute(
                "SELECT name, version FROM prompts WHERE slot=? AND active=1 LIMIT 1",
                (slot,),
            ).fetchone()
        out.append({
            "slot": slot,
            "label": meta["label"],
            "benchmarkable": meta["benchmarkable"],
            "active_name": active["name"] if active else None,
            "active_version": active["version"] if active else None,
            "prompt_count": int(count),
        })
    return {"slots": out}


@router.post("/prompts")
def create_prompt(body: PromptCreate):
    import uuid
    from datetime import datetime, timezone
    from orivellum.capabilities.mcos import PROMPT_SLOTS
    db = get_db()
    slot = body.slot.strip()
    name = body.name.strip()
    content = body.content
    if not slot or not name or not content:
        raise HTTPException(status_code=400, detail="slot, name and content are required")
    if slot not in PROMPT_SLOTS:
        raise HTTPException(status_code=400, detail=f"Unknown slot: {slot}")
    with db._lock:
        row = db._conn.execute(
            "SELECT COALESCE(MAX(version),0) AS mv FROM prompts WHERE slot=?", (slot,)
        ).fetchone()
        version = int(row["mv"]) + 1
        pid = str(uuid.uuid4())
        created = datetime.now(timezone.utc).isoformat()
        db._conn.execute(
            "INSERT INTO prompts(id,slot,name,content,version,active,notes,created_at)"
            " VALUES(?,?,?,?,?,0,?,?)",
            (pid, slot, name, content, version, body.notes, created),
        )
        db._conn.commit()
        new_row = db._conn.execute("SELECT * FROM prompts WHERE id=?", (pid,)).fetchone()
    return {"prompt": _prompt_dict(db, new_row)}


def _get_prompt(db, prompt_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    return dict(row) if row else None


@router.post("/prompts/{prompt_id}/benchmark")
def benchmark_prompt(prompt_id: str, background_tasks: BackgroundTasks):
    import uuid
    from datetime import datetime, timezone
    from orivellum.capabilities import mcos
    db = get_db()
    cfg = get_config()
    prompt = _get_prompt(db, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="Prompt not found")
    slot = prompt["slot"]
    # Only the chat persona can be benchmarked as a system preamble.
    if not mcos.PROMPT_SLOTS.get(slot, {}).get("benchmarkable"):
        raise HTTPException(status_code=400, detail="This slot cannot be benchmarked")
    # Reap stale prompt runs (>30 min) before the 409 check so a crashed worker
    # doesn't wrongly 409 a fresh request.
    _reap_stale_prompt_runs(db, slot)

    model = ""
    try:
        model = cfg.serving.workhorse_model or ""
    except Exception:
        model = ""
    suites = mcos._enabled_llm_benchmarks(db)

    # Race-safe: the running-check AND all paired eval_runs reservations happen
    # inside ONE lock, so two concurrent requests cannot both start pairs — the
    # loser sees the winner's freshly-inserted running rows and gets a 409.
    candidate_runs: list[str] = []
    active_runs: list[str] = []
    plan: list[dict] = []  # worker execution plan (run_id, content, meta)
    now = datetime.now(timezone.utc).isoformat()
    with db._lock:
        busy = db._conn.execute(
            "SELECT 1 FROM eval_runs WHERE status='running' AND json_valid(meta) "
            "AND json_extract(meta,'$.prompt_slot')=? LIMIT 1",
            (slot,),
        ).fetchone()
        if busy is not None:
            raise HTTPException(
                status_code=409,
                detail="A prompt benchmark for this slot is already running")

        active = db._conn.execute(
            "SELECT id, content, version FROM prompts WHERE slot=? AND active=1 LIMIT 1",
            (slot,),
        ).fetchone()
        active = dict(active) if active else None

        def _reserve(content, meta):
            rid = str(uuid.uuid4())
            n_cases = db._conn.execute(
                "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id=?",
                (meta["benchmark_id"],),
            ).fetchone()[0]
            db._conn.execute(
                "INSERT INTO eval_runs(id,benchmark_id,started_at,model,status,"
                "total_cases,meta) VALUES(?,?,?,?,'running',?,?)",
                (rid, meta["benchmark_id"], now, model, int(n_cases), _dumps(meta)),
            )
            plan.append({"run_id": rid, "content": content, "meta": meta})
            return rid

        for suite in suites:
            bid = suite["id"]
            c_meta = {"benchmark_id": bid, "prompt_id": prompt_id,
                      "prompt_version": prompt["version"],
                      "prompt_role": "candidate", "prompt_slot": slot}
            candidate_runs.append(_reserve(prompt["content"], c_meta))
            if active:
                a_meta = {"benchmark_id": bid, "prompt_id": prompt_id,
                          "prompt_version": active["version"],
                          "prompt_role": "active", "prompt_slot": slot,
                          "active_prompt_id": active["id"]}
                active_runs.append(_reserve(active["content"], a_meta))
        db._conn.commit()

    background_tasks.add_task(_run_prompt_pairs, db, cfg, plan)
    return {"candidate_runs": candidate_runs, "active_runs": active_runs}


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _run_prompt_pairs(db, cfg, plan):
    """Execute the pre-reserved candidate/active runs sequentially (background).

    Each plan entry carries its own run_id, system-prompt content and meta.  The
    eval_runs rows already exist (status='running'); the worker only executes and
    finalizes them, and never strands a reserved row on failure.
    """
    from orivellum.capabilities import mcos
    for entry in plan:
        rid = entry["run_id"]
        meta = entry["meta"]
        bid = meta["benchmark_id"]
        try:
            mcos._execute_run(db, cfg, bid, rid,
                              system_prompt=entry["content"], run_meta=meta)
        except Exception as exc:  # never strand a reserved row
            logger.warning("prompt run %s failed: %s", rid[:8], exc)
            mcos._finalize_run(db, rid, status="failed", avg_score=None,
                               meta={**meta, "error": str(exc)[:300]})


def _reap_stale_prompt_runs(db, slot: str) -> None:
    with db._lock:
        db._conn.execute(
            "UPDATE eval_runs SET status='failed', finished_at=datetime('now'), "
            "meta=json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
            "'$.error','stale prompt run reaped') "
            "WHERE status='running' AND json_valid(meta) "
            "AND json_extract(meta,'$.prompt_slot')=? "
            "AND started_at < datetime('now', ?)",
            (slot, f"-{_STALE_RUN_MINUTES} minutes"),
        )
        db._conn.commit()


def _prompt_benchmark_status(db, prompt_id: str, quiet: bool = False) -> dict | None:
    """Aggregate the paired runs for a prompt by role.

    Returns None when there are no prompt runs (used for last_benchmark).
    Otherwise {status, candidate:{avg,per_suite}, active:{...}, delta}.
    """
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, benchmark_id, status, avg_score, meta FROM eval_runs "
            "WHERE json_valid(meta) AND json_extract(meta,'$.prompt_id')=? "
            "ORDER BY started_at",
            (prompt_id,),
        ).fetchall()
    if not rows:
        return None

    def _role_agg(role: str) -> dict:
        per_suite = []
        avgs = []
        any_running = False
        for r in rows:
            meta = _jload(r["meta"], {}) or {}
            if meta.get("prompt_role") != role:
                continue
            if r["status"] == "running":
                any_running = True
            per_suite.append({
                "benchmark_id": r["benchmark_id"],
                "avg_score": r["avg_score"],
                "status": r["status"],
            })
            if r["avg_score"] is not None and r["status"] == "done":
                avgs.append(r["avg_score"])
        avg = (sum(avgs) / len(avgs)) if avgs else None
        return {"avg": avg, "per_suite": per_suite, "_running": any_running}

    cand = _role_agg("candidate")
    act = _role_agg("active")
    running = cand["_running"] or act["_running"]
    status = "running" if running else "done"
    delta = None
    if cand["avg"] is not None and act["avg"] is not None:
        delta = round(cand["avg"] - act["avg"], 6)
    result = {
        "status": status,
        "candidate": {"avg": cand["avg"], "per_suite": cand["per_suite"]},
        "active": {"avg": act["avg"], "per_suite": act["per_suite"]},
        "delta": delta,
    }
    return result


@router.get("/prompts/{prompt_id}/benchmark")
def get_prompt_benchmark(prompt_id: str):
    db = get_db()
    if _get_prompt(db, prompt_id) is None:
        raise HTTPException(status_code=404, detail="Prompt not found")
    status = _prompt_benchmark_status(db, prompt_id)
    if status is None:
        return {"status": "none", "candidate": {"avg": None, "per_suite": []},
                "active": {"avg": None, "per_suite": []}, "delta": None}
    return status


@router.post("/prompts/{prompt_id}/activate")
def activate_prompt(prompt_id: str):
    db = get_db()
    # Existence check AND the deactivate/activate swap all happen under ONE lock
    # so a concurrent delete cannot leave the slot with no active prompt (or
    # delete the row between our read and our activation).
    with db._lock:
        prompt = db._conn.execute(
            "SELECT slot, version FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        if prompt is None:
            raise HTTPException(status_code=404, detail="Prompt not found")
        slot = prompt["slot"]
        version = prompt["version"]
        db._conn.execute("UPDATE prompts SET active=0 WHERE slot=?", (slot,))
        cur = db._conn.execute("UPDATE prompts SET active=1 WHERE id=?", (prompt_id,))
        if cur.rowcount == 0:  # deleted between the SELECT and this UPDATE
            db._conn.rollback()
            raise HTTPException(status_code=404, detail="Prompt not found")
        db._conn.commit()
        new_row = db._conn.execute("SELECT * FROM prompts WHERE id=?", (prompt_id,)).fetchone()
    db.audit("prompt_activated", object_id=prompt_id, object_type="prompt",
             actor="mcos", detail=f"slot={slot} version={version}")
    return {"prompt": _prompt_dict(db, new_row)}


@router.delete("/prompts/{prompt_id}", status_code=204)
def delete_prompt(prompt_id: str):
    db = get_db()
    # Existence + active check + DELETE inside ONE lock/transaction so a
    # concurrent activate cannot make this row active between our check and the
    # delete (which would drop the slot's only active prompt).  The WHERE clause
    # enforces "not active"; rowcount disambiguates 404 vs 409.
    with db._lock:
        exists = db._conn.execute(
            "SELECT active FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Prompt not found")
        cur = db._conn.execute(
            "DELETE FROM prompts WHERE id=? AND active=0", (prompt_id,)
        )
        if cur.rowcount == 0:
            db._conn.rollback()
            raise HTTPException(status_code=409, detail="Cannot delete the active prompt")
        db._conn.commit()
    return Response(status_code=204)


# ── Phase 5: RAG calibration ─────────────────────────────────────────────────

_RAG_DEFAULTS = {"target_words": 500, "overlap_words": 50}
_RAG_TARGET_MIN, _RAG_TARGET_MAX = 100, 2000


@router.get("/rag/config")
def rag_config():
    db = get_db()
    try:
        target = int(db.get_setting("chunk_target_words", "500"))
    except (TypeError, ValueError):
        target = _RAG_DEFAULTS["target_words"]
    try:
        overlap = int(db.get_setting("chunk_overlap_words", "50"))
    except (TypeError, ValueError):
        overlap = _RAG_DEFAULTS["overlap_words"]
    return {"target_words": target, "overlap_words": overlap,
            "defaults": dict(_RAG_DEFAULTS)}


def _reap_stale_sweeps(db) -> None:
    """Mark sweeps stuck at 'running' for >30 min as failed (crashed worker).

    Mirrors the eval_runs stale-reap so the UI never polls a phantom sweep
    forever.
    """
    with db._lock:
        db._conn.execute(
            "UPDATE rag_sweeps SET status='failed', finished_at=datetime('now'), "
            "meta=json_set(CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
            "'$.error','stale sweep reaped') "
            "WHERE status='running' AND started_at < datetime('now', ?)",
            (f"-{_STALE_RUN_MINUTES} minutes",),
        )
        db._conn.commit()


@router.post("/rag/sweep", status_code=202)
def rag_sweep_start(background_tasks: BackgroundTasks):
    from orivellum.capabilities import mcos
    db = get_db()
    # Reap stale sweeps first, then race-safely reserve a running row: reject
    # with 409 if a non-stale sweep is already running.
    _reap_stale_sweeps(db)
    with db._lock:
        busy = db._conn.execute(
            "SELECT 1 FROM rag_sweeps WHERE status='running' LIMIT 1"
        ).fetchone()
        if busy is not None:
            raise HTTPException(status_code=409, detail="A sweep is already running")
        sweep_id = mcos.create_sweep_row(db)
    background_tasks.add_task(mcos.rag_sweep, db, sweep_id)
    return {"sweep_id": sweep_id}


@router.get("/rag/sweeps")
def rag_sweeps(limit: int = Query(5, ge=1, le=50)):
    db = get_db()
    # Reap stale sweeps so the listing reflects crashed workers as 'failed'.
    _reap_stale_sweeps(db)
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM rag_sweeps ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        meta = _jload(r["meta"], {}) or {}
        out.append({
            "id": r["id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
            "status": r["status"],
            "results": _jload(r["results"], []) or [],
            "best": meta.get("best"),
            "docs_sampled": r["docs_sampled"],
        })
    return {"sweeps": out}


@router.post("/rag/apply")
def rag_apply(body: RagApply, background_tasks: BackgroundTasks):
    db = get_db()
    target = body.target_words
    overlap = body.overlap_words
    if not isinstance(target, int) or not (_RAG_TARGET_MIN <= target <= _RAG_TARGET_MAX):
        raise HTTPException(status_code=400,
                            detail=f"target_words must be {_RAG_TARGET_MIN}-{_RAG_TARGET_MAX}")
    if not isinstance(overlap, int) or overlap < 0 or overlap > target // 2:
        raise HTTPException(status_code=400,
                            detail="overlap_words must be 0..target_words/2")
    db.set_setting("chunk_target_words", str(target), actor="mcos")
    db.set_setting("chunk_overlap_words", str(overlap), actor="mcos")

    resp = {"target_words": target, "overlap_words": overlap}
    detail = f"target={target} overlap={overlap}"
    if body.reprocess_library:
        # Reuse the shared library reprocess machinery — do NOT duplicate the
        # pipeline.  Runs in the background after this response is sent.
        from orivellum.api.routes.library import queue_library_reprocess
        summary = queue_library_reprocess(db, background_tasks, force=True)
        reprocess_started = int(summary.get("queued", 0))
        resp["reprocess_started"] = reprocess_started
        detail += f" reprocess_started={reprocess_started}"

    db.audit("rag_config_changed", object_id="chunking", object_type="setting",
             actor="mcos", detail=detail)
    return resp


@router.get("/rag/reprocess-status")
def rag_reprocess_status():
    """Docs currently mid-pipeline (non-terminal readiness) vs total docs.

    The UI polls this every 3s while ``processing > 0`` after a reprocess.
    """
    from orivellum.api.routes.library import REPROCESS_INFLIGHT_STATES
    db = get_db()
    placeholders = ",".join("?" * len(REPROCESS_INFLIGHT_STATES))
    with db._lock:
        processing = db._conn.execute(
            f"SELECT COUNT(*) FROM documents WHERE readiness IN ({placeholders})",
            REPROCESS_INFLIGHT_STATES,
        ).fetchone()[0]
        total = db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    return {"processing": int(processing), "total": int(total)}
