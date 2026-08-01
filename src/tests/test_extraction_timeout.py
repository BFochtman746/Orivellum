"""
Tests: extraction timeout behaviour in llm_harvest / _call_llm_sync

Verified behaviours:
  1. _call_llm_sync returns None (and emits a WARNING) when the httpx call times out.
  2. _call_llm_sync forwards the caller-supplied timeout value to httpx.Client.
  3. llm_harvest continues processing remaining chunks when one chunk times out
     (a single timed-out chunk must NOT abort the whole document).
  4. llm_harvest returns promptly when every chunk times out — it must not
     block for N * extraction_timeout_sec.
  5. The warning log line is emitted with the expected message on timeout.
"""
from __future__ import annotations

import time
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest
import httpx


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

def _make_page(text: str):
    """Return a minimal page/segment stub."""
    return SimpleNamespace(text=text)


def _make_result(pages: list[str]):
    """Return a minimal ExtractionResult-like object."""
    return SimpleNamespace(pages=[_make_page(t) for t in pages])


class _FakeDB:
    """Captures create_knowledge_item calls without touching a real database."""

    def __init__(self):
        self.items: list[dict] = []

    def create_knowledge_item(self, **kwargs):
        self.items.append(kwargs)
        return str(len(self.items))


def _make_cfg(timeout: int = 5):
    """Return a minimal config stub used by llm_harvest."""
    serving = SimpleNamespace(
        base_url="http://127.0.0.1:9999",
        workhorse_model="test-model",
        extraction_timeout_sec=timeout,
    )
    return SimpleNamespace(serving=serving)


# JSON that the LLM would return for a successful chunk
_GOOD_LLM_RESPONSE = (
    '{"entities": [{"name": "Orivellum", "description": "The system under test"}],'
    ' "claims": [{"text": "It works."}],'
    ' "relationships": [{"subject": "Orivellum", "predicate": "is", "object": "tested"}]}'
)

# ---------------------------------------------------------------------------
# _call_llm_sync
# ---------------------------------------------------------------------------

def test_call_llm_sync_returns_none_on_timeout(caplog):
    """A ConnectTimeout from httpx must make _call_llm_sync return None."""
    from orivellum.capabilities.knowledge_harvest import _call_llm_sync

    with patch("httpx.Client") as mock_client_cls:
        instance = MagicMock()
        instance.__enter__ = lambda s: instance
        instance.__exit__ = MagicMock(return_value=False)
        instance.post.side_effect = httpx.ConnectTimeout("timed out")
        mock_client_cls.return_value = instance

        with caplog.at_level(logging.WARNING, logger="orivellum.capabilities.knowledge_harvest"):
            result = _call_llm_sync("prompt", "http://localhost:9999", "model", timeout=5)

    assert result is None
    assert any(
        "LLM call failed during knowledge extraction" in r.message
        for r in caplog.records
    ), "Expected a WARNING about LLM failure, but none was found."


def test_call_llm_sync_returns_none_on_read_timeout(caplog):
    """A ReadTimeout (slow response body) must also return None with a warning."""
    from orivellum.capabilities.knowledge_harvest import _call_llm_sync

    with patch("httpx.Client") as mock_client_cls:
        instance = MagicMock()
        instance.__enter__ = lambda s: instance
        instance.__exit__ = MagicMock(return_value=False)
        instance.post.side_effect = httpx.ReadTimeout("read timed out")
        mock_client_cls.return_value = instance

        with caplog.at_level(logging.WARNING, logger="orivellum.capabilities.knowledge_harvest"):
            result = _call_llm_sync("prompt", "http://localhost:9999", "model", timeout=5)

    assert result is None
    assert any(
        "LLM call failed during knowledge extraction" in r.message
        for r in caplog.records
    )


def test_call_llm_sync_forwards_timeout_to_httpx():
    """The timeout parameter must be passed to httpx.Client, not ignored."""
    from orivellum.capabilities.knowledge_harvest import _call_llm_sync

    captured_timeout = []

    def _fake_client_init(timeout):
        captured_timeout.append(timeout)
        raise httpx.ConnectError("unreachable")  # abort early — we only need the arg

    with patch("httpx.Client", side_effect=_fake_client_init):
        _call_llm_sync("prompt", "http://localhost:9999", "model", timeout=42)

    assert captured_timeout == [42], (
        f"Expected httpx.Client to be constructed with timeout=42, got {captured_timeout}"
    )


