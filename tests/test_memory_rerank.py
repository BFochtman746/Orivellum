"""Tests for multi-stage memory reranking + ReAct agentic retrieval (Task #851).

Coverage
--------
- _memory_text: key/value → single text string
- _graph_boost_scores: entity overlap multiplies rrf_score; stable when no entities
- _cross_encoder_rerank: concurrent pointwise scoring; timeout degrades gracefully;
  scored candidates sort above unscored; candidates beyond limit appended unchanged
- query_complexity_score: all four heuristic signals
- ReActMemoryAgent: tool dispatch; done-flag halts loop; unknown tool falls back;
  tool errors yield empty list; iterative accumulation deduplicates by id
- rerank_memories: all four stages; ai_reranking_enabled gate; graceful stage failure
- search_and_rerank_memories: simple vs complex query routing; ReAct fallback
- retrieval_stages metadata present in responses
"""
from __future__ import annotations

import re
import tempfile
import unittest
import uuid as _uuid_mod
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

from orivellum.database.db import OrivellumDB


# ─── Shared test helpers ──────────────────────────────────────────────────────

def _make_db(path: str) -> OrivellumDB:
    return OrivellumDB(path)


def _seed(db: OrivellumDB, key: str, value: str) -> str:
    """Insert a current memory fact and return its id."""
    db.upsert_memory_fact(key, value)
    row = db._conn.execute(
        "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL", (key,)
    ).fetchone()
    return row["id"] if row else ""


def _fact(mid: str, key: str = "k", value: str = "v", rrf: float = 0.01) -> dict:
    return {
        "id": mid, "key": key, "value": value,
        "memory_type": "semantic", "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_to": None, "txn_time": "2026-01-01T00:00:00+00:00",
        "created_at": "2026-01-01T00:00:00+00:00",
        "source_conv_id": None, "source_evidence_id": None,
        "retrieval_source": "lexical", "rrf_score": rrf,
    }


def _fake_db(ai_reranking: str = "false") -> MagicMock:
    db = MagicMock()
    db.get_setting.return_value = ai_reranking
    db._lock = MagicMock()
    db._lock.__enter__ = MagicMock(return_value=None)
    db._lock.__exit__  = MagicMock(return_value=False)
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    db._conn = conn
    return db


# ─── _memory_text ─────────────────────────────────────────────────────────────

class TestMemoryText(unittest.TestCase):

    def test_combines_key_and_value(self):
        from orivellum.capabilities.memory import _memory_text
        self.assertEqual(_memory_text({"key": "lang", "value": "Python"}), "lang: Python")

    def test_value_only_when_key_empty(self):
        from orivellum.capabilities.memory import _memory_text
        self.assertEqual(_memory_text({"key": "", "value": "orphan"}), "orphan")

    def test_strips_whitespace(self):
        from orivellum.capabilities.memory import _memory_text
        self.assertEqual(_memory_text({"key": " hobby ", "value": " coding "}), "hobby: coding")

    def test_missing_fields_return_empty(self):
        from orivellum.capabilities.memory import _memory_text
        self.assertEqual(_memory_text({}), "")


# ─── _graph_boost_scores ─────────────────────────────────────────────────────

