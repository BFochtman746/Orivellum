"""Shared thread-pool executor for background work.

All fire-and-forget background tasks (document processing, embeddings,
TTS registration, image registration) submit work here instead of spawning
unlimited ``threading.Thread(daemon=True)`` threads.

Usage::

    from orivellum.api.executor import get_executor

    get_executor().submit(some_fn, arg1, arg2)

The executor is initialized by the FastAPI lifespan in ``app.py``.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Deque

logger = logging.getLogger("orivellum.executor")

# Module-level executor — None until init() is called in the lifespan.
_executor: ThreadPoolExecutor | None = None

# Default worker count: enough for concurrent uploads + TTS + embeddings
# without exhausting OS thread limits.  Tunable via ORIVELLUM_WORKERS env var.
_DEFAULT_WORKERS = 8

# ── Job registry — recent jobs for the dashboard ─────────────────────────────
# Keeps at most _MAX_JOBS entries (deque with maxlen).  Thread-safe via lock.
_MAX_JOBS = 200
_jobs_lock = threading.Lock()
_jobs: Deque[dict] = deque(maxlen=_MAX_JOBS)


def _job_entry(kind: str, label: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "label": label,
        "state": "running",  # running | done | failed
        "started_at": time.time(),
        "finished_at": None,
        "error": None,
    }


def _tracked_submit(fn, *args, kind: str = "background", label: str = "", **kwargs) -> Future:
    """Submit work and record a job entry for the dashboard."""
    entry = _job_entry(kind, label or getattr(fn, "__name__", "job"))
    with _jobs_lock:
        _jobs.append(entry)

    def _wrapped():
        try:
            result = fn(*args, **kwargs)
            with _jobs_lock:
                entry["state"] = "done"
                entry["finished_at"] = time.time()
            return result
        except Exception as exc:
            with _jobs_lock:
                entry["state"] = "failed"
                entry["finished_at"] = time.time()
                entry["error"] = str(exc)[:300]
            raise

    return get_executor().submit(_wrapped)


def get_recent_jobs(limit: int = 50) -> list[dict]:
    """Return most recent jobs (newest first) for the dashboard endpoint."""
    with _jobs_lock:
        items = list(_jobs)
    items.sort(key=lambda j: j["started_at"], reverse=True)
    return items[:limit]


def init(max_workers: int = _DEFAULT_WORKERS) -> ThreadPoolExecutor:
    """Create and register the shared executor.  Called once at app startup."""
    global _executor
    if _executor is not None:
        return _executor
    _executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="orivellum-bg",
    )
    logger.info("Background executor started (max_workers=%d)", max_workers)
    return _executor


def get_executor() -> ThreadPoolExecutor:
    """Return the shared executor, creating it lazily if necessary."""
    global _executor
    if _executor is None:
        _executor = init()
    return _executor


def shutdown(wait: bool = True) -> None:
    """Shut down the executor cleanly.  Called once at app shutdown."""
    global _executor
    if _executor is not None:
        logger.info("Shutting down background executor (wait=%s)", wait)
        _executor.shutdown(wait=wait)
        _executor = None
