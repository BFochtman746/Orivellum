"""Tests: per-chunk silence timeout in _stream_response and _stream_continuation.

Uses asynccontextmanager to correctly mock httpx's nested async context managers.

Verifies:
  1. A stalled initial stream emits a {timeout:true} SSE event.
  2. The persisted message has meta.incomplete=True and meta.cut_short=True.
  3. A stalled continuation stream emits a {timeout:true} SSE event.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Minimal stubs ──────────────────────────────────────────────────────────────

def _make_conv(conv_id: str = "conv-1") -> dict:
    return {
        "id": conv_id, "title": "T", "work_id": None,
        "model": None, "archived": False, "created_at": "2026-01-01",
    }


def _make_db(conv: dict) -> MagicMock:
    db = MagicMock()
    db.get_conversation.return_value = conv
    db._lock = threading.Lock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    db._conn = conn
    db.get_messages.return_value = []

    _msgs: list[dict] = []
    # Track every SQL call as (sql, args) so tests can inspect any query.
    _sql_calls: list[tuple[str, tuple]] = []

    def _add(conv_id, role, text, state=None, meta=None):
        mid = f"msg-{len(_msgs)}"
        m = {"id": mid, "role": role, "text": text, "state": state,
             "meta": json.dumps(meta) if meta else None}
        _msgs.append(m)
        return m

    def _execute(sql, args=None, *extra):
        _sql_calls.append((str(sql), tuple(args) if args else ()))
        return MagicMock()

    db.add_message.side_effect = _add
    db.finalize_message.return_value = {"id": "msg-0"}
    db.transition_message.return_value = None
    db._messages = _msgs
    db._sql_calls = _sql_calls
    conn.execute.side_effect = _execute
    return db


def _make_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.serving.base_url = "http://localhost:11434"
    cfg.serving.timeout_sec = 120
    return cfg


# ── Async iterator that raises TimeoutError mid-stream ────────────────────────

class _TimeoutAfterTokens:
    """Yields SSE lines for `tokens`, then raises asyncio.TimeoutError."""

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx < len(self._tokens):
            tok = self._tokens[self._idx]
            self._idx += 1
            payload = json.dumps(
                {"choices": [{"delta": {"content": tok}, "finish_reason": None}]}
            )
            return f"data: {payload}"
        raise asyncio.TimeoutError("forced stall for test")


class _FinishCleanly:
    """Yields SSE lines for `tokens` then a stop finish_reason, simulating normal completion."""

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)
        self._phase = 0  # 0: tokens, 1: finish_reason, 2: [DONE], 3: done

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._phase == 0 and self._tokens:
            tok = self._tokens.pop(0)
            if not self._tokens:
                self._phase = 1
            return f"data: {json.dumps({'choices': [{'delta': {'content': tok}, 'finish_reason': None}]})}"
        if self._phase == 1:
            self._phase = 2
            return f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}"
        if self._phase == 2:
            self._phase = 3
            return "data: [DONE]"
        raise StopAsyncIteration


# ── httpx mock helpers using asynccontextmanager ──────────────────────────────

def _make_httpx_mock(line_iter):
    """Return a patch target (the `httpx.AsyncClient` class) that wires up:
      - async with httpx.AsyncClient(...) as client  → mock_client
      - async with client.stream(...) as resp         → mock_resp
      - resp.aiter_lines().__aiter__()               → line_iter
    """
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines.return_value = line_iter

    @contextlib.asynccontextmanager
    async def _stream_ctx(*args, **kwargs):
        yield mock_resp

    mock_client = MagicMock()
    mock_client.stream = _stream_ctx

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return mock_client

        async def __aexit__(self, *args):
            return False

    return _MockAsyncClient


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestStreamResponseTimeout:

    @pytest.mark.asyncio
    async def test_timeout_event_emitted(self):
        """Stalled initial stream must emit {timeout:true, message_id: ...}."""
        conv = _make_conv()
        db = _make_db(conv)

        from orivellum.api.routes import conversations as cv

        line_iter = _TimeoutAfterTokens(["Hello "])
        MockClient = _make_httpx_mock(line_iter)

        with (
            patch.object(cv, "_build_messages",
                         return_value=[{"role": "user", "content": "hi"}]),
            patch.object(cv, "_build_system_prompt", return_value=""),
            patch.object(cv, "_maybe_dispatch_intent",
                         new=AsyncMock(return_value=None)),
            patch.object(cv, "_model_for", return_value="llama3"),
            patch.object(cv, "get_config", return_value=_make_cfg()),
            patch("httpx.AsyncClient", MockClient),
        ):
            events: list[str] = []
            async for chunk in cv._stream_response(db, conv, "hi"):
                events.append(chunk)

        timeout_events = [
            e for e in events
            if e.startswith("data: ") and '"timeout"' in e
        ]
        assert timeout_events, (
            f"Expected {{timeout:true}} SSE event.\nAll events:\n" +
            "\n".join(events)
        )
        data = json.loads(timeout_events[0][6:])
        assert data.get("timeout") is True
        assert data.get("message_id"), "timeout event must carry message_id"

    @pytest.mark.asyncio
    async def test_cut_short_and_incomplete_in_meta(self):
        """Timed-out message must persist cut_short=True and incomplete=True in meta."""
        conv = _make_conv()
        db = _make_db(conv)

        from orivellum.api.routes import conversations as cv

        line_iter = _TimeoutAfterTokens(["part"])
        MockClient = _make_httpx_mock(line_iter)

        with (
            patch.object(cv, "_build_messages",
                         return_value=[{"role": "user", "content": "hi"}]),
            patch.object(cv, "_build_system_prompt", return_value=""),
            patch.object(cv, "_maybe_dispatch_intent",
                         new=AsyncMock(return_value=None)),
            patch.object(cv, "_model_for", return_value="llama3"),
            patch.object(cv, "get_config", return_value=_make_cfg()),
            patch("httpx.AsyncClient", MockClient),
        ):
            async for _ in cv._stream_response(db, conv, "hi"):
                pass

        # Every UPDATE messages SET meta=? call is captured in db._sql_calls.
        # Find any call where the meta JSON contains both cut_short and incomplete.
        update_meta_calls = [
            args for sql, args in db._sql_calls
            if "UPDATE messages SET meta" in sql
        ]
        assert update_meta_calls, (
            "No 'UPDATE messages SET meta' SQL call found after initial-stream timeout"
        )
        found = any(
            (lambda d: d.get("cut_short") and d.get("incomplete"))(
                json.loads(args[0]) if args else {}
            )
            for args in update_meta_calls
            if args and isinstance(args[0], str)
        )
        assert found, (
            "Expected meta to contain cut_short=True and incomplete=True after timeout.\n"
            f"Captured meta args: {[a[0] for a in update_meta_calls if a]}"
        )


class TestStreamContinuationTimeout:

    @pytest.mark.asyncio
    async def test_timeout_event_emitted(self):
        """Stalled continuation stream must emit {timeout:true, message_id: orig_id}."""
        conv = _make_conv()
        db = _make_db(conv)
        cut_short_msg = {
            "id": "orig-msg",
            "role": "assistant",
            "text": "partial",
            "meta": {"model": "llama3", "cut_short": True, "partial_text": "partial"},
        }

        from orivellum.api.routes import conversations as cv

        line_iter = _TimeoutAfterTokens([" token"])
        MockClient = _make_httpx_mock(line_iter)

        with (
            patch.object(cv, "_build_system_prompt", return_value=""),
            patch.object(cv, "_model_for", return_value="llama3"),
            patch.object(cv, "get_config", return_value=_make_cfg()),
            patch("httpx.AsyncClient", MockClient),
        ):
            events: list[str] = []
            async for chunk in cv._stream_continuation(db, conv, cut_short_msg):
                events.append(chunk)

        timeout_events = [
            e for e in events
            if e.startswith("data: ") and '"timeout"' in e
        ]
        assert timeout_events, (
            f"Expected {{timeout:true}} SSE event from continuation.\n"
            f"All events:\n" + "\n".join(events)
        )
        data = json.loads(timeout_events[0][6:])
        assert data.get("timeout") is True
        assert data.get("message_id") == cut_short_msg["id"], \
            f"Expected message_id={cut_short_msg['id']}, got {data.get('message_id')}"

    @pytest.mark.asyncio
    async def test_timed_out_continuation_persists_incomplete_in_meta(self):
        """When continuation itself times out, the DB update must set incomplete=True."""
        conv = _make_conv()
        db = _make_db(conv)
        cut_short_msg = {
            "id": "orig-timeout",
            "role": "assistant",
            "text": "partial",
            "meta": {"model": "llama3", "cut_short": True, "partial_text": "partial"},
        }

        from orivellum.api.routes import conversations as cv

        # Produce one real token so continuation is non-empty, then timeout
        line_iter = _TimeoutAfterTokens([" more"])
        MockClient = _make_httpx_mock(line_iter)

        with (
            patch.object(cv, "_build_system_prompt", return_value=""),
            patch.object(cv, "_model_for", return_value="llama3"),
            patch.object(cv, "get_config", return_value=_make_cfg()),
            patch("httpx.AsyncClient", MockClient),
        ):
            async for _ in cv._stream_continuation(db, conv, cut_short_msg):
                pass

        # Find the UPDATE messages SET text=?, meta=? call
        update_text_calls = [
            args for sql, args in db._sql_calls
            if "UPDATE messages SET text" in sql and "meta" in sql
        ]
        assert update_text_calls, (
            "Expected 'UPDATE messages SET text=?, meta=?' after timed-out continuation.\n"
            f"All SQL calls: {[s for s, _ in db._sql_calls]}"
        )
        # The second positional arg is the meta JSON
        meta_arg = update_text_calls[-1][1] if len(update_text_calls[-1]) > 1 else None
        assert meta_arg, "meta argument missing from UPDATE call"
        meta = json.loads(meta_arg)
        assert meta.get("incomplete") is True, (
            f"Expected meta.incomplete=True after timed-out continuation; got {meta}"
        )
        assert meta.get("cut_short") is True, (
            f"Expected meta.cut_short=True after timed-out continuation; got {meta}"
        )

    @pytest.mark.asyncio
    async def test_successful_continuation_clears_incomplete_from_meta(self):
        """Completing a timed-out message successfully must remove incomplete from meta."""
        conv = _make_conv()
        db = _make_db(conv)
        # Simulate a previously timed-out message that has both flags set
        cut_short_msg = {
            "id": "orig-incomplete",
            "role": "assistant",
            "text": "partial",
            "meta": {
                "model": "llama3",
                "cut_short": True,
                "incomplete": True,
                "partial_text": "partial",
            },
        }

        from orivellum.api.routes import conversations as cv

        # Produce tokens that finish cleanly (stop finish_reason)
        line_iter = _FinishCleanly([" complete"])
        MockClient = _make_httpx_mock(line_iter)

        with (
            patch.object(cv, "_build_system_prompt", return_value=""),
            patch.object(cv, "_model_for", return_value="llama3"),
            patch.object(cv, "get_config", return_value=_make_cfg()),
            patch("httpx.AsyncClient", MockClient),
        ):
            events: list[str] = []
            async for chunk in cv._stream_continuation(db, conv, cut_short_msg):
                events.append(chunk)

        # Find the UPDATE messages SET text=?, meta=? call
        update_text_calls = [
            args for sql, args in db._sql_calls
            if "UPDATE messages SET text" in sql and "meta" in sql
        ]
        assert update_text_calls, (
            "Expected 'UPDATE messages SET text=?, meta=?' after successful continuation.\n"
            f"All SQL calls: {[s for s, _ in db._sql_calls]}"
        )
        meta_arg = update_text_calls[-1][1] if len(update_text_calls[-1]) > 1 else None
        assert meta_arg, "meta argument missing from UPDATE call"
        meta = json.loads(meta_arg)
        assert "incomplete" not in meta, (
            f"Expected meta.incomplete to be cleared after successful continuation; got {meta}"
        )
        assert "cut_short" not in meta, (
            f"Expected meta.cut_short to be cleared after successful continuation; got {meta}"
        )