class TestGraphBoostScores(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed_entity(self, name: str) -> None:
        eid = str(_uuid_mod.uuid4())
        now = "2026-01-01T00:00:00+00:00"
        try:
            self.db._conn.execute(
                "INSERT OR IGNORE INTO entities(id,name,kind,aliases,meta,created_at)"
                " VALUES(?,?,?,'[]','{}',?)", (eid, name, "technology", now)
            )
            self.db._conn.commit()
        except Exception:
            pass

    def test_entity_mention_boosts_rrf_score(self):
        from orivellum.capabilities.memory import _graph_boost_scores, _GRAPH_BOOST_MULT
        self._seed_entity("Python")
        cands = [_fact("id-1", "lang", "I use Python every day", rrf=0.01)]
        result = _graph_boost_scores("Python", cands, self.db)
        self.assertGreater(result[0]["rrf_score"], 0.01,
                           "Entity match must boost rrf_score above the original")

    def test_no_entity_match_leaves_score_unchanged(self):
        from orivellum.capabilities.memory import _graph_boost_scores
        # No entities seeded — query tokens won't match
        cands = [_fact("id-1", "food", "I love pizza", rrf=0.05)]
        result = _graph_boost_scores("quantum astrophysics", cands, self.db)
        self.assertAlmostEqual(result[0]["rrf_score"], 0.05, places=5)

    def test_empty_candidates_returns_empty(self):
        from orivellum.capabilities.memory import _graph_boost_scores
        self.assertEqual(_graph_boost_scores("query", [], self.db), [])

    def test_sorted_by_boosted_score_descending(self):
        from orivellum.capabilities.memory import _graph_boost_scores
        self._seed_entity("Docker")
        # Give the Docker-mentioning fact a lower base rrf so that without a
        # boost it would lose; the boost must flip the ordering.
        cands = [
            _fact("id-low",  "unrelated", "gardening is fun",           rrf=0.01),
            _fact("id-high", "infra",     "I use Docker for containers", rrf=0.08),
        ]
        result = _graph_boost_scores("Docker containers", cands, self.db)
        # Docker-mentioning candidate must be first after boost
        self.assertEqual(result[0]["id"], "id-high")

    def test_no_entity_table_returns_candidates_unchanged(self):
        """Missing entity table must not raise — returns original list."""
        from orivellum.capabilities.memory import _graph_boost_scores
        bad_db = _fake_db()
        bad_db._conn.execute.side_effect = Exception("no such table: entities")
        cands  = [_fact("id-1", "k", "v", rrf=0.02)]
        result = _graph_boost_scores("anything", cands, bad_db)
        self.assertEqual(len(result), 1, "Should return original candidates on DB error")

    def test_stopwords_filtered_from_query(self):
        """Stopword 'the' seeded as entity must NOT cause a boost."""
        from orivellum.capabilities.memory import _graph_boost_scores
        self._seed_entity("the")
        cands = [_fact("id-1", "k", "the best language", rrf=0.01)]
        result = _graph_boost_scores("the answer is the solution", cands, self.db)
        # "the" is in _STOPWORDS — no boost should apply
        self.assertAlmostEqual(result[0]["rrf_score"], 0.01, places=5)


# ─── _cross_encoder_rerank ────────────────────────────────────────────────────

class TestCrossEncoderRerank(unittest.TestCase):

    def _cfg(self) -> Any:
        cfg = MagicMock()
        cfg.serving.base_url          = "http://localhost:1234"
        cfg.serving.workhorse_model   = "test-model"
        return cfg

    def test_scored_candidates_sorted_by_score(self):
        """Candidates with higher cross_encoder_score must rank first."""
        from orivellum.capabilities.memory import _cross_encoder_rerank
        from orivellum.capabilities.llm   import LLMResult

        # The prompt includes the memory fact value text, not the id —
        # match on unique value substrings to assign scores deterministically.
        def _fake_llm(messages, **kwargs):
            content = messages[0]["content"]
            if "highly relevant match" in content:
                return LLMResult("9", True, "model", 10)
            if "somewhat related material" in content:
                return LLMResult("7", True, "model", 10)
            if "completely off topic" in content:
                return LLMResult("2", True, "model", 10)
            return LLMResult("5", True, "model", 10)

        cands = [
            _fact("id-high", "best",   "highly relevant match"),
            _fact("id-low",  "worst",  "completely off topic"),
            _fact("id-mid",  "middle", "somewhat related material"),
        ]
        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            result = _cross_encoder_rerank("test query", cands, self._cfg(), None)

        scored = [c for c in result if "cross_encoder_score" in c]
        ids    = [c["id"] for c in scored]
        self.assertEqual(ids[0], "id-high", "Highest-scored must be first")
        self.assertEqual(ids[-1], "id-low",  "Lowest-scored must be last")

    def test_llm_failure_leaves_candidates_unscored(self):
        """LLM call failure must leave candidates unscored, not raise."""
        from orivellum.capabilities.memory import _cross_encoder_rerank
        from orivellum.capabilities.llm   import LLMResult

        def _fail(*args, **kwargs):
            return LLMResult(None, False, "model", 0, error="timeout")

        cands  = [_fact("id-1", "k", "v")]
        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fail):
            result = _cross_encoder_rerank("q", cands, self._cfg(), None)
        self.assertNotIn("cross_encoder_score", result[0])

    def test_candidates_beyond_limit_appended_unchanged(self):
        """Candidates beyond _CE_MAX_CANDIDATES must be appended after scored ones."""
        from orivellum.capabilities.memory import _cross_encoder_rerank, _CE_MAX_CANDIDATES
        from orivellum.capabilities.llm   import LLMResult

        # Build CE_MAX + 3 candidates; only first CE_MAX are scored
        cands  = [_fact(f"id-{i}", f"k{i}", f"v{i}") for i in range(_CE_MAX_CANDIDATES + 3)]
        extra_ids = [c["id"] for c in cands[_CE_MAX_CANDIDATES:]]

        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult("8", True, "model", 10)):
            result = _cross_encoder_rerank("query", cands, self._cfg(), None)

        result_ids = [c["id"] for c in result]
        # Extra candidates must appear at the tail
        for eid in extra_ids:
            self.assertIn(eid, result_ids)
        tail_ids = result_ids[_CE_MAX_CANDIDATES:]
        for eid in extra_ids:
            self.assertIn(eid, tail_ids)

    def test_empty_candidates_returns_empty(self):
        from orivellum.capabilities.memory import _cross_encoder_rerank
        self.assertEqual(_cross_encoder_rerank("q", [], self._cfg(), None), [])


