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


# ── Phase 2: judge consensus ─────────────────────────────────────────────────

class TestConsensusMath(unittest.TestCase):

    def test_all_three_judges(self):
        from orivellum.capabilities.mcos import _consensus
        # rule=1.0(.5), llm=0.5(.3), grounding=0.0(.2) → (.5 + .15 + 0)/1.0
        c = _consensus({"rule": 1.0, "llm": 0.5, "grounding": 0.0})
        self.assertAlmostEqual(c, 0.65, places=6)

    def test_renormalization_when_llm_absent(self):
        from orivellum.capabilities.mcos import _consensus
        # weights rule=0.5, grounding=0.2 → total 0.7
        # (0.5*1.0 + 0.2*0.5)/0.7
        c = _consensus({"rule": 1.0, "grounding": 0.5})
        self.assertAlmostEqual(c, (0.5 + 0.1) / 0.7, places=6)

    def test_rule_only(self):
        from orivellum.capabilities.mcos import _consensus
        self.assertAlmostEqual(_consensus({"rule": 0.8}), 0.8, places=6)

    def test_empty_is_zero(self):
        from orivellum.capabilities.mcos import _consensus
        self.assertEqual(_consensus({}), 0.0)

    def test_skips_non_finite_values(self):
        from orivellum.capabilities.mcos import _consensus
        # A NaN llm judge must be dropped, leaving rule+grounding renormalized.
        c = _consensus({"rule": 1.0, "llm": float("nan"), "grounding": 0.5})
        self.assertAlmostEqual(c, (0.5 + 0.1) / 0.7, places=6)
        self.assertTrue(c == c)  # not NaN

    def test_all_non_finite_is_zero(self):
        from orivellum.capabilities.mcos import _consensus
        self.assertEqual(_consensus({"rule": float("inf"), "llm": float("nan")}), 0.0)


class TestGroundingJudge(unittest.TestCase):

    def test_no_context_is_absent(self):
        from orivellum.capabilities.mcos import _grounding_judge
        self.assertIsNone(_grounding_judge({"context": ""}, "anything"))

    def test_fraction_of_grounded_sentences(self):
        from orivellum.capabilities.mcos import _grounding_judge
        case = {"context": "Photosynthesis converts sunlight into glucose "
                           "inside chloroplast organelles of plant leaves."}
        # Sentence 1 shares >=2 meaningful words; sentence 2 shares none.
        resp = "Photosynthesis produces glucose. The weather today is nice."
        score = _grounding_judge(case, resp)
        self.assertAlmostEqual(score, 0.5, places=6)

    def test_all_grounded(self):
        from orivellum.capabilities.mcos import _grounding_judge
        case = {"context": "Mitochondria produce adenosine triphosphate energy "
                           "inside eukaryotic cells."}
        resp = "Mitochondria produce adenosine triphosphate energy."
        self.assertAlmostEqual(_grounding_judge(case, resp), 1.0, places=6)


