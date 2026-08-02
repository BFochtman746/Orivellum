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
from unittest.mock import patch

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
