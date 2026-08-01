"""
Tests: review_status gating in _build_system_prompt()

Rules:
  - "auto"     (rule-based extraction) → INCLUDED
  - "approved" (user-confirmed AI item) → INCLUDED
  - "ai_auto"  (pending AI review)      → EXCLUDED
  - "rejected" (user dismissed)         → EXCLUDED
  - None / ""  (legacy / missing)       → EXCLUDED (not in allowlist)

Both scope="work" and scope="all" paths are covered.
"""
from __future__ import annotations

import sqlite3
import tempfile
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


# ── Minimal DB stub ───────────────────────────────────────────────────────────

class _FakeDB:
    """Tiny in-memory stand-in with the interface _build_system_prompt uses."""

    def __init__(self, knowledge_rows: list[dict], work: dict | None = None):
        self._knowledge = knowledge_rows
        self._work = work
        self._lock = MagicMock()
        self._conn = _FakeConn()

    def list_knowledge(self, work_id=None, kind=None, limit=200):
        rows = self._knowledge
        if work_id:
            rows = [k for k in rows if k.get("work_id") == work_id]
        return rows[:limit]

    def get_work(self, work_id):
        return self._work


class _FakeConn:
    """Stub for the user_memory query (returns no rows so memory block is skipped)."""
    def execute(self, sql, *args):
        return _FakeCursor([])


class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


# ── Helper ────────────────────────────────────────────────────────────────────

def _item(text: str, review_status: str, work_id: str = "w1") -> dict:
    return {"id": str(uuid.uuid4()), "text": text, "kind": "fact",
            "review_status": review_status, "work_id": work_id,
            "subject": None, "predicate": None, "object": None}


def build_prompt(db, conv, scope="work") -> str:
    from orivellum.api.routes.conversations import _build_system_prompt
    return _build_system_prompt(db, conv, scope=scope)


# ── scope="work" tests ────────────────────────────────────────────────────────

WORK = {"id": "w1", "title": "Test Work"}
CONV_WITH_WORK = {"id": "c1", "work_id": "w1", "model": None}
CONV_NO_WORK   = {"id": "c2", "work_id": None,  "model": None}


def test_auto_item_included_in_work_scope():
    db = _FakeDB([_item("rule fact", "auto")], work=WORK)
    prompt = build_prompt(db, CONV_WITH_WORK, scope="work")
    assert "rule fact" in prompt


def test_approved_item_included_in_work_scope():
    db = _FakeDB([_item("approved fact", "approved")], work=WORK)
    prompt = build_prompt(db, CONV_WITH_WORK, scope="work")
    assert "approved fact" in prompt


def test_ai_auto_item_excluded_from_work_scope():
    """Pending AI items must NOT appear until the user approves them."""
    db = _FakeDB([_item("pending ai fact", "ai_auto")], work=WORK)
    prompt = build_prompt(db, CONV_WITH_WORK, scope="work")
    assert "pending ai fact" not in prompt


def test_rejected_item_excluded_from_work_scope():
    db = _FakeDB([_item("dismissed fact", "rejected")], work=WORK)
    prompt = build_prompt(db, CONV_WITH_WORK, scope="work")
    assert "dismissed fact" not in prompt


def test_mixed_statuses_work_scope():
    """Only trusted items appear; pending + rejected are silently dropped."""
    items = [
        _item("trusted auto",    "auto"),
        _item("trusted approved","approved"),
        _item("pending ai",      "ai_auto"),
        _item("dismissed",       "rejected"),
    ]
    db = _FakeDB(items, work=WORK)
    prompt = build_prompt(db, CONV_WITH_WORK, scope="work")
    assert "trusted auto" in prompt
    assert "trusted approved" in prompt
    assert "pending ai" not in prompt
    assert "dismissed" not in prompt


def test_no_work_id_returns_base_prompt():
    db = _FakeDB([_item("some fact", "auto")], work=None)
    prompt = build_prompt(db, CONV_NO_WORK, scope="work")
    # No knowledge block when there's no work context
    assert "some fact" not in prompt


# ── scope="all" tests ─────────────────────────────────────────────────────────

CONV_ALL = {"id": "c3", "work_id": None, "model": None}


def test_auto_item_included_in_all_scope():
    db = _FakeDB([_item("global auto", "auto", work_id="w2")])
    prompt = build_prompt(db, CONV_ALL, scope="all")
    assert "global auto" in prompt


def test_approved_item_included_in_all_scope():
    db = _FakeDB([_item("global approved", "approved", work_id="w2")])
    prompt = build_prompt(db, CONV_ALL, scope="all")
    assert "global approved" in prompt


def test_ai_auto_item_excluded_from_all_scope():
    db = _FakeDB([_item("global pending", "ai_auto", work_id="w2")])
    prompt = build_prompt(db, CONV_ALL, scope="all")
    assert "global pending" not in prompt


def test_rejected_item_excluded_from_all_scope():
    db = _FakeDB([_item("global rejected", "rejected", work_id="w2")])
    prompt = build_prompt(db, CONV_ALL, scope="all")
    assert "global rejected" not in prompt


def test_mixed_statuses_all_scope():
    items = [
        _item("all auto",     "auto",     work_id="w2"),
        _item("all approved", "approved", work_id="w2"),
        _item("all ai_auto",  "ai_auto",  work_id="w2"),
        _item("all rejected", "rejected", work_id="w2"),
    ]
    db = _FakeDB(items)
    prompt = build_prompt(db, CONV_ALL, scope="all")
    assert "all auto" in prompt
    assert "all approved" in prompt
    assert "all ai_auto" not in prompt
    assert "all rejected" not in prompt
