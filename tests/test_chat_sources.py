"""Tests for knowledge-source attribution on AI chat replies.

Covers:
  1. _build_system_prompt() captures a compact machine-readable source list
     (id / title / kind / work_id / work_title / source_doc_id) for every
     trusted knowledge item it injects.
  2. Those sources round-trip through the assistant message's meta column
     (persistence survives a refetch via get_messages).
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
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.api import _deps

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return db


def _seed_knowledge(db):
    """Seed a work + document + trusted knowledge item and return
    (conv, hit) where ``hit`` is the deterministic search result to patch in."""
    work = db.create_work(title="Rocketry")
    doc = db.create_document(title="Thrust Notes", work_id=work["id"])
    kid = db.create_knowledge_item(
        work_id=work["id"], kind="fact",
        text="Specific impulse measures rocket engine efficiency.",
        source_doc_id=doc["id"], review_status="approved",
    )
    conv = db.create_conversation(title="Chat", work_id=work["id"])
    hit = {
        "id": kid,
        "text": "Specific impulse measures rocket engine efficiency.",
        "kind": "fact",
        "work_id": work["id"],
        "source_doc_id": doc["id"],
        "review_status": "approved",
    }
    return conv, hit


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

def test_build_system_prompt_captures_sources():
    """A trusted knowledge item injected into the prompt is recorded in
    out_sources with the full machine-readable shape."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        work = db.create_work(title="Rocketry")
        doc = db.create_document(title="Thrust Notes", work_id=work["id"])
        kid = db.create_knowledge_item(
            work_id=work["id"], kind="fact",
            text="Specific impulse measures rocket engine efficiency.",
            source_doc_id=doc["id"], review_status="approved",
        )
        conv = db.create_conversation(title="Chat", work_id=work["id"])

        from orivellum.api.routes import conversations as C

        # Deterministic search result — bypass embeddings/FTS variance
        hit = {
            "id": kid,
            "text": "Specific impulse measures rocket engine efficiency.",
            "kind": "fact",
            "work_id": work["id"],
            "source_doc_id": doc["id"],
            "review_status": "approved",
        }
        out_sources: list = []
        with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                   return_value=[hit]):
            with patch.object(db, "search_chunks", return_value=[]):
                prompt = C._build_system_prompt(
                    db, conv, user_query="rocket efficiency",
                    out_sources=out_sources,
                )

        assert "Specific impulse" in prompt
        assert len(out_sources) == 1
        s = out_sources[0]
        assert s["id"] == kid
        assert s["kind"] == "fact"
        assert s["work_id"] == work["id"]
        assert s["work_title"] == "Rocketry"
        assert s["source_doc_id"] == doc["id"]
        assert s["title"]  # non-empty snippet
        db.close()


def test_sources_persist_in_message_meta():
    """Sources stored in an assistant message's meta survive a refetch."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="Persistence")

        sources = [
            {
                "id": "k1", "title": "A fact about ships", "kind": "fact",
                "work_id": "w1", "work_title": "Naval", "source_doc_id": "d1",
            }
        ]
        db.add_message(conv["id"], "assistant", "Ships float.",
                       meta={"model": "test-model", "sources": sources})

        messages = db.get_messages(conv["id"])
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant) == 1
        meta = assistant[0]["meta"]
        assert meta.get("model") == "test-model"
        assert meta.get("sources") == sources
        db.close()


@pytest.mark.anyio
async def test_sources_persisted_on_disconnect():
    """The truncation/disconnect path must persist injected sources in meta."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv, hit = _seed_knowledge(db)

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"Isp is "}}]}',
            'data: {"choices":[{"delta":{"content":"efficiency."}}]}',
            "data: [DONE]",
        ]
        mock_client = _make_httpx_mock(sse_lines)

        from orivellum.api.routes.conversations import _stream_response

        with patch(
            "orivellum.api.routes.conversations._maybe_dispatch_intent",
            new_callable=AsyncMock, return_value=None,
        ), patch(
            "orivellum.capabilities.embeddings.hybrid_search_knowledge",
            return_value=[hit],
        ), patch.object(db, "search_chunks", return_value=[]), \
                patch("httpx.AsyncClient", return_value=mock_client):
            gen = _stream_response(db, conv, "How efficient are rockets?")
            # The generator now emits a control event (message_id/state) before the
            # first token.  Advance past all control events until the first token
            # event so full_reply has content before we simulate disconnect.
            for _ in range(15):
                ev = await gen.__anext__()
                if '"token"' in ev:
                    break
            await gen.aclose()

        messages = db.get_messages(conv["id"])
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant) == 1
        meta = assistant[0]["meta"]
        assert meta.get("cut_short") is True
        srcs = meta.get("sources")
        assert srcs and len(srcs) == 1
        assert srcs[0]["source_doc_id"] == hit["source_doc_id"]
        assert srcs[0]["work_title"] == "Rocketry"
        db.close()


