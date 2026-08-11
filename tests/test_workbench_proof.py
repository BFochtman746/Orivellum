"""Tests for the six-gate workbook proving bridge (workbench_proof).

Covers:
- prove_workbook: an LLM-style workbook (formulas, no cached values) is
  repaired, gated, and atomically promoted — data_only reads show real numbers
- prove_workbook failure: a genuinely erroring formula fails the gates and the
  original file stays byte-for-byte untouched
- promote=False (imports): gates run, verdict recorded, file stays verbatim
- unavailable harness degrades to 'unverified', never 'proven'
- _verify_output(prove=True): proof result lands in checks; failed proof
  rejects the version
- archive gate: an unproven / failed latest version refuses to archive unless
  allow_unproven=True; the complete route maps that to 409 code=unproven
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from tests.conftest import AUTH_HEADERS


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _good_workbook(path: Path) -> None:
    """Formulas with NO cached values — how openpyxl-built files come out."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = 10
    ws["A2"] = 32
    ws["A3"] = "=SUM(A1:A2)"
    ws["B1"] = "=A1*2"
    wb.save(path)


def _error_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws["A1"] = 0
    ws["A2"] = "=1/A1"  # genuine #DIV/0! — never cache-repairable
    wb.save(path)


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


class TestProveWorkbook(unittest.TestCase):
    def test_good_workbook_is_proven_and_promoted(self):
        from orivellum.capabilities.workbench_proof import GATE_NAMES, prove_workbook

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.xlsx"
            _good_workbook(p)
            before = _sha(p)

            res = prove_workbook(p)
            self.assertEqual(res["verdict"], "proven", res)
            for g in GATE_NAMES:
                self.assertTrue(res["gates"][g], g)
            # promotion happened: repaired file differs, caches are real values
            self.assertNotEqual(_sha(p), before)
            wb = load_workbook(p, data_only=True)
            self.assertEqual(wb["Data"]["A3"].value, 42)
            self.assertEqual(wb["Data"]["B1"].value, 20)
            wb.close()
            self.assertGreaterEqual(res["repairs"]["refreshed_cells"], 2)
            # no candidate debris
            self.assertEqual(list(Path(tmp).glob(".candidate_*")), [])

    def test_error_formula_fails_proof_and_original_untouched(self):
        from orivellum.capabilities.workbench_proof import prove_workbook

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "broken.xlsx"
            _error_workbook(p)
            before = _sha(p)

            res = prove_workbook(p)
            self.assertEqual(res["verdict"], "failed")
            self.assertIn("G2_values_match", res["failed_gates"])
            self.assertTrue(res["problems"])
            self.assertEqual(_sha(p), before)  # never mutated on failure
            self.assertEqual(list(Path(tmp).glob(".candidate_*")), [])

    def test_promote_false_keeps_file_verbatim_and_never_lies(self):
        from orivellum.capabilities.workbench_proof import prove_workbook

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.xlsx"
            _good_workbook(p)
            before = _sha(p)
            # gates pass only after cache repairs the file never received →
            # 'provable', NOT 'proven' (the verbatim bytes were never gated)
            res = prove_workbook(p, promote=False)
            self.assertEqual(res["verdict"], "provable")
            self.assertEqual(_sha(p), before)
            self.assertTrue(any("verbatim" in msg for msg in res["problems"]))

            # once actually promoted, a promote=False re-run needs no repairs
            # → the file itself is proven
            self.assertEqual(prove_workbook(p)["verdict"], "proven")
            after_promotion = _sha(p)
            res2 = prove_workbook(p, promote=False)
            self.assertEqual(res2["verdict"], "proven")
            self.assertEqual(_sha(p), after_promotion)

    def test_missing_harness_is_unverified_never_proven(self):
        from orivellum.capabilities import workbench_proof

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "book.xlsx"
            _good_workbook(p)
            with patch.object(workbench_proof, "_load_runner_modules", return_value=(None, None)):
                res = workbench_proof.prove_workbook(p)
            self.assertEqual(res["verdict"], "unverified")
            self.assertIn("unavailable", res["error"])


