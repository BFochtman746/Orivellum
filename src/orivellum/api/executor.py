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
        # Internal retry support — stripped before the entry is returned to the
        # API client.  Storing the callable + args lets the retry endpoint
        # re-submit the same work without callers needing to register handlers.
        "_retry_fn": None,
        "_retry_args": (),
        "_retry_kwargs": {},
    }


def _tracked_submit(fn, *args, kind: str = "background", label: str = "", **kwargs) -> Future:
    """Submit work and record a job entry for the dashboard.

    The registry entry is created AFTER a successful submit() call so that a
    submission failure (e.g. executor shutdown race) never leaves a permanently
    ``running`` entry in the dashboard.  If submit() raises, the entry is
    pre-marked ``failed`` and then appended so callers can inspect it, but it
    will not linger as ``running``.

    The callable + arguments are stored internally on the entry so that
    ``retry_job()`` can re-dispatch the same work when the user clicks Retry
    on a failed job in the dashboard.  These private fields are stripped from
    the API response by ``get_recent_jobs``.
    """
    entry = _job_entry(kind, label or getattr(fn, "__name__", "job"))
    # Stash callable + args so retry can re-submit identically.
    entry["_retry_fn"] = fn
    entry["_retry_args"] = args
    entry["_retry_kwargs"] = kwargs

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

    try:
        future = get_executor().submit(_wrapped)
        # Only register the entry after successful submission so the dashboard
        # never shows a permanently-running ghost job.
        with _jobs_lock:
            _jobs.append(entry)
        return future
    except Exception as exc:
        # Submission failed (executor shut down, etc.) — mark entry failed and
        # register it so the caller's except block can see it in the dashboard.
        entry["state"] = "failed"
        entry["finished_at"] = time.time()
        entry["error"] = f"submit_failed: {exc!s}"[:300]
        with _jobs_lock:
            _jobs.append(entry)
        raise


def submit_bg(fn, *args, kind: str = "background", label: str = "", **kwargs) -> None:
    """Fire-and-forget background submit — the preferred replacement for bare
    ``threading.Thread(daemon=True).start()`` calls throughout the codebase.

    Uses the tracked executor when available so the job appears in the dashboard
    and can be retried.  Falls back to a daemon thread if the executor is shut
    down or the submission queue is full.  **Never raises** — callers do not need
    a try/except wrapper.

    Usage::

        from orivellum.api.executor import submit_bg
        submit_bg(my_fn, arg1, arg2, kind="pipeline", label="my_fn")
    """
    try:
        _tracked_submit(fn, *args, kind=kind, label=label, **kwargs)
    except Exception as exc:
        logger.warning(
            "executor submit failed (%s), falling back to thread: %s",
            getattr(fn, "__name__", "?"), exc,
        )
        t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
        t.start()


def _public_entry(entry: dict) -> dict:
    """Return a copy of a job entry with private (_-prefixed) fields removed."""
    return {k: v for k, v in entry.items() if not k.startswith("_")}


def get_recent_jobs(limit: int = 50) -> list[dict]:
    """Return most recent jobs (newest first) for the dashboard endpoint.

    Private fields (``_retry_fn`` etc.) are stripped so callables are never
    serialised to JSON.
    """
    with _jobs_lock:
        items = list(_jobs)
    items.sort(key=lambda j: j["started_at"], reverse=True)
    return [_public_entry(j) for j in items[:limit]]


def retry_job(job_id: str) -> Future:
    """Re-submit a failed job by its id.

    Finds the job entry in the in-memory registry, resets its state to
    ``running``, and submits it again via ``_tracked_submit``.

    Raises:
        KeyError:  if no job with that id is found.
        ValueError: if the job is not in state ``failed`` (cannot retry a
                    running or done job).
        RuntimeError: if the job has no stored callable (e.g. it was registered
                      before retry support was added or the entry was evicted).
    """
    with _jobs_lock:
        entry = next((j for j in _jobs if j["id"] == job_id), None)

    if entry is None:
        raise KeyError(f"Job {job_id!r} not found in dashboard registry")
    if entry["state"] != "failed":
        raise ValueError(
            f"Job {job_id!r} is in state {entry['state']!r}; only 'failed' jobs can be retried"
        )
    fn = entry.get("_retry_fn")
    if fn is None:
        raise RuntimeError(
            f"Job {job_id!r} has no stored callable — it may pre-date retry support"
        )

    retry_args = entry.get("_retry_args", ())
    retry_kwargs = entry.get("_retry_kwargs", {})
    retry_label = entry["label"] if not entry["label"].endswith(" (retry)") else entry["label"]
    return _tracked_submit(
        fn, *retry_args,
        kind=entry["kind"],
        label=f"{retry_label} (retry)",
        **retry_kwargs,
    )


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
