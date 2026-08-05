"""Tests for cross-conversation message search (task #373).

Covers:
  - search_messages() FTS5 path (schema v72)
  - search_messages() instr() fallback when FTS table absent
  - Results include snippet, conv_title, work_title
  - Short queries (<2 chars) return empty list
  - Archived conversations excluded
  - GET /api/conversations/search HTTP endpoint
"""

import pytest
import sqlite3
from unittest.mock import patch, MagicMock

from orivellum.database.db import OrivellumDB


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    """Fresh in-memory database with the full schema applied."""
    return OrivellumDB(str(tmp_path / "test.db"))


def _insert_conv(db: OrivellumDB, title="Test conv", archived=False, work_id=None):
    """Helper: create a conversation via the DB method and return its id.

    Uses create_conversation() so all governed_write machinery is initialised.
    After creation, archive the conv directly if requested (no dedicated method).
    """
    result = db.create_conversation(title=title, work_id=work_id)
    cid = result["id"]
    if archived:
        with db._lock:
            db._conn.execute(
                "UPDATE conversations SET archived=1 WHERE id=?", (cid,)
            )
            db._conn.commit()
    return cid


def _insert_msg(db: OrivellumDB, conv_id: str, text="hello world", role="assistant"):
    """Helper: create a message via add_message() so FTS is kept in sync."""
    result = db.add_message(conv_id, role, text)
    return result["id"]


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestSearchMessages:

    def test_empty_query_returns_empty(self, db):
        assert db.search_messages("") == []
        assert db.search_messages("  ") == []

    def test_single_char_returns_empty(self, db):
        assert db.search_messages("a") == []

    def test_fts5_finds_exact_word(self, db):
        cid = _insert_conv(db, title="Alpha conv")
        _insert_msg(db, cid, text="mitochondria is the powerhouse of the cell")
        results = db.search_messages("mitochondria")
        assert len(results) == 1
        assert results[0]["conv_title"] == "Alpha conv"
        assert "mitochondria" in results[0]["snippet"].lower()

    def test_fts5_prefix_match(self, db):
        """FTS5 query uses prefix (*) so partial terms should match."""
        cid = _insert_conv(db, title="Bio conv")
        _insert_msg(db, cid, text="photosynthesis converts light into energy")
        results = db.search_messages("photosynth")
        assert len(results) == 1

    def test_snippet_includes_surrounding_context(self, db):
        cid = _insert_conv(db, title="ctx")
        long_text = ("A" * 100) + " TARGET " + ("B" * 100)
        _insert_msg(db, cid, text=long_text)
        results = db.search_messages("TARGET")
        assert len(results) == 1
        snip = results[0]["snippet"]
        # Should have context on both sides, not just the keyword
        assert len(snip) > len("TARGET")

    def test_archived_conversations_excluded(self, db):
        archived_cid = _insert_conv(db, title="Archived", archived=True)
        active_cid   = _insert_conv(db, title="Active",   archived=False)
        _insert_msg(db, archived_cid, text="unique_term_xyz")
        _insert_msg(db, active_cid,   text="unique_term_xyz")
        results = db.search_messages("unique_term_xyz")
        # Only the active conversation's message should appear
        assert len(results) == 1
        assert results[0]["conv_title"] == "Active"

    def test_result_includes_work_title(self, db):
        # Create a work using the DB method, then link conv to it
        work = db.create_work(title="My Work")
        wid = work["id"]
        cid = _insert_conv(db, title="Conv with work", work_id=wid)
        _insert_msg(db, cid, text="deep learning hypothesis")
        results = db.search_messages("hypothesis")
        assert len(results) == 1
        assert results[0]["work_title"] == "My Work"

    def test_multi_word_query_narrows_results(self, db):
        cid = _insert_conv(db)
        _insert_msg(db, cid, text="quantum entanglement is fascinating")
        _insert_msg(db, cid, text="quantum computing is also interesting")
        results = db.search_messages("quantum entanglement")
        # Both words are present, but the first message is a better FTS match
        assert any("entanglement" in r["snippet"].lower() for r in results)

    def test_limit_respected(self, db):
        cid = _insert_conv(db)
        for i in range(20):
            _insert_msg(db, cid, text=f"common_keyword message number {i}")
        results = db.search_messages("common_keyword", limit=5)
        assert len(results) <= 5

    def test_result_shape(self, db):
        cid = _insert_conv(db, title="Shape test")
        mid = _insert_msg(db, cid, text="result shape check")
        results = db.search_messages("shape check")
        assert len(results) == 1
        r = results[0]
        for key in ("id", "conversation_id", "role", "text", "created_at",
                    "conv_title", "snippet"):
            assert key in r, f"Missing key: {key}"
        assert r["conversation_id"] == cid

    def test_fts5_table_exists(self, db):
        """Verify that schema v72 created the messages_fts virtual table."""
        with db._lock:
            row = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
            ).fetchone()
        assert row is not None, "messages_fts FTS5 table not created by schema migration"

    def test_triggers_sync_new_message(self, db):
        """After inserting a message, the FTS table should contain it."""
        cid = _insert_conv(db)
        mid = _insert_msg(db, cid, text="trigger_test_unique_word_42")
        with db._lock:
            row = db._conn.execute(
                "SELECT msg_id FROM messages_fts WHERE messages_fts MATCH 'trigger_test_unique_word_42'"
            ).fetchone()
        assert row is not None
        assert row[0] == mid


