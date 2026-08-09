"""Measurement layer acceptance tests — schema v109, telemetry, bench, evalset.

Covers:
  1. nDCG@k / Recall@k math (pure functions, hand-checked values).
  2. Golden query CRUD + validation + auto-seeding from chunks.
  3. evaluate_retrieval: FTS channel scores real seeded content; semantic
     channel failure is reported as unavailable (None), never zero.
  4. record_llm_call persists ttft_ms / tok_per_s / streamed.
  5. bench_runs save/list round-trip and telemetry_summary aggregation.
  6. /api/bench routes: run validation, goldens CRUD, eval guard.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-1234567890abcdef")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "artifacts" / "api-server" / "src"))

from tests.conftest import AUTH_HEADERS  # noqa: E402

try:
    from fastapi.testclient import TestClient
    from orivellum.api.app import create_app
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.capabilities import evalset
    from orivellum.capabilities import bench
    from orivellum.capabilities.llm import record_llm_call, decode_tok_per_s
    _DEPS_OK = True
    _MISSING = ""
except Exception as _e:  # pragma: no cover
    _DEPS_OK = False
    _MISSING = str(_e)


def _make_db(tmp: Path) -> "OrivellumDB":
    return OrivellumDB(str(tmp / "test.db"))


# ──────────────────────────────────────────────────────────────────────────────
# 1. Metric math
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestMetrics(unittest.TestCase):
    def test_ndcg_perfect_ranking_is_1(self):
        self.assertAlmostEqual(
            evalset.ndcg_at_k(["a", "b", "c"], ["a", "b"], k=5), 1.0)

    def test_ndcg_zero_when_nothing_relevant_found(self):
        self.assertEqual(evalset.ndcg_at_k(["x", "y"], ["a"], k=5), 0.0)

    def test_ndcg_rank_position_matters(self):
        # relevant item first scores higher than relevant item second
        first = evalset.ndcg_at_k(["a", "x"], ["a"], k=2)
        second = evalset.ndcg_at_k(["x", "a"], ["a"], k=2)
        self.assertGreater(first, second)
        self.assertAlmostEqual(first, 1.0)
        # DCG@2 for hit at rank 2 = 1/log2(3); IDCG = 1
        import math
        self.assertAlmostEqual(second, 1.0 / math.log2(3), places=6)

    def test_ndcg_empty_relevant_is_0(self):
        self.assertEqual(evalset.ndcg_at_k(["a"], [], k=5), 0.0)

    def test_recall_counts_fraction_of_relevant_found(self):
        self.assertAlmostEqual(
            evalset.recall_at_k(["a", "b", "x"], ["a", "b", "c", "d"], k=3), 0.5)

    def test_recall_respects_k_cutoff(self):
        self.assertEqual(evalset.recall_at_k(["x", "y", "a"], ["a"], k=2), 0.0)
        self.assertEqual(evalset.recall_at_k(["x", "y", "a"], ["a"], k=3), 1.0)

    def test_decode_rate_excludes_first_token(self):
        # Window starts AFTER token 1 arrives, so numerator must be n-1:
        # 101 tokens with 100 generated inside a 2 s window → 50 tok/s.
        self.assertAlmostEqual(decode_tok_per_s(101, 2.0), 50.0)

    def test_decode_rate_guards_small_samples(self):
        self.assertIsNone(decode_tok_per_s(1, 10.0))   # single token: no window
        self.assertIsNone(decode_tok_per_s(None, 5.0))
        self.assertIsNone(decode_tok_per_s(0, 5.0))
        self.assertIsNone(decode_tok_per_s(50, 0.4))   # window too short
        self.assertIsNone(decode_tok_per_s(50, 0.0))


# ──────────────────────────────────────────────────────────────────────────────
# 2. Golden CRUD
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestGoldenCrud(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_list_delete_roundtrip(self):
        g = evalset.add_golden(
            self.db, query="sling angle rated capacity", kind="chunk",
            relevant_ids=["doc-1"], notes="test")
        self.assertEqual(g["kind"], "chunk")
        self.assertEqual(g["relevant_ids"], ["doc-1"])
        listed = evalset.list_goldens(self.db)
        self.assertEqual(len(listed), 1)
        self.assertTrue(evalset.delete_golden(self.db, g["id"]))
        self.assertEqual(evalset.list_goldens(self.db), [])

    def test_delete_missing_returns_false(self):
        self.assertFalse(evalset.delete_golden(self.db, "nope"))

    def test_validation_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            evalset.add_golden(self.db, query="", kind="chunk",
                               relevant_ids=["d"])
        with self.assertRaises(ValueError):
            evalset.add_golden(self.db, query="q", kind="bogus",
                               relevant_ids=["d"])
        with self.assertRaises(ValueError):
            evalset.add_golden(self.db, query="q", kind="chunk",
                               relevant_ids=[])

    def test_kind_filter(self):
        evalset.add_golden(self.db, query="alpha beta", kind="chunk",
                           relevant_ids=["d1"])
        evalset.add_golden(self.db, query="gamma delta", kind="knowledge",
                           relevant_ids=["k1"])
        self.assertEqual(len(evalset.list_goldens(self.db, kind="chunk")), 1)
        self.assertEqual(
            evalset.list_goldens(self.db, kind="knowledge")[0]["kind"],
            "knowledge")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Auto-seed + evaluate_retrieval against real FTS
# ──────────────────────────────────────────────────────────────────────────────

_DOC_TEXT = (
    "The certification inspector examines the crane's load path thoroughly. "
    "Wire rope slings must maintain a minimum angle of sixty degrees from "
    "horizontal during any tandem lift operation near powerlines. "
    "Rated capacity placards are verified against the manufacturer chart "
    "before every critical lift on the northern gantry platform. "
) * 4


@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestEvaluateRetrieval(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(Path(self._tmp.name))
        doc = self.db.create_document(
            title="Rigging Safety Manual", kind="txt", source="test.txt")
        self.doc_id = doc["id"]
        self.db.add_chunk(self.doc_id, _DOC_TEXT, 0)

    def tearDown(self):
        self._tmp.cleanup()

    def test_auto_seed_creates_chunk_goldens(self):
        out = evalset.auto_seed_goldens(self.db, n=5)
        self.assertGreaterEqual(out["created"], 1)
        for g in out["goldens"]:
            self.assertEqual(g["kind"], "chunk")
            self.assertEqual(g["relevant_ids"], [self.doc_id])
            self.assertEqual(g["source"], "auto")

    def test_fts_channel_scores_seeded_content(self):
        evalset.add_golden(
            self.db, query="tandem lift operation near powerlines",
            kind="chunk", relevant_ids=[self.doc_id])
        # Force semantic/hybrid unavailable so the test has no network.
        with patch("orivellum.capabilities.embeddings.semantic_search",
                   side_effect=RuntimeError("embeddings down")), \
             patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                   side_effect=RuntimeError("embeddings down")):
            summary = evalset.evaluate_retrieval(self.db, k=5)
        fts = summary["channels"]["fts"]
        self.assertEqual(fts["ndcg"], 1.0, summary)
        self.assertEqual(fts["recall"], 1.0)
        # Unavailable channels report None + error, never 0.
        self.assertIsNone(summary["channels"]["semantic"]["ndcg"])
        self.assertIn("embeddings down",
                      summary["channels"]["semantic"]["error"] or "")

    def test_eval_persists_bench_run(self):
        evalset.add_golden(
            self.db, query="rated capacity placards manufacturer chart",
            kind="chunk", relevant_ids=[self.doc_id])
        with patch("orivellum.capabilities.embeddings.semantic_search",
                   return_value=[]), \
             patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                   return_value=[]):
            summary = evalset.evaluate_retrieval(self.db, k=5)
        self.assertIn("run_id", summary)
        runs = bench.list_bench_runs(self.db, kind="retrieval_eval")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["summary"]["n_goldens"], 1)

    def test_eval_with_no_goldens_returns_empty_summary(self):
        summary = evalset.evaluate_retrieval(self.db, k=5)
        self.assertEqual(summary["n_goldens"], 0)
        self.assertNotIn("run_id", summary)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Telemetry columns
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestTelemetryColumns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_persists_ttft_and_rate(self):
        record_llm_call(
            self.db, purpose="chat.stream", model="m", latency_ms=1500,
            completion_tokens=90, ok=True,
            ttft_ms=412.34, tok_per_s=61.567, streamed=True)
        row = self.db._conn.execute(
            "SELECT ttft_ms, tok_per_s, streamed, completion_tokens "
            "FROM llm_calls").fetchone()
        self.assertAlmostEqual(row[0], 412.3)
        self.assertAlmostEqual(row[1], 61.57)
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], 90)

    def test_defaults_stay_null_not_zero(self):
        record_llm_call(self.db, purpose="harvest", model="m", latency_ms=10)
        row = self.db._conn.execute(
            "SELECT ttft_ms, tok_per_s, streamed FROM llm_calls").fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])
        self.assertEqual(row[2], 0)

    def test_telemetry_summary_aggregates(self):
        for i in range(4):
            record_llm_call(
                self.db, purpose="chat.stream", model="m",
                latency_ms=1000 + i * 100, completion_tokens=50,
                ok=(i != 3), error="boom" if i == 3 else None,
                ttft_ms=300 + i * 50, tok_per_s=40 + i, streamed=True)
        s = bench.telemetry_summary(self.db, hours=1)
        chat = s["purposes"]["chat.stream"]
        self.assertEqual(chat["calls"], 4)
        self.assertEqual(chat["errors"], 1)
        self.assertIsNotNone(chat["ttft_ms_p50"])
        self.assertIsNotNone(chat["tok_per_s_median"])
        self.assertEqual(chat["measured_ttft"], 4)


# ──────────────────────────────────────────────────────────────────────────────
# 5. bench_runs round-trip
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestBenchRuns(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_list(self):
        stored = bench.save_bench_run(
            self.db, "ttft", "baseline", {"points": [1, 2]})
        self.assertEqual(stored["kind"], "ttft")
        self.assertEqual(stored["summary"], {"points": [1, 2]})
        runs = bench.list_bench_runs(self.db)
        self.assertEqual(len(runs), 1)
        self.assertEqual(bench.list_bench_runs(self.db, kind="cache"), [])

    def test_bench_sweep_records_failure_gracefully(self):
        """Unreachable server → run saved with all_ok False, probes recorded."""

        class _Cfg:
            class serving:
                base_url = "http://127.0.0.1:1/api/v1"  # nothing listens here
                workhorse_model = "test-model"

        result = bench.run_ttft_sweep(
            _Cfg, self.db, sizes_chars=(500,), label="unreachable")
        self.assertFalse(result["summary"]["all_ok"])
        self.assertEqual(len(result["summary"]["points"]), 1)
        # Probe telemetry recorded with ok=0
        row = self.db._conn.execute(
            "SELECT purpose, ok FROM llm_calls").fetchone()
        self.assertEqual(row[0], "bench.ttft")
        self.assertEqual(row[1], 0)


# ──────────────────────────────────────────────────────────────────────────────
# 6. API routes
# ──────────────────────────────────────────────────────────────────────────────

@unittest.skipUnless(_DEPS_OK, f"deps unavailable: {_MISSING}")
class TestBenchRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self._tmp.name) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = OrivellumDB(str(data_dir / "test.db"))
        cfg = OrivellumConfig(
            data_dir=str(data_dir),
            serving=ServingConfig(base_url="http://localhost:99999/api/v1"),
        )
        _deps.init(db=self.db, cfg=cfg)
        app = create_app()
        self.client = TestClient(app, raise_server_exceptions=False,
                                 headers=AUTH_HEADERS)

    def tearDown(self):
        from orivellum.api import executor as _exec
        _exec.shutdown(wait=True)  # drain bench submissions; lazily re-created
        self._tmp.cleanup()

    def test_run_rejects_unknown_kind(self):
        r = self.client.post("/api/bench/run", json={"kind": "bogus"})
        self.assertEqual(r.status_code, 400)

    def test_run_rejects_overlapping_benchmarks(self):
        """Second start while one is active → 409; guard clears afterwards."""
        from orivellum.api.routes import bench as bench_routes

        with bench_routes._bench_guard:
            bench_routes._bench_active["kind"] = "generation"
        try:
            r = self.client.post("/api/bench/run", json={"kind": "ttft"})
            self.assertEqual(r.status_code, 409)
            self.assertIn("already running", r.json()["detail"])
            s = self.client.get("/api/bench/status")
            self.assertTrue(s.json()["running"])
            self.assertEqual(s.json()["kind"], "generation")
        finally:
            with bench_routes._bench_guard:
                bench_routes._bench_active["kind"] = None
        s = self.client.get("/api/bench/status")
        self.assertFalse(s.json()["running"])

    def test_guard_clears_after_bench_finishes(self):
        """A real (unreachable-server) bench run releases the guard."""
        r = self.client.post("/api/bench/run", json={"kind": "ttft"})
        self.assertEqual(r.status_code, 200)
        # Drain the executor so the guarded wrapper's finally runs.
        from orivellum.api import executor as _exec
        _exec.shutdown(wait=True)
        s = self.client.get("/api/bench/status")
        self.assertFalse(s.json()["running"])
        # The failed run still produced a stored bench_runs row.
        runs = self.client.get("/api/bench/runs?kind=ttft").json()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertFalse(runs[0]["summary"]["all_ok"])

    def test_goldens_crud_via_api(self):
        r = self.client.post("/api/bench/goldens", json={
            "query": "crane load path", "kind": "chunk",
            "relevant_ids": ["d1"]})
        self.assertEqual(r.status_code, 200, r.text)
        gid = r.json()["golden"]["id"]
        r = self.client.get("/api/bench/goldens")
        self.assertEqual(len(r.json()["goldens"]), 1)
        r = self.client.delete(f"/api/bench/goldens/{gid}")
        self.assertEqual(r.status_code, 200)
        r = self.client.delete(f"/api/bench/goldens/{gid}")
        self.assertEqual(r.status_code, 404)

    def test_goldens_post_validates(self):
        r = self.client.post("/api/bench/goldens", json={
            "query": "", "kind": "chunk", "relevant_ids": ["d"]})
        self.assertEqual(r.status_code, 400)

    def test_eval_guard_requires_goldens(self):
        r = self.client.post("/api/bench/eval/retrieval", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("golden", r.json()["detail"].lower())

    def test_telemetry_summary_route(self):
        r = self.client.get("/api/bench/telemetry/summary?hours=1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["hours"], 1)
        self.assertIn("purposes", body)

    def test_runs_route_empty(self):
        r = self.client.get("/api/bench/runs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["runs"], [])


if __name__ == "__main__":
    unittest.main()
