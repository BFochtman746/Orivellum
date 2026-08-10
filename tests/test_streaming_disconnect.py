"""Tests for mid-stream disconnect handling in _stream_response.

Verifies that when a client disconnects (GeneratorExit) while the assistant
is still streaming tokens, whatever partial reply has arrived is persisted
with the truncation marker — leaving the conversation with a complete
assistant turn rather than a missing one.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helper: build a minimal real DB wired into deps
# ---------------------------------------------------------------------------


def _make_db(tmp: str):
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return db


# ---------------------------------------------------------------------------
# Helper: mock httpx.AsyncClient that streams a fixed list of SSE lines
# ---------------------------------------------------------------------------


def _make_httpx_mock(sse_lines: list[str]):
    """Return a mock AsyncClient that yields the given SSE lines then stops."""

    async def _aiter_lines():
        for line in sse_lines:
            yield line

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = _aiter_lines
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    return mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_partial_reply_persisted_on_client_disconnect():
    """GeneratorExit mid-stream must save the partial reply with truncation marker."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="Test disconnect")

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Hello "}}]}',
            'data: {"choices":[{"delta":{"content":"world"}}]}',
            'data: {"choices":[{"delta":{"content":"! More text here."}}]}',
            "data: [DONE]",
        ]
        mock_client = _make_httpx_mock(sse_lines)

        from orivellum.api.routes.conversations import _stream_response

        with (
            patch(
                "orivellum.api.routes.conversations._maybe_dispatch_intent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            gen = _stream_response(db, conv, "Say hello")

            # The generator now emits a control event (message_id/state) before
            # any token.  Advance past all control events until we land on the
            # first token event so at least one token is buffered in full_reply.
            first_token_event: str | None = None
            for _ in range(15):
                ev = await gen.__anext__()
                if "Hello" in ev:
                    first_token_event = ev
                    break
            assert first_token_event is not None, (
                "Expected a 'Hello' token event within the first 15 SSE events"
            )

            # Simulate client navigating away (disconnect)
            await gen.aclose()

        messages = db.get_messages(conv["id"])
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        assert len(assistant_msgs) == 1, (
            f"Exactly one assistant message must be persisted after disconnect; "
            f"found {len(assistant_msgs)}"
        )
        saved_text = assistant_msgs[0]["text"]
        assert "Hello" in saved_text, (
            "Saved reply must contain the tokens that arrived before disconnect"
        )
        assert "*(Response was cut short" in saved_text, (
            "Saved reply must include the truncation marker"
        )

        db.close()


@pytest.mark.anyio
async def test_no_message_saved_when_disconnect_before_any_tokens():
    """If GeneratorExit fires before a single token arrives, nothing is saved
    (no empty assistant turn pollutes the conversation)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="Early disconnect")

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"First token"}}]}',
            "data: [DONE]",
        ]
        mock_client = _make_httpx_mock(sse_lines)

        from orivellum.api.routes.conversations import _stream_response

        with (
            patch(
                "orivellum.api.routes.conversations._maybe_dispatch_intent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            gen = _stream_response(db, conv, "Say something")

            # Close immediately — before the generator reaches its first yield
            await gen.aclose()

        messages = db.get_messages(conv["id"])
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        # No partial reply should be stored when disconnect precedes all tokens
        assert len(assistant_msgs) == 0, (
            "No assistant message should be saved when disconnect precedes all tokens"
        )

        db.close()


@pytest.mark.anyio
async def test_full_reply_saved_normally_without_disconnect():
    """Control: when the client stays connected, the full reply is saved without
    the truncation marker."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="Normal completion")

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Complete "}}]}',
            'data: {"choices":[{"delta":{"content":"reply."}}]}',
            "data: [DONE]",
        ]
        mock_client = _make_httpx_mock(sse_lines)

        from orivellum.api.routes.conversations import _stream_response

        with (
            patch(
                "orivellum.api.routes.conversations._maybe_dispatch_intent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            # Consume all events — no disconnect
            events = []
            async for event in _stream_response(db, conv, "Finish this"):
                events.append(event)

        messages = db.get_messages(conv["id"])
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]

        assert len(assistant_msgs) == 1
        saved_text = assistant_msgs[0]["text"]
        assert saved_text == "Complete reply.", (
            f"Full reply should be saved verbatim, got: {saved_text!r}"
        )
        assert "cut short" not in saved_text, (
            "Truncation marker must NOT appear in a normally-completed reply"
        )
        # Final DONE event must be present
        assert any("[DONE]" in e for e in events)

        db.close()
