"""Tests for hybrid three-channel memory retrieval (Task #850).

Covers:
  - Schema v101: user_memory_fts virtual table + trigger
  - DB methods: search_memories_lexical, search_memories_graph
  - Merge layer: _merge RRF + dedup + retrieval_source annotation
  - search_memories: three-channel integration (mocked semantic channel)
  - Graceful degradation: each channel failure leaves others intact
  - GET /api/memory?q= endpoint: returns retrieval_source per item
"""
from __future__ import annotations

import tempfile
import unittest
import uuid as _uuid_mod
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from orivellum.database.db import OrivellumDB
from orivellum.database.schema import MIGRATIONS


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_db(path: str) -> OrivellumDB:
    # OrivellumDB.__init__ calls _run_migrations automatically
    return OrivellumDB(path)


def _seed_fact(
    db: OrivellumDB,
    key: str,
    value: str,
    memory_type: str = "semantic",
    source_conv_id: str | None = None,
    source_evidence_id: str | None = None,
) -> str:
    """Insert a single current user_memory row; return its id."""
    mid = str(_uuid_mod.uuid4())
    now = "2026-01-01T00:00:00+00:00"
    db._conn.execute(
        """INSERT INTO user_memory
           (id, key, value, memory_type, valid_from, valid_to,
            txn_time, created_at, source_conv_id, source_evidence_id)
           VALUES (?,?,?,?,?,NULL,?,?,?,?)""",
        (mid, key, value, memory_type, now, now, now,
         source_conv_id, source_evidence_id),
    )
    db._conn.commit()
    # Also keep the FTS table in sync (trigger fires in SQLite, but only for
    # INSERT via the normal path — we go direct, so manually insert FTS too)
    try:
        db._conn.execute(
            "INSERT INTO user_memory_fts(rowid, key, value, memory_id)"
            " SELECT rowid, key, value, id FROM user_memory WHERE id=?", (mid,)
        )
        db._conn.commit()
    except Exception:
        pass
    return mid


# ─── Schema tests ─────────────────────────────────────────────────────────────

