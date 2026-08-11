"""Context compiler + worker acceptance tests (task: full sight of the book).

Proves by assertion — not by eyeball — that:
- each stage's declared recipe delivers the right sources within budget on an
  80-chapter synthetic fixture (seal artifacts, canon, contracts, prose);
- B0 must cite real G-stage artifacts when a seal exists;
- B1 must reconcile to the blueprint chapter count, never invent one;
- B3 dependency output is checked for cycles/forward references by code.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities.context_compiler import STAGE_RECIPES, compile_context
from orivellum.capabilities.pipeline_workers import (
    check_architecture_dag,
    render_registered_prompt,
    run_stage_worker,
)
from orivellum.configuration.config import OrivellumConfig
from orivellum.database.db import OrivellumDB

_NOW = datetime.now(UTC).isoformat()
_N_CHAPTERS = 80


def _uid() -> str:
    return str(uuid.uuid4())


def _seed_fixture(db: OrivellumDB) -> tuple[str, str]:
    """Create a work + pipeline + sealed GENESIS book + canon + 80 chapters."""
    work = db.create_work("Ash and Silence", work_type="book")
    work_id = work["id"]
    pipeline = db.create_book_pipeline(work_id, "Ash and Silence pipeline")
    pipeline_id = pipeline["id"]

    book_id = _uid()
    with db._lock:
        manifest = {
            "book_id": book_id,
            "package_sha256": "f" * 64,
            "sealed_at": _NOW,
            "author_signoff": "Author",
            "artifacts": [{"code": f"G{i}", "sha256": "a" * 64} for i in range(10)],
        }
        db._conn.execute(
            "INSERT INTO genesis_books (id, work_id, mode, length, acts, state, "
            "manifest_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                book_id,
                work_id,
                "cold",
                _N_CHAPTERS,
                4,
                "READY_FOR_B0",
                json.dumps(manifest),
                _NOW,
                _NOW,
            ),
        )
        for i in range(10):
            code = f"G{i}"
            content = f"# {code} artifact\n" + (f"{code} line of sealed material. " * 40)
            db._conn.execute(
                "INSERT INTO genesis_artifacts (id, book_id, stage_code, content, "
                "sha256, updated_at) VALUES (?,?,?,?,?,?)",
                (_uid(), book_id, code, content, "a" * 64, _NOW),
            )
            db._conn.execute(
                "INSERT INTO genesis_stages (id, book_id, stage_code, status) VALUES (?,?,?,?)",
                (_uid(), book_id, code, "PASSED"),
            )
        # Canon facts: one HISTORICAL with a source, one INVENTED, one series-wide
        for stmt, cls, src, wid in (
            ("Job lived in the land of Uz.", "HISTORICAL", "Job 1:1", work_id),
            ("The narrator's sister is named Adah.", "INVENTED", "AUTHOR", work_id),
            ("The series spans three generations.", "INVENTED", "AUTHOR", None),
        ):
            db._conn.execute(
                "INSERT INTO canon_fact (id, work_id, statement, classification, "
                "source_ref, signed_by, created_at) VALUES (?,?,?,?,?,?,?)",
                (_uid(), wid, stmt, cls, src, "Author", _NOW),
            )
        # 80 chapters, each with a contract in meta and real prose
        for seq in range(1, _N_CHAPTERS + 1):
            cid = _uid()
            db._conn.execute(
                "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,"
                "created_at,updated_at,created_by) "
                "VALUES(?,?,1,'active','{}','{}',?,?,'system')",
                (cid, "chapter", _NOW, _NOW),
            )
            contract = {
                "pov": "Adah" if seq % 2 else "Narrator",
                "beat": f"beat-{seq}",
                "exit_state": f"state-{seq}",
            }
            text = f"Chapter {seq} prose. " + ("The ash settled over Uz. " * 30)
            db._conn.execute(
                "INSERT INTO book_chapters(id,pipeline_id,work_id,seq,title,text,meta,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    pipeline_id,
                    work_id,
                    seq,
                    f"Chapter {seq}",
                    text,
                    json.dumps({"contract": contract}),
                    _NOW,
                    _NOW,
                ),
            )
        db._conn.commit()
    return work_id, pipeline_id


class ContextCompilerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cfg = OrivellumConfig(data_dir=cls._tmp.name)
        cls.db = OrivellumDB(str(Path(cls._tmp.name) / "test.db"))
        cls.work_id, cls.pipeline_id = _seed_fixture(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls._tmp.cleanup()


class TestRecipesAndBudgets(ContextCompilerFixture):
    def test_every_stage_stays_within_declared_budgets(self):
        """The budget applies to the EXACT delivered block, not an estimate."""
        for stage, recipe in STAGE_RECIPES.items():
            ctx = compile_context(self.pipeline_id, stage, self.db)
            report = ctx["context_report"]
            for source, budget in recipe.items():
                self.assertIn(source, report, f"{stage}: no report entry for {source}")
                block = ctx["blocks"][source]
                self.assertLessEqual(
                    len(block),
                    max(budget, 0),
                    f"{stage}/{source} delivered more than its budget",
                )
                self.assertEqual(
                    report[source]["chars"],
                    len(block),
                    f"{stage}/{source} report does not match the delivered block",
                )
                if budget == 0:
                    self.assertEqual(block, "")
                    self.assertEqual(
                        report[source]["items"],
                        0,
                        f"{stage}/{source} delivered items despite a 0 budget",
                    )

    def test_b0_receives_seal_and_canon_but_no_prose(self):
        ctx = compile_context(self.pipeline_id, "B0", self.db)
        self.assertTrue(ctx["genesis"]["sealed"])
        self.assertEqual(ctx["genesis"]["manifest"]["package_sha256"], "f" * 64)
        # highest-priority artifacts survive clipping
        self.assertIn("G9", ctx["genesis"]["artifacts"])
        self.assertIn("G1", ctx["genesis"]["artifacts"])
        self.assertGreater(ctx["context_report"]["genesis"]["chars"], 0)
        # canon reaches B0, incl. series-scoped facts
        stmts = [f["statement"] for f in ctx["canon_facts"]]
        self.assertIn("Job lived in the land of Uz.", stmts)
        self.assertIn("The series spans three generations.", stmts)
        self.assertEqual(
            [f for f in ctx["canon_facts"] if f["classification"] == "HISTORICAL"][0]["source_ref"],
            "Job 1:1",
        )
        # no prose for the intake stage
        self.assertEqual(ctx["chapters"], [])
        self.assertEqual(ctx["chapter_contracts"], [])

    def test_b1_sees_blueprint_count_and_contracts(self):
        ctx = compile_context(self.pipeline_id, "B1", self.db)
        self.assertEqual(ctx["genesis"]["blueprint_chapter_count"], _N_CHAPTERS)
        self.assertIn("G8", ctx["genesis"]["artifacts"])  # blueprint is top priority
        self.assertGreater(len(ctx["chapter_contracts"]), 0)
        first = ctx["chapter_contracts"][0]
        self.assertEqual(first["seq"], 1)
        self.assertEqual(first["contract"]["beat"], "beat-1")

    def test_b6_receives_prose_from_every_chapter_within_budget(self):
        ctx = compile_context(self.pipeline_id, "B6", self.db)
        report = ctx["context_report"]["chapter_text"]
        self.assertEqual(report["items"], _N_CHAPTERS)
        self.assertLessEqual(report["chars"], STAGE_RECIPES["B6"]["chapter_text"])
        seqs = [c["seq"] for c in ctx["chapters"]]
        self.assertEqual(seqs, list(range(1, _N_CHAPTERS + 1)))
        for c in ctx["chapters"]:
            self.assertTrue(c["excerpt"])
            self.assertGreater(c["words"], 0)
        # documents are excluded from B6 by recipe
        self.assertEqual(ctx["documents"], [])

    def test_b7_receives_canon_and_prose(self):
        ctx = compile_context(self.pipeline_id, "B7", self.db)
        self.assertGreater(len(ctx["canon_facts"]), 0)
        self.assertGreater(len(ctx["chapters"]), 0)

    def test_unknown_stage_or_pipeline_fail_loudly(self):
        with self.assertRaises(ValueError):
            compile_context(self.pipeline_id, "B99", self.db)
        with self.assertRaises(ValueError):
            compile_context("nope", "B0", self.db)


class TestDagCheck(unittest.TestCase):
    def test_valid_backward_dag(self):
        chapters = [
            {"seq": 1, "title": "One", "depends_on": []},
            {"seq": 2, "title": "Two", "depends_on": [1]},
            {"seq": 3, "title": "Three", "depends_on": ["1", "Two"]},
        ]
        self.assertEqual(check_architecture_dag(chapters), [])

    def test_forward_reference_rejected(self):
        chapters = [
            {"seq": 1, "depends_on": [2]},
            {"seq": 2, "depends_on": []},
        ]
        problems = check_architecture_dag(chapters)
        self.assertTrue(any("forward reference" in p for p in problems))

    def test_self_reference_rejected(self):
        problems = check_architecture_dag([{"seq": 1, "depends_on": [1]}])
        self.assertTrue(any("self reference" in p for p in problems))

    def test_cycle_rejected(self):
        chapters = [
            {"seq": 1, "depends_on": ["Two"]},
            {"seq": 2, "title": "Two", "depends_on": [1]},
        ]
        self.assertNotEqual(check_architecture_dag(chapters), [])

    def test_unknown_dependency_rejected(self):
        problems = check_architecture_dag([{"seq": 1, "depends_on": ["Ghost Chapter"]}])
        self.assertTrue(any("unresolvable" in p for p in problems))

    def test_duplicate_seq_rejected(self):
        problems = check_architecture_dag([{"seq": 1}, {"seq": 1}])
        self.assertTrue(any("Duplicate" in p for p in problems))


def _llm(payload: dict):
    """Return a stub llm_call producing the given JSON payload."""

    def fake(messages, **kwargs):
        return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)

    return fake


class TestWorkerAcceptance(ContextCompilerFixture):
    def _artifact(self, stage):
        return self.db.get_pipeline_artifact(self.pipeline_id, stage)

    def test_b0_without_citations_fails_when_sealed(self):
        payload = {"title": "X", "premise": "Y", "goals": []}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B0", self.db, self.cfg)
        self.assertIn("cites no G-stage artifacts", str(cm.exception))
        self.assertEqual(self._artifact("B0")["status"], "failed")

    def test_b0_with_bogus_citation_fails(self):
        payload = {"title": "X", "source_citations": ["G1", "G42"]}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B0", self.db, self.cfg)
        self.assertIn("G42", str(cm.exception))

    def test_b0_with_real_citations_passes(self):
        payload = {"title": "Ash and Silence", "premise": "…", "source_citations": ["g1", "G9"]}
        with patch("orivellum.capabilities.llm.llm_call", _llm(payload)):
            content = run_stage_worker(self.pipeline_id, "B0", self.db, self.cfg)
        self.assertEqual(content["source_citations"], ["G1", "G9"])
        self.assertEqual(self._artifact("B0")["status"], "done")

    def test_b1_invented_chapter_count_fails(self):
        payload = {"total_chapters": 75, "chapters": [], "blueprint_deltas": []}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)
        self.assertIn("invented a chapter count", str(cm.exception))
        self.assertEqual(self._artifact("B1")["status"], "failed")

    def test_b1_missing_deltas_fails(self):
        payload = {"total_chapters": _N_CHAPTERS, "chapters": []}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError),
        ):
            run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)

    def test_b1_empty_chapters_fails(self):
        payload = {"total_chapters": _N_CHAPTERS, "chapters": [], "blueprint_deltas": []}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)
        self.assertIn("non-empty list", str(cm.exception))

    def test_b1_non_list_chapters_fails(self):
        payload = {"total_chapters": _N_CHAPTERS, "chapters": "lots", "blueprint_deltas": []}
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError),
        ):
            run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)

    def test_b1_duplicate_seq_fails(self):
        payload = {
            "total_chapters": _N_CHAPTERS,
            "chapters": [{"seq": 1, "title": "A"}, {"seq": 1, "title": "B"}],
            "blueprint_deltas": [],
        }
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)
        self.assertIn("duplicate", str(cm.exception))

    def test_b1_reconciled_outline_covers_full_blueprint(self):
        payload = {
            "total_chapters": _N_CHAPTERS,
            "chapters": [{"seq": 1, "title": "Opening"}, {"seq": 80, "title": "Close"}],
            "blueprint_deltas": [],
        }
        with patch("orivellum.capabilities.llm.llm_call", _llm(payload)):
            content = run_stage_worker(self.pipeline_id, "B1", self.db, self.cfg)
        self.assertEqual(self._artifact("B1")["status"], "done")
        # deterministic reconciliation: outline ALWAYS covers exactly 1..N
        seqs = [c["seq"] for c in content["chapters"]]
        self.assertEqual(seqs, list(range(1, _N_CHAPTERS + 1)))
        self.assertEqual(content["chapters"][0]["title"], "Opening")
        # missing entries are filled from the scaffolded chapters, flagged as such
        filled = content["chapters"][1]
        self.assertTrue(filled["from_blueprint"])
        self.assertEqual(filled["title"], "Chapter 2")

    def test_b3_forward_dependency_fails_deterministically(self):
        payload = {
            "arc_type": "chronological",
            "chapters": [
                {"seq": 1, "title": "One", "depends_on": [3]},
                {"seq": 2, "title": "Two", "depends_on": []},
                {"seq": 3, "title": "Three", "depends_on": [2]},
            ],
        }
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B3", self.db, self.cfg)
        self.assertIn("dependency graph invalid", str(cm.exception))
        self.assertEqual(self._artifact("B3")["status"], "failed")

    def test_b3_empty_or_missing_chapters_fails(self):
        for payload in ({}, {"chapters": []}, {"chapters": "many"}):
            with (
                patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
                self.assertRaises(RuntimeError) as cm,
            ):
                run_stage_worker(self.pipeline_id, "B3", self.db, self.cfg)
            self.assertIn("non-empty list", str(cm.exception))
        self.assertEqual(self._artifact("B3")["status"], "failed")

    def test_b3_partial_blueprint_coverage_fails(self):
        payload = {
            "arc_type": "chronological",
            "chapters": [
                {"seq": 1, "title": "One", "depends_on": []},
                {"seq": 2, "title": "Two", "depends_on": [1]},
            ],
        }
        with (
            patch("orivellum.capabilities.llm.llm_call", _llm(payload)),
            self.assertRaises(RuntimeError) as cm,
        ):
            run_stage_worker(self.pipeline_id, "B3", self.db, self.cfg)
        self.assertIn("covers 2 of 80", str(cm.exception))

    def test_b3_valid_full_dag_passes(self):
        payload = {
            "arc_type": "chronological",
            "chapters": [
                {"seq": s, "title": f"Ch {s}", "depends_on": [s - 1] if s > 1 else []}
                for s in range(1, _N_CHAPTERS + 1)
            ],
        }
        with patch("orivellum.capabilities.llm.llm_call", _llm(payload)):
            run_stage_worker(self.pipeline_id, "B3", self.db, self.cfg)
        self.assertEqual(self._artifact("B3")["status"], "done")


class TestRegisteredPromptCompat(ContextCompilerFixture):
    """Legacy registered templates with literal JSON braces must not crash."""

    _SLOT = "pipeline.b0.brief"

    def tearDown(self):
        with self.db._lock:
            self.db._conn.execute("DELETE FROM prompts WHERE slot=?", (self._SLOT,))
            self.db._conn.commit()

    def test_legacy_template_with_literal_braces_renders(self):
        template = (
            "Work: {work_title}\nDocs:\n{documents}\n"
            'Return JSON like {"title": "x", "source_citations": ["G1"]}'
        )
        with self.db._lock:
            self.db._conn.execute(
                "INSERT INTO prompts (id, slot, name, content, active) VALUES (?,?,?,?,1)",
                (_uid(), self._SLOT, "legacy", template),
            )
            self.db._conn.commit()
        payload = {"title": "X", "source_citations": ["G1"]}
        with patch("orivellum.capabilities.llm.llm_call", _llm(payload)):
            run_stage_worker(self.pipeline_id, "B0", self.db, self.cfg)
        self.assertEqual(self.db.get_pipeline_artifact(self.pipeline_id, "B0")["status"], "done")

    def test_render_covers_all_new_placeholders(self):
        values = {
            "work_title": "T",
            "work_description": "D",
            "documents": "docs",
            "knowledge": "kn",
            "prior_stages": "pr",
            "genesis": "gen",
            "canon": "can",
            "contracts": "con",
            "chapters": "cha",
        }
        template = " ".join("{" + k + "}" for k in values) + ' {"literal": true} {unknown}'
        rendered = render_registered_prompt(template, values)
        for v in values.values():
            self.assertIn(v, rendered)
        self.assertIn('{"literal": true}', rendered)
        self.assertIn("{unknown}", rendered)


class TestOversizedBook(unittest.TestCase):
    """A book larger than budget/min-share must still be delivered within budget."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "big.db"))
        work = self.db.create_work("Oversized", work_type="book")
        self.pipeline_id = self.db.create_book_pipeline(work["id"], "big")["id"]
        n = 150  # > B6 chapter_text budget / 200-char minimum share
        with self.db._lock:
            for seq in range(1, n + 1):
                cid = _uid()
                self.db._conn.execute(
                    "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,"
                    "created_at,updated_at,created_by) "
                    "VALUES(?,?,1,'active','{}','{}',?,?,'system')",
                    (cid, "chapter", _NOW, _NOW),
                )
                self.db._conn.execute(
                    "INSERT INTO book_chapters(id,pipeline_id,work_id,seq,title,text,meta,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        self.pipeline_id,
                        work["id"],
                        seq,
                        f"Chapter {seq}",
                        "word " * 3000,
                        "{}",
                        _NOW,
                        _NOW,
                    ),
                )
            self.db._conn.commit()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_chapter_text_never_exceeds_budget(self):
        ctx = compile_context(self.pipeline_id, "B6", self.db)
        budget = STAGE_RECIPES["B6"]["chapter_text"]
        block = ctx["blocks"]["chapter_text"]
        self.assertLessEqual(len(block), budget)
        self.assertEqual(ctx["context_report"]["chapter_text"]["chars"], len(block))
        self.assertTrue(ctx["context_report"]["chapter_text"]["truncated"])


if __name__ == "__main__":
    unittest.main()
