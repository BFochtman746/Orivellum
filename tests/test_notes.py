"""Commonplace notes — capture, classification policy, filing, and review.

Covers:
- _normalize_proposal enforces the server-owned category policy: unknown
  categories → unsorted + warning; unstated actions dropped; bad values clamped.
- file_block is append-only and idempotent (re-filing never duplicates).
- build_daily_report mechanical fallback when the LLM is unreachable, and
  derivation from approved/filed blocks only.
- API: capture → list, delete only from inbox, process with empty inbox.
- Review resolver: approve files the block + creates knowledge; reject claims;
  double-resolve is a 409.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.capabilities.notes import (
    _normalize_proposal,
    build_daily_report,
    file_block,
    today_str,
)
from orivellum.database.db import OrivellumDB
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


_GOOD_PROPOSAL = {
    "title": "Fix the gutter",
    "summary": "The gutter over the porch is loose.",
    "categories": ["home"],
    "kind": "action",
    "actions": [{"text": "Call the gutter guy", "due": "", "stated": True}],
    "open_questions": [],
    "confidence": 0.9,
    "warnings": [],
}


class TestNormalizeProposal(unittest.TestCase):
    def test_unknown_category_replaced_with_unsorted_and_warned(self):
        out = _normalize_proposal({"categories": ["garage-stuff"]}, "text")
        self.assertEqual(out["categories"], ["unsorted"])
        self.assertTrue(any("garage-stuff" in w for w in out["warnings"]))

    def test_no_categories_falls_back_to_unsorted(self):
        out = _normalize_proposal({}, "some text")
        self.assertEqual(out["categories"], ["unsorted"])

    def test_valid_categories_kept_deduped_capped_at_five(self):
        cats = ["home", "home", "work", "health", "faith", "finance", "ideas"]
        out = _normalize_proposal({"categories": cats}, "t")
        self.assertLessEqual(len(out["categories"]), 5)
        self.assertEqual(len(out["categories"]), len(set(out["categories"])))

    def test_unstated_actions_are_dropped(self):
        raw = {
            "actions": [
                {"text": "fix the explicit thing", "stated": True},
                {"text": "fix the explicit thing", "stated": False},
                {"text": "fix the explicit thing"},
            ]
        }
        out = _normalize_proposal(raw, "I must fix the explicit thing today")
        self.assertEqual([a["text"] for a in out["actions"]], ["fix the explicit thing"])

    def test_bad_kind_and_confidence_clamped(self):
        out = _normalize_proposal({"kind": "prophecy", "confidence": 7}, "t")
        self.assertEqual(out["kind"], "note")
        self.assertEqual(out["confidence"], 1.0)

    def test_hallucinated_action_dropped(self):
        """An action whose words are not grounded in the note never survives."""
        raw = {"actions": [{"text": "Book a flight to Paris", "stated": True}]}
        out = _normalize_proposal(raw, "Remember to water the plants")
        self.assertEqual(out["actions"], [])
        self.assertTrue(any("not grounded" in w for w in out["warnings"]))

    def test_grounded_action_kept(self):
        raw = {"actions": [{"text": "water the plants", "stated": True}]}
        out = _normalize_proposal(raw, "Remember to water the plants tomorrow")
        self.assertEqual(len(out["actions"]), 1)

    def test_unstated_due_date_dropped(self):
        raw = {"actions": [{"text": "water the plants", "due": "2026-09-15", "stated": True}]}
        out = _normalize_proposal(raw, "Remember to water the plants")
        self.assertEqual(out["actions"][0]["due"], "")

    def test_missing_title_derived_from_text(self):
        out = _normalize_proposal({}, "First line here\nsecond line")
        self.assertEqual(out["title"], "First line here")


class TestFilingIdempotent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _, self.db, self.cfg = _make_app(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_file_block_writes_daily_and_index_once(self):
        block = self.db.create_note_block("Call the gutter guy", today_str(), source="web")
        paths1 = file_block(self.db, self.cfg, block, _GOOD_PROPOSAL)
        paths2 = file_block(self.db, self.cfg, block, _GOOD_PROPOSAL)  # re-run
        self.assertEqual(paths1, paths2)

        daily = Path(self.cfg.data_dir) / "vault" / paths1[0]
        index = Path(self.cfg.data_dir) / "vault" / "Journal" / "_indexes" / "Home.md"
        self.assertTrue(daily.exists())
        self.assertTrue(index.exists())
        # Idempotent: the block marker appears exactly once in each file
        marker = f"block:{block['id']}"
        self.assertEqual(daily.read_text().count(marker), 1)
        self.assertEqual(index.read_text().count(marker), 1)
        # Content made it into the daily entry
        self.assertIn("Call the gutter guy", daily.read_text())


class TestApprovalRecovery(unittest.TestCase):
    """A crash between claim and filing must never strand a note."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _, self.db, self.cfg = _make_app(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_resume_approved_completes_interrupted_filing(self):
        from orivellum.capabilities.notes import resume_approved

        block = self.db.create_note_block("Call the gutter guy", today_str(), source="web")
        self.db.set_note_block_proposal(block["id"], _GOOD_PROPOSAL)
        # Simulate: approved in review, then crash before filing
        self.assertTrue(self.db.claim_note_block(block["id"], "approved", expected="proposed"))

        done = resume_approved(self.db, self.cfg)
        self.assertEqual(done, 1)
        after = self.db.get_note_block(block["id"])
        self.assertEqual(after["status"], "filed")
        daily = Path(self.cfg.data_dir) / "vault" / "Journal" / "Daily" / f"{block['day']}.md"
        self.assertTrue(daily.exists())

    def test_complete_approval_is_replay_safe(self):
        """Running completion twice never duplicates vault entries, tasks,
        or knowledge."""
        from orivellum.capabilities.notes import complete_approval

        block = self.db.create_note_block("Call the gutter guy", today_str(), source="web")
        self.db.set_note_block_proposal(block["id"], _GOOD_PROPOSAL)
        self.db.claim_note_block(block["id"], "approved", expected="proposed")
        blk = self.db.get_note_block(block["id"])

        complete_approval(self.db, self.cfg, blk)
        complete_approval(self.db, self.cfg, blk)  # replay

        daily = Path(self.cfg.data_dir) / "vault" / "Journal" / "Daily" / f"{blk['day']}.md"
        self.assertEqual(daily.read_text().count(f"block:{blk['id']}"), 1)
        with self.db._lock:
            n_tasks = self.db._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE work_id IS NULL AND text LIKE 'Call the gutter%'"
            ).fetchone()[0]
            n_knowledge = self.db._conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE json_extract(meta,'$.block_id')=?",
                (blk["id"],),
            ).fetchone()[0]
        self.assertEqual(n_tasks, 1)
        self.assertEqual(n_knowledge, 1)


class TestDailyReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _, self.db, self.cfg = _make_app(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_only_uses_approved_blocks_mechanical_fallback(self):
        day = today_str()
        approved = self.db.create_note_block("Approved thing", today_str(), source="web")
        self.db.set_note_block_proposal(approved["id"], _GOOD_PROPOSAL)
        self.db.claim_note_block(approved["id"], "approved", expected="proposed")
        self.db.create_note_block("Still in inbox", today_str(), source="web")

        # LLM unreachable in tests → mechanical narrative
        out = build_daily_report(self.db, self.cfg, day)
        self.assertEqual(out["block_count"], 1)
        self.assertIn("1 approved note(s)", out["report"])
        self.assertNotIn("Still in inbox", out["report"])
        # Stored + written to the vault
        self.assertIsNotNone(self.db.get_note_report(day))
        vault_report = Path(self.cfg.data_dir) / "vault" / "Reports" / f"{day}-daily-report.md"
        self.assertTrue(vault_report.exists())

    def test_empty_day_reports_nothing_approved(self):
        out = build_daily_report(self.db, self.cfg, "2001-01-01")
        self.assertIn("Nothing was approved", out["report"])


class TestNotesApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(app, headers=AUTH_HEADERS)

    def tearDown(self):
        self._tmp.cleanup()

    def test_capture_then_list(self):
        r = self.client.post("/api/notes", json={"text": "Buy milk"})
        self.assertEqual(r.status_code, 200, r.text)
        day = r.json()["day"]
        r2 = self.client.get(f"/api/notes?day={day}")
        self.assertEqual(r2.status_code, 200)
        body = r2.json()
        self.assertEqual(body["counts"].get("inbox"), 1)
        self.assertEqual(body["blocks"][0]["text"], "Buy milk")

    def test_blank_capture_rejected(self):
        r = self.client.post("/api/notes", json={"text": "   "})
        self.assertIn(r.status_code, (400, 422))

    def test_delete_only_from_inbox(self):
        block = self.db.create_note_block("temp", today_str(), source="web")
        r = self.client.delete(f"/api/notes/{block['id']}")
        self.assertEqual(r.status_code, 200)
        # Proposed blocks can no longer be deleted
        b2 = self.db.create_note_block("keep", today_str(), source="web")
        self.db.set_note_block_proposal(b2["id"], _GOOD_PROPOSAL)
        r2 = self.client.delete(f"/api/notes/{b2['id']}")
        self.assertEqual(r2.status_code, 409)

    def test_process_with_empty_inbox_reports_not_started(self):
        r = self.client.post("/api/notes/process")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["started"])


