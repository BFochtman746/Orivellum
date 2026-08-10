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
_jobs: deque[dict] = deque(maxlen=_MAX_JOBS)

# Maximum number of times a single job may be (re-)submitted, including the
# original attempt.  A manual retry past this cap is refused with a clear
# message (FA-06 attempt cap).
_MAX_ATTEMPTS = 5

# Bounded fallback: when the pool refuses a submit we do NOT spawn an untracked,
# unbounded daemon thread (FA-06).  Instead we run a small, capped pool of
# tracked fallback threads.  Work beyond the cap is rejected so the caller can
# surface a failure rather than silently over-spawning.
_MAX_FALLBACK_THREADS = 2
_fallback_lock = threading.Lock()
_fallback_active = 0

# Durable-record hook — set at startup so executor jobs persist a minimal row
# (id, kind, state, attempts) for restart reconciliation.  Kept as a callable
# indirection so the executor module has no hard import dependency on the DB
# layer and stays importable in isolation (e.g. unit tests).
_db_provider = None  # type: ignore[var-annotated]


def set_db_provider(provider) -> None:
    """Register a zero-arg callable returning the OrivellumDB for durability.

    Called once from the app lifespan.  When unset (e.g. in unit tests) the
    durable-record calls become no-ops.
    """
    global _db_provider
    _db_provider = provider


