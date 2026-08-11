"""AUTONOMY runner tests (Masterpiece Pipeline M12).

Proves by assertion:
- the acceptance fixture: an unattended run drafts chapters under a budget,
  runs the check battery, and finishes with a clean queue and a full report;
- the injected-error fixture: a run that draws an open critical finding it
  cannot revise away HALTS cleanly — run row 'halted', ONE autonomy_halt
  suggestion queued with full context, no leaked 'running' rows anywhere;
- bounded revision: a finding fixed by the surgical edit lets the run
  continue; the revision attempt is recorded in the report;
- budgets: the chapter cap stops the run between chapters;
- kill switch: flipping autonomy_enabled off mid-run stops the run cleanly;
- signatures stay human: when drafting is complete but a signature gate is
  unsigned, the run halts and queues — and NEVER writes an assay_signature;
- the run row is the claim: double-dispatch refused; a crashed run finishes
  its row as 'error';
- halt_policy 'continue' queues per chapter and moves on instead of stopping.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities import autonomy
from orivellum.database.db import OrivellumDB, _now


def _cfg():
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid", workhorse_model="drafter", reasoner_model="judge"
        )
    )


def _clean_battery():
    return {
        "instruments": [
            {"key": "gate.d13", "status": "done", "verdict": "clean", "findings_count": 0}
        ],
        "constory": {"status": "done"},
        "ced": {},
    }


class AutonomyBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Unattended Book", work_type="writing")["id"]
        self.db.set_setting("autonomy_enabled", "true")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed_chapter(self, seq, text=None, status="draft"):
        oid = self.db._create_object("book_chapter")
        meta = {
            "contract": {
                "beat": f"Beat {seq}",
                "act": 1,
                "cast": [],
                "location": "yard",
                "word_range": [50, 400],
            }
        }
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
                   source_doc_id, status, meta, created_at, updated_at)
                   VALUES(?,?,?,1,?,?,NULL,?,?,?,?)""",
                (
                    oid,
                    self.work_id,
                    seq,
                    f"Chapter {seq}",
                    text,
                    status,
                    json.dumps(meta),
                    _now(),
                    _now(),
                ),
            )
            self.db._conn.commit()
        return oid

    def _seed_finding(self, chapter_id, seq, severity="critical"):
        fid = str(uuid.uuid4())
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO narrative_finding(id, work_id, chapter_id, category,
                   subtype, fact_quote, fact_chapter, fact_offset,
                   contradiction_quote, contradiction_chapter, contradiction_offset,
                   reasoning, severity, dedupe_key, created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid,
                    self.work_id,
                    chapter_id,
                    "timeline_plot",
                    "date_conflict",
                    "the war ended in spring",
                    1,
                    0,
                    "the war ended in autumn",
                    seq,
                    10,
                    "spring vs autumn contradiction",
                    severity,
                    f"dedupe-{fid}",
                    _now(),
                ),
            )
            self.db._conn.commit()
        return fid

    def _fix_finding(self, fid):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE narrative_finding SET disposition='fixed' WHERE id=?", (fid,)
            )
            self.db._conn.commit()

    def _fake_draft(self, side_effect=None, escalate_seqs=()):
        db = self.db

        def fake(db_, cfg, *, run_id, work_id, chapter_id):
            with db._lock:
                seq = db._conn.execute(
                    "SELECT seq FROM book_chapters WHERE id=?", (chapter_id,)
                ).fetchone()["seq"]
            if seq in escalate_seqs:
                db.finish_loom_run(run_id, status="escalated", error="beat stall")
                return {"status": "escalated", "reason": "beat stall", "evidence": {}}
            with db._lock:
                db._conn.execute(
                    "UPDATE book_chapters SET text=?, updated_at=? WHERE id=?",
                    (f"Drafted prose for chapter {seq}. " * 20, _now(), chapter_id),
                )
                db._conn.commit()
            db.finish_loom_run(run_id, status="done", evidence={})
            if side_effect:
                side_effect(seq, chapter_id)
            return {"status": "done", "evidence": {}}

        return fake

    def _run(self, budget=None, draft=None, battery=None, constory=None):
        run_id = self.db.create_autonomy_run(
            self.work_id, {**autonomy.DEFAULT_BUDGET, **(budget or {})}
        )
        with (
            patch.object(autonomy, "run_loom_draft", draft or self._fake_draft()),
            patch.object(
                autonomy, "run_battery", battery or (lambda db, cfg, wid: _clean_battery())
            ),
            patch.object(
                autonomy,
                "run_constory_check",
                constory or (lambda db, cfg, *, work_id: {"chapters": 0}),
            ),
        ):
            result = autonomy.run_autonomy(self.db, _cfg(), run_id=run_id, work_id=self.work_id)
        return run_id, result

    def _queued_halts(self):
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM suggestions WHERE kind='autonomy_halt'"
            ).fetchall()
        return [dict(r) for r in rows]

    def _no_running_rows(self):
        with self.db._lock:
            auto = self.db._conn.execute(
                "SELECT COUNT(*) c FROM autonomy_run WHERE status='running'"
            ).fetchone()["c"]
            loom = self.db._conn.execute(
                "SELECT COUNT(*) c FROM loom_run WHERE status='running'"
            ).fetchone()["c"]
        self.assertEqual((auto, loom), (0, 0), "leaked running row")