class TestNoteblockReview(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(app, headers=AUTH_HEADERS)

    def tearDown(self):
        self._tmp.cleanup()

    def _proposed_block(self) -> dict:
        block = self.db.create_note_block("Call the gutter guy", today_str(), source="web")
        self.assertTrue(self.db.set_note_block_proposal(block["id"], _GOOD_PROPOSAL))
        return self.db.get_note_block(block["id"])

    def test_proposed_block_appears_in_queue(self):
        block = self._proposed_block()
        r = self.client.get("/api/review/queue")
        self.assertEqual(r.status_code, 200)
        ids = [i["id"] for i in r.json()["items"]]
        self.assertIn(f"noteblock:{block['id']}", ids)

    def test_approve_files_block_and_creates_knowledge(self):
        block = self._proposed_block()
        r = self.client.post(
            f"/api/review/noteblock:{block['id']}/resolve", json={"decision": "approve"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertTrue(out["filed_paths"])
        self.assertEqual(out["tasks_created"], 1)

        after = self.db.get_note_block(block["id"])
        self.assertEqual(after["status"], "filed")
        daily = Path(self.cfg.data_dir) / "vault" / "Journal" / "Daily" / f"{block['day']}.md"
        self.assertTrue(daily.exists())
        self.assertIn("Call the gutter guy", daily.read_text())
        # Knowledge item created with the notes source marker
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT meta FROM knowledge WHERE json_extract(meta,'$.source')='commonplace'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_reject_claims_block(self):
        block = self._proposed_block()
        r = self.client.post(
            f"/api/review/noteblock:{block['id']}/resolve", json={"decision": "reject"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.db.get_note_block(block["id"])["status"], "rejected")

    def test_double_resolve_is_conflict(self):
        block = self._proposed_block()
        first = self.client.post(
            f"/api/review/noteblock:{block['id']}/resolve", json={"decision": "reject"}
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            f"/api/review/noteblock:{block['id']}/resolve", json={"decision": "approve"}
        )
        self.assertEqual(second.status_code, 409)

    def test_unknown_noteblock_404(self):
        r = self.client.post(
            f"/api/review/noteblock:{uuid.uuid4()}/resolve", json={"decision": "approve"}
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