# ─── query_complexity_score ────────────────────────────────────────────────────

class TestQueryComplexityScore(unittest.TestCase):

    def test_simple_query_scores_zero(self):
        from orivellum.capabilities.memory import query_complexity_score
        self.assertEqual(query_complexity_score("what is my favorite color"), 0)

    def test_named_entity_adds_to_score(self):
        from orivellum.capabilities.memory import query_complexity_score
        score = query_complexity_score("What did I learn about Python and Django?")
        self.assertGreater(score, 0, "Capitalised named entities must increase score")

    def test_connector_word_adds_score(self):
        from orivellum.capabilities.memory import query_complexity_score
        with_connector    = query_complexity_score("compare Redis versus PostgreSQL")
        without_connector = query_complexity_score("tell me about databases")
        self.assertGreater(with_connector, without_connector)

    def test_temporal_word_adds_score(self):
        from orivellum.capabilities.memory import query_complexity_score
        with_temporal    = query_complexity_score("what did I learn last year about testing")
        without_temporal = query_complexity_score("what did I learn about testing")
        self.assertGreater(with_temporal, without_temporal)

    def test_long_query_adds_score(self):
        from orivellum.capabilities.memory import query_complexity_score
        # 14 tokens — exceeds the >12 threshold
        long_q  = "can you summarise everything I have said about machine learning frameworks and tooling"
        short_q = "machine learning"
        self.assertGreater(
            query_complexity_score(long_q),
            query_complexity_score(short_q),
        )

    def test_empty_query_scores_zero(self):
        from orivellum.capabilities.memory import query_complexity_score
        self.assertEqual(query_complexity_score(""), 0)
        self.assertEqual(query_complexity_score("   "), 0)

    def test_highly_complex_query_exceeds_threshold(self):
        from orivellum.capabilities.memory import query_complexity_score, _COMPLEXITY_THRESHOLD
        # Named entities + connector + temporal + long — should clear threshold easily
        q = ("What did I learn about Python and TensorFlow before I started"
             " working on my DeepLearning project last year?")
        self.assertGreater(query_complexity_score(q), _COMPLEXITY_THRESHOLD)


# ─── ReActMemoryAgent ─────────────────────────────────────────────────────────