class TestCleanRun(AutonomyBase):
    def test_unattended_run_drafts_chapters_with_clean_queue(self):
        self.db.latest_assay_signature = lambda *a: {"decision": "go"}  # type: ignore[method-assign]
        for seq in (1, 2):
            self._seed_chapter(seq)
        run_id, result = self._run(budget={"max_chapters": 2})
        self.assertEqual(result["status"], "done")
        drafted = [c for c in result["report"]["chapters"] if c["drafted"]]
        self.assertEqual(len(drafted), 2)
        self.assertEqual(self._queued_halts(), [])
        run = self.db.get_autonomy_run(run_id)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["consumed"]["chapters"], 2)
        self.assertTrue(run["report"]["chapters"])  # full report persisted
        self._no_running_rows()

    def test_budget_chapter_cap_stops_between_chapters(self):
        for seq in (1, 2, 3):
            self._seed_chapter(seq)
        _run_id, result = self._run(budget={"max_chapters": 1})
        self.assertEqual(result["status"], "done")
        self.assertIn("chapter cap", result["stop_reason"])
        self.assertEqual(result["consumed"]["chapters"], 1)

    def test_kill_switch_stops_run_cleanly_mid_flight(self):
        for seq in (1, 2):
            self._seed_chapter(seq)
        draft = self._fake_draft(
            side_effect=lambda seq, cid: self.db.set_setting("autonomy_enabled", "false")
        )
        run_id, result = self._run(budget={"max_chapters": 5}, draft=draft)
        self.assertEqual(result["status"], "stopped")
        self.assertIn("kill switch", result["stop_reason"])
        self.assertEqual(result["consumed"]["chapters"], 1)
        self.assertEqual(self.db.get_autonomy_run(run_id)["status"], "stopped")
        self._no_running_rows()


class TestInjectedErrorHalts(AutonomyBase):
    def test_injected_finding_halts_cleanly_and_queues(self):
        cid = self._seed_chapter(1)
        fid_holder = {}

        def battery(db, cfg, wid):
            fid_holder["fid"] = self._seed_finding(cid, 1)
            return _clean_battery()

        # Revision is attempted but the finding survives the re-check.
        with patch(
            "orivellum.capabilities.band.surgical_edit",
            lambda *a, **k: {"committed": False, "reasons": ["regression"]},
        ):
            run_id, result = self._run(battery=battery)

        self.assertEqual(result["status"], "halted")
        halts = self._queued_halts()
        self.assertEqual(len(halts), 1)
        meta = json.loads(halts[0]["meta"])
        self.assertEqual(meta["run_id"], run_id)
        self.assertEqual(meta["chapter_seq"], 1)
        self.assertIn(fid_holder["fid"], meta["finding_ids"])
        self.assertTrue(meta["reasons"])
        chapter_entry = result["report"]["chapters"][0]
        self.assertTrue(chapter_entry["halted"])
        self.assertEqual(len(chapter_entry["revisions"]), 1)
        self.assertFalse(chapter_entry["revisions"][0]["committed"])
        self._no_running_rows()

    def test_errored_constory_check_never_counts_as_clean(self):
        self._seed_chapter(1)

        def battery(db, cfg, wid):
            return {
                "instruments": [],
                "constory": {"status": "error", "error": "gateway down"},
                "ced": {},
            }

        _run_id, result = self._run(battery=battery)
        self.assertEqual(result["status"], "halted")
        self.assertIn(
            "continuity check failed to run", result["report"]["chapters"][0]["reasons"][0]
        )
        self.assertEqual(len(self._queued_halts()), 1)

    def test_draft_escalation_halts_and_queues(self):
        self._seed_chapter(1)
        draft = self._fake_draft(escalate_seqs=(1,))
        _run_id, result = self._run(draft=draft)
        self.assertEqual(result["status"], "halted")
        self.assertIn("escalated", result["stop_reason"])
        self.assertEqual(len(self._queued_halts()), 1)
        self._no_running_rows()

    def test_halt_policy_continue_queues_and_moves_on(self):
        for seq in (1, 2):
            self._seed_chapter(seq)
        self.db.latest_assay_signature = lambda *a: {"decision": "go"}  # type: ignore[method-assign]
        draft = self._fake_draft(escalate_seqs=(1,))
        _run_id, result = self._run(
            budget={"max_chapters": 2, "halt_policy": "continue"}, draft=draft
        )
        self.assertEqual(result["status"], "done")
        self.assertEqual(len(self._queued_halts()), 1)
        seq2 = [c for c in result["report"]["chapters"] if c["seq"] == 2]
        self.assertTrue(seq2 and seq2[0]["drafted"] and not seq2[0]["halted"])