# ---------------------------------------------------------------------------
# Graceful-degradation tests — malformed / missing meta
# ---------------------------------------------------------------------------

def test_sources_empty_when_no_knowledge_matched():
    """_build_system_prompt returns an empty out_sources list when no
    trusted knowledge items match the query."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        work = db.create_work(title="Empty Work")
        conv = db.create_conversation(title="Chat", work_id=work["id"])

        from orivellum.api.routes import conversations as C
        from unittest.mock import patch

        out_sources: list = []
        with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                   return_value=[]):
            with patch.object(db, "search_chunks", return_value=[]):
                C._build_system_prompt(
                    db, conv, user_query="anything", out_sources=out_sources,
                )

        # No knowledge injected → out_sources must be empty, never crash
        assert out_sources == []
        db.close()


def test_sources_with_deleted_document_returns_safe_entry():
    """When a source's source_doc_id references a document that has since
    been deleted, _build_system_prompt still records the source_doc_id in
    out_sources (no crash at citation time — the frontend handles dead links
    gracefully; the source chip still appears, tapping it produces a 404
    which the router handles)."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        from unittest.mock import patch

        work = db.create_work(title="Rocketry")
        doc = db.create_document(title="Thrust Notes", work_id=work["id"])
        kid = db.create_knowledge_item(
            work_id=work["id"], kind="fact",
            text="Specific impulse measures efficiency.",
            source_doc_id=doc["id"], review_status="approved",
        )
        conv = db.create_conversation(title="Chat", work_id=work["id"])

        # Delete the document — knowledge item retains its source_doc_id
        db.delete_document(doc["id"])

        from orivellum.api.routes import conversations as C

        hit = {
            "id": kid,
            "text": "Specific impulse measures efficiency.",
            "kind": "fact",
            "work_id": work["id"],
            "source_doc_id": doc["id"],   # still the original id
            "review_status": "approved",
        }
        out_sources: list = []
        with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                   return_value=[hit]):
            with patch.object(db, "search_chunks", return_value=[]):
                C._build_system_prompt(
                    db, conv, user_query="rocket efficiency",
                    out_sources=out_sources,
                )

        # Source is returned — frontend is responsible for graceful dead-link handling
        assert len(out_sources) == 1
        assert out_sources[0]["source_doc_id"] == doc["id"]
        db.close()