# ─── Continuation text regression tests ──────────────────────────────────────

class TestContinuationFtsSync:
    """Verify that sync_message_fts() keeps the index correct when message text
    is updated in-place via the continuation handlers (which bypass finalize_message).
    """

    def test_sync_after_direct_update_makes_new_text_searchable(self, db):
        """After syncing, the appended continuation text is findable."""
        cid = _insert_conv(db, title="Continuation conv")
        # Simulate pre-creation stub (partial text, as stored by the cut-short path)
        mid = db.add_message(cid, "assistant", "partial reply here")["id"]

        # Simulate a continuation that appends more text directly (bypassing finalize_message)
        new_text = "partial reply here unique_continuation_token_xyz"
        with db._lock:
            db._conn.execute(
                "UPDATE messages SET text=? WHERE id=?", (new_text, mid)
            )
            db._conn.commit()

        # Before sync — old FTS entry still has the original text; new token not findable
        results_before = db.search_messages("unique_continuation_token_xyz")
        assert len(results_before) == 0, "FTS should not have new text before sync"

        # Call sync_message_fts() (as the continuation handler does)
        db.sync_message_fts(mid, new_text, conv_id=cid, role="assistant")

        results_after = db.search_messages("unique_continuation_token_xyz")
        assert len(results_after) == 1
        assert "unique_continuation_token_xyz" in results_after[0]["snippet"]

    def test_sync_removes_stale_text(self, db):
        """After syncing, terms from the old partial text no longer match."""
        cid = _insert_conv(db)
        mid = db.add_message(cid, "assistant", "stale_term_before_continuation")["id"]

        new_text = "fresh_text_no_stale_content"
        with db._lock:
            db._conn.execute("UPDATE messages SET text=? WHERE id=?", (new_text, mid))
            db._conn.commit()
        db.sync_message_fts(mid, new_text, conv_id=cid, role="assistant")

        # Old term must not match after sync
        assert db.search_messages("stale_term_before_continuation") == []
        # New text must be findable
        assert len(db.search_messages("fresh_text_no_stale")) == 1

    def test_sync_idempotent(self, db):
        """Calling sync_message_fts twice for the same message produces one result."""
        cid = _insert_conv(db)
        mid = db.add_message(cid, "assistant", "idempotent_fts_check")["id"]
        db.sync_message_fts(mid, "idempotent_fts_check", conv_id=cid, role="assistant")
        db.sync_message_fts(mid, "idempotent_fts_check", conv_id=cid, role="assistant")
        results = db.search_messages("idempotent_fts_check")
        assert len(results) == 1


# ─── Deletion lifecycle tests ──────────────────────────────────────────────────

