"""Tests: server-authored activity events on the chat streaming path (WP4).

The WP4 gate: the activity display never claims an action the server did not
emit. That requires the server to emit truthful activity frames at real
stages. These tests verify:

  1. A normal streamed reply emits retrieval start/done (with source_count),
     generation start/done (with elapsed_ms) — in that order.
  2. A stalled stream emits generation state=failed with reason="timeout".
  3. An intent/tool reply emits a tool done event carrying the tool name.
  4. genjournal classifies activity and code_progress frames under their own
     journal kinds so they replay after a reconnect.
"""

from __future__ import annotations

import contextlib
import json
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Minimal stubs (same shape as tests/test_stream_timeout.py) ────────────────


def _make_conv(conv_id: str = "conv-1") -> dict:
    return {
        "id": conv_id,
        "title": "T",
        "work_id": None,
        "model": None,
        "archived": False,
        "created_at": "2026-01-01",
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

    def _add(conv_id, role, text, state=None, meta=None):
        mid = f"msg-{len(_msgs)}"
        m = {
            "id": mid,
            "role": role,
            "text": text,
            "state": state,
            "meta": json.dumps(meta) if meta else None,
        }
        _msgs.append(m)
        return m

    db.add_message.side_effect = _add
    db.finalize_message.return_value = {"id": "msg-0"}
    db.transition_message.return_value = None
    db._messages = _msgs
    return db


def _make_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.serving.base_url = "http://localhost:11434"
    cfg.serving.timeout_sec = 120
    return cfg


class _FinishCleanly:
    """Yields SSE lines for `tokens` then a stop finish_reason."""

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)
        self._phase = 0

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


class _TimeoutAfterTokens:
    """Yields SSE lines for `tokens`, then raises TimeoutError (stall)."""

    def __init__(self, tokens: list[str]):
        self._tokens = list(tokens)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._tokens:
            tok = self._tokens.pop(0)
            payload = json.dumps({"choices": [{"delta": {"content": tok}, "finish_reason": None}]})
            return f"data: {payload}"
        raise TimeoutError("forced stall for test")


def _make_httpx_mock(line_iter):
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


def _activity_events(raw_events: list[str]) -> list[dict]:
    """Extract the activity payloads from raw SSE frame strings, in order."""
    out: list[dict] = []
    for e in raw_events:
        if not e.startswith("data: "):
            continue
        body = e[6:].strip()
        if body == "[DONE]":
            continue
        try:
            d = json.loads(body)
        except Exception:
            continue
        if isinstance(d, dict) and isinstance(d.get("activity"), dict):
            out.append(d["activity"])
    return out


async def _collect_stream(db, conv, text: str, line_iter, *, intent=None) -> list[str]:
    from orivellum.api.routes import conversations as cv

    MockClient = _make_httpx_mock(line_iter)
    with (
        patch.object(cv, "_build_messages", return_value=[{"role": "user", "content": text}]),
        patch.object(cv, "_build_system_prompt", return_value=""),
        patch.object(cv, "_maybe_dispatch_intent", new=AsyncMock(return_value=intent)),
        patch.object(cv, "_model_for", return_value="llama3"),
        patch.object(cv, "get_config", return_value=_make_cfg()),
        patch("httpx.AsyncClient", MockClient),
    ):
        events: list[str] = []
        async for chunk in cv._stream_response(db, conv, text):
            events.append(chunk)
    return events


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestActivityEventsHappyPath:
    @pytest.mark.asyncio
    async def test_retrieval_and_generation_events_in_order(self):
        conv = _make_conv()
        db = _make_db(conv)
        events = await _collect_stream(db, conv, "hi", _FinishCleanly(["Hello ", "world"]))

        acts = _activity_events(events)
        keys = [(a["stage"], a["state"]) for a in acts]
        assert keys == [
            ("retrieval", "start"),
            ("retrieval", "done"),
            ("generation", "start"),
            ("generation", "done"),
        ], f"Unexpected activity sequence: {keys}"

        retr_done = acts[1]
        assert isinstance(retr_done.get("source_count"), int)
        assert isinstance(retr_done.get("elapsed_ms"), int)
        gen_done = acts[3]
        assert isinstance(gen_done.get("elapsed_ms"), int)

    @pytest.mark.asyncio
    async def test_retrieval_start_precedes_first_token(self):
        conv = _make_conv()
        db = _make_db(conv)
        events = await _collect_stream(db, conv, "hi", _FinishCleanly(["Hello"]))

        first_activity = next(i for i, e in enumerate(events) if '"activity"' in e)
        first_token = next(i for i, e in enumerate(events) if '"token"' in e)
        assert first_activity < first_token


class TestActivityEventsFailure:
    @pytest.mark.asyncio
    async def test_timeout_emits_generation_failed(self):
        conv = _make_conv()
        db = _make_db(conv)
        events = await _collect_stream(db, conv, "hi", _TimeoutAfterTokens(["part"]))

        acts = _activity_events(events)
        failed = [a for a in acts if a["stage"] == "generation" and a["state"] == "failed"]
        assert failed, f"Expected generation failed event. Got: {acts}"
        assert failed[0].get("reason") == "timeout"
        # No generation done event may follow a failure — that would be a lie.
        assert not any(a["stage"] == "generation" and a["state"] == "done" for a in acts)


class TestActivityEventsIntent:
    @pytest.mark.asyncio
    async def test_tool_event_carries_tool_name(self):
        conv = _make_conv()
        db = _make_db(conv)
        intent = ("It is sunny.", {"intent": "weather"})
        events = await _collect_stream(db, conv, "weather?", _FinishCleanly([]), intent=intent)

        acts = _activity_events(events)
        tool_done = [a for a in acts if a["stage"] == "tool" and a["state"] == "done"]
        assert tool_done, f"Expected tool done event. Got: {acts}"
        assert tool_done[0].get("tool") == "weather"
        assert isinstance(tool_done[0].get("elapsed_ms"), int)
        # The intent path never calls the provider, so it must not claim generation.
        assert not any(a["stage"] == "generation" for a in acts)


class TestJournalClassification:
    def test_activity_and_code_progress_have_own_kinds(self):
        from orivellum.api.genjournal import _classify

        assert _classify({"activity": {"stage": "retrieval", "state": "start"}}) == "activity"
        assert _classify({"code_progress": {"stage": "planning"}}) == "code_progress"
        assert _classify({"sources": []}) == "sources"
        assert _classify({"anything_else": 1}) == "meta"