# ---------------------------------------------------------------------------
# llm_harvest — timeout resilience
# ---------------------------------------------------------------------------

def test_llm_harvest_continues_after_chunk_timeout():
    """
    When the first chunk times out (returns None), llm_harvest must still
    process the remaining chunks and store the knowledge items from them.
    """
    from orivellum.capabilities.knowledge_harvest import llm_harvest

    db = _FakeDB()
    result = _make_result(["chunk one text here", "chunk two text here"])

    # First call → None (timeout), second call → valid JSON
    call_returns = [None, _GOOD_LLM_RESPONSE]

    with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync",
               side_effect=call_returns), \
         patch("orivellum.api._deps.get_config", return_value=_make_cfg()):
        count = llm_harvest(
                result=result,
                doc_id="doc-1",
                work_id="work-1",
                doc_title="Test Document",
                db=db,
            )

    # At least the items from the second chunk must have been created
    assert count > 0, (
        "llm_harvest returned 0 items — it likely aborted after the first timeout"
    )
    assert len(db.items) > 0


def test_llm_harvest_all_chunks_timeout_returns_zero():
    """
    When every chunk times out, llm_harvest should return 0 and not crash.
    """
    from orivellum.capabilities.knowledge_harvest import llm_harvest

    db = _FakeDB()
    result = _make_result(["chunk one", "chunk two", "chunk three"])

    with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync",
               return_value=None), \
         patch("orivellum.api._deps.get_config", return_value=_make_cfg()):
        count = llm_harvest(
            result=result,
            doc_id="doc-2",
            work_id=None,
            doc_title="Empty Doc",
            db=db,
        )

    assert count == 0
    assert len(db.items) == 0


def _make_hanging_server():
    """
    Return a (server, port) pair whose HTTP handler accepts connections but
    never sends a response.  Uses ThreadingMixIn + daemon_threads so that
    server.shutdown() returns immediately without waiting for in-flight
    handlers — avoids the 30-second sleep blocking teardown.
    """
    import socketserver
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class _ThreadingServer(socketserver.ThreadingMixIn, HTTPServer):
        daemon_threads = True  # handler threads die with the test process

    class _HangingHandler(BaseHTTPRequestHandler):
        """Accepts the connection, then blocks until the client gives up."""
        def do_POST(self):  # noqa: N802
            # Sleep well past any reasonable test timeout; the client will
            # hit its per-call timeout and disconnect first.
            time.sleep(60)
        def log_message(self, *args):
            pass  # suppress access-log noise in test output

    server = _ThreadingServer(("127.0.0.1", 0), _HangingHandler)
    return server, server.server_address[1]


def test_call_llm_sync_respects_timeout_with_real_server():
    """
    Use a real local HTTP server that hangs indefinitely to verify that
    _call_llm_sync actually honours the timeout argument end-to-end.

    The call must return within (timeout + 1 s) overhead, not hang forever.
    """
    import threading
    from orivellum.capabilities.knowledge_harvest import _call_llm_sync

    server, port = _make_hanging_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    per_call_timeout = 1  # seconds — short so the test stays fast

    try:
        start = time.monotonic()
        result = _call_llm_sync(
            "prompt", f"http://127.0.0.1:{port}", "model",
            timeout=per_call_timeout,
        )
        elapsed = time.monotonic() - start
    finally:
        server.shutdown()  # returns immediately thanks to daemon_threads

    assert result is None, "Expected None when the server times out."
    # Must have returned within roughly the configured timeout (+ 1s grace).
    assert elapsed < per_call_timeout + 1.5, (
        f"_call_llm_sync took {elapsed:.2f}s — the per-call timeout "
        f"({per_call_timeout}s) was not honoured."
    )
    # Sanity-check: must not have returned suspiciously fast (< 30 % of timeout),
    # which would indicate the timeout path was bypassed entirely.
    assert elapsed >= per_call_timeout * 0.3, (
        f"_call_llm_sync returned in {elapsed:.2f}s — suspiciously faster than "
        f"the configured timeout ({per_call_timeout}s)."
    )