def test_message_with_legacy_meta_no_sources_key():
    """An assistant message whose meta has no 'sources' key (messages from
    before citation support was added) stores and retrieves its text without
    error.  meta.get('sources') returns None — callers guard before iterating."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="Legacy")

        # Simulate a pre-sources message
        db.add_message(conv["id"], "assistant", "The answer is 42.",
                       meta={"model": "old-model"})

        messages = db.get_messages(conv["id"])
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant) == 1
        assert assistant[0]["text"] == "The answer is 42."
        meta = assistant[0]["meta"]
        assert meta.get("model") == "old-model"
        # Must not crash; sources absent or falsy — both are fine
        srcs = meta.get("sources")
        assert not srcs   # None or missing — SourcesFooter guard treats this as empty
        db.close()


def test_sources_with_work_id_only_no_doc_id():
    """A knowledge item created without a source_doc_id (e.g. from manual
    entry or synthesis) produces a source entry with work_id set but
    source_doc_id as None.  SourcesFooter falls back to a Work-level link
    instead of a document link — no crash, no broken <a href>."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        from unittest.mock import patch

        work = db.create_work(title="Theory")
        # Knowledge item with no document backing
        kid = db.create_knowledge_item(
            work_id=work["id"], kind="summary",
            text="General overview of the theory.",
            review_status="approved",
        )
        conv = db.create_conversation(title="Chat", work_id=work["id"])

        from orivellum.api.routes import conversations as C

        hit = {
            "id": kid,
            "text": "General overview of the theory.",
            "kind": "summary",
            "work_id": work["id"],
            "source_doc_id": None,   # no document backing
            "review_status": "approved",
        }
        out_sources: list = []
        with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                   return_value=[hit]):
            with patch.object(db, "search_chunks", return_value=[]):
                C._build_system_prompt(
                    db, conv, user_query="theory overview",
                    out_sources=out_sources,
                )

        assert len(out_sources) == 1
        s = out_sources[0]
        # work_id is set so SourcesFooter can link to the Work page
        assert s["work_id"] == work["id"]
        # source_doc_id is absent/None — frontend renders a non-clickable chip
        assert s.get("source_doc_id") is None
        db.close()


def test_message_with_null_and_empty_sources_stored_safely():
    """Messages stored with meta.sources=None and meta.sources=[] both
    retrieve without error and without surfacing as crashes to SourcesFooter."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv = db.create_conversation(title="NullAndEmpty")

        # null sources
        db.add_message(conv["id"], "assistant", "Reply A.",
                       meta={"model": "m", "sources": None})
        # empty array sources
        db.add_message(conv["id"], "assistant", "Reply B.",
                       meta={"model": "m", "sources": []})

        messages = db.get_messages(conv["id"])
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant) == 2

        texts = {m["text"] for m in assistant}
        assert "Reply A." in texts
        assert "Reply B." in texts

        for msg in assistant:
            meta = msg["meta"]
            srcs = meta.get("sources")
            # Both None and [] are falsy — SourcesFooter guard treats them identically
            # as "no sources" and renders nothing (not a crash)
            assert not srcs or srcs == []
        db.close()


@pytest.mark.anyio
async def test_sources_persisted_and_emitted_on_intent_branch():
    """The streaming intent branch must persist sources in meta AND emit a
    terminal `sources` SSE event."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(tmp)
        conv, hit = _seed_knowledge(db)

        from orivellum.api.routes import conversations as C

        # Intent dispatch returns a tool reply (no sources of its own)
        async def _fake_intent(*_a, **_k):
            return ("🌐 Web result text", {"intent": "web_search", "query": "q"})

        events: list[str] = []
        with patch.object(C, "_maybe_dispatch_intent", _fake_intent), \
                patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                      return_value=[hit]), \
                patch.object(db, "search_chunks", return_value=[]):
            async for ev in C._stream_response(db, conv, "search for rockets"):
                events.append(ev)

        # Persistence: assistant message meta carries both intent and sources
        messages = db.get_messages(conv["id"])
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert len(assistant) == 1
        meta = assistant[0]["meta"]
        assert meta.get("intent") == "web_search"
        srcs = meta.get("sources")
        assert srcs and srcs[0]["source_doc_id"] == hit["source_doc_id"]

        # SSE: a terminal sources event was emitted before [DONE]
        assert any('"sources"' in e for e in events), \
            "streaming intent branch must emit a sources SSE event"
        assert any("[DONE]" in e for e in events)
        db.close()