def _durable(method: str, *args, **kwargs) -> None:
    """Best-effort call to a bg_job_* method on the DB.  Never raises."""
    if _db_provider is None:
        return
    try:
        db = _db_provider()
        getattr(db, method)(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - durability is best-effort
        logger.debug("durable %s failed: %s", method, exc)


def reconcile_orphans() -> int:
    """Mark durable jobs left running/queued by a prior process as failed.

    Called once at startup.  Returns the number of rows reconciled.
    """
    if _db_provider is None:
        return 0
    try:
        db = _db_provider()
        n = db.bg_job_reconcile_orphans()
        if n:
            logger.info("Reconciled %d orphaned background job(s) from prior run", n)
        return n
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("Orphan reconciliation failed: %s", exc)
        return 0


def _job_entry(kind: str, label: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "label": label,
        "state": "running",  # running | done | failed
        "attempts": 1,  # submissions so far, including this one (FA-06 cap)
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


def _tracked_submit(
    fn, *args, kind: str = "background", label: str = "", _attempts: int = 1, **kwargs
) -> Future:
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
    entry["attempts"] = _attempts
    # Stash callable + args so retry can re-submit identically.
    entry["_retry_fn"] = fn
    entry["_retry_args"] = args
    entry["_retry_kwargs"] = kwargs

    job_id = entry["id"]

    def _wrapped():
        try:
            result = fn(*args, **kwargs)
            with _jobs_lock:
                entry["state"] = "done"
                entry["finished_at"] = time.time()
            _durable("bg_job_set_state", job_id, "done")
            return result
        except Exception as exc:
            logger.exception(
                "Background job failed (kind=%s, label=%s)",
                entry["kind"],
                entry["label"],
            )
            with _jobs_lock:
                entry["state"] = "failed"
                entry["finished_at"] = time.time()
                entry["error"] = str(exc)[:300]
            _durable("bg_job_set_state", job_id, "failed", error=str(exc)[:300])
            raise

    # Register + persist BEFORE submit(): a fast job could otherwise publish
    # its terminal state first, and a later "running" upsert would resurrect
    # it — after a restart the row would be falsely reconciled as orphaned.
    # If submit() fails, the except block flips this same entry/row to failed,
    # so nothing lingers as permanently ``running``.
    with _jobs_lock:
        _jobs.append(entry)
    _durable(
        "bg_job_upsert",
        job_id,
        kind=entry["kind"],
        label=entry["label"],
        state="running",
        attempts=entry["attempts"],
    )
    try:
        return get_executor().submit(_wrapped)
    except Exception as exc:
        # Submission failed (executor shut down, etc.) — mark the already
        # registered entry + durable row as failed.
        with _jobs_lock:
            entry["state"] = "failed"
            entry["finished_at"] = time.time()
            entry["error"] = f"submit_failed: {exc!s}"[:300]
        _durable("bg_job_set_state", job_id, "failed", error=entry["error"])
        raise


def _run_fallback_tracked(fn, args, kwargs, kind: str, label: str) -> bool:
    """Run work in a small, capped pool of TRACKED fallback threads.

    Returns True if a fallback thread was started, False if the fallback pool
    is already saturated (caller should surface a failure).  Never spawns an
    untracked or unbounded thread — the concurrent fallback count is hard-capped
    at ``_MAX_FALLBACK_THREADS`` (FA-06).
    """
    global _fallback_active
    with _fallback_lock:
        if _fallback_active >= _MAX_FALLBACK_THREADS:
            return False
        _fallback_active += 1

    entry = _job_entry(kind, (label or getattr(fn, "__name__", "job")) + " (fallback)")
    entry["_retry_fn"] = fn
    entry["_retry_args"] = args
    entry["_retry_kwargs"] = kwargs
    job_id = entry["id"]
    with _jobs_lock:
        _jobs.append(entry)
    _durable(
        "bg_job_upsert",
        job_id,
        kind=entry["kind"],
        label=entry["label"],
        state="running",
        attempts=1,
    )

    def _runner():
        global _fallback_active
        try:
            fn(*args, **kwargs)
            with _jobs_lock:
                entry["state"] = "done"
                entry["finished_at"] = time.time()
            _durable("bg_job_set_state", job_id, "done")
        except Exception as exc:
            logger.exception("Fallback background job failed (kind=%s)", kind)
            with _jobs_lock:
                entry["state"] = "failed"
                entry["finished_at"] = time.time()
                entry["error"] = str(exc)[:300]
            _durable("bg_job_set_state", job_id, "failed", error=str(exc)[:300])
        finally:
            with _fallback_lock:
                _fallback_active -= 1

    t = threading.Thread(target=_runner, daemon=True, name=f"orivellum-fallback-{kind}")
    t.start()
    return True


def submit_bg(fn, *args, kind: str = "background", label: str = "", **kwargs) -> None:
    """Fire-and-forget background submit — the preferred replacement for bare
    ``threading.Thread(daemon=True).start()`` calls throughout the codebase.

    Uses the tracked executor when available so the job appears in the dashboard
    and can be retried.  If the pool refuses the submit (shut down / queue full)
    the work runs in a small, hard-capped pool of TRACKED fallback threads —
    never an untracked, unbounded daemon thread (FA-06).  When even that pool is
    saturated the failure is logged and the work is dropped (a ``failed`` job
    row records it); ``submit_bg`` still **never raises** so callers need no
    try/except wrapper.

    Usage::

        from orivellum.api.executor import submit_bg
        submit_bg(my_fn, arg1, arg2, kind="pipeline", label="my_fn")
    """
    try:
        _tracked_submit(fn, *args, kind=kind, label=label, **kwargs)
    except Exception as exc:
        logger.warning(
            "executor submit failed (%s), using bounded fallback: %s",
            getattr(fn, "__name__", "?"),
            exc,
        )
        try:
            started = _run_fallback_tracked(fn, args, kwargs, kind, label)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Fallback dispatch itself failed (kind=%s)", kind)
            started = False
        if not started:
            logger.error(
                "Rejected background work (kind=%s, label=%s): executor pool "
                "unavailable and fallback pool saturated (max=%d)",
                kind,
                label,
                _MAX_FALLBACK_THREADS,
            )


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
    """Re-submit a failed job by its id — with an atomic claim (FA-06).

    The check ("is this job failed?") and the claim (flip failed→queued) are
    performed together under ``_jobs_lock`` so two rapid retries of the same
    ``failed`` entry cannot both win: only the caller that flips the state
    proceeds to re-submit; the loser sees the entry is no longer ``failed`` and
    raises ``ValueError`` (mapped to 409).

    Raises:
        KeyError:  if no job with that id is found.
        ValueError: if the job is not in state ``failed`` (cannot retry a
                    running/queued/done job — the atomic claim already ran).
        RuntimeError: if the job has no stored callable (e.g. it was registered
                      before retry support was added or the entry was evicted),
                      or the attempt cap has been reached.
    """
    # ── Atomic claim: check + flip under one lock. ────────────────────────────
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
        attempts_so_far = entry.get("attempts", 1)
        if attempts_so_far >= _MAX_ATTEMPTS:
            raise RuntimeError(
                f"Job {job_id!r} has reached the retry cap "
                f"({attempts_so_far}/{_MAX_ATTEMPTS} attempts) and will not be "
                "retried again"
            )
        # Claim it: flip state so a concurrent retry sees a non-'failed' entry
        # and loses the race.  We capture the args while holding the lock.
        entry["state"] = "queued"
        retry_args = tuple(entry.get("_retry_args", ()))
        retry_kwargs = dict(entry.get("_retry_kwargs", {}))
        retry_kind = entry["kind"]
        retry_label = entry["label"]
        next_attempts = attempts_so_far + 1

    return _tracked_submit(
        fn,
        *retry_args,
        kind=retry_kind,
        label=f"{retry_label} (retry)",
        _attempts=next_attempts,
        **retry_kwargs,
    )


def init(max_workers: int = _DEFAULT_WORKERS) -> ThreadPoolExecutor:
    """Create and register the shared executor.  Called once at app startup.

    On first init we also wire the durable-record provider (so executor jobs
    persist a minimal row) and reconcile any durable jobs left ``running`` /
    ``queued`` by a crashed prior process (FA-06 restart reconciliation).  Both
    are best-effort and never block startup.
    """
    global _executor
    if _executor is not None:
        return _executor
    _executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="orivellum-bg",
    )
    logger.info("Background executor started (max_workers=%d)", max_workers)

    # Wire durability + reconcile orphans from a previous process.  Guarded so
    # the executor stays importable/usable in unit tests without a DB.
    try:
        from orivellum.api import _deps as _deps_mod

        set_db_provider(_deps_mod.get_db)
        reconcile_orphans()
    except Exception as exc:  # pragma: no cover - best-effort startup wiring
        logger.warning("Executor durability wiring failed (non-fatal): %s", exc)

    return _executor


def get_executor() -> ThreadPoolExecutor:
    """Return the shared executor, creating it lazily if necessary."""
    global _executor
    if _executor is None:
        _executor = init()
    return _executor


def shutdown(wait: bool = True, drain_timeout: float | None = None) -> None:
    """Shut down the executor cleanly.  Called once at app shutdown.

    When ``drain_timeout`` is set, perform a brief bounded drain (FA-06): give
    in-flight work up to ``drain_timeout`` seconds to finish, then stop waiting
    and cancel any still-queued futures so shutdown never hangs indefinitely.
    ``wait=True`` with no ``drain_timeout`` retains the old blocking behaviour
    (used by tests).
    """
    global _executor
    if _executor is None:
        return

    if drain_timeout is not None:
        logger.info("Draining background executor (timeout=%.1fs)", drain_timeout)
        ex = _executor
        _executor = None
        # Cancel queued-but-not-started work immediately; running futures get a
        # bounded grace period via a watchdog thread so we never block forever.
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # pragma: no cover - Python <3.9 lacks cancel_futures
            ex.shutdown(wait=False)
        drainer = threading.Thread(
            target=lambda: ex.shutdown(wait=True),
            daemon=True,
            name="orivellum-bg-drain",
        )
        drainer.start()
        drainer.join(timeout=drain_timeout)
        if drainer.is_alive():
            logger.warning(
                "Executor drain exceeded %.1fs — abandoning remaining in-flight "
                "work to avoid blocking shutdown",
                drain_timeout,
            )
        return

    logger.info("Shutting down background executor (wait=%s)", wait)
    _executor.shutdown(wait=wait)
    _executor = None