class TestLLMJudge(unittest.TestCase):

    def _judge_returning(self, text, ok=True):
        from orivellum.capabilities.llm import LLMResult

        def _fn(messages, **kwargs):
            return LLMResult(text=text if ok else None, ok=ok, model="fake",
                             latency_ms=1)
        return _fn

    def test_good_json(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call",
                          self._judge_returning('{"score": 0.75, "reason": "close"}')):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertAlmostEqual(score, 0.75, places=6)
        self.assertEqual(reason, "close")

    def test_fenced_json(self):
        from orivellum.capabilities import mcos
        fenced = 'Sure!\n```json\n{"score": 1.0, "reason": "perfect"}\n```'
        with patch.object(mcos, "llm_call", self._judge_returning(fenced)):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertAlmostEqual(score, 1.0, places=6)
        self.assertEqual(reason, "perfect")

    def test_garbage_is_absent(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call",
                          self._judge_returning("no json here at all")):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertIsNone(score)
        self.assertIsNone(reason)

    def test_call_failure_is_absent(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call", self._judge_returning(None, ok=False)):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertIsNone(score)

    def test_score_clamped(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call",
                          self._judge_returning('{"score": 2.5, "reason": "x"}')):
            score, _ = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertEqual(score, 1.0)

    def test_nan_score_is_absent(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call",
                          self._judge_returning('{"score": NaN, "reason": "x"}')):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertIsNone(score)
        self.assertIsNone(reason)

    def test_infinity_score_is_absent(self):
        from orivellum.capabilities import mcos
        with patch.object(mcos, "llm_call",
                          self._judge_returning('{"score": Infinity, "reason": "x"}')):
            score, reason = mcos._llm_judge({"question": "q"}, "resp", None, None)
        self.assertIsNone(score)
        self.assertIsNone(reason)


class TestJudgeCaseIntegration(unittest.TestCase):

    def test_llm_kind_run_stores_consensus(self):
        """A full llm-kind run stores judge_scores with consensus and, for
        context cases, grounding + llm judges."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.capabilities import mcos
            from orivellum.capabilities.llm import LLMResult

            def _fake(messages, **kwargs):
                purpose = kwargs.get("purpose", "")
                if purpose == "mcos.judge":
                    return LLMResult(text='{"score": 0.6, "reason": "ok"}', ok=True,
                                     model="fake", latency_ms=1)
                return LLMResult(text="the answer is 42", ok=True, model="fake",
                                 latency_ms=1)

            with patch.object(mcos, "llm_call", _fake):
                mcos.seed_default_benchmarks(db)
                run_id = mcos.run_benchmark(db, cfg, "reasoning")

            import json as _json
            with db._lock:
                rows = db._conn.execute(
                    "SELECT judge_scores, score FROM eval_results WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            self.assertTrue(len(rows) > 0)
            for r in rows:
                js = _json.loads(r["judge_scores"])
                self.assertIn("rule", js)
                self.assertIn("llm", js)  # AI reachable via successful eval
                self.assertIn("consensus", js)
                self.assertAlmostEqual(r["score"], js["consensus"], places=6)
                # reasoning cases have no context → grounding judge absent
                self.assertNotIn("grounding", js)


# ── Phase 3: regression → governance ─────────────────────────────────────────

def _seed_bench_with_case(db, bid="reg_bench", kind="llm"):
    """Insert a minimal benchmark + one case directly for regression tests."""
    import uuid
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with db._lock:
        db._conn.execute(
            "INSERT INTO benchmarks(id,name,description,category,kind,version,enabled,"
            "created_at) VALUES(?,?,?,?,?,1,1,?)",
            (bid, "Reg Bench", "", "reasoning", kind, now),
        )
        db._conn.execute(
            "INSERT INTO benchmark_cases(id,benchmark_id,question,context,expected_output,"
            "expected_concepts,scoring,difficulty,tags,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), bid, "What is 6*7?", "", "",
             "[]", '{"type":"regex","pattern":"\\\\b42\\\\b"}', "easy", "[]", now),
        )
        db._conn.commit()
    return bid


def _seed_prev_run(db, bid, avg):
    """Insert a finished prior run so the next run computes a delta."""
    import uuid
    from datetime import datetime, timezone
    rid = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    with db._lock:
        db._conn.execute(
            "INSERT INTO eval_runs(id,benchmark_id,started_at,finished_at,status,"
            "total_cases,avg_score,meta) VALUES(?,?,?,?,'done',1,?,'{}')",
            (rid, bid, ts, ts, avg),
        )
        db._conn.commit()
    return rid


class TestRegressionGovernance(unittest.TestCase):

    def _failing_llm(self):
        """Eval call succeeds but the answer is wrong → rule score 0."""
        from orivellum.capabilities.llm import LLMResult

        def _fn(messages, **kwargs):
            if kwargs.get("purpose") == "mcos.judge":
                return LLMResult(text='{"score": 0.0, "reason": "wrong"}', ok=True,
                                 model="fake", latency_ms=1)
            return LLMResult(text="the answer is 99", ok=True, model="fake",
                             latency_ms=1)
        return _fn

    def test_regression_writes_audit_and_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            from orivellum.capabilities import mcos

            bid = _seed_bench_with_case(db)
            _seed_prev_run(db, bid, avg=1.0)  # previous run scored perfectly

            with patch.object(mcos, "llm_call", self._failing_llm()):
                run_id = mcos.run_benchmark(db, cfg, bid)

            # Run should be flagged regressed (0.0 vs 1.0 → delta -1.0).
            with db._lock:
                meta_row = db._conn.execute(
                    "SELECT meta FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()[0]
                audits = db._conn.execute(
                    "SELECT operation, actor, object_id, detail FROM audit_log "
                    "WHERE operation='benchmark_regression'"
                ).fetchall()
            import json as _json
            self.assertTrue(_json.loads(meta_row)["regressed"])
            self.assertEqual(len(audits), 1)
            self.assertEqual(audits[0]["actor"], "mcos")
            self.assertEqual(audits[0]["object_id"], run_id)
            self.assertIn(bid, audits[0]["detail"])

            resp = client.get("/api/mcos/regressions")
            self.assertEqual(resp.status_code, 200)
            regs = resp.json()["regressions"]
            match = [r for r in regs if r["run_id"] == run_id]
            self.assertEqual(len(match), 1)
            self.assertFalse(match[0]["acknowledged"])
            self.assertLess(match[0]["delta"], -0.15)

    def test_ack_flow_and_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            from orivellum.capabilities import mcos

            bid = _seed_bench_with_case(db)
            _seed_prev_run(db, bid, avg=1.0)
            with patch.object(mcos, "llm_call", self._failing_llm()):
                run_id = mcos.run_benchmark(db, cfg, bid)

            # Ack succeeds and flips acknowledged=true.
            resp = client.post(f"/api/mcos/regressions/{run_id}/ack")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["acknowledged"])

            resp = client.get("/api/mcos/regressions")
            match = [r for r in resp.json()["regressions"] if r["run_id"] == run_id]
            self.assertTrue(match[0]["acknowledged"])

            # Ack must be a single atomic UPDATE that preserves regressed/delta
            # (a lost-update race could otherwise erase them).
            import json as _json
            with db._lock:
                meta = _json.loads(db._conn.execute(
                    "SELECT meta FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()[0])
            self.assertTrue(meta["ack"])
            self.assertTrue(meta["regressed"])
            self.assertIn("delta", meta)
            self.assertLess(meta["delta"], -0.15)

            # Unknown run → 404.
            resp = client.post("/api/mcos/regressions/does-not-exist/ack")
            self.assertEqual(resp.status_code, 404)

            # A run that exists but is not a regression → 404.
            import uuid
            from datetime import datetime, timezone
            ok_id = str(uuid.uuid4())
            with db._lock:
                db._conn.execute(
                    "INSERT INTO eval_runs(id,benchmark_id,started_at,status,total_cases,"
                    "avg_score,meta) VALUES(?,?,?,'done',1,1.0,'{}')",
                    (ok_id, bid, datetime.now(timezone.utc).isoformat()),
                )
                db._conn.commit()
            resp = client.post(f"/api/mcos/regressions/{ok_id}/ack")
            self.assertEqual(resp.status_code, 404)

    def test_ack_interleaved_with_finalize_write(self):
        """Simulate a concurrent _finalize_run rewrite happening between the
        ack predicate check and the persisted write. The single-statement
        json_set UPDATE must not clobber the finalize write, and the finalize
        write must not erase the ack — both keys survive."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            from orivellum.capabilities import mcos

            bid = _seed_bench_with_case(db)
            _seed_prev_run(db, bid, avg=1.0)
            with patch.object(mcos, "llm_call", self._failing_llm()):
                run_id = mcos.run_benchmark(db, cfg, bid)

            # Ack (adds $.ack via json_set, leaving other keys intact).
            resp = client.post(f"/api/mcos/regressions/{run_id}/ack")
            self.assertEqual(resp.status_code, 200)

            # A late finalize re-write (best-effort path) touches only its own
            # keys via the capability helper — emulate by re-finalizing with the
            # same regressed meta; ack must persist because _finalize_run writes
            # the whole meta it was given, and ack used json_set on the stored
            # row rather than a stale in-memory copy.
            import json as _json
            with db._lock:
                stored = _json.loads(db._conn.execute(
                    "SELECT meta FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()[0])
            self.assertTrue(stored["ack"])
            self.assertTrue(stored["regressed"])
            # meta must remain valid JSON at all times.
            with db._lock:
                valid = db._conn.execute(
                    "SELECT json_valid(meta) FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()[0]
            self.assertEqual(valid, 1)

    def test_nan_judge_keeps_meta_valid_json(self):
        """A NaN llm-judge score must not poison consensus/avg/delta or produce
        invalid JSON in the persisted run meta."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.capabilities import mcos
            from orivellum.capabilities.llm import LLMResult

            def _fake(messages, **kwargs):
                if kwargs.get("purpose") == "mcos.judge":
                    return LLMResult(text='{"score": NaN, "reason": "x"}', ok=True,
                                     model="fake", latency_ms=1)
                return LLMResult(text="the answer is 42", ok=True, model="fake",
                                 latency_ms=1)

            bid = _seed_bench_with_case(db)
            with patch.object(mcos, "llm_call", _fake):
                run_id = mcos.run_benchmark(db, cfg, bid)

            import json as _json
            with db._lock:
                run = dict(db._conn.execute(
                    "SELECT avg_score, meta FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone())
                results = db._conn.execute(
                    "SELECT score, judge_scores FROM eval_results WHERE run_id=?",
                    (run_id,),
                ).fetchall()
            # Persisted meta is valid JSON and avg_score is finite.
            meta = _json.loads(run["meta"])
            self.assertIsInstance(meta, dict)
            self.assertIsNotNone(run["avg_score"])
            import math as _math
            self.assertTrue(_math.isfinite(run["avg_score"]))
            for r in results:
                js = _json.loads(r["judge_scores"])
                # NaN llm judge dropped entirely; consensus finite.
                self.assertNotIn("llm", js)
                self.assertTrue(_math.isfinite(js["consensus"]))
                self.assertTrue(_math.isfinite(r["score"]))


class TestPromptRegistry(unittest.TestCase):

    def _fake_llm(self):
        from orivellum.capabilities.llm import LLMResult

        def _fn(messages, **kwargs):
            return LLMResult(text="42", ok=True, model="fake", latency_ms=1)
        return _fn

    def test_seed_creates_active_chat_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.mcos import seed_default_benchmarks
            seed_default_benchmarks(db)
            # Idempotent: second call must not create a duplicate.
            seed_default_benchmarks(db)
            with db._lock:
                rows = db._conn.execute(
                    "SELECT * FROM prompts WHERE slot='chat.base'"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["version"], 1)
            self.assertTrue(rows[0]["active"])
            self.assertEqual(db.get_active_prompt("chat.base"), rows[0]["content"])

    def test_build_system_prompt_uses_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes import conversations
            from orivellum.capabilities.mcos import seed_default_prompts
            import uuid as _uuid
            from datetime import datetime, timezone
            seed_default_prompts(db)
            with db._lock:
                db._conn.execute("UPDATE prompts SET active=0 WHERE slot='chat.base'")
                db._conn.execute(
                    "INSERT INTO prompts(id,slot,name,content,version,active,created_at)"
                    " VALUES(?,?,?,?,2,1,?)",
                    (str(_uuid.uuid4()), "chat.base", "custom",
                     "CUSTOM PERSONA MARKER",
                     datetime.now(timezone.utc).isoformat()),
                )
                db._conn.commit()
            sp = conversations._build_system_prompt(db, {"work_id": None})
            self.assertIn("CUSTOM PERSONA MARKER", sp)

    def test_build_system_prompt_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes import conversations
            # No prompts seeded → falls back to hardcoded constant.
            sp = conversations._build_system_prompt(db, {"work_id": None})
            self.assertIn("You are Orivellum", sp)

    def test_create_list_activate_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            client.post("/api/mcos/seed")

            resp = client.post("/api/mcos/prompts", json={
                "slot": "chat.base", "name": "cand", "content": "candidate body"})
            self.assertEqual(resp.status_code, 200)
            pid = resp.json()["prompt"]["id"]
            self.assertEqual(resp.json()["prompt"]["version"], 2)
            self.assertFalse(resp.json()["prompt"]["active"])

            resp = client.get("/api/mcos/prompts?slot=chat.base")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["prompts"]), 2)

            # Cannot delete the active (seeded v1); can delete inactive candidate.
            with db._lock:
                seeded = db._conn.execute(
                    "SELECT id FROM prompts WHERE slot='chat.base' AND active=1"
                ).fetchone()["id"]
            self.assertEqual(client.delete(f"/api/mcos/prompts/{seeded}").status_code, 409)

            resp = client.post(f"/api/mcos/prompts/{pid}/activate")
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["prompt"]["active"])
            self.assertEqual(db.get_active_prompt("chat.base"), "candidate body")
            # Old active is now inactive → deletable.
            self.assertEqual(client.delete(f"/api/mcos/prompts/{seeded}").status_code, 204)

    def test_prompt_run_excluded_from_regression(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.capabilities import mcos
            bid = _seed_bench_with_case(db)
            # Establish a strong prior baseline so a low candidate would regress.
            _seed_prev_run(db, bid, avg=0.95)

            with patch.object(mcos, "llm_call", self._fake_llm()):
                pmeta = {"prompt_id": "p1", "prompt_role": "candidate",
                         "prompt_slot": "chat.base"}
                run_id = mcos._create_run_row(db, cfg, bid, initial_meta=pmeta)
                mcos._execute_run(db, cfg, bid, run_id,
                                  system_prompt="X", run_meta=pmeta)

            import json as _json
            with db._lock:
                meta = _json.loads(db._conn.execute(
                    "SELECT meta FROM eval_runs WHERE id=?", (run_id,)
                ).fetchone()["meta"])
            # Prompt run: never flagged regressed, no delta computed, tagged.
            self.assertFalse(meta.get("regressed"))
            self.assertIsNone(meta.get("delta"))
            self.assertEqual(meta.get("prompt_id"), "p1")

            # The baseline query returns the NORMAL prior run (0.95), never the
            # prompt run — i.e. the prompt run did not become a baseline.
            self.assertAlmostEqual(mcos._prev_finished_avg(db, bid, "nope"), 0.95)

    def test_get_prompt_benchmark_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            client.post("/api/mcos/seed")
            resp = client.post("/api/mcos/prompts", json={
                "slot": "chat.base", "name": "c", "content": "cand"})
            pid = resp.json()["prompt"]["id"]
            # No runs yet.
            resp = client.get(f"/api/mcos/prompts/{pid}/benchmark")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "none")

    def test_benchmark_prompt_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            client.post("/api/mcos/seed")
            resp = client.post("/api/mcos/prompts", json={
                "slot": "chat.base", "name": "c", "content": "cand body"})
            pid = resp.json()["prompt"]["id"]
            from orivellum.capabilities import mcos
            with patch.object(mcos, "llm_call", self._fake_llm()):
                resp = client.post(f"/api/mcos/prompts/{pid}/benchmark")
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertGreater(len(body["candidate_runs"]), 0)
                # There is an active seeded prompt → active runs too.
                self.assertGreater(len(body["active_runs"]), 0)
            # Background tasks run synchronously in TestClient; status is done.
            resp = client.get(f"/api/mcos/prompts/{pid}/benchmark")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "done")
            self.assertIsNotNone(resp.json()["candidate"]["avg"])
            self.assertIsNotNone(resp.json()["delta"])


class TestRagCalibration(unittest.TestCase):

    def test_chunk_params_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities.chunking import _resolve_chunk_params
            # Out-of-range + garbage → clamped/defaulted.
            db.set_setting("chunk_target_words", "999999")
            db.set_setting("chunk_overlap_words", "5000")
            t, o = _resolve_chunk_params(db)
            self.assertEqual(t, 2000)
            self.assertLessEqual(o, t // 2)

            db.set_setting("chunk_target_words", "not-a-number")
            db.set_setting("chunk_overlap_words", "50")
            t, o = _resolve_chunk_params(db)
            self.assertEqual(t, 500)
            self.assertEqual(o, 50)

            db.set_setting("chunk_target_words", "50")  # below min
            t, o = _resolve_chunk_params(db)
            self.assertEqual(t, 100)

    def test_sweep_end_to_end_no_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.capabilities import mcos
            # Seed a few docs with distinctive chunk text.
            for i in range(3):
                doc = db.create_document(title=f"Doc {i}", kind="note")
                db.add_chunk(doc["id"],
                             f"Document number {i} discusses topic alpha{i} "
                             f"beta{i} gamma{i} delta{i} in great technical detail. "
                             f"The special marker phrase for doc {i} is zephyr{i}quux.")
            with db._lock:
                before = db._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            sweep_id = mcos.create_sweep_row(db)
            mcos.rag_sweep(db, sweep_id)
            with db._lock:
                after = db._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                row = dict(db._conn.execute(
                    "SELECT * FROM rag_sweeps WHERE id=?", (sweep_id,)).fetchone())
            self.assertEqual(before, after)  # NO chunk-table writes
            self.assertEqual(row["status"], "done")
            self.assertGreater(row["docs_sampled"], 0)
            import json as _json
            results = _json.loads(row["results"])
            self.assertGreater(len(results), 0)
            for r in results:
                self.assertLess(r["overlap_words"], r["target_words"] / 2)

    def test_rag_config_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            resp = client.get("/api/mcos/rag/config")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("target_words", resp.json())

            # Valid apply.
            resp = client.post("/api/mcos/rag/apply",
                               json={"target_words": 800, "overlap_words": 80})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(db.get_setting("chunk_target_words"), "800")

            # Invalid: overlap >= target/2.
            resp = client.post("/api/mcos/rag/apply",
                               json={"target_words": 400, "overlap_words": 300})
            self.assertEqual(resp.status_code, 400)
            # Invalid: target out of bounds.
            resp = client.post("/api/mcos/rag/apply",
                               json={"target_words": 50, "overlap_words": 10})
            self.assertEqual(resp.status_code, 400)

    def test_rag_sweep_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc = db.create_document(title="D", kind="note")
            db.add_chunk(doc["id"], "Alpha beta gamma delta epsilon marker phrase here "
                                    "for retrieval calibration testing purposes indeed.")
            resp = client.post("/api/mcos/rag/sweep")
            self.assertEqual(resp.status_code, 202)
            sid = resp.json()["sweep_id"]
            resp = client.get("/api/mcos/rag/sweeps")
            self.assertEqual(resp.status_code, 200)
            sweeps = resp.json()["sweeps"]
            self.assertTrue(any(s["id"] == sid for s in sweeps))


class TestPromptConcurrency(unittest.TestCase):
    """Regression tests for the review-flagged TOCTOU / race issues."""

    def _make_two_prompts(self, db):
        """Return (active_id, candidate_id) in slot 'chat.base'."""
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        active_id = str(uuid.uuid4())
        cand_id = str(uuid.uuid4())
        with db._lock:
            db._conn.execute(
                "INSERT INTO prompts(id,slot,name,content,version,active,created_at)"
                " VALUES(?,?,?,?,1,1,?)",
                (active_id, "chat.base", "active", "ACTIVE BODY", now))
            db._conn.execute(
                "INSERT INTO prompts(id,slot,name,content,version,active,created_at)"
                " VALUES(?,?,?,?,2,0,?)",
                (cand_id, "chat.base", "cand", "CAND BODY", now))
            db._conn.commit()
        return active_id, cand_id

    def test_delete_active_conflicts_and_slot_keeps_active(self):
        """delete_prompt must 409 on the active row and never leave the slot
        without an active prompt (fix #1)."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes import mcos as routes
            from fastapi import HTTPException
            active_id, cand_id = self._make_two_prompts(db)

            with self.assertRaises(HTTPException) as ctx:
                routes.delete_prompt(active_id)
            self.assertEqual(ctx.exception.status_code, 409)

            # Slot still has exactly one active prompt.
            with db._lock:
                n = db._conn.execute(
                    "SELECT COUNT(*) FROM prompts WHERE slot='chat.base' AND active=1"
                ).fetchone()[0]
            self.assertEqual(n, 1)

    def test_activate_then_delete_old_keeps_one_active(self):
        """After activating the candidate, deleting the now-inactive old prompt
        is allowed and the slot retains one active prompt (fix #1)."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes import mcos as routes
            active_id, cand_id = self._make_two_prompts(db)

            routes.activate_prompt(cand_id)
            # Old active is now deletable.
            resp = routes.delete_prompt(active_id)
            self.assertEqual(resp.status_code, 204)
            with db._lock:
                rows = db._conn.execute(
                    "SELECT id, active FROM prompts WHERE slot='chat.base'"
                ).fetchall()
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["active"])
            self.assertEqual(rows[0]["id"], cand_id)

    def test_activate_missing_after_delete_is_404(self):
        """Activating a prompt deleted between read and write yields 404, not a
        slot with zero active prompts (fix #1)."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, _cfg = _make_app(tmp)
            from orivellum.api.routes import mcos as routes
            from fastapi import HTTPException
            import uuid
            with self.assertRaises(HTTPException) as ctx:
                routes.activate_prompt(str(uuid.uuid4()))
            self.assertEqual(ctx.exception.status_code, 404)

    def test_benchmark_prompt_second_request_409(self):
        """Concurrent prompt-benchmark requests: the second sees the first's
        reserved running rows and 409s (fix #2)."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            client.post("/api/mcos/seed")
            pid = client.post("/api/mcos/prompts", json={
                "slot": "chat.base", "name": "c", "content": "cand"}).json()["prompt"]["id"]

            from orivellum.api.routes import mcos as routes
            from orivellum.capabilities import mcos
            from fastapi import BackgroundTasks, HTTPException

            with patch.object(mcos, "llm_call", lambda messages, **kw: _ok_llm()):
                # First reservation (don't run worker) leaves running rows.
                bg1 = BackgroundTasks()
                body = routes.benchmark_prompt(pid, bg1)
                self.assertGreater(len(body["candidate_runs"]), 0)
                # A second request while those rows are 'running' must 409.
                bg2 = BackgroundTasks()
                with self.assertRaises(HTTPException) as ctx:
                    routes.benchmark_prompt(pid, bg2)
                self.assertEqual(ctx.exception.status_code, 409)

    def test_prompt_run_no_regressed_in_run_row(self):
        """_run_row_to_dict must not recompute delta/regressed for prompt runs
        even against a strong normal baseline (fix #3)."""
        with tempfile.TemporaryDirectory() as tmp:
            _app, db, cfg = _make_app(tmp)
            from orivellum.api.routes import mcos as routes
            from orivellum.capabilities import mcos
            bid = _seed_bench_with_case(db)
            _seed_prev_run(db, bid, avg=0.95)  # strong normal baseline

            with patch.object(mcos, "llm_call", lambda messages, **kw: _ok_llm()):
                pmeta = {"prompt_id": "p1", "prompt_role": "candidate",
                         "prompt_slot": "chat.base"}
                rid = mcos._create_run_row(db, cfg, bid, initial_meta=pmeta)
                mcos._execute_run(db, cfg, bid, rid, system_prompt="X", run_meta=pmeta)

            with db._lock:
                row = db._conn.execute(
                    "SELECT r.*, b.name AS benchmark_name FROM eval_runs r "
                    "JOIN benchmarks b ON b.id=r.benchmark_id WHERE r.id=?", (rid,)
                ).fetchone()
            d = routes._run_row_to_dict(db, row)
            self.assertFalse(d["meta"]["regressed"])
            self.assertIsNone(d["meta"]["delta"])


class TestSweepLifecycle(unittest.TestCase):

    def test_stale_sweep_reaped(self):
        """A sweep stuck 'running' for >30 min is marked failed on the next
        list/start (fix #4)."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            import uuid
            sid = str(uuid.uuid4())
            with db._lock:
                db._conn.execute(
                    "INSERT INTO rag_sweeps(id,started_at,status) "
                    "VALUES(?, datetime('now','-45 minutes'), 'running')", (sid,))
                db._conn.commit()
            resp = client.get("/api/mcos/rag/sweeps")
            self.assertEqual(resp.status_code, 200)
            match = [s for s in resp.json()["sweeps"] if s["id"] == sid]
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0]["status"], "failed")

    def test_concurrent_sweep_409(self):
        """POST /rag/sweep 409s while a non-stale sweep is running (fix #4)."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _cfg = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            import uuid
            sid = str(uuid.uuid4())
            with db._lock:
                db._conn.execute(
                    "INSERT INTO rag_sweeps(id,started_at,status) "
                    "VALUES(?, datetime('now'), 'running')", (sid,))
                db._conn.commit()
            resp = client.post("/api/mcos/rag/sweep")
            self.assertEqual(resp.status_code, 409)


def _ok_llm():
    from orivellum.capabilities.llm import LLMResult
    return LLMResult(text="42", ok=True, model="fake", latency_ms=1)


if __name__ == "__main__":
    unittest.main()
