"""Measurement layer routes — benchmarks, telemetry summary, retrieval eval.

  POST /api/bench/run                {kind: ttft|generation|cache, label?}
  GET  /api/bench/runs               ?kind=&limit=
  GET  /api/bench/telemetry/summary  ?hours=&purpose=
  GET  /api/bench/goldens            ?kind=
  POST /api/bench/goldens            {query, kind, relevant_ids, work_id?, notes?}
  DELETE /api/bench/goldens/{id}
  POST /api/bench/goldens/auto-seed  {n?}
  POST /api/bench/eval/retrieval     {k?, label?}

Benchmarks hit the live LLM server, so ``POST /run`` dispatches to the shared
background executor (visible on the job dashboard) and returns immediately;
results land in ``bench_runs``.  The retrieval eval is DB-local (plus the
embeddings endpoint) and runs inline in a worker thread.
"""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Body, HTTPException

from orivellum.api._deps import get_config, get_db

logger = logging.getLogger("orivellum.api.bench")

router = APIRouter(prefix="/api/bench", tags=["bench"])

# Server-side benchmark concurrency guard.  Probes stream against the live
# LLM server for up to minutes; overlapping runs would contend for executor
# workers AND corrupt each other's timings (a TTFT probe measured while a
# generation bench saturates decode is meaningless).  One bench at a time.
_bench_guard = threading.Lock()
_bench_active: dict = {"kind": None}


def _run_bench_guarded(fn, cfg, db, kind: str, label: str) -> None:
    try:
        fn(cfg, db, label=label)
    finally:
        with _bench_guard:
            _bench_active["kind"] = None


@router.post("/run")
async def start_bench(payload: dict = Body(...)):
    from orivellum.api.executor import submit_bg
    from orivellum.capabilities.bench import BENCH_KINDS

    kind = (payload.get("kind") or "").strip()
    label = (payload.get("label") or "").strip()[:100]
    fn = BENCH_KINDS.get(kind)
    if fn is None:
        raise HTTPException(400, f"kind must be one of {sorted(BENCH_KINDS)}")
    with _bench_guard:
        if _bench_active["kind"] is not None:
            raise HTTPException(
                409,
                f"A '{_bench_active['kind']}' benchmark is already running — "
                "wait for it to finish (overlapping runs corrupt timings).",
            )
        _bench_active["kind"] = kind
    db = get_db()
    cfg = get_config()
    try:
        submit_bg(_run_bench_guarded, fn, cfg, db, kind, label,
                  label=f"bench.{kind}", kind="bench")
    except Exception:
        with _bench_guard:
            _bench_active["kind"] = None
        raise
    return {"started": True, "kind": kind}


@router.get("/status")
async def bench_status():
    with _bench_guard:
        kind = _bench_active["kind"]
    return {"running": kind is not None, "kind": kind}


@router.get("/runs")
async def get_runs(kind: str | None = None, limit: int = 20):
    from orivellum.capabilities.bench import list_bench_runs

    return {"runs": list_bench_runs(get_db(), kind=kind, limit=limit)}


@router.get("/telemetry/summary")
async def get_telemetry_summary(hours: int = 24, purpose: str | None = None):
    import asyncio

    from orivellum.capabilities.bench import telemetry_summary

    return await asyncio.to_thread(
        telemetry_summary, get_db(), hours=hours, purpose=purpose
    )


# ── Golden set ────────────────────────────────────────────────────────────────

@router.get("/goldens")
async def get_goldens(kind: str | None = None):
    from orivellum.capabilities.evalset import list_goldens

    return {"goldens": list_goldens(get_db(), kind=kind)}


@router.post("/goldens")
async def create_golden(payload: dict = Body(...)):
    from orivellum.capabilities.evalset import add_golden

    try:
        golden = add_golden(
            get_db(),
            query=payload.get("query") or "",
            kind=payload.get("kind") or "chunk",
            relevant_ids=payload.get("relevant_ids") or [],
            work_id=payload.get("work_id"),
            notes=(payload.get("notes") or "")[:500],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"golden": golden}


@router.delete("/goldens/{golden_id}")
async def remove_golden(golden_id: str):
    from orivellum.capabilities.evalset import delete_golden

    if not delete_golden(get_db(), golden_id):
        raise HTTPException(404, "golden not found")
    return {"deleted": True}


@router.post("/goldens/auto-seed")
async def seed_goldens(payload: dict = Body(default={})):
    import asyncio

    from orivellum.capabilities.evalset import auto_seed_goldens

    n = payload.get("n") or 20
    try:
        n = max(1, min(int(n), 50))
    except (TypeError, ValueError):
        raise HTTPException(400, "n must be an integer")
    return await asyncio.to_thread(auto_seed_goldens, get_db(), n=n)


# ── Retrieval eval ────────────────────────────────────────────────────────────

@router.post("/eval/retrieval")
async def run_retrieval_eval(payload: dict = Body(default={})):
    import asyncio

    from orivellum.capabilities.evalset import evaluate_retrieval, list_goldens

    db = get_db()
    if not list_goldens(db):
        raise HTTPException(
            400,
            "No golden queries yet — add some or POST /api/bench/goldens/auto-seed",
        )
    k = payload.get("k") or 5
    try:
        k = max(1, min(int(k), 20))
    except (TypeError, ValueError):
        raise HTTPException(400, "k must be an integer")
    label = (payload.get("label") or "")[:100]
    return await asyncio.to_thread(evaluate_retrieval, db, k=k, label=label)