class TestBoundedRevision(AutonomyBase):
    def test_finding_fixed_by_surgical_edit_lets_run_continue(self):
        cid = self._seed_chapter(1)
        state = {}

        def battery(db, cfg, wid):
            state["fid"] = self._seed_finding(cid, 1)
            return _clean_battery()

        def edit(db, cfg, **kwargs):
            self.assertEqual(kwargs.get("author"), "")  # never signed
            self.assertFalse(kwargs.get("accept_regression"))
            return {"committed": True}

        def recheck(db, cfg, *, work_id):
            self._fix_finding(state["fid"])
            return {"chapters": 1}

        self.db.latest_assay_signature = lambda *a: {"decision": "go"}  # type: ignore[method-assign]
        with patch("orivellum.capabilities.band.surgical_edit", edit):
            _run_id, result = self._run(battery=battery, constory=recheck)
        self.assertEqual(result["status"], "done")
        entry = result["report"]["chapters"][0]
        self.assertFalse(entry["halted"])
        self.assertEqual(len(entry["revisions"]), 1)
        self.assertTrue(entry["revisions"][0]["committed"])
        self.assertEqual(self._queued_halts(), [])


class TestSignaturesStayHuman(AutonomyBase):
    def test_unsigned_gate_halts_queues_and_never_signs(self):
        # Everything already drafted: the runner reaches the signature check.
        self._seed_chapter(1, text="Already drafted prose. " * 30)
        _run_id, result = self._run()
        self.assertEqual(result["status"], "halted")
        self.assertIn("signature_required", result["stop_reason"])
        halts = self._queued_halts()
        self.assertEqual(len(halts), 1)
        self.assertIn("signature", halts[0]["text"])
        # The runner must NEVER write a signature.
        with self.db._lock:
            sigs = self.db._conn.execute("SELECT COUNT(*) c FROM assay_signature").fetchone()["c"]
        self.assertEqual(sigs, 0)

    def test_signed_gates_complete_cleanly(self):
        self._seed_chapter(1, text="Already drafted prose. " * 30)
        for key in autonomy.SIGNATURE_GATES:
            with self.db._lock:
                self.db._conn.execute(
                    """INSERT INTO assay_signature(id, work_id, gate_key, author,
                       decision, signed_at) VALUES(?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), self.work_id, key, "Brian", "go", _now()),
                )
                self.db._conn.commit()
        _run_id, result = self._run()
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["stop_reason"], "no chapters need drafting")
        self.assertEqual(self._queued_halts(), [])


class TestRunClaim(AutonomyBase):
    def test_concurrent_run_refused(self):
        self.db.create_autonomy_run(self.work_id)
        with self.assertRaises(RuntimeError):
            self.db.create_autonomy_run(self.work_id)

    def test_crashed_run_finishes_row_as_error(self):
        self._seed_chapter(1)

        def boom(db, cfg, wid):
            raise RuntimeError("battery exploded")

        run_id = self.db.create_autonomy_run(self.work_id, autonomy.DEFAULT_BUDGET)
        with (
            patch.object(autonomy, "run_loom_draft", self._fake_draft()),
            patch.object(autonomy, "run_battery", boom),
            self.assertRaises(RuntimeError),
        ):
            autonomy.run_autonomy(self.db, _cfg(), run_id=run_id, work_id=self.work_id)
        run = self.db.get_autonomy_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("battery exploded", run["stop_reason"])
        self._no_running_rows()

    def test_recover_orphaned_runs_releases_claims(self):
        self.db.create_autonomy_run(self.work_id)
        self.assertEqual(self.db.recover_orphaned_autonomy_runs(), 1)
        self._no_running_rows()
        # And a new run can claim again.
        self.db.create_autonomy_run(self.work_id)


class TestNightshiftPass(AutonomyBase):
    def test_disabled_settings_skip_the_pass(self):
        self.db.set_setting("autonomy_enabled", "false")
        report: list[str] = []
        autonomy.run_nightshift_pass(self.db, _cfg(), report)
        self.assertIn("disabled", report[0])
        self.db.set_setting("autonomy_enabled", "true")
        report = []
        autonomy.run_nightshift_pass(self.db, _cfg(), report)
        self.assertIn("nightly runs off", report[0])

    def test_optin_work_runs_under_settings_budget(self):
        self.db.set_setting("autonomy_nightshift_enabled", "true")
        work = self.db.get_work(self.work_id)
        meta = work["meta"] if isinstance(work["meta"], dict) else json.loads(work["meta"] or "{}")
        meta["autonomy_optin"] = True
        self.db.update_work(self.work_id, meta=meta)
        self._seed_chapter(1)
        self.db.latest_assay_signature = lambda *a: {"decision": "go"}  # type: ignore[method-assign]
        report: list[str] = []
        with (
            patch.object(autonomy, "run_loom_draft", self._fake_draft()),
            patch.object(autonomy, "run_battery", lambda db, cfg, wid: _clean_battery()),
        ):
            autonomy.run_nightshift_pass(self.db, _cfg(), report)
        self.assertTrue(any("Unattended Book" in line for line in report))
        runs = self.db.list_autonomy_runs(self.work_id)
        self.assertEqual(len(runs), 1)
        self.assertIn(runs[0]["status"], ("done", "halted"))
        self._no_running_rows()


if __name__ == "__main__":
    unittest.main()