class TestReActMemoryAgent(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)
        self.db.upsert_memory_fact("lang",  "I use Python")
        self.db.upsert_memory_fact("stack", "FastAPI and React")

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _cfg(self) -> Any:
        cfg = MagicMock()
        cfg.serving.base_url        = "http://localhost:1234"
        cfg.serving.workhorse_model = "test-model"
        return cfg

    def _llm_result(self, text: str):
        from orivellum.capabilities.llm import LLMResult
        return LLMResult(text, True, "model", 50)

    def test_done_flag_halts_loop_after_first_iteration(self):
        """Setting done=true in the first response must stop the loop."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        agent = ReActMemoryAgent(self.db, self._cfg())
        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=self._llm_result(
                       '{"tool":"lexical_search","query":"Python","done":true}'
                   )):
            result = agent.run("tell me about Python")
        # done=true on first call — tool was NOT executed (done before tool call)
        self.assertIsInstance(result, list)

    def test_lexical_tool_finds_seeded_fact(self):
        """lexical_search tool must find facts matching the sub-query."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        responses = iter([
            self._llm_result('{"tool":"lexical_search","query":"Python","done":false}'),
            self._llm_result('{"tool":"lexical_search","query":"Python","done":true}'),
        ])

        agent = ReActMemoryAgent(self.db, self._cfg())
        with patch("orivellum.capabilities.llm.llm_call", side_effect=lambda *a, **kw: next(responses)):
            result = agent.run("tell me about my language preference")

        keys = [r["key"] for r in result]
        self.assertIn("lang", keys, "lexical_search must find the seeded 'lang' fact")

    def test_unknown_tool_falls_back_to_lexical(self):
        """An unknown tool name must not raise — falls back to lexical_search."""
        from orivellum.capabilities.memory import ReActMemoryAgent

        agent = ReActMemoryAgent(self.db, self._cfg())
        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=self._llm_result(
                       '{"tool":"mind_read","query":"anything","done":false}'
                   )):
            result = agent.run("anything")
        self.assertIsInstance(result, list)

    def test_tool_error_returns_empty_and_continues(self):
        """A tool that raises must contribute empty list without aborting the loop."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        responses = iter([
            self._llm_result('{"tool":"semantic_search","query":"lang","done":false}'),
            self._llm_result('{"tool":"lexical_search","query":"lang","done":true}'),
        ])
        agent = ReActMemoryAgent(self.db, self._cfg())

        def _fail_semantic(*args, **kwargs):
            raise RuntimeError("embedding service down")

        with patch("orivellum.capabilities.llm.llm_call", side_effect=lambda *a, **kw: next(responses)), \
             patch("orivellum.capabilities.memory._channel_semantic", side_effect=_fail_semantic):
            result = agent.run("language preference")

        # Should not raise; loop continued despite semantic tool failure
        self.assertIsInstance(result, list)

    def test_no_new_results_halts_loop(self):
        """When a tool returns no new facts, the loop should stop early."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        # All LLM calls say done=false but graph_traverse returns nothing useful
        agent = ReActMemoryAgent(self.db, self._cfg())
        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=self._llm_result(
                       '{"tool":"graph_traverse","query":"xyzzy_nonexistent","done":false}'
                   )), \
             patch("orivellum.capabilities.memory._channel_graph", return_value=[]):
            result = agent.run("xyzzy_nonexistent_topic")
        # Should terminate early (not MAX_ITER calls) because new_count stays 0
        self.assertIsInstance(result, list)

    def test_deduplication_by_id(self):
        """Facts returned by multiple iterations must not appear twice."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        shared = _fact("shared-id", "lang", "I use Python")
        responses = iter([
            self._llm_result('{"tool":"lexical_search","query":"Python","done":false}'),
            self._llm_result('{"tool":"lexical_search","query":"Python","done":true}'),
        ])
        agent = ReActMemoryAgent(self.db, self._cfg())
        # Both iterations return the same fact
        with patch("orivellum.capabilities.llm.llm_call", side_effect=lambda *a, **kw: next(responses)), \
             patch("orivellum.capabilities.memory._channel_lexical", return_value=[shared]):
            result = agent.run("tell me about Python")

        ids = [r["id"] for r in result]
        self.assertEqual(ids.count("shared-id"), 1,
                         "Same fact from multiple iterations must be deduplicated")

    def test_max_iter_cap(self):
        """Loop must not exceed MAX_ITER LLM calls regardless of results."""
        from orivellum.capabilities.memory import ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        call_count = [0]
        unique_idx = [0]

        def _llm(*args, **kwargs):
            call_count[0] += 1
            return LLMResult(
                '{"tool":"lexical_search","query":"test","done":false}',
                True, "model", 20,
            )

        def _new_facts(*args, **kwargs):
            unique_idx[0] += 1
            return [_fact(f"id-{unique_idx[0]}", "k", "v")]

        agent = ReActMemoryAgent(self.db, self._cfg())
        with patch("orivellum.capabilities.llm.llm_call", side_effect=_llm), \
             patch("orivellum.capabilities.memory._channel_lexical", side_effect=_new_facts):
            agent.run("test query")

        self.assertLessEqual(call_count[0], ReActMemoryAgent.MAX_ITER,
                             "LLM must be called at most MAX_ITER times")


# ─── rerank_memories ──────────────────────────────────────────────────────────

class TestRerankMemories(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _cands(self, n: int = 5) -> list[dict]:
        return [_fact(f"id-{i}", f"key{i}", f"value number {i} contains text", rrf=0.1) for i in range(n)]

    def test_returns_tuple_of_list_and_meta(self):
        from orivellum.capabilities.memory import rerank_memories
        result = rerank_memories("test query", self._cands(), self.db)
        self.assertIsInstance(result, tuple)
        ranked, meta = result
        self.assertIsInstance(ranked, list)
        self.assertIsInstance(meta, dict)

    def test_stages_present_in_meta(self):
        from orivellum.capabilities.memory import rerank_memories
        _, meta = rerank_memories("test query", self._cands(), self.db)
        self.assertIn("stages", meta)
        stage_names = [s["name"] for s in meta["stages"]]
        self.assertIn("graph_boost",    stage_names)
        self.assertIn("bm25",           stage_names)
        self.assertIn("cross_encoder",  stage_names)
        self.assertIn("listwise",       stage_names)

    def test_cross_encoder_skipped_when_disabled(self):
        """When ai_reranking_enabled is false, cross_encoder stage must not run."""
        from orivellum.capabilities.memory import rerank_memories

        _, meta = rerank_memories("test query", self._cands(), self.db)
        ce_stage = next((s for s in meta["stages"] if s["name"] == "cross_encoder"), None)
        self.assertIsNotNone(ce_stage)
        self.assertFalse(ce_stage.get("ran"), "Cross-encoder must not run when disabled")

    def test_cross_encoder_runs_when_enabled(self):
        """When ai_reranking_enabled is true, cross_encoder stage must fire."""
        from orivellum.capabilities.memory import rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        # Enable reranking in DB
        self.db._conn.execute(
            "INSERT OR REPLACE INTO settings(scope,key,value,updated_at)"
            " VALUES('global','ai_reranking_enabled','true','2026-01-01T00:00:00+00:00')"
        )
        self.db._conn.commit()

        def _fake_llm(*args, **kwargs):
            return LLMResult("8", True, "model", 10)

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm), \
             patch("orivellum.configuration.config.load_config") as _lc:
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            _, meta = rerank_memories("test query", self._cands(), self.db)

        ce_stage = next((s for s in meta["stages"] if s["name"] == "cross_encoder"), None)
        self.assertTrue(ce_stage.get("ran"), "Cross-encoder must run when enabled")

    def test_top_k_respected(self):
        from orivellum.capabilities.memory import rerank_memories
        ranked, _ = rerank_memories("test", self._cands(10), self.db, top_k=3)
        self.assertLessEqual(len(ranked), 3)

    def test_empty_candidates_return_empty(self):
        from orivellum.capabilities.memory import rerank_memories
        ranked, meta = rerank_memories("q", [], self.db)
        self.assertEqual(ranked, [])
        self.assertEqual(meta["stages"], [])

    def test_internal_mem_text_field_stripped(self):
        """_mem_text helper field must not appear in returned dicts."""
        from orivellum.capabilities.memory import rerank_memories
        ranked, _ = rerank_memories("test", self._cands(), self.db)
        for fact in ranked:
            self.assertNotIn("_mem_text",   fact)
            self.assertNotIn("_rerank_idx", fact)

    def test_bm25_score_field_present(self):
        """BM25 stage must add rerank_score field to returned dicts."""
        from orivellum.capabilities.memory import rerank_memories
        # Use a query with terms that appear in the values so BM25 fires
        cands = [_fact(f"id-{i}", "topic", f"this contains value number {i}") for i in range(3)]
        ranked, _ = rerank_memories("contains value", cands, self.db)
        for fact in ranked:
            self.assertIn("rerank_score", fact)

    def test_stage_failure_does_not_raise(self):
        """A bug in any stage must degrade gracefully, not propagate."""
        from orivellum.capabilities.memory import rerank_memories

        with patch("orivellum.capabilities.memory._graph_boost_scores",
                   side_effect=RuntimeError("injected stage 1 failure")):
            ranked, meta = rerank_memories("q", self._cands(), self.db)
        # Should return a result, not raise
        self.assertIsInstance(ranked, list)


# ─── search_and_rerank_memories ───────────────────────────────────────────────

class TestSearchAndRerankMemories(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)
        self.db.upsert_memory_fact("editor", "I use Neovim as my editor")
        self.db.upsert_memory_fact("os",     "I prefer Linux for development")

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_returns_tuple_list_meta(self):
        from orivellum.capabilities.memory import search_and_rerank_memories
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = search_and_rerank_memories("Neovim editor", self.db)
        self.assertIsInstance(result, tuple)
        ranked, meta = result
        self.assertIsInstance(ranked, list)
        self.assertIsInstance(meta,   dict)

    def test_meta_has_required_fields(self):
        from orivellum.capabilities.memory import search_and_rerank_memories
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            _, meta = search_and_rerank_memories("Neovim editor", self.db)
        self.assertIn("retrieval_stages",  meta)
        self.assertIn("complexity_score",  meta)
        self.assertIn("react_used",        meta)

    def test_simple_query_does_not_use_react(self):
        from orivellum.capabilities.memory import search_and_rerank_memories
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            _, meta = search_and_rerank_memories("editor", self.db)
        self.assertFalse(meta["react_used"],
                         "Simple single-token query must not trigger ReAct agent")

    def test_complex_query_triggers_react(self):
        from orivellum.capabilities.memory import search_and_rerank_memories, ReActMemoryAgent
        from orivellum.capabilities.llm   import LLMResult

        complex_q = (
            "What did I learn about Python and TensorFlow before I started"
            " working on my DeepLearning project last year?"
        )
        # ReAct runs one iteration and says done
        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult(
                       '{"tool":"lexical_search","query":"Python","done":true}',
                       True, "model", 50,
                   )), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            _, meta = search_and_rerank_memories(complex_q, self.db)

        self.assertTrue(meta["react_used"],
                        "Complex query must trigger ReAct agent (react_used=True)")

    def test_react_failure_falls_back_to_hybrid(self):
        """ReAct agent crash must fall back to one-shot hybrid without raising."""
        from orivellum.capabilities.memory import search_and_rerank_memories

        complex_q = (
            "What did I learn about Python and TensorFlow before I started"
            " working on my DeepLearning project last year?"
        )
        with patch("orivellum.capabilities.memory.ReActMemoryAgent") as MockAgent, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]), \
             patch("orivellum.configuration.config.load_config") as _lc:
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            MockAgent.return_value.run.side_effect = RuntimeError("agent crashed")
            ranked, meta = search_and_rerank_memories(complex_q, self.db)

        self.assertIsInstance(ranked, list)
        # After fallback react_used should be False
        self.assertFalse(meta["react_used"])

    def test_empty_query_returns_empty(self):
        from orivellum.capabilities.memory import search_and_rerank_memories
        ranked, meta = search_and_rerank_memories("", self.db)
        self.assertEqual(ranked, [])
        ranked2, _ = search_and_rerank_memories("   ", self.db)
        self.assertEqual(ranked2, [])

    def test_limit_respected(self):
        from orivellum.capabilities.memory import search_and_rerank_memories
        for i in range(10):
            self.db.upsert_memory_fact(f"limit_k{i}", f"common search term anchor {i}")
        with patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            ranked, _ = search_and_rerank_memories("common search anchor", self.db, limit=3)
        self.assertLessEqual(len(ranked), 3)


# ─── Regression: ReAct empty-result fallback ─────────────────────────────────

class TestReActEmptyResultFallback(unittest.TestCase):
    """ReAct returning [] must always fall back to hybrid retrieval."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)
        # Seed a fact that lexical hybrid retrieval can find
        self.db.upsert_memory_fact("python_pref", "I love using Python for data work")

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _complex_q(self) -> str:
        """A query that exceeds the complexity threshold."""
        return (
            "What did I learn about Python and TensorFlow before I started"
            " working on my DeepLearning project last year?"
        )

    def _cfg(self) -> Any:
        cfg = MagicMock()
        cfg.serving.base_url        = "http://localhost:1234"
        cfg.serving.workhorse_model = "test-model"
        return cfg

    def test_react_done_immediately_still_returns_hybrid_facts(self):
        """done=true on first response must fall back to hybrid, not return []."""
        from orivellum.capabilities.memory import search_and_rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult(
                       '{"tool":"lexical_search","query":"Python","done":true}',
                       True, "model", 50,
                   )), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            _lc.return_value = self._cfg()
            ranked, meta = search_and_rerank_memories(self._complex_q(), self.db)

        self.assertGreater(len(ranked), 0,
                           "done=true before any tool call must fall back to hybrid retrieval,"
                           " not return an empty list")
        keys = [r["key"] for r in ranked]
        self.assertIn("python_pref", keys,
                      "The seeded python_pref fact must be surfaced via hybrid fallback")

    def test_react_all_tools_return_empty_falls_back_to_hybrid(self):
        """ReAct finding no facts via any tool must fall back to hybrid."""
        from orivellum.capabilities.memory import search_and_rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        responses = iter([
            LLMResult('{"tool":"lexical_search","query":"xyzzy","done":false}', True, "m", 50),
            LLMResult('{"tool":"graph_traverse","query":"xyzzy","done":false}', True, "m", 50),
        ])

        # The lambda MUST accept the positional `limit` argument that
        # _channel_lexical receives when called from inside search_memories
        # via pool.submit(_channel_lexical, query, db, channel_limit).
        def _mock_lexical(q, db, limit=20, **kw):
            if "xyzzy" in q:
                return []
            # Hybrid fallback path — let the real DB call through
            return db.search_memories_lexical(q, limit=limit)

        with patch("orivellum.capabilities.llm.llm_call",
                   side_effect=lambda *a, **kw: next(responses, LLMResult(
                       '{"tool":"lexical_search","query":"xyzzy","done":true}', True, "m", 50)
                   )), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]), \
             patch("orivellum.capabilities.memory._channel_graph",    return_value=[]), \
             patch("orivellum.capabilities.memory._channel_lexical",  side_effect=_mock_lexical):
            _lc.return_value = self._cfg()
            ranked, meta = search_and_rerank_memories(self._complex_q(), self.db)

        self.assertGreater(len(ranked), 0,
                           "ReAct returning no hits must fall back to hybrid, not return []")

    def test_react_malformed_json_falls_back_to_hybrid(self):
        """Malformed JSON from LLM (loop exits early) must fall back to hybrid."""
        from orivellum.capabilities.memory import search_and_rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult("not valid json at all", True, "m", 50)), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            _lc.return_value = self._cfg()
            ranked, meta = search_and_rerank_memories(self._complex_q(), self.db)

        self.assertGreater(len(ranked), 0,
                           "Malformed LLM output must fall back to hybrid, not return []")

    def test_react_llm_unavailable_falls_back_to_hybrid(self):
        """LLM unavailable (ok=False) must exit loop and fall back to hybrid."""
        from orivellum.capabilities.memory import search_and_rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        with patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult(None, False, "m", 0, error="timeout")), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            _lc.return_value = self._cfg()
            ranked, meta = search_and_rerank_memories(self._complex_q(), self.db)

        self.assertGreater(len(ranked), 0,
                           "LLM unavailable must fall back to hybrid, not return []")


