"""Tests for the Workbench portfolio layer (health, needs, close-out).

Covers:
- compute_health deterministic scoring: new project, open findings,
  last build error, never-analyzed penalty, clamping and grades
- generate_needs: strict JSON validation, caching in project meta,
  failure raises (never stores garbage)
- run_closeout: lessons stored as knowledge items, deterministic summary
  survives an offline model (never raises for LLM problems)
- routes: rundown shape, needs guards, complete runs close-out, shelve
  makes the project read-only, reactivate brings it back
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


def _llm_ok(payload: dict):
    return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)


_LLM_DOWN = SimpleNamespace(ok=False, text="", error="model offline")


# ── Health score ──────────────────────────────────────────────────────────────


class TestComputeHealth(unittest.TestCase):
    def _health(self, proj=None, versions=None):
        from orivellum.capabilities.workbench_portfolio import compute_health

        base = {"status": "active", "last_error": None, "building": 0, "updated_at": None}
        return compute_health({**base, **(proj or {})}, versions or [])

    def test_no_versions_is_new(self):
        h = self._health()
        self.assertIsNone(h["score"])
        self.assertEqual(h["grade"], "new")

    def test_clean_verified_project_is_healthy(self):
        v = {"verdict": "analyzed", "checks_json": json.dumps({"issues": []})}
        h = self._health(versions=[v])
        self.assertEqual(h["score"], 100)
        self.assertEqual(h["grade"], "healthy")

    def test_findings_and_error_lower_the_score(self):
        v = {
            "verdict": "analyzed",
            "checks_json": json.dumps({"issues": ["a", "b", "c"]}),
        }
        h = self._health({"last_error": "boom"}, [v])
        # 100 - 25 (error) - 24 (3 findings) = 51 → at_risk
        self.assertEqual(h["score"], 51)
        self.assertEqual(h["grade"], "at_risk")
        self.assertEqual(h["open_findings"], 3)

    def test_never_analyzed_penalty(self):
        v = {"verdict": "verified", "checks_json": json.dumps({"problems": []})}
        h = self._health(versions=[v])
        self.assertEqual(h["score"], 90)
        self.assertTrue(any("review" in p["label"].lower() for p in h["parts"]))

    def test_score_clamped_and_malformed_checks_ignored(self):
        v = {
            "verdict": "analyzed",
            "checks_json": json.dumps({"issues": [f"i{n}" for n in range(20)]}),
        }
        bad = {"verdict": "verified", "checks_json": "{not json"}
        h = self._health({"last_error": "x", "updated_at": "2020-01-01T00:00:00+00:00"}, [bad, v])
        self.assertGreaterEqual(h["score"], 5)
        self.assertEqual(h["grade"], "at_risk")


# ── Needs assessment ──────────────────────────────────────────────────────────


class TestGenerateNeeds(unittest.TestCase):
    def test_valid_answer_is_cached_in_meta(self):
        from orivellum.capabilities.workbench_portfolio import generate_needs, project_meta

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "monthly budget")
            db.create_wb_version(p["id"], "initial", [])
            payload = {
                "summary": "Solid start.",
                "needs": [
                    {"title": "Add a summary sheet", "why": "brief asks for it", "priority": "now"},
                    {"title": "Weird priority", "why": "", "priority": "whenever"},
                    {"not_a": "dict-entry-missing-title"},
                ],
            }
            with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
                needs = generate_needs(db, cfg, p["id"])
            self.assertEqual(len(needs["items"]), 2)
            self.assertEqual(needs["items"][0]["priority"], "now")
            self.assertEqual(needs["items"][1]["priority"], "soon")  # coerced
            meta = project_meta(db.get_wb_project(p["id"]))
            self.assertEqual(meta["needs"]["summary"], "Solid start.")
            self.assertIn("generated_at", meta["needs"])

    def test_model_failure_raises_and_stores_nothing(self):
        from orivellum.capabilities.workbench_portfolio import generate_needs, project_meta

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Tool", "code", "cli tool")
            db.create_wb_version(p["id"], "initial", [])
            with (
                patch("orivellum.capabilities.llm.llm_call", return_value=_LLM_DOWN),
                self.assertRaises(RuntimeError),
            ):
                generate_needs(db, cfg, p["id"])
            with (
                patch(
                    "orivellum.capabilities.llm.llm_call",
                    return_value=SimpleNamespace(ok=True, text="not json at all", error=None),
                ),
                self.assertRaises(RuntimeError),
            ):
                generate_needs(db, cfg, p["id"])
            self.assertNotIn("needs", project_meta(db.get_wb_project(p["id"])))


# ── Close-out ─────────────────────────────────────────────────────────────────


class TestRunCloseout(unittest.TestCase):
    def test_lessons_become_knowledge_items(self):
        from orivellum.capabilities.workbench_portfolio import project_meta, run_closeout

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Ledger", "xlsx", "a ledger")
            db.create_wb_version(p["id"], "initial", [])
            payload = {
                "summary": "Built a ledger in one version.",
                "lessons": [
                    {"text": "Lock the column layout early.", "category": "technical"},
                    {"text": "Scope creep hurt.", "category": "made-up-category"},
                ],
            }
            with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
                closeout = run_closeout(db, cfg, p["id"])
            self.assertEqual(len(closeout["lessons"]), 2)
            self.assertEqual(closeout["lessons"][1]["category"], "process")  # server-owned
            self.assertIsNone(closeout["note"])
            items = {i["id"]: i for i in db.list_knowledge(kind="lesson")}
            for lesson in closeout["lessons"]:
                item = items.get(lesson["knowledge_id"])
                self.assertIsNotNone(item)
                self.assertEqual(item["kind"], "lesson")
                self.assertEqual(item["review_status"], "ai_auto")
                self.assertEqual(item["meta"]["source"], "workbench_closeout")
            self.assertEqual(
                project_meta(db.get_wb_project(p["id"]))["closeout"]["summary"],
                "Built a ledger in one version.",
            )

    def test_offline_model_never_blocks_completion(self):
        from orivellum.capabilities.workbench_portfolio import run_closeout

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Tool", "code", "t")
            db.create_wb_version(p["id"], "initial", [])
            with patch("orivellum.capabilities.llm.llm_call", return_value=_LLM_DOWN):
                closeout = run_closeout(db, cfg, p["id"])
            self.assertEqual(closeout["lessons"], [])
            self.assertIn("unavailable", closeout["note"])
            self.assertEqual(closeout["stats"]["version_count"], 1)


# ── Routes ────────────────────────────────────────────────────────────────────


class TestPortfolioRoutes(unittest.TestCase):
    def test_list_carries_health_and_rundown_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _ = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")
            db.create_wb_version(p["id"], "initial", [])
            client = TestClient(app)
            r = client.get("/api/workbench/projects", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            proj = r.json()["projects"][0]
            self.assertIn("health", proj)
            self.assertNotIn("meta", proj)  # raw blob never leaks
            r = client.get(f"/api/workbench/projects/{p['id']}/rundown", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("health", body)
            self.assertIsNone(body["needs"])
            self.assertIsNone(body["closeout"])

    def test_needs_route_guards_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _ = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")
            client = TestClient(app)
            r = client.post(f"/api/workbench/projects/{p['id']}/needs", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409)  # no versions yet
            db.create_wb_version(p["id"], "initial", [])
            with patch("orivellum.capabilities.llm.llm_call", return_value=_LLM_DOWN):
                r = client.post(f"/api/workbench/projects/{p['id']}/needs", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 503)
            payload = {"summary": "ok", "needs": [{"title": "Do X", "why": "", "priority": "now"}]}
            with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
                r = client.post(f"/api/workbench/projects/{p['id']}/needs", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["needs"]["items"][0]["title"], "Do X")

    def test_complete_runs_closeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            from orivellum.capabilities.workbench import version_dir

            p = db.create_wb_project("Budget", "xlsx", "b")
            vdir = version_dir(cfg, p["id"], 1)
            vdir.mkdir(parents=True)
            f = vdir / "book.xlsx"
            f.write_bytes(b"data")
            import hashlib

            db.create_wb_version(
                p["id"],
                "initial",
                [{"name": "book.xlsx", "size": 4, "sha256": hashlib.sha256(b"data").hexdigest()}],
            )
            client = TestClient(app)
            with patch("orivellum.capabilities.llm.llm_call", return_value=_LLM_DOWN):
                r = client.post(f"/api/workbench/projects/{p['id']}/complete", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["archived"])
            self.assertIn("closeout", body)
            self.assertEqual(body["closeout"]["stats"]["version_count"], 1)
            self.assertEqual(db.get_wb_project(p["id"])["status"], "archived")

    def test_shelve_reactivate_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, _ = _make_app(tmp)
            p = db.create_wb_project("Tool", "code", "t")
            db.create_wb_version(p["id"], "initial", [])
            client = TestClient(app)
            r = client.post(f"/api/workbench/projects/{p['id']}/shelve", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "shelved")
            # shelved projects are read-only for builds/needs
            r = client.post(f"/api/workbench/projects/{p['id']}/needs", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409)
            self.assertIn("shelved", r.json()["detail"])
            # ...and can't be shelved twice or completed
            r = client.post(f"/api/workbench/projects/{p['id']}/shelve", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409)
            r = client.post(f"/api/workbench/projects/{p['id']}/complete", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409)
            r = client.post(f"/api/workbench/projects/{p['id']}/reactivate", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["status"], "active")
            self.assertFalse(db.get_wb_project(p["id"])["building"])
            # active projects can't be "reactivated"
            r = client.post(f"/api/workbench/projects/{p['id']}/reactivate", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