class TestVerifyOutputProof(unittest.TestCase):
    def test_verify_records_proof_and_accepts(self):
        from orivellum.capabilities.workbench import _verify_output

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _good_workbook(out / "model.xlsx")
            ok, checks = _verify_output("xlsx", out, prove=True)
            self.assertTrue(ok, checks)
            self.assertEqual(checks["proof"]["verdict"], "proven")
            self.assertIn("model.xlsx", checks["proof"]["workbooks"])

    def test_verify_rejects_failed_proof(self):
        from orivellum.capabilities.workbench import _verify_output

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _error_workbook(out / "broken.xlsx")
            ok, checks = _verify_output("xlsx", out, prove=True)
            self.assertFalse(ok)
            self.assertTrue(any("failed proof" in p for p in checks["problems"]))

    def test_verify_without_prove_keeps_old_behavior(self):
        from orivellum.capabilities.workbench import _verify_output

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _error_workbook(out / "broken.xlsx")
            ok, checks = _verify_output("xlsx", out)
            self.assertTrue(ok)  # loads fine — old contract for imports
            self.assertNotIn("proof", checks)


def _publish_xlsx_version(db, cfg, project_id: str, checks: dict, good: bool = True):
    from orivellum.capabilities.workbench import _publish_version, _snapshot

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "out"
        src.mkdir()
        (_good_workbook if good else _error_workbook)(src / "model.xlsx")
        files = _snapshot(src)
        return _publish_version(db, cfg, project_id, src, "test", files, checks)


