"""Tests: run_intake returns promptly when web_search_synthesize stalls.

Key invariants:
  1. The ThreadPoolExecutor is shut down with wait=False — a stalled worker never
     blocks run_intake from returning.
  2. run_intake returns a valid profile (no research data) after timeout.
  3. Wall-clock: run_intake returns well before a stalling synthesis finishes.

All tests force an immediate TimeoutError from Future.result() so the suite
runs in under a second.  The patch target for the executor is
``concurrent.futures.ThreadPoolExecutor`` — the class that intake.py binds
locally via ``import concurrent.futures as _cf``.

The patch target for web_search_synthesize is
``orivellum.capabilities.websearch.web_search_synthesize`` — the canonical
location from which intake.py does its inner-function import.
"""

from __future__ import annotations

import concurrent.futures as _cf_real
import threading
import time
from unittest.mock import MagicMock, patch

# ── Minimal stubs ─────────────────────────────────────────────────────────────


def _make_db(doc: dict) -> MagicMock:
    db = MagicMock()
    db.get_document.return_value = doc
    db._lock = threading.Lock()
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    db._conn = conn
    return db


def _make_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.data_dir = "/tmp"
    cfg.serving.base_url = "http://localhost:11434"
    cfg.llm.model = "llama3"
    return cfg


def _ready_doc(doc_id: str = "doc-test") -> dict:
    return {
        "id": doc_id,
        "title": "Test Document",
        "kind": "pdf",
        "source": "test.pdf",
        "readiness": "ready",
        "tier": "canon",
        "word_count": 100,
        "extracted_text": "Some extracted text.",
        "content_path": None,
        "work_id": None,
    }


# ── Patching helpers ──────────────────────────────────────────────────────────

_shutdown_calls: list[dict] = []


def _make_executor_class(wait_override: bool | None = None):
    """Return a ThreadPoolExecutor subclass that:
    - wraps submit() to return a future that raises TimeoutError after 1 second,
    - records shutdown() calls,
    - and calls super().shutdown(wait=False) so tests never block.
    """

    class _TrackedExecutor(_cf_real.ThreadPoolExecutor):
        def submit(self, fn, *args, **kwargs):
            real_future = super().submit(fn, *args, **kwargs)

            class _ShortTimeoutFuture:
                def result(self_, timeout=None):
                    # Force timeout after at most 1 second regardless of the
                    # requested timeout (which would be 60 in production).
                    try:
                        return real_future.result(timeout=1)
                    except _cf_real.TimeoutError:
                        raise

                def cancel(self_):
                    return real_future.cancel()

            return _ShortTimeoutFuture()

        def shutdown(self, wait=True, **kwargs):
            effective_wait = wait_override if wait_override is not None else wait
            _shutdown_calls.append({"wait": effective_wait})
            # Always non-blocking in tests to prevent hangs
            super().shutdown(wait=False)

    return _TrackedExecutor


class TestIntakeResearchTimeout:
    def setup_method(self):
        _shutdown_calls.clear()

    # ── 1. shutdown(wait=False) ───────────────────────────────────────────────

    def test_shutdown_called_with_wait_false(self):
        """The executor must be shut down with wait=False after a timeout."""
        done = threading.Event()

        def _stalling(*args, **kwargs):
            done.wait(timeout=30)
            return ("never", [])

        doc = _ready_doc()
        executor_cls = _make_executor_class()

        with (
            patch("orivellum.capabilities.websearch.web_search_synthesize", _stalling),
            patch("concurrent.futures.ThreadPoolExecutor", executor_cls),
        ):
            from orivellum.capabilities.intake import run_intake

            run_intake(
                doc["id"],
                db=_make_db(doc),
                cfg=_make_cfg(),
                research=True,
                research_query="wait-false test",
            )

        done.set()

        assert _shutdown_calls, "shutdown() was never called on the executor"
        for call in _shutdown_calls:
            assert call["wait"] is False, (
                f"shutdown(wait=True) detected — a stalled worker would block run_intake. "
                f"Recorded calls: {_shutdown_calls}"
            )

    # ── 2. graceful no-research profile ─────────────────────────────────────

    def test_profile_returned_without_research_on_timeout(self):
        """After a synthesis timeout, profile is valid with no research data."""
        done = threading.Event()

        def _stalling(*args, **kwargs):
            done.wait(timeout=30)
            return ("never", [])

        doc = _ready_doc()
        executor_cls = _make_executor_class()

        with (
            patch("orivellum.capabilities.websearch.web_search_synthesize", _stalling),
            patch("concurrent.futures.ThreadPoolExecutor", executor_cls),
        ):
            from orivellum.capabilities.intake import run_intake

            profile = run_intake(
                doc["id"],
                db=_make_db(doc),
                cfg=_make_cfg(),
                research=True,
                research_query="graceful-profile test",
            )

        done.set()

        assert profile is not None, "run_intake returned None"
        assert profile.doc_id == doc["id"]
        # Timeout is a soft failure — no research results, no profile error
        assert not profile.research_summary
        assert profile.research_sources == []
        assert profile.error is None

    # ── 3. wall-clock bound ──────────────────────────────────────────────────

    def test_run_intake_returns_before_stalling_synthesis_completes(self):
        """Wall-clock: run_intake must return well before a 10-second synthesis finishes.

        With the wait=False fix, run_intake should return in ~1–2 seconds (1s forced
        future timeout + overhead).  If shutdown(wait=True) were still used, it would
        block for the full 10 seconds the stalling function takes.
        """
        done = threading.Event()

        def _ten_second_synthesis(*args, **kwargs):
            done.wait(timeout=10)
            return ("slow reply", [])

        doc = _ready_doc()
        executor_cls = _make_executor_class()

        start = time.monotonic()
        with (
            patch("orivellum.capabilities.websearch.web_search_synthesize", _ten_second_synthesis),
            patch("concurrent.futures.ThreadPoolExecutor", executor_cls),
        ):
            from orivellum.capabilities.intake import run_intake

            profile = run_intake(
                doc["id"],
                db=_make_db(doc),
                cfg=_make_cfg(),
                research=True,
                research_query="wall-clock test",
            )
        elapsed = time.monotonic() - start

        done.set()  # release the stalling thread so it can clean up

        # Should finish in under 4 seconds (1s forced timeout + overhead).
        # A shutdown(wait=True) regression would take ~10 seconds.
        assert elapsed < 4, (
            f"run_intake took {elapsed:.2f}s — expected < 4s. "
            "Check that the executor is shut down with wait=False."
        )
        assert profile is not None
        assert not profile.research_summary
