"""Tests for the MCOS Phase 1 benchmark/evaluation engine.

Covers:
- seeding idempotency (static once, dynamic refreshed)
- score_response rule engine (concepts / regex / exact / json_keys)
- retrieval benchmark run end-to-end against a seeded doc/chunk (no LLM)
- run listing + run detail endpoints
- telemetry endpoint (fake llm_calls rows)
- 409 on concurrent run

llm_call is monkeypatched for llm-kind runs so no network is needed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


def _seed_doc_and_chunk(db):
    """Create one document with a distinctive chunk for retrieval tests."""
    doc = db.create_document(title="Photosynthesis Primer", kind="note")
    text = ("Photosynthesis converts sunlight carbon dioxide and water into "
            "glucose inside chloroplast organelles of green plant leaves.")
    db.add_chunk(doc["id"], text)
    return doc["id"], text


class TestScoreEngine(unittest.TestCase):

    def test_concepts_scoring(self):
        from orivellum.capabilities.mcos import score_response
        case = {"expected_concepts": ["alpha", "beta", "gamma", "delta"],
                "scoring": {"type": "concepts"}}
        self.assertEqual(score_response(case, "Alpha and BETA appear here"), 0.5)
        self.assertEqual(score_response(case, "nothing relevant"), 0.0)
        self.assertEqual(score_response(case, "alpha beta gamma delta"), 1.0)

    def test_regex_scoring(self):
        from orivellum.capabilities.mcos import score_response
        case = {"scoring": {"type": "regex", "pattern": r"\b42\b"}}
        self.assertEqual(score_response(case, "the answer is 42."), 1.0)
        self.assertEqual(score_response(case, "the answer is 43."), 0.0)

    def test_exact_scoring(self):
        from orivellum.capabilities.mcos import score_response
        case = {"scoring": {"type": "exact", "expected": "DONE"}}
        self.assertEqual(score_response(case, "  done  "), 1.0)
        self.assertEqual(score_response(case, "not done"), 0.0)

    def test_json_keys_scoring(self):
        from orivellum.capabilities.mcos import score_response
        case = {"scoring": {"type": "json_keys", "keys": ["a", "b"]}}
        self.assertEqual(score_response(case, 'here you go: {"a":1,"b":2} ok'), 1.0)
        self.assertEqual(score_response(case, '{"a":1}'), 0.5)
        self.assertEqual(score_response(case, "no json at all"), 0.0)

    def test_unknown_scoring_type(self):
        from orivellum.capabilities.mcos import score_response
        self.assertEqual(score_response({"scoring": {"type": "???"}}, "x"), 0.0)


class TestSeedingIdempotency(unittest.TestCase):

    def test_seed_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import seed_default_benchmarks

            _seed_doc_and_chunk(db)
            first = seed_default_benchmarks(db)
            self.assertGreaterEqual(first["benchmarks"], 4)

            # Static reasoning suite case count and version stable on re-seed.
            with db._lock:
                cases_before = db._conn.execute(
                    "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id='reasoning'"
                ).fetchone()[0]
                ver_before = db._conn.execute(
                    "SELECT version FROM benchmarks WHERE id='reasoning'"
                ).fetchone()[0]

            second = seed_default_benchmarks(db)
            self.assertEqual(first["benchmarks"], second["benchmarks"])

            with db._lock:
                cases_after = db._conn.execute(
                    "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id='reasoning'"
                ).fetchone()[0]
                ver_after = db._conn.execute(
                    "SELECT version FROM benchmarks WHERE id='reasoning'"
                ).fetchone()[0]
            self.assertEqual(cases_before, cases_after)
            self.assertEqual(ver_before, ver_after)

    def test_dynamic_version_bump_on_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import seed_default_benchmarks

            _seed_doc_and_chunk(db)
            seed_default_benchmarks(db)
            with db._lock:
                ver1 = db._conn.execute(
                    "SELECT version FROM benchmarks WHERE id='rag_retrieval'"
                ).fetchone()[0]

            # Add another chunk → dynamic case set changes → version bumps.
            doc2 = db.create_document(title="Second Doc", kind="note")
            db.add_chunk(doc2["id"], "Mitochondria are the powerhouse organelle of "
                                     "eukaryotic cells producing adenosine triphosphate.")
            seed_default_benchmarks(db)
            with db._lock:
                ver2 = db._conn.execute(
                    "SELECT version FROM benchmarks WHERE id='rag_retrieval'"
                ).fetchone()[0]
            self.assertGreater(ver2, ver1)


class TestRetrievalRun(unittest.TestCase):

    def test_retrieval_run_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import (
                seed_default_benchmarks, run_benchmark,
            )
            _seed_doc_and_chunk(db)
            seed_default_benchmarks(db)

            run_id = run_benchmark(db, cfg, "rag_retrieval")
            with db._lock:
                run = dict(db._conn.execute(
                    "SELECT * FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone())
                results = db._conn.execute(
                    "SELECT * FROM eval_results WHERE run_id=?", (run_id,)
                ).fetchall()
            self.assertEqual(run["status"], "done")
            self.assertIsNotNone(run["avg_score"])
            self.assertTrue(len(results) >= 1)
            # The seeded chunk's own phrase should retrieve its doc perfectly.
            self.assertGreater(run["avg_score"], 0.0)


class TestApiEndpoints(unittest.TestCase):

    def _fake_llm(self):
        from orivellum.capabilities.llm import LLMResult

        def _fn(messages, **kwargs):
            return LLMResult(text="42", ok=True, model="fake", latency_ms=5,
                             prompt_tokens=3, completion_tokens=1)
        return _fn

    def test_seed_and_list_and_run_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            _seed_doc_and_chunk(db)

            resp = client.post("/api/mcos/seed")
            self.assertEqual(resp.status_code, 200)
            self.assertGreaterEqual(resp.json()["benchmarks"], 4)

            resp = client.get("/api/mcos/benchmarks")
            self.assertEqual(resp.status_code, 200)
            benches = resp.json()["benchmarks"]
            ids = {b["id"] for b in benches}
            self.assertIn("reasoning", ids)
            self.assertIn("rag_retrieval", ids)
            for b in benches:
                self.assertIn("case_count", b)
                self.assertIn("last_run", b)

            # Run a retrieval benchmark synchronously via the capability, then
            # verify it shows up in listing + detail.
            from orivellum.capabilities.mcos import run_benchmark
            run_id = run_benchmark(db, _cfg, "rag_retrieval")

            resp = client.get("/api/mcos/runs?benchmark_id=rag_retrieval")
            self.assertEqual(resp.status_code, 200)
            runs = resp.json()["runs"]
            self.assertTrue(any(r["id"] == run_id for r in runs))
            self.assertEqual(runs[0]["benchmark_name"], "RAG Retrieval")
            self.assertIsInstance(runs[0]["meta"], dict)

            resp = client.get(f"/api/mcos/runs/{run_id}")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["run"]["id"], run_id)
            self.assertTrue(len(body["results"]) >= 1)
            self.assertIn("question", body["results"][0])

    def test_run_endpoint_and_409(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            _seed_doc_and_chunk(db)
            client.post("/api/mcos/seed")

            with patch("orivellum.capabilities.mcos.llm_call", self._fake_llm()):
                resp = client.post("/api/mcos/run/reasoning")
                self.assertEqual(resp.status_code, 200)
                self.assertIn("run_id", resp.json())

            # Simulate an in-flight run by inserting a running row directly.
            import uuid
            from datetime import datetime, timezone
            with db._lock:
                db._conn.execute(
                    "INSERT INTO eval_runs(id,benchmark_id,started_at,status,total_cases)"
                    " VALUES(?,?,?,'running',0)",
                    (str(uuid.uuid4()), "reasoning",
                     datetime.now(timezone.utc).isoformat()),
                )
                db._conn.commit()
            resp = client.post("/api/mcos/run/reasoning")
            self.assertEqual(resp.status_code, 409)

    def test_run_unknown_benchmark_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, _db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            resp = client.post("/api/mcos/run/does-not-exist")
            self.assertEqual(resp.status_code, 404)

    def test_worker_setup_failure_marks_run_failed(self):
        """A pre-loop worker crash must leave the reserved row as 'failed',
        never stuck at 'running' (which would 409-block the benchmark forever)."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.capabilities import mcos
            _seed_doc_and_chunk(db)
            mcos.seed_default_benchmarks(db)

            run_id = mcos._create_run_row(db, cfg, "rag_retrieval")
            with db._lock:
                status = db._conn.execute(
                    "SELECT status FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()[0]
            self.assertEqual(status, "running")

            # Force the pre-loop benchmark lookup (first statement in the guard)
            # to raise; the worker must still finalize the row as failed.
            def _boom(_db, _bid):
                raise RuntimeError("setup exploded")

            with patch.object(mcos, "_get_benchmark", _boom):
                returned = mcos._execute_run(db, cfg, "rag_retrieval", run_id)
            self.assertEqual(returned, run_id)

            with db._lock:
                row = dict(db._conn.execute(
                    "SELECT status, finished_at, meta FROM eval_runs WHERE id=?",
                    (run_id,),
                ).fetchone())
            self.assertEqual(row["status"], "failed")
            self.assertIsNotNone(row["finished_at"])
            import json as _json
            meta = _json.loads(row["meta"])
            self.assertIn("error", meta)
            self.assertIn("setup exploded", meta["error"])

    def test_stale_running_takeover(self):
        """A 'running' row older than the stale window is reaped, letting a new
        run start instead of returning 409."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            _seed_doc_and_chunk(db)
            client.post("/api/mcos/seed")

            # Insert a stale running row (started 40 minutes ago).
            import uuid
            stale_id = str(uuid.uuid4())
            with db._lock:
                db._conn.execute(
                    "INSERT INTO eval_runs(id,benchmark_id,started_at,status,total_cases)"
                    " VALUES(?,?,datetime('now','-40 minutes'),'running',0)",
                    (stale_id, "reasoning"),
                )
                db._conn.commit()

            with patch("orivellum.capabilities.mcos.llm_call", self._fake_llm()):
                resp = client.post("/api/mcos/run/reasoning")
            self.assertEqual(resp.status_code, 200, resp.text)
            new_run_id = resp.json()["run_id"]
            self.assertNotEqual(new_run_id, stale_id)

            with db._lock:
                stale_status = db._conn.execute(
                    "SELECT status FROM eval_runs WHERE id=?", (stale_id,)
                ).fetchone()[0]
            self.assertEqual(stale_status, "failed")

    def test_fresh_running_still_409s(self):
        """A recently-started 'running' row must still block (not be reaped)."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            _seed_doc_and_chunk(db)
            client.post("/api/mcos/seed")

            import uuid
            from datetime import datetime, timezone
            with db._lock:
                db._conn.execute(
                    "INSERT INTO eval_runs(id,benchmark_id,started_at,status,total_cases)"
                    " VALUES(?,?,?,'running',0)",
                    (str(uuid.uuid4()), "reasoning",
                     datetime.now(timezone.utc).isoformat()),
                )
                db._conn.commit()
            resp = client.post("/api/mcos/run/reasoning")
            self.assertEqual(resp.status_code, 409)

    def test_telemetry_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            with db._lock:
                for ok, purpose, lat in [
                    (1, "mcos.eval", 100), (1, "mcos.eval", 200),
                    (0, "mcos.eval", 50), (1, "chat", 300),
                ]:
                    db._conn.execute(
                        "INSERT INTO llm_calls(purpose,model,latency_ms,prompt_tokens,"
                        "completion_tokens,ok) VALUES(?,?,?,?,?,?)",
                        (purpose, "m", lat, 10, 5, ok),
                    )
                db._conn.commit()

            resp = client.get("/api/mcos/telemetry?days=7")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            by_purpose = {p["purpose"]: p for p in body["by_purpose"]}
            self.assertIn("mcos.eval", by_purpose)
            self.assertEqual(by_purpose["mcos.eval"]["calls"], 3)
            self.assertAlmostEqual(by_purpose["mcos.eval"]["error_rate"], 1 / 3, places=3)
            self.assertTrue(len(body["daily"]) >= 1)


if __name__ == "__main__":
    unittest.main()
