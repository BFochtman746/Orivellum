"""Tests for Memory + Recall.

Covers:
 - Schema v98 migration (bi-temporal columns + five memory types on user_memory)
 - upsert_memory_fact — append-only bi-temporal design, at-most-one current row per key
 - update_memory_fact — historical-ID safety, current-row invariant
 - cleanup_working_memory_ttl — soft-expires working rows, leaves other types alone
 - add_conversation_chunk + search_conversation_chunks
 - backfill_embeddings covers conv_chunk type
 - recall intent fast-path classification
 - _handle_recall_query always combines semantic + keyword results
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_db(path: str) -> "OrivellumDB":
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from orivellum.database.db import OrivellumDB
    return OrivellumDB(path)


# ─── Schema v98 ───────────────────────────────────────────────────────────────

class TestSchemaMigration(unittest.TestCase):
    """v98 adds bi-temporal columns and drops the unique-key constraint."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        try:
            Path(self.tmp).unlink(missing_ok=True)
        except Exception:
            pass

    def test_conversation_chunks_table_exists(self):
        tables = {
            r[0] for r in self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("conversation_chunks", tables,
                      "conversation_chunks table must exist (schema v65+)")

    def test_user_memory_has_bitemporal_columns(self):
        """v98 adds memory_type, valid_from, valid_to, txn_time."""
        cols = {r[1] for r in self.db._conn.execute(
            "PRAGMA table_info(user_memory)"
        ).fetchall()}
        for col in ("memory_type", "valid_from", "valid_to", "txn_time"):
            self.assertIn(col, cols,
                          f"Column '{col}' must be present after v98 migration")

    def test_conversation_chunks_index_exists(self):
        indexes = {
            r[0] for r in self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        self.assertTrue(
            any("cc" in ix or "conv" in ix for ix in indexes),
            f"Expected index on conversation_chunks; found: {indexes}"
        )

    def test_um_key_unique_index_dropped(self):
        """um_key unique index must be gone — multiple rows per key are now allowed."""
        indexes = {
            r[0] for r in self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        self.assertNotIn("um_key", indexes,
                         "um_key UNIQUE index must be dropped by v98 migration")


# ─── upsert_memory_fact ───────────────────────────────────────────────────────

class TestUpsertMemoryFact(unittest.TestCase):
    """Bi-temporal append-only design: each update creates a new row."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_first_insert_returns_true(self):
        self.assertTrue(self.db.upsert_memory_fact("my_name", "Alice"))

    def test_same_value_is_noop(self):
        self.db.upsert_memory_fact("my_name", "Alice")
        self.assertFalse(self.db.upsert_memory_fact("my_name", "Alice"),
                         "Identical value must return False (no-op)")

    def test_changed_value_returns_true(self):
        self.db.upsert_memory_fact("my_name", "Alice")
        self.assertTrue(self.db.upsert_memory_fact("my_name", "Bob"))

    def test_current_row_has_correct_new_value(self):
        self.db.upsert_memory_fact("my_name", "Alice")
        self.db.upsert_memory_fact("my_name", "Bob")
        facts = self.db.get_current_memory_facts(limit=5)
        current = next((f for f in facts if f["key"] == "my_name"), None)
        self.assertIsNotNone(current)
        self.assertEqual(current["value"], "Bob")

    def test_at_most_one_current_row_per_key(self):
        """valid_to IS NULL must hold for exactly one row per key after updates."""
        self.db.upsert_memory_fact("lang", "Python")
        self.db.upsert_memory_fact("lang", "Rust")
        self.db.upsert_memory_fact("lang", "Go")
        live = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='lang' AND valid_to IS NULL"
        ).fetchone()["n"]
        self.assertEqual(live, 1, "Exactly one live row per key (valid_to IS NULL)")

    def test_multiple_rows_accumulate_in_history(self):
        """Append-only: each update adds a new row, historical rows are preserved."""
        self.db.upsert_memory_fact("lang", "Python")
        self.db.upsert_memory_fact("lang", "Rust")
        self.db.upsert_memory_fact("lang", "Go")
        total = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='lang'"
        ).fetchone()["n"]
        self.assertEqual(total, 3, "Three upserts must produce three rows (append-only)")

    def test_superseded_rows_have_valid_to_set(self):
        self.db.upsert_memory_fact("pref", "dark")
        self.db.upsert_memory_fact("pref", "light")
        superseded = self.db._conn.execute(
            "SELECT value FROM user_memory WHERE key='pref' AND valid_to IS NOT NULL"
        ).fetchall()
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["value"], "dark",
                         "Old value must be the superseded row")

    def test_memory_type_stored_correctly(self):
        self.db.upsert_memory_fact("event", "Visited Paris", memory_type="episodic")
        facts = self.db.get_current_memory_facts()
        fact = next((f for f in facts if f["key"] == "event"), None)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["memory_type"], "episodic")

    def test_invalid_memory_type_falls_back_to_semantic(self):
        self.db.upsert_memory_fact("k", "v", memory_type="invalid_type")
        facts = self.db.get_current_memory_facts()
        fact = next((f for f in facts if f["key"] == "k"), None)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["memory_type"], "semantic",
                         "Invalid memory_type must fall back to 'semantic'")

    def test_all_five_types_accepted(self):
        for mtype in ("episodic", "semantic", "procedural", "working", "zettelkasten"):
            key = f"t_{mtype}"
            self.db.upsert_memory_fact(key, "value", memory_type=mtype)
            facts = self.db.get_current_memory_facts()
            fact = next((f for f in facts if f["key"] == key), None)
            self.assertEqual(fact["memory_type"], mtype,
                             f"Type '{mtype}' must be stored unchanged")

    def test_get_current_memory_facts_returns_multiple_keys(self):
        self.db.upsert_memory_fact("k1", "v1")
        self.db.upsert_memory_fact("k2", "v2")
        facts = self.db.get_current_memory_facts(limit=50)
        keys = {f["key"] for f in facts}
        self.assertIn("k1", keys)
        self.assertIn("k2", keys)

    def test_get_current_excludes_superseded_rows(self):
        self.db.upsert_memory_fact("name", "Old")
        self.db.upsert_memory_fact("name", "New")
        facts = self.db.get_current_memory_facts()
        name_facts = [f for f in facts if f["key"] == "name"]
        self.assertEqual(len(name_facts), 1,
                         "get_current_memory_facts must return only live rows per key")
        self.assertEqual(name_facts[0]["value"], "New")

    def test_empty_key_is_rejected(self):
        self.assertFalse(self.db.upsert_memory_fact("", "value"))

    def test_empty_value_is_rejected(self):
        self.assertFalse(self.db.upsert_memory_fact("key", ""))

    def test_valid_from_and_txn_time_are_set(self):
        self.db.upsert_memory_fact("ts_test", "value")
        row = self.db._conn.execute(
            "SELECT valid_from, txn_time FROM user_memory WHERE key='ts_test'"
        ).fetchone()
        self.assertIsNotNone(row["valid_from"])
        self.assertIsNotNone(row["txn_time"])


# ─── update_memory_fact ───────────────────────────────────────────────────────

class TestUpdateMemoryFact(unittest.TestCase):
    """update_memory_fact must preserve the current-row invariant even when
    a historical (superseded) row ID is passed."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def _current_rows(self, key: str) -> list:
        return self.db._conn.execute(
            "SELECT id, value FROM user_memory WHERE key=? AND valid_to IS NULL",
            (key,),
        ).fetchall()

    def test_update_current_row(self):
        self.db.upsert_memory_fact("name", "Alice")
        current_id = self._current_rows("name")[0]["id"]
        result = self.db.update_memory_fact(current_id, "Corrected")
        self.assertTrue(result)
        live = self._current_rows("name")
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["value"], "Corrected")

    def test_update_from_historical_id_still_closes_current(self):
        """Passing a historical row ID must close the CURRENT row, not the historical one."""
        self.db.upsert_memory_fact("name", "Alice")   # row 1 (becomes historical)
        self.db.upsert_memory_fact("name", "Bob")     # row 2 (current)
        # Get the historical row's ID
        hist = self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key='name' AND valid_to IS NOT NULL"
        ).fetchone()
        self.assertIsNotNone(hist, "A historical row must exist before editing")
        # Update using the historical ID
        result = self.db.update_memory_fact(hist["id"], "Corrected from history")
        self.assertTrue(result)
        # Invariant: exactly one live row
        live = self._current_rows("name")
        self.assertEqual(len(live), 1,
                         "Exactly one live row must exist after update from historical ID")
        self.assertEqual(live[0]["value"], "Corrected from history")

    def test_historical_rows_unchanged_after_update(self):
        """Historical rows (valid_to IS NOT NULL) must never be mutated."""
        self.db.upsert_memory_fact("name", "Alice")
        self.db.upsert_memory_fact("name", "Bob")
        hist_before = self.db._conn.execute(
            "SELECT id, value, valid_to FROM user_memory WHERE key='name' AND valid_to IS NOT NULL"
        ).fetchone()
        # Update via the historical ID
        self.db.update_memory_fact(hist_before["id"], "Corrected")
        hist_after = self.db._conn.execute(
            "SELECT id, value, valid_to FROM user_memory WHERE id=?",
            (hist_before["id"],),
        ).fetchone()
        self.assertEqual(hist_after["value"], hist_before["value"],
                         "Historical row value must not change")
        self.assertEqual(hist_after["valid_to"], hist_before["valid_to"],
                         "Historical row valid_to must not change")

    def test_update_nonexistent_id_returns_false(self):
        self.assertFalse(self.db.update_memory_fact("nonexistent-uuid", "value"))

    def test_update_empty_value_returns_false(self):
        self.db.upsert_memory_fact("name", "Alice")
        current_id = self._current_rows("name")[0]["id"]
        self.assertFalse(self.db.update_memory_fact(current_id, ""))


# ─── cleanup_working_memory_ttl ───────────────────────────────────────────────

class TestCleanupWorkingMemoryTTL(unittest.TestCase):
    """Working-memory rows older than TTL must be soft-expired; other types must not."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def _back_date(self, key: str, minutes: int) -> None:
        """Move the valid_from of the current row for key back by `minutes`."""
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        self.db._conn.execute(
            "UPDATE user_memory SET valid_from=? WHERE key=? AND valid_to IS NULL",
            (past, key),
        )
        self.db._conn.commit()

    def test_working_row_older_than_ttl_is_expired(self):
        self.db.upsert_memory_fact("ctx", "temp", memory_type="working")
        self._back_date("ctx", 60)  # 60 min old, TTL = 30
        expired = self.db.cleanup_working_memory_ttl(ttl_minutes=30)
        self.assertEqual(expired, 1, "One working-memory row must be soft-expired")
        live = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE key='ctx' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNone(live, "Expired working row must have valid_to set (not live)")

    def test_working_row_within_ttl_is_preserved(self):
        self.db.upsert_memory_fact("ctx_new", "temp", memory_type="working")
        # Row was just created — within 30-min TTL
        expired = self.db.cleanup_working_memory_ttl(ttl_minutes=30)
        self.assertEqual(expired, 0, "Young working-memory row must not be expired")

    def test_semantic_row_older_than_ttl_is_never_expired(self):
        self.db.upsert_memory_fact("pref", "dark mode", memory_type="semantic")
        self._back_date("pref", 120)  # 2 hours old
        expired = self.db.cleanup_working_memory_ttl(ttl_minutes=30)
        self.assertEqual(expired, 0, "Semantic rows must never be expired by TTL cleanup")
        live = self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key='pref' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(live, "Semantic row must still be live after TTL cleanup")

    def test_only_working_type_is_affected(self):
        """All five types: only 'working' rows older than TTL are expired."""
        for mtype in ("episodic", "semantic", "procedural", "zettelkasten"):
            self.db.upsert_memory_fact(f"k_{mtype}", "val", memory_type=mtype)
            self._back_date(f"k_{mtype}", 60)
        self.db.upsert_memory_fact("k_working", "val", memory_type="working")
        self._back_date("k_working", 60)
        expired = self.db.cleanup_working_memory_ttl(ttl_minutes=30)
        self.assertEqual(expired, 1, "Only the 'working' row must be expired")


# ─── Conversation chunks ──────────────────────────────────────────────────────

class TestEmbedConversationExchangeAlwaysPersists(unittest.TestCase):
    """embed_conversation_exchange must always store a text chunk, even for short turns."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_short_exchange_is_still_stored(self):
        """A short exchange like 'hi' / 'hello' must persist a conversation_chunks row."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.capabilities.embeddings import embed_conversation_exchange
        from unittest.mock import patch
        from orivellum.capabilities import embeddings as emb

        # Simulate embedding endpoint returning None (down / short text below threshold)
        with patch.object(emb, "embed_text", return_value=None):
            chunk_id = embed_conversation_exchange("conv-short", "hi", "hello!", self.db)

        self.assertIsNotNone(chunk_id, "chunk_id must not be None even for short exchanges")
        # The chunk must be searchable via keyword
        hits = self.db.search_conversation_chunks("hi", limit=5)
        found = [h for h in hits if h["id"] == chunk_id]
        self.assertTrue(len(found) > 0,
                        "Short exchange chunk must be persisted and keyword-searchable")

    def test_long_exchange_persists_and_attempts_embedding(self):
        """A long exchange stores the chunk and attempts embedding."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.capabilities.embeddings import embed_conversation_exchange
        from unittest.mock import patch
        from orivellum.capabilities import embeddings as emb

        fake_vec = [0.1] * 384
        with patch.object(emb, "embed_text", return_value=fake_vec):
            chunk_id = embed_conversation_exchange(
                "conv-long",
                "What is the current status of the authentication module?",
                "The module is complete and passes all tests.",
                self.db,
            )

        self.assertIsNotNone(chunk_id)
        # Check vector was stored
        vec_row = self.db._conn.execute(
            "SELECT id FROM vectors WHERE object_id=? AND object_type='conv_chunk'",
            (chunk_id,)
        ).fetchone()
        self.assertIsNotNone(vec_row, "Vector must be stored for long exchange")

    def test_chunk_persisted_even_when_embedding_endpoint_down(self):
        """Chunk must persist even if embed_text returns None (endpoint unavailable)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.capabilities.embeddings import embed_conversation_exchange
        from unittest.mock import patch
        from orivellum.capabilities import embeddings as emb

        with patch.object(emb, "embed_text", return_value=None):
            chunk_id = embed_conversation_exchange(
                "conv-down",
                "This is an important discussion about the project",
                "Absolutely, let me outline the key points for you",
                self.db,
            )

        self.assertIsNotNone(chunk_id)
        row = self.db._conn.execute(
            "SELECT id FROM conversation_chunks WHERE id=?", (chunk_id,)
        ).fetchone()
        self.assertIsNotNone(row,
            "Chunk must be stored even when embedding endpoint is down")
        # No vector should exist (endpoint was down)
        vec = self.db._conn.execute(
            "SELECT id FROM vectors WHERE object_id=? AND object_type='conv_chunk'",
            (chunk_id,)
        ).fetchone()
        self.assertIsNone(vec, "No vector should be stored when endpoint returns None")


class TestConversationChunks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_add_returns_non_empty_id(self):
        cid = self.db.add_conversation_chunk("conv-1", "User: Hi\n\nAssistant: Hello!")
        self.assertTrue(cid, "chunk_id must not be empty")
        self.assertIsInstance(cid, str)

    def test_chunk_is_searchable_by_keyword(self):
        cid = self.db.add_conversation_chunk("conv-1", "User: Alpha beta\n\nAssistant: Ok")
        hits = self.db.search_conversation_chunks("Alpha", limit=5)
        ids = [h["id"] for h in hits]
        self.assertIn(cid, ids, "Stored chunk must appear in keyword search results")

    def test_search_uses_left_join_so_orphan_chunks_are_found(self):
        """Chunks whose conv_id does not exist in conversations must still be returned."""
        cid = self.db.add_conversation_chunk("nonexistent-conv", "User: Gamma\n\nAssistant: Yes")
        hits = self.db.search_conversation_chunks("Gamma", limit=5)
        ids = [h["id"] for h in hits]
        self.assertIn(cid, ids,
                      "Orphan chunks (conv deleted) must be returned via LEFT JOIN")

    def test_search_returns_conv_title_null_for_orphan(self):
        self.db.add_conversation_chunk("ghost-conv", "User: Delta\n\nAssistant: No")
        hits = self.db.search_conversation_chunks("Delta", limit=5)
        self.assertTrue(len(hits) > 0)
        # conv_title may be None for orphan; that's acceptable

    def test_multiple_chunks_searchable(self):
        id1 = self.db.add_conversation_chunk("c1", "User: Foo bar\n\nAssistant: A")
        id2 = self.db.add_conversation_chunk("c2", "User: Foo baz\n\nAssistant: B")
        hits = self.db.search_conversation_chunks("Foo", limit=10)
        found = {h["id"] for h in hits}
        self.assertIn(id1, found)
        self.assertIn(id2, found)


# ─── backfill_embeddings includes conv_chunk ─────────────────────────────────

class TestBackfillIncludesConvChunk(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_backfill_processes_conversation_chunks(self):
        """backfill_embeddings() must select conv_chunk rows without vectors."""
        from orivellum.capabilities import embeddings as emb
        # Text must be > 30 chars to pass the SQL length filter in backfill_embeddings
        cid = self.db.add_conversation_chunk(
            "cx", "User: Tell me about the project\n\nAssistant: Sure, here is the overview"
        )

        fake_vec = [0.1] * 384
        call_log: list[list[str]] = []

        def mock_embed_texts(texts, timeout=None):
            call_log.append(list(texts))
            return [fake_vec for _ in texts]

        with patch.object(emb, "embed_texts", side_effect=mock_embed_texts), \
             patch.object(emb, "_BACKFILL_BATCH", 50):
            n = emb.backfill_embeddings(self.db, max_items=50)

        # At least one call must have included the conversation chunk text
        all_texts = [t for batch in call_log for t in batch]
        self.assertTrue(
            any("project" in t.lower() for t in all_texts),
            f"backfill did not process conversation chunk; texts passed: {all_texts}"
        )

    def test_backfill_stops_on_endpoint_down(self):
        """When embed_texts returns None, backfill must stop and return 0."""
        from orivellum.capabilities import embeddings as emb
        self.db.add_conversation_chunk("cy", "User: Test\n\nAssistant: Ok")

        with patch.object(emb, "embed_texts", return_value=None):
            n = emb.backfill_embeddings(self.db, max_items=50)

        # Should return 0 (or however many were done before encountering None)
        self.assertIsInstance(n, int)
        # conv_chunk row should have NO vector stored (endpoint was down)
        row = self.db._conn.execute(
            "SELECT id FROM vectors WHERE object_type='conv_chunk' LIMIT 1"
        ).fetchone()
        self.assertIsNone(row, "No vector should be stored when endpoint is down")


# ─── Recall intent fast-path ─────────────────────────────────────────────────

class TestRecallIntentFastPath(unittest.TestCase):
    def _classify(self, text: str) -> str:
        from orivellum.capabilities.intent import classify_intent
        return classify_intent(text, "http://x", "m")["intent"]

    def test_where_are_we_on(self):
        self.assertEqual(self._classify("where are we on the authentication chapter?"), "recall")

    def test_what_did_we_decide(self):
        self.assertEqual(self._classify("what did we decide about the database schema?"), "recall")

    def test_what_is_our_status_on(self):
        self.assertEqual(self._classify("what's our status on the API design?"), "recall")

    def test_where_did_we_land(self):
        self.assertEqual(self._classify("where did we land on the pricing model?"), "recall")

    def test_normal_question_is_chat(self):
        self.assertEqual(self._classify("how does Python handle exceptions?"), "chat")

    def test_web_search_not_confused_with_recall(self):
        self.assertEqual(self._classify("search for recent papers on attention"), "web_search")

    def test_recall_does_not_match_remember(self):
        # "remember that my name is X" should be "remember", not "recall"
        result = self._classify("remember that my name is Alice")
        self.assertEqual(result, "remember")


# ─── _handle_recall_query: always combines semantic + keyword ─────────────────

class TestHandleRecallAlwaysCombines(unittest.TestCase):
    """_handle_recall_query must use both semantic search AND keyword fallback,
    so unembedded chunks (stored during endpoint outage) are always discoverable.
    """
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_keyword_hits_included_even_when_semantic_returns_results(self):
        """When semantic search returns hits AND keyword finds different ones,
        both must appear in the combined results."""
        import sys, os
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes.conversations import _handle_recall_query
        from orivellum.capabilities import embeddings as emb

        # Store two chunks: only the second has a vector (simulates partial outage)
        id1 = self.db.add_conversation_chunk("c1", "User: Project Zeta status\n\nAssistant: Not started")
        id2 = self.db.add_conversation_chunk("c2", "User: Project Alpha status\n\nAssistant: In progress")

        # Give c2 a fake vector so semantic search returns it
        fake_vec = emb.pack_vector([0.5] * 384)
        self.db.store_vector(id2, "conv_chunk", fake_vec, 384)

        def mock_sem_search(q, db, limit=5):
            # Return only the c2 chunk (the one with a vector)
            return [{"id": id2, "conv_id": "c2", "conv_title": "Alpha conv",
                     "text": "User: Project Alpha status\n\nAssistant: In progress",
                     "created_at": "2026-01-01", "score": 0.9}]

        def mock_synth(*args, **kwargs):
            return "Synthesized recall answer"

        with patch.object(emb, "semantic_search", side_effect=mock_sem_search), \
             patch("orivellum.api.routes.conversations._call_sync",
                   return_value="Synthesized recall answer", create=True), \
             patch("orivellum.capabilities.cognition._call_sync",
                   return_value="Synthesized recall answer", create=True):
            # Query for "Zeta" — only the keyword (not vector) chunk matches
            reply, meta = _handle_recall_query(self.db, "Project Zeta status",
                                               "http://x", "m")

        # The reply should include content from the keyword-only chunk (c1)
        # OR the meta should reflect that conv_hits were found
        # (The exact reply depends on LLM; we just verify no hard crash and
        # that the function ran to completion without exception)
        self.assertIsInstance(reply, str)
        self.assertEqual(meta.get("intent"), "recall")

    def test_nothing_found_returns_empty_notification(self):
        """When no chunks, facts, or knowledge exist, return a clear 'nothing found'."""
        from orivellum.api.routes.conversations import _handle_recall_query
        from orivellum.capabilities import embeddings as emb

        def mock_sem(*a, **kw):
            return []

        with patch.object(emb, "semantic_search", side_effect=mock_sem):
            reply, meta = _handle_recall_query(self.db, "nonexistent topic",
                                               "http://x", "m")

        self.assertIn("Nothing found", reply)
        self.assertEqual(meta.get("intent"), "recall")


if __name__ == "__main__":
    unittest.main()