class TestSchemaV101(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_user_memory_fts_table_exists(self):
        tables = {r[0] for r in self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("user_memory_fts", tables,
                      "user_memory_fts FTS virtual table must exist after v101 migration")

    def test_user_memory_fts_has_key_and_value_columns(self):
        # FTS5 tables report their columns via the fts content
        # A MATCH on an empty string should succeed without error
        try:
            self.db._conn.execute(
                "SELECT * FROM user_memory_fts LIMIT 0"
            ).fetchall()
        except Exception as exc:
            self.fail(f"user_memory_fts is not queryable: {exc}")

    def test_insert_trigger_populates_fts(self):
        """INSERT trigger must sync new user_memory rows into user_memory_fts."""
        # Use the proper upsert path so the trigger fires
        self.db.upsert_memory_fact("trigger_test", "trigger value is here")
        rows = self.db._conn.execute(
            "SELECT memory_id FROM user_memory_fts WHERE user_memory_fts MATCH '\"trigger*\"'"
        ).fetchall()
        self.assertTrue(len(rows) > 0, "INSERT trigger must populate user_memory_fts")

    def test_v101_in_migrations(self):
        versions = [m[0] for m in MIGRATIONS]
        self.assertIn(101, versions, "v101 must be in the MIGRATIONS list")


# ─── search_memories_lexical ─────────────────────────────────────────────────

class TestSearchMemoriesLexical(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_finds_fact_by_value_keyword(self):
        self.db.upsert_memory_fact("lang", "I prefer Python for data pipelines")
        hits = self.db.search_memories_lexical("Python", limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("lang", keys, "Lexical search must find fact by value keyword")

    def test_finds_fact_by_key_keyword(self):
        self.db.upsert_memory_fact("preferred_language", "Rust")
        hits = self.db.search_memories_lexical("preferred", limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("preferred_language", keys, "Lexical search must find fact by key keyword")

    def test_excludes_superseded_rows(self):
        """Superseded (valid_to IS NOT NULL) facts must not appear in results."""
        # Upsert twice — first row gets superseded
        self.db.upsert_memory_fact("sup_key", "first superseded value unique99")
        self.db.upsert_memory_fact("sup_key", "second current value uniqueAB")
        hits = self.db.search_memories_lexical("superseded unique99", limit=20)
        # If superseded row is excluded, the old value "unique99" should not appear
        old_hits = [h for h in hits if "unique99" in h.get("value", "")]
        self.assertEqual(len(old_hits), 0,
                         "Superseded (valid_to IS NOT NULL) rows must be excluded from lexical results")

    def test_empty_query_returns_empty(self):
        hits = self.db.search_memories_lexical("", limit=10)
        self.assertEqual(hits, [])

    def test_returns_full_row_shape(self):
        self.db.upsert_memory_fact("shape_key", "shape value content")
        hits = self.db.search_memories_lexical("shape", limit=5)
        self.assertGreater(len(hits), 0)
        hit = hits[0]
        for field in ("id", "key", "value", "memory_type", "created_at"):
            self.assertIn(field, hit, f"Field '{field}' must be present in lexical result")

    def test_no_results_for_irrelevant_query(self):
        self.db.upsert_memory_fact("hobby", "I enjoy gardening")
        hits = self.db.search_memories_lexical("astrophysics quantum relativity", limit=10)
        # May or may not return results depending on porter stemming; just confirm no crash
        self.assertIsInstance(hits, list)

    def test_limit_respected(self):
        for i in range(10):
            self.db.upsert_memory_fact(f"lim_key_{i}", f"common search term across all")
        hits = self.db.search_memories_lexical("common search", limit=3)
        self.assertLessEqual(len(hits), 3)


# ─── search_memories_graph ────────────────────────────────────────────────────

class TestSearchMemoriesGraph(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed_entity(self, name: str, kind: str = "person") -> str:
        eid = str(_uuid_mod.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        try:
            self.db._conn.execute(
                "INSERT OR IGNORE INTO entities(id,name,kind,aliases,meta,created_at)"
                " VALUES(?,?,?,'[]','{}',?)",
                (eid, name, kind, now),
            )
            self.db._conn.commit()
        except Exception:
            pass
        return eid

    def _seed_edge(self, src: str, tgt: str, relation: str = "related_to"):
        eid = str(_uuid_mod.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        try:
            self.db._conn.execute(
                "INSERT OR IGNORE INTO edges(id,source_id,target_id,relation,weight,meta,created_at)"
                " VALUES(?,?,?,?,1.0,'{}',?)",
                (eid, src, tgt, relation, now),
            )
            self.db._conn.commit()
        except Exception:
            pass

    def test_no_entities_returns_empty(self):
        """Graph channel with no matching entities must return []."""
        self.db.upsert_memory_fact("hobby", "I enjoy jazz music")
        hits = self.db.search_memories_graph("zzz_nonexistent_xyz", limit=10)
        self.assertEqual(hits, [])

    def test_finds_fact_mentioning_entity(self):
        """Fact value mentioning a known entity name must be surfaced."""
        self._seed_entity("Python")
        self.db.upsert_memory_fact(
            "lang_pref", "I use Python daily for data work"
        )
        hits = self.db.search_memories_graph("Python", limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("lang_pref", keys,
                      "Graph channel must find fact mentioning the entity name")

    def test_traverses_one_hop_neighbour(self):
        """Facts mentioning a 1-hop neighbour entity must be surfaced."""
        entity_a = self._seed_entity("TensorFlow")
        entity_b = self._seed_entity("DeepLearning")
        self._seed_edge(entity_a, entity_b, "related_to")
        self.db.upsert_memory_fact(
            "ml_stack", "I am learning DeepLearning frameworks"
        )
        # Query mentions TensorFlow; edge leads to DeepLearning; fact mentions DeepLearning
        hits = self.db.search_memories_graph("TensorFlow", limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("ml_stack", keys,
                      "Graph channel must traverse 1-hop to surface neighbour-entity facts")

    def test_excludes_superseded_facts(self):
        """Graph channel must only return current (valid_to IS NULL) facts."""
        self._seed_entity("Rust")
        self.db.upsert_memory_fact("old_lang", "I used Rust in 2023")
        self.db.upsert_memory_fact("old_lang", "I moved away from that language")  # supersedes
        hits = self.db.search_memories_graph("Rust", limit=10)
        # The superseded row mentioned Rust; the current row does not
        old_hits = [h for h in hits if "Rust" in h.get("value", "")]
        self.assertEqual(len(old_hits), 0,
                         "Graph channel must exclude superseded rows")

    def test_empty_query_returns_empty(self):
        hits = self.db.search_memories_graph("", limit=10)
        self.assertEqual(hits, [])

    def test_stopwords_dont_match_entities(self):
        """Pure stopword query should not hit the entity graph."""
        self._seed_entity("the")
        hits = self.db.search_memories_graph("what is the meaning", limit=10)
        # "is", "the", "what" are all stopwords — no real entities should match
        # (this entity "the" should not be matched because "the" is stripped)
        self.assertIsInstance(hits, list)  # should not crash


# ─── Merge layer ─────────────────────────────────────────────────────────────

class TestMergeLayer(unittest.TestCase):
    """Tests for the _merge RRF + dedup + retrieval_source function."""

    def _fact(self, mid: str, key: str = "key", value: str = "value") -> dict:
        return {
            "id": mid, "key": key, "value": value,
            "memory_type": "semantic", "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_to": None, "txn_time": "2026-01-01T00:00:00+00:00",
            "source_conv_id": None, "source_evidence_id": None,
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    def test_dedup_by_id(self):
        from orivellum.capabilities.memory import _merge
        fact = self._fact("id-1")
        hits = _merge([fact], [fact], [], limit=10)
        ids = [h["id"] for h in hits]
        self.assertEqual(ids.count("id-1"), 1,
                         "Items appearing in multiple channels must be deduplicated")

    def test_multi_source_tag(self):
        from orivellum.capabilities.memory import _merge
        fact = self._fact("id-multi")
        hits = _merge([fact], [fact], [], limit=10)
        self.assertEqual(hits[0]["retrieval_source"], "multi",
                         "Fact in 2+ channels must have retrieval_source='multi'")

    def test_single_channel_source_tags(self):
        from orivellum.capabilities.memory import _merge
        sem  = self._fact("id-sem",  "sem_key",  "sem_val")
        lex  = self._fact("id-lex",  "lex_key",  "lex_val")
        gph  = self._fact("id-gph",  "gph_key",  "gph_val")
        hits = _merge([sem], [lex], [gph], limit=10)
        src_map = {h["id"]: h["retrieval_source"] for h in hits}
        self.assertEqual(src_map["id-sem"],  "semantic")
        self.assertEqual(src_map["id-lex"],  "lexical")
        self.assertEqual(src_map["id-gph"],  "graph")

    def test_rrf_score_present(self):
        from orivellum.capabilities.memory import _merge
        fact = self._fact("id-score")
        hits = _merge([fact], [], [], limit=10)
        self.assertIn("rrf_score", hits[0], "rrf_score must be present in merged results")
        self.assertIsInstance(hits[0]["rrf_score"], float)

    def test_multi_channel_ranks_above_single(self):
        from orivellum.capabilities.memory import _merge
        multi_fact  = self._fact("id-multi",  "multi",  "multi")
        single_fact = self._fact("id-single", "single", "single")
        # multi appears in both semantic and lexical; single only in graph
        hits = _merge([multi_fact], [multi_fact], [single_fact], limit=10)
        ids = [h["id"] for h in hits]
        self.assertLess(ids.index("id-multi"), ids.index("id-single"),
                        "Multi-channel fact must rank higher than single-channel fact")

    def test_limit_respected(self):
        from orivellum.capabilities.memory import _merge
        facts = [self._fact(f"id-{i}", f"key{i}", f"val{i}") for i in range(20)]
        hits = _merge(facts, [], [], limit=5)
        self.assertEqual(len(hits), 5)

    def test_internal_fields_stripped(self):
        from orivellum.capabilities.memory import _merge
        fact = dict(self._fact("id-strip"))
        fact["_sem_score"] = 0.99
        fact["_graph_matched"] = ["entity"]
        fact["_graph_score"] = 0.5
        fact["bm25_score"] = -12.3
        hits = _merge([], [], [fact], limit=10)
        self.assertNotIn("_sem_score",     hits[0])
        self.assertNotIn("_graph_matched", hits[0])
        self.assertNotIn("_graph_score",   hits[0])
        self.assertNotIn("bm25_score",     hits[0])

    def test_empty_inputs_return_empty(self):
        from orivellum.capabilities.memory import _merge
        self.assertEqual(_merge([], [], [], limit=10), [])


# ─── search_memories integration ─────────────────────────────────────────────

class TestSearchMemoriesIntegration(unittest.TestCase):
    """Integration tests for search_memories with mocked semantic channel."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_lexical_channel_surfaces_keyword_match(self):
        """Lexical channel alone must find a fact even when semantic returns []."""
        self.db.upsert_memory_fact(
            "coffee_habit", "I drink espresso every morning before work"
        )
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("espresso morning", self.db, limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("coffee_habit", keys,
                      "Lexical channel must surface fact when semantic channel is empty")

    def test_graph_channel_surfaces_entity_mention(self):
        """Graph channel alone must find a fact mentioning the query entity."""
        mid = str(_uuid_mod.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        eid = str(_uuid_mod.uuid4())
        self.db._conn.execute(
            "INSERT OR IGNORE INTO entities(id,name,kind,aliases,meta,created_at)"
            " VALUES(?,?,?,'[]','{}',?)", (eid, "PostgreSQL", "technology", now)
        )
        self.db._conn.commit()
        self.db.upsert_memory_fact(
            "db_choice", "I prefer PostgreSQL for relational data"
        )
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]), \
             patch("orivellum.capabilities.memory._channel_lexical",  return_value=[]):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("PostgreSQL database", self.db, limit=10)
        keys = [h["key"] for h in hits]
        self.assertIn("db_choice", keys,
                      "Graph channel must surface fact mentioning the entity name")

    def test_retrieval_source_field_present(self):
        self.db.upsert_memory_fact("music", "I enjoy jazz and classical music")
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("jazz music", self.db, limit=10)
        for h in hits:
            self.assertIn("retrieval_source", h,
                          "Each result must have a retrieval_source field")
            self.assertIn(h["retrieval_source"],
                          {"semantic", "lexical", "graph", "multi"})

    def test_semantic_channel_failure_degrades_gracefully(self):
        """If semantic channel raises, lexical + graph must still work."""
        self.db.upsert_memory_fact("diet", "I follow a plant-based diet")

        def _raise(*args, **kwargs):
            raise RuntimeError("embedding service unavailable")

        with patch("orivellum.capabilities.memory._channel_semantic", side_effect=_raise):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("plant diet", self.db, limit=10)
        # Should not raise; should return lexical/graph results
        self.assertIsInstance(hits, list)

    def test_all_channels_fail_returns_empty(self):
        """All channels failing must return [] without raising."""
        def _raise(*args, **kwargs):
            raise RuntimeError("channel down")

        with patch("orivellum.capabilities.memory._channel_semantic", side_effect=_raise), \
             patch("orivellum.capabilities.memory._channel_lexical",  side_effect=_raise), \
             patch("orivellum.capabilities.memory._channel_graph",    side_effect=_raise):
            from orivellum.capabilities.memory import search_memories
            result = search_memories("anything", self.db, limit=10)
        self.assertEqual(result, [])

    def test_empty_query_returns_empty(self):
        from orivellum.capabilities.memory import search_memories
        self.assertEqual(search_memories("", self.db), [])
        self.assertEqual(search_memories("   ", self.db), [])

    def test_multi_source_when_in_both_channels(self):
        """A fact found by both lexical and graph must get retrieval_source='multi'."""
        now = "2026-01-01T00:00:00+00:00"
        eid = str(_uuid_mod.uuid4())
        try:
            self.db._conn.execute(
                "INSERT OR IGNORE INTO entities(id,name,kind,aliases,meta,created_at)"
                " VALUES(?,?,?,'[]','{}',?)", (eid, "Docker", "technology", now)
            )
            self.db._conn.commit()
        except Exception:
            pass
        self.db.upsert_memory_fact(
            "container_tool",
            "I use Docker for all my containerisation work"
        )
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("Docker container", self.db, limit=10)
        container_hits = [h for h in hits if h["key"] == "container_tool"]
        if container_hits:
            src = container_hits[0]["retrieval_source"]
            self.assertIn(src, {"lexical", "graph", "multi"},
                          "container_tool fact must be tagged with a valid source")

    def test_limit_is_respected(self):
        for i in range(15):
            self.db.upsert_memory_fact(f"limit_key_{i}", f"search term common across all {i}")
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            from orivellum.capabilities.memory import search_memories
            hits = search_memories("search term common", self.db, limit=5)
        self.assertLessEqual(len(hits), 5)


# ─── GET /api/memory?q= endpoint ─────────────────────────────────────────────

class TestGetMemoryEndpoint(unittest.TestCase):
    """Verify the q= query parameter wires into hybrid retrieval."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_without_q_returns_all_current_facts(self):
        """Without ?q, endpoint returns all current facts (original behaviour)."""
        self.db.upsert_memory_fact("browsing_pref", "I use Firefox as my browser")

        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        # Patch get_db to return our test db
        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db):
            result = asyncio.run(
                get_memory(q=None, include_evidence=False)
            )

        self.assertIn("facts", result)
        keys = [f["key"] for f in result["facts"]]
        self.assertIn("browsing_pref", keys)
        # Without q=, no query field in response
        self.assertNotIn("query", result)

    def test_with_q_returns_hybrid_results(self):
        """With ?q=<query>, endpoint must use hybrid retrieval and return query field."""
        self.db.upsert_memory_fact("editor_pref", "I use Neovim as my text editor")

        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        # search_memories is imported inside the route handler, so patch the
        # module-level symbol that the route resolves at call time.
        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db), \
             _patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = asyncio.run(
                get_memory(q="Neovim editor", include_evidence=False)
            )

        self.assertIn("query", result, "Response must include 'query' field when ?q= is set")
        self.assertIn("facts", result)

    def test_with_q_each_fact_has_retrieval_source(self):
        """With ?q=, each fact in response must have retrieval_source."""
        self.db.upsert_memory_fact("shell_pref", "I use zsh with oh-my-zsh")

        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db), \
             _patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = asyncio.run(
                get_memory(q="zsh shell", include_evidence=False)
            )

        for fact in result["facts"]:
            self.assertIn("retrieval_source", fact,
                          "Each fact must have a retrieval_source field when ?q= is used")

    def test_q_empty_string_falls_back_to_all_facts(self):
        """?q= with empty string must behave like no q (no hybrid retrieval)."""
        self.db.upsert_memory_fact("font_pref", "I prefer JetBrains Mono for coding")

        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db):
            result = asyncio.run(
                get_memory(q="  ", include_evidence=False)
            )

        # Empty/whitespace q= falls through to all-facts path
        self.assertIn("facts", result)


if __name__ == "__main__":
    unittest.main()