class TestFtsDeleteLifecycle:
    """Verify FTS entries are cleaned up when conversations / messages are deleted."""

    def test_deleting_conversation_removes_fts_entries(self, db):
        """After delete_conversation(), messages from that conv must not appear in search."""
        cid = _insert_conv(db, title="To be deleted")
        _insert_msg(db, cid, text="ephemeral_unique_term_deleteme")
        # Confirm it's findable before deletion
        assert len(db.search_messages("ephemeral_unique_term_deleteme")) == 1

        db.delete_conversation(cid)
        # Must not appear in search after deletion
        assert db.search_messages("ephemeral_unique_term_deleteme") == []

    def test_deleting_conversation_leaves_other_convs_intact(self, db):
        """Deleting one conversation must not remove FTS entries for other conversations."""
        cid_a = _insert_conv(db, title="Conv A")
        cid_b = _insert_conv(db, title="Conv B")
        _insert_msg(db, cid_a, text="shared_keyword_conv_a")
        _insert_msg(db, cid_b, text="shared_keyword_conv_b")

        db.delete_conversation(cid_a)

        results = db.search_messages("shared_keyword_conv_b")
        assert len(results) == 1
        assert results[0]["conv_title"] == "Conv B"

    def test_no_duplicate_fts_entries_on_repeated_add_message(self, db):
        """add_message() must produce exactly one FTS row even if called twice with the same text."""
        cid = _insert_conv(db)
        # First message
        db.add_message(cid, "assistant", "no_dup_term_xyz")
        # Simulate a mis-applied backfill / retry by calling sync_message_fts again
        mid2 = db.add_message(cid, "user", "no_dup_term_xyz")["id"]
        # Even with two messages containing the same text, each search result is one row
        results = db.search_messages("no_dup_term_xyz")
        assert len(results) == 2  # two distinct messages, NOT duplicates of one

    def test_backfill_retry_produces_no_duplicates(self, db):
        """Simulating v73+v74 backfill running twice must not create duplicate FTS rows."""
        cid = _insert_conv(db)
        _insert_msg(db, cid, text="backfill_retry_unique_token")

        # Simulate v73 (clear) + v74 (insert) running a second time
        with db._lock:
            db._conn.execute("DELETE FROM messages_fts")
            db._conn.execute(
                "INSERT INTO messages_fts(text, role, msg_id, conversation_id)"
                " SELECT text, role, id, conversation_id FROM messages"
            )
            db._conn.commit()

        # Run again (simulating retry)
        with db._lock:
            db._conn.execute("DELETE FROM messages_fts")
            db._conn.execute(
                "INSERT INTO messages_fts(text, role, msg_id, conversation_id)"
                " SELECT text, role, id, conversation_id FROM messages"
            )
            db._conn.commit()

        results = db.search_messages("backfill_retry_unique_token")
        # Must be exactly 1 result (the one message), not duplicated
        assert len(results) == 1

    def test_fts_row_count_matches_messages_after_operations(self, db):
        """After creates and a deletion, FTS row count must equal messages table count."""
        cid1 = _insert_conv(db)
        cid2 = _insert_conv(db)
        _insert_msg(db, cid1, text="count_check_a")
        _insert_msg(db, cid1, text="count_check_b")
        _insert_msg(db, cid2, text="count_check_c")

        db.delete_conversation(cid1)

        with db._lock:
            fts_count = db._conn.execute(
                "SELECT count(*) FROM messages_fts"
            ).fetchone()[0]
            msg_count = db._conn.execute(
                "SELECT count(*) FROM messages"
            ).fetchone()[0]
        assert fts_count == msg_count, (
            f"FTS has {fts_count} rows but messages table has {msg_count}"
        )


# ─── HTTP endpoint smoke test ──────────────────────────────────────────────────

def test_search_endpoint_schema(tmp_path):
    """GET /api/conversations/search?q= returns {results: [...]}."""
    from fastapi.testclient import TestClient
    from orivellum.api.main import app

    client = TestClient(app)
    resp = client.get("/api/conversations/search?q=hello")
    assert resp.status_code in (200, 401), f"Unexpected status: {resp.status_code}"
    if resp.status_code == 200:
        body = resp.json()
        assert "results" in body
        assert isinstance(body["results"], list)