class TestArchiveProofGate(unittest.TestCase):
    def test_archive_refuses_unproven_latest_unless_forced(self):
        from orivellum.capabilities.workbench import UnprovenError, archive_project

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")

            # no proof recorded at all → unproven
            _publish_xlsx_version(db, cfg, p["id"], checks={"workbooks": 1})
            with self.assertRaises(UnprovenError):
                archive_project(db, cfg, p["id"])
            self.assertEqual(db.get_wb_project(p["id"])["status"], "active")

            path = archive_project(db, cfg, p["id"], allow_unproven=True)
            self.assertTrue(Path(path).is_file())
            self.assertEqual(db.get_wb_project(p["id"])["status"], "archived")

    def test_archive_refuses_failed_proof(self):
        from orivellum.capabilities.workbench import UnprovenError, archive_project

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")
            _publish_xlsx_version(
                db,
                cfg,
                p["id"],
                checks={
                    "proof": {
                        "verdict": "failed",
                        "workbooks": {
                            "model.xlsx": {"verdict": "failed", "problems": ["bad cell"]}
                        },
                    }
                },
                good=False,
            )
            with self.assertRaises(UnprovenError) as ctx:
                archive_project(db, cfg, p["id"])
            self.assertIn("FAILED", str(ctx.exception))

    def test_archive_allows_proven_latest(self):
        from orivellum.capabilities.workbench import archive_project

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")
            _publish_xlsx_version(
                db,
                cfg,
                p["id"],
                checks={"proof": {"verdict": "proven", "workbooks": {}}},
            )
            path = archive_project(db, cfg, p["id"])
            self.assertTrue(Path(path).is_file())

    def test_archive_refuses_provable_import(self):
        """An import that would pass only after repairs is NOT archivable as
        proven — the verbatim bytes were never certified."""
        from orivellum.capabilities.workbench import UnprovenError, archive_project

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")
            _publish_xlsx_version(
                db,
                cfg,
                p["id"],
                checks={"proof": {"verdict": "provable", "workbooks": {}}},
            )
            with self.assertRaises(UnprovenError) as ctx:
                archive_project(db, cfg, p["id"])
            self.assertIn("verbatim", str(ctx.exception))

    def test_proof_inherited_when_workbook_bytes_unchanged(self):
        """Analysis / revert versions copy the workbook forward without
        re-gating; identical bytes must carry the earlier proof — and
        different bytes must NOT."""
        from orivellum.capabilities.workbench import (
            _publish_version,
            _snapshot,
            archive_project,
            latest_proof_status,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Budget", "xlsx", "b")

            with tempfile.TemporaryDirectory() as tmp2:
                src = Path(tmp2) / "out"
                src.mkdir()
                _good_workbook(src / "model.xlsx")
                v1_files = _snapshot(src)
                _publish_version(
                    db,
                    cfg,
                    p["id"],
                    src,
                    "build",
                    v1_files,
                    {"proof": {"verdict": "proven", "workbooks": {}}},
                )
                # analysis-style version: same workbook bytes + a report
                (src / "ANALYSIS_REPORT.md").write_text("# findings\n")
                _publish_version(
                    db, cfg, p["id"], src, "analyze", _snapshot(src), {"analysis": True}
                )

            proj = db.get_wb_project(p["id"])
            versions = db.list_wb_versions(p["id"])
            self.assertEqual(latest_proof_status(proj, versions)[0], "proven")
            self.assertTrue(Path(archive_project(db, cfg, p["id"])).is_file())

    def test_proof_not_inherited_when_bytes_differ(self):
        from orivellum.capabilities.workbench import latest_proof_status

        proj = {"kind": "xlsx"}
        versions = [
            {
                "version_no": 1,
                "files_json": json.dumps([{"name": "m.xlsx", "sha256": "a" * 64}]),
                "checks_json": json.dumps({"proof": {"verdict": "proven", "workbooks": {}}}),
            },
            {
                "version_no": 2,
                "files_json": json.dumps([{"name": "m.xlsx", "sha256": "b" * 64}]),
                "checks_json": json.dumps({}),
            },
        ]
        self.assertEqual(latest_proof_status(proj, versions)[0], "unproven")

    def test_non_xlsx_projects_unaffected(self):
        from orivellum.capabilities.workbench import archive_project, project_dir

        with tempfile.TemporaryDirectory() as tmp:
            _, db, cfg = _make_app(tmp)
            p = db.create_wb_project("Tool", "code", "t")
            with tempfile.TemporaryDirectory() as tmp2:
                src = Path(tmp2) / "out"
                src.mkdir()
                (src / "main.py").write_text("x = 1\n")
                from orivellum.capabilities.workbench import _publish_version, _snapshot

                _publish_version(db, cfg, p["id"], src, "t", _snapshot(src), {})
            path = archive_project(db, cfg, p["id"])
            self.assertTrue(Path(path).is_file())
            self.assertTrue(project_dir(cfg, p["id"]).exists())


class TestCompleteRouteProofGate(unittest.TestCase):
    def test_complete_409_unproven_then_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db, cfg = _make_app(tmp)
            client = TestClient(app)
            p = db.create_wb_project("Budget", "xlsx", "b")
            _publish_xlsx_version(db, cfg, p["id"], checks={"workbooks": 1})

            r = client.post(f"/api/workbench/projects/{p['id']}/complete", headers=AUTH_HEADERS)
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(r.json()["detail"]["code"], "unproven")
            self.assertEqual(db.get_wb_project(p["id"])["status"], "active")

            r = client.post(
                f"/api/workbench/projects/{p['id']}/complete",
                headers=AUTH_HEADERS,
                json={"force": True},
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["archived"])
            self.assertEqual(db.get_wb_project(p["id"])["status"], "archived")


class TestLatestProofStatus(unittest.TestCase):
    def test_status_mapping(self):
        from orivellum.capabilities.workbench import latest_proof_status

        proj = {"kind": "xlsx"}
        self.assertEqual(latest_proof_status(proj, [])[0], "n/a")
        self.assertEqual(latest_proof_status({"kind": "code"}, [{}])[0], "n/a")

        def v(checks):
            return [{"version_no": 1, "checks_json": json.dumps(checks)}]

        self.assertEqual(latest_proof_status(proj, v({}))[0], "unproven")
        self.assertEqual(
            latest_proof_status(proj, v({"proof": {"verdict": "proven", "workbooks": {}}}))[0],
            "proven",
        )
        status, detail = latest_proof_status(
            proj,
            v(
                {
                    "proof": {
                        "verdict": "failed",
                        "workbooks": {"a.xlsx": {"verdict": "failed", "problems": ["boom"]}},
                    }
                }
            ),
        )
        self.assertEqual(status, "failed")
        self.assertIn("boom", detail)


if __name__ == "__main__":
    unittest.main()
