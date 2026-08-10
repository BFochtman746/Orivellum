"""Tests for topic profile generation (task #349).

Covers:
- ai_extraction_enabled=false: llm_call is NEVER invoked, regardless of force flag
- ai_extraction_enabled=true:  llm_call IS invoked and profiles are written to the DB
- Disabled path via the rebuild-background helper (same gate at the route level)
- Idempotency: topics that already have a profile are skipped unless force=True
- Graceful handling of empty-chunk topics (no LLM call, counted as skipped)
- LLM failure path: errors counted, no DB row written
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Patch target: llm_call is imported lazily inside generate_topic_profiles so
# we patch it at its definition site, not at the module that imports it.
# ---------------------------------------------------------------------------
_LLM_PATCH = "orivellum.capabilities.llm.llm_call"


# ---------------------------------------------------------------------------
# Lightweight DB fixture (real OrivellumDB + schema migrations)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path):
    from orivellum.database.db import OrivellumDB

    instance = OrivellumDB(str(tmp_path / "test.db"))
    yield instance


def _make_topic(db, name: str = "test-topic") -> str:
    """Insert a minimal topic row and return its id."""
    tid = str(uuid.uuid4())
    now = "2026-01-01T00:00:00+00:00"
    with db._lock:
        db._conn.execute(
            "INSERT INTO topics(id, name, kind, meta, created_at) VALUES(?,?,?,?,?)",
            (tid, name, "semantic_cluster", "{}", now),
        )
        db._conn.commit()
    return tid


def _add_chunk(db, topic_id: str, text: str) -> None:
    """Insert a document + chunk and link the document to the topic."""
    now = "2026-01-01T00:00:00+00:00"
    # Every document must have a row in objects (FK)
    doc_id = str(uuid.uuid4())
    chunk_id = str(uuid.uuid4())
    with db._lock:
        # objects row for the document (id must match doc_id)
        db._conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (doc_id, "document", 1, "active", "{}", "{}", now, now),
        )
        db._conn.execute(
            "INSERT INTO documents(id,title,readiness,created_at) VALUES(?,?,?,?)",
            (doc_id, "Doc", "ready", now),
        )
        # objects row for the chunk
        db._conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (chunk_id, "chunk", 1, "active", "{}", "{}", now, now),
        )
        db._conn.execute(
            "INSERT INTO chunks(id,doc_id,page,text,created_at) VALUES(?,?,?,?,?)",
            (chunk_id, doc_id, 1, text, now),
        )
        db._conn.execute(
            "INSERT OR IGNORE INTO topic_members(topic_id,object_id,object_type) VALUES(?,?,?)",
            (topic_id, doc_id, "document"),
        )
        db._conn.commit()


def _get_profile(db, topic_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM topic_profiles WHERE topic_id=?", (topic_id,)
        ).fetchone()
    return dict(row) if row else None


_GOOD_LLM_TEXT = json.dumps(
    {
        "what_it_is": "A cluster about policy research.",
        "purpose": "Helps users understand regulations.",
        "connected": ["law", "governance"],
        "gaps": ["case studies"],
    }
)


def _good_llm_result():
    m = MagicMock()
    m.ok = True
    m.text = _GOOD_LLM_TEXT
    m.error = None
    return m


# ---------------------------------------------------------------------------
# Gate tests: disabled mode NEVER calls llm_call
# ---------------------------------------------------------------------------


class TestGatingWhenAIDisabled:
    """When ai_extraction_enabled=false, llm_call must not be called."""

    def test_disabled_by_default_no_llm_call(self, db):
        # ai_extraction_enabled not set → defaults to "false"
        _make_topic(db)
        with patch(_LLM_PATCH) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)
        mock_llm.assert_not_called()
        assert result["generated"] == 0

    def test_disabled_explicitly_no_llm_call(self, db):
        db.set_setting("ai_extraction_enabled", "false")
        tid = _make_topic(db)
        _add_chunk(db, tid, "Research on policy and governance frameworks in democratic systems.")
        with patch(_LLM_PATCH) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)
        mock_llm.assert_not_called()
        assert result["generated"] == 0
        assert _get_profile(db, tid) is None

    def test_disabled_with_force_still_no_llm_call(self, db):
        """force=True must not bypass the ai_extraction_enabled gate."""
        db.set_setting("ai_extraction_enabled", "false")
        tid = _make_topic(db)
        _add_chunk(
            db, tid, "Important policy content that must never be transmitted to an external LLM."
        )
        with patch(_LLM_PATCH) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None, force=True)
        mock_llm.assert_not_called()
        assert result["generated"] == 0
        assert _get_profile(db, tid) is None

    def test_disabled_returns_zero_counts(self, db):
        db.set_setting("ai_extraction_enabled", "false")
        _make_topic(db, "alpha")
        _make_topic(db, "beta")
        from orivellum.capabilities.topic_profile import generate_topic_profiles

        result = generate_topic_profiles(db, cfg=None)
        assert result == {"generated": 0, "skipped": 0, "errors": 0}


# ---------------------------------------------------------------------------
# Gate tests: enabled mode DOES call llm_call and writes to DB
# ---------------------------------------------------------------------------


class TestGatingWhenAIEnabled:
    """When ai_extraction_enabled=true, llm_call is invoked and profiles are stored."""

    def test_enabled_calls_llm_and_writes_profile(self, db):
        db.set_setting("ai_extraction_enabled", "true")
        tid = _make_topic(db, "policy-research")
        _add_chunk(db, tid, "This cluster covers policy, regulation, and governance research.")

        with patch(_LLM_PATCH, return_value=_good_llm_result()) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        mock_llm.assert_called_once()
        assert result["generated"] == 1
        assert result["errors"] == 0

        profile = _get_profile(db, tid)
        assert profile is not None
        assert profile["what_it_is"] == "A cluster about policy research."
        assert profile["purpose"] == "Helps users understand regulations."
        assert json.loads(profile["connected"]) == ["law", "governance"]
        assert json.loads(profile["gaps"]) == ["case studies"]

    def test_enabled_multiple_topics_all_profiled(self, db):
        db.set_setting("ai_extraction_enabled", "true")
        tids = []
        for name in ("alpha", "beta", "gamma"):
            tid = _make_topic(db, name)
            _add_chunk(
                db,
                tid,
                f"Comprehensive research content about {name} topics and related frameworks.",
            )
            tids.append(tid)

        with patch(_LLM_PATCH, return_value=_good_llm_result()):
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        assert result["generated"] == 3
        for tid in tids:
            assert _get_profile(db, tid) is not None


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_existing_profile_skipped_without_force(self, db):
        db.set_setting("ai_extraction_enabled", "true")
        tid = _make_topic(db)
        _add_chunk(
            db, tid, "Comprehensive analysis of research methodologies and findings across domains."
        )
        now = "2026-01-01T00:00:00+00:00"
        with db._lock:
            db._conn.execute(
                "INSERT INTO topic_profiles(topic_id,what_it_is,purpose,connected,gaps,generated_at)"
                " VALUES(?,?,?,?,?,?)",
                (tid, "Existing description.", "Existing purpose.", "[]", "[]", now),
            )
            db._conn.commit()

        with patch(_LLM_PATCH, return_value=_good_llm_result()) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        mock_llm.assert_not_called()
        assert result["generated"] == 0
        assert _get_profile(db, tid)["what_it_is"] == "Existing description."

    def test_existing_profile_overwritten_with_force(self, db):
        db.set_setting("ai_extraction_enabled", "true")
        tid = _make_topic(db)
        _add_chunk(
            db,
            tid,
            "Policy and governance research covering regulatory frameworks and civic institutions.",
        )
        now = "2026-01-01T00:00:00+00:00"
        with db._lock:
            db._conn.execute(
                "INSERT INTO topic_profiles(topic_id,what_it_is,purpose,connected,gaps,generated_at)"
                " VALUES(?,?,?,?,?,?)",
                (tid, "Old description.", "Old purpose.", "[]", "[]", now),
            )
            db._conn.commit()

        with patch(_LLM_PATCH, return_value=_good_llm_result()):
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None, force=True)

        assert result["generated"] == 1
        assert _get_profile(db, tid)["what_it_is"] == "A cluster about policy research."


# ---------------------------------------------------------------------------
# Edge-case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_topic_without_chunks_is_skipped_not_errored(self, db):
        """A topic with no chunk text should count as skipped, not an error."""
        db.set_setting("ai_extraction_enabled", "true")
        _make_topic(db, "empty-topic")

        with patch(_LLM_PATCH) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        mock_llm.assert_not_called()
        assert result["skipped"] == 1
        assert result["errors"] == 0
        assert result["generated"] == 0

    def test_llm_failure_counted_as_error_no_db_write(self, db):
        """When llm_call returns ok=False, count as error and write nothing to DB."""
        db.set_setting("ai_extraction_enabled", "true")
        tid = _make_topic(db)
        _add_chunk(db, tid, "Detailed thematic content covering key aspects of this topic cluster.")

        bad = MagicMock()
        bad.ok = False
        bad.text = None
        bad.error = "connection refused"

        with patch(_LLM_PATCH, return_value=bad):
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        assert result["errors"] == 1
        assert result["generated"] == 0
        assert _get_profile(db, tid) is None

    def test_llm_returns_empty_what_it_is_counted_as_error(self, db):
        """An LLM reply without what_it_is must not write a blank profile."""
        db.set_setting("ai_extraction_enabled", "true")
        tid = _make_topic(db)
        _add_chunk(
            db,
            tid,
            "Detailed content for verifying the system handles a malformed LLM reply correctly.",
        )

        partial = MagicMock()
        partial.ok = True
        partial.text = json.dumps({"purpose": "Something", "connected": [], "gaps": []})
        partial.error = None

        with patch(_LLM_PATCH, return_value=partial):
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None)

        assert result["errors"] == 1
        assert _get_profile(db, tid) is None

    def test_rebuild_route_gate_skips_llm_when_disabled(self, db):
        """Module-level gate prevents LLM call even when caller passes force=True
        — simulates what the rebuild background task does with run_profiles=True."""
        db.set_setting("ai_extraction_enabled", "false")
        tid = _make_topic(db)
        _add_chunk(db, tid, "Document content that must not be sent externally.")

        with patch(_LLM_PATCH) as mock_llm:
            from orivellum.capabilities.topic_profile import generate_topic_profiles

            result = generate_topic_profiles(db, cfg=None, force=True)

        mock_llm.assert_not_called()
        assert result["generated"] == 0
        assert _get_profile(db, tid) is None