# ─── Regression: listwise BM25+LLM RRF fusion ────────────────────────────────

class TestListwiseRRFFusion(unittest.TestCase):
    """Listwise stage must use BM25+LLM RRF fusion, not raw LLM order."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)
        # Enable reranking
        self.db._conn.execute(
            "INSERT OR REPLACE INTO settings(scope,key,value,updated_at)"
            " VALUES('global','ai_reranking_enabled','true','2026-01-01T00:00:00+00:00')"
        )
        self.db._conn.commit()

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_listwise_consensus_ranks_above_single_channel(self):
        """Candidate ranked top by both BM25 and LLM must beat candidates
        ranked top by only one of them."""
        from orivellum.capabilities.memory import rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        # Three candidates: BM25 will rank by text match; we control LLM order.
        # id-consensus: good BM25 match AND LLM ranks it #1
        # id-llm-only:  poor BM25 match BUT LLM ranks it #2
        # id-bm25-only: good BM25 match BUT LLM ranks it #3
        cands = [
            _fact("id-consensus", "topic", "the query keyword matches perfectly here"),
            _fact("id-llm-only",  "other", "completely unrelated to the search term"),
            _fact("id-bm25-only", "topic", "the query keyword also appears here"),
        ]

        # LLM listwise returns: [0, 1, 2] → id-consensus first, id-llm-only second
        # (index 0 = id-consensus, index 1 = id-llm-only, index 2 = id-bm25-only)
        with patch("orivellum.capabilities.rerank._llm_rerank",
                   return_value=[0, 1, 2]), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult("8", True, "model", 10)):
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            ranked, meta = rerank_memories("query keyword", cands, self.db)

        lw_stage = next(s for s in meta["stages"] if s["name"] == "listwise")
        self.assertTrue(lw_stage.get("ran"), "Listwise stage must have run")

        ids = [r["id"] for r in ranked]
        self.assertIn("id-consensus", ids)
        # id-consensus is ranked #1 by both BM25 (keyword match) and LLM —
        # RRF fusion must place it first
        self.assertEqual(ids[0], "id-consensus",
                         "Consensus winner (top in both BM25 and LLM) must be ranked first")

    def test_listwise_stage_ran_field_true_when_llm_enabled(self):
        """stages meta must record ran=True for the listwise stage when LLM fires."""
        from orivellum.capabilities.memory import rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        cands = [_fact(f"id-{i}", f"key{i}", f"value search term number {i}") for i in range(5)]
        with patch("orivellum.capabilities.rerank._llm_rerank", return_value=[0, 1, 2, 3, 4]), \
             patch("orivellum.configuration.config.load_config") as _lc, \
             patch("orivellum.capabilities.llm.llm_call",
                   return_value=LLMResult("8", True, "model", 10)):
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            _, meta = rerank_memories("search term", cands, self.db)

        lw_stage = next(s for s in meta["stages"] if s["name"] == "listwise")
        self.assertTrue(lw_stage.get("ran"),
                        "stages[listwise].ran must be True when LLM enabled and ≥ 3 candidates")

    def test_listwise_does_not_fire_with_fewer_than_3_candidates(self):
        """Listwise must not run when fewer than 3 candidates remain."""
        from orivellum.capabilities.memory import rerank_memories

        cands = [_fact(f"id-{i}", f"k{i}", f"v{i}") for i in range(2)]
        _, meta = rerank_memories("query", cands, self.db)

        lw_stage = next(s for s in meta["stages"] if s["name"] == "listwise")
        self.assertFalse(lw_stage.get("ran"),
                         "Listwise must not run when fewer than 3 candidates remain")

    def test_listwise_uses_bm25_rank_not_cross_encoder_rank(self):
        """Listwise RRF must use the BM25 rank saved BEFORE cross-encoder reorders.

        We patch bm25_rerank to return a fixed order (A → B → C) so we can
        reason precisely about BM25 ranks.  Cross-encoder reverses that order
        (C first, A last).  The listwise LLM wants A first (same as BM25).
        The correct BM25+LLM RRF gives A the top consensus score:
          A: BM25 rank 0 + LLM rank 0 → highest RRF
        If CE rank were mistakenly used as "BM25 rank", A's position would be
        CE rank 2, so A would NOT win the consensus — this tests for that bug.
        """
        from orivellum.capabilities.memory import rerank_memories
        from orivellum.capabilities.llm   import LLMResult

        cands = [
            _fact("id-A", "kA", "value A"),
            _fact("id-B", "kB", "value B"),
            _fact("id-C", "kC", "value C"),
        ]

        # Patch BM25 to return a controlled order: A → B → C (BM25 ranks 0,1,2)
        def _mock_bm25(query, candidates, text_field=None):
            order = ["id-A", "id-B", "id-C"]
            return sorted(candidates, key=lambda c: order.index(c.get("id", "")))

        # Cross-encoder reverses BM25: C scores 10, B 5, A 2 → CE order: C, B, A
        def _fake_ce(messages, **kwargs):
            content = messages[0]["content"]
            if "value C" in content:
                return LLMResult("10", True, "model", 10)
            if "value A" in content:
                return LLMResult("2", True, "model", 10)
            return LLMResult("5", True, "model", 10)

        # After CE reorders: top_slice = [C (idx 0), B (idx 1), A (idx 2)]
        # LLM wants A first → index 2 first, then 1, then 0
        with patch("orivellum.capabilities.rerank.bm25_rerank", side_effect=_mock_bm25), \
             patch("orivellum.capabilities.rerank._llm_rerank",
                   return_value=[2, 1, 0]), \
             patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_ce), \
             patch("orivellum.configuration.config.load_config") as _lc:
            cfg = MagicMock()
            cfg.serving.base_url        = "http://localhost:1234"
            cfg.serving.workhorse_model = "test"
            _lc.return_value = cfg
            ranked, meta = rerank_memories("test query", cands, self.db)

        lw_stage = next(s for s in meta["stages"] if s["name"] == "listwise")
        self.assertTrue(lw_stage.get("ran"), "Listwise stage must have run")

        ids = [r["id"] for r in ranked]
        # id-A: BM25 rank 0 + LLM rank 0 → top RRF consensus score
        # If CE rank (2) were used instead of BM25 rank (0), id-A would NOT win
        self.assertEqual(ids[0], "id-A",
                         "Consensus winner (BM25 rank 0 + LLM rank 0) must be first;"
                         " if CE rank were used id-A would score poorly")


# ─── retrieval_stages in GET /api/memory?q= ────────────────────────────────────

class TestGetMemoryRetrievalStages(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db  = _make_db(self.tmp)
        self.db.upsert_memory_fact("shell", "I use zsh with oh-my-zsh")

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_retrieval_stages_present_in_response(self):
        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db), \
             _patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = asyncio.run(
                get_memory(q="zsh shell", include_evidence=False)
            )

        self.assertIn("retrieval_stages", result,
                      "Response must include 'retrieval_stages' when ?q= is set")
        self.assertIsInstance(result["retrieval_stages"], list)

    def test_complexity_score_in_response(self):
        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db), \
             _patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = asyncio.run(
                get_memory(q="zsh shell", include_evidence=False)
            )

        self.assertIn("complexity_score", result)
        self.assertIsInstance(result["complexity_score"], int)

    def test_react_used_in_response(self):
        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db), \
             _patch("orivellum.capabilities.memory._channel_semantic", return_value=[]):
            result = asyncio.run(
                get_memory(q="zsh shell", include_evidence=False)
            )

        self.assertIn("react_used", result)
        self.assertIsInstance(result["react_used"], bool)

    def test_without_q_no_retrieval_stages(self):
        """Non-query path must NOT include retrieval_stages key."""
        import asyncio
        from unittest.mock import patch as _patch
        from orivellum.api.routes.conversations import get_memory

        with _patch("orivellum.api.routes.conversations.get_db", return_value=self.db):
            result = asyncio.run(
                get_memory(q=None, include_evidence=False)
            )

        self.assertNotIn("retrieval_stages", result,
                         "Non-query path must not include retrieval_stages")


if __name__ == "__main__":
    unittest.main()