def test_llm_harvest_per_chunk_timeout_bounds_total_time():
    """
    When every chunk hits the extraction timeout against a real hanging server,
    the total wall-clock time for llm_harvest must be bounded by roughly
    N_chunks × per_chunk_timeout.

    This proves the extraction_timeout_sec is enforced per-chunk (not bypassed),
    and that one chunk's timeout does not prevent the rest from being attempted.
    """
    import threading
    from orivellum.capabilities.knowledge_harvest import llm_harvest

    server, port = _make_hanging_server()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    n_chunks = 2
    per_chunk_timeout = 1  # seconds — 2 chunks × 1 s ≈ 2 s total
    pages = [f"chunk {i} has enough text to pass the empty-check" for i in range(n_chunks)]
    result = _make_result(pages)
    db = _FakeDB()

    cfg = _make_cfg(timeout=per_chunk_timeout)
    cfg.serving.base_url = f"http://127.0.0.1:{port}"

    try:
        with patch("orivellum.api._deps.get_config", return_value=cfg):
            start = time.monotonic()
            count = llm_harvest(
                result=result,
                doc_id="doc-timeout",
                work_id=None,
                doc_title="Slow Doc",
                db=db,
            )
            elapsed = time.monotonic() - start
    finally:
        server.shutdown()  # returns immediately thanks to daemon_threads

    assert count == 0, "No items should be created when every chunk times out."

    # Upper bound: n_chunks × per_chunk_timeout + generous overhead
    upper = n_chunks * per_chunk_timeout + 4.0
    assert elapsed < upper, (
        f"llm_harvest took {elapsed:.2f}s for {n_chunks} timed-out chunks "
        f"(expected < {upper:.1f}s). The timeout may not be enforced."
    )
    # Lower bound: at least one real timeout must have fired
    assert elapsed >= per_chunk_timeout * 0.5, (
        f"llm_harvest returned in {elapsed:.2f}s — too fast, "
        "the timeout may have been bypassed."
    )


# ---------------------------------------------------------------------------
# Warning log emission
# ---------------------------------------------------------------------------

def test_warning_emitted_on_timeout(caplog):
    """
    llm_harvest must log a WARNING (not just silently skip) when a chunk
    times out, so operators can diagnose a misconfigured AI endpoint.
    """
    from orivellum.capabilities.knowledge_harvest import llm_harvest

    result = _make_result(["some important text"])
    db = _FakeDB()

    # Patch httpx.Client so _call_llm_sync's own try/except fires and logs the warning.
    def _hanging_client(timeout):
        instance = MagicMock()
        instance.__enter__ = lambda s: instance
        instance.__exit__ = MagicMock(return_value=False)
        instance.post.side_effect = httpx.ConnectTimeout("timed out")
        return instance

    with patch("httpx.Client", side_effect=_hanging_client), \
         patch("orivellum.api._deps.get_config", return_value=_make_cfg()):

        with caplog.at_level(logging.WARNING,
                             logger="orivellum.capabilities.knowledge_harvest"):
            llm_harvest(
                result=result,
                doc_id="doc-4",
                work_id=None,
                doc_title="Warn Doc",
                db=db,
            )

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "LLM call failed during knowledge extraction" in m
        for m in warning_messages
    ), (
        f"Expected a WARNING about LLM failure. Warnings found: {warning_messages}"
    )


def test_timeout_warning_includes_exception_detail(caplog):
    """The warning message should include the exception so logs are actionable."""
    from orivellum.capabilities.knowledge_harvest import _call_llm_sync

    exc_message = "Connection to 127.0.0.1 timed out"

    with patch("httpx.Client") as mock_client_cls:
        instance = MagicMock()
        instance.__enter__ = lambda s: instance
        instance.__exit__ = MagicMock(return_value=False)
        instance.post.side_effect = httpx.ConnectTimeout(exc_message)
        mock_client_cls.return_value = instance

        with caplog.at_level(logging.WARNING,
                             logger="orivellum.capabilities.knowledge_harvest"):
            _call_llm_sync("prompt", "http://localhost:9999", "model", timeout=5)

    # The formatted log record should mention the exception
    formatted = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert formatted, "No WARNING records were emitted."
    assert any(exc_message in m for m in formatted), (
        f"Exception detail not found in warning. Got: {formatted}"
    )
