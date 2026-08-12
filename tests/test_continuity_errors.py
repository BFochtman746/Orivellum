"""Continuity-error surfacing: list + disposition routes for graph_inconsistency.

Contract under test:
- GET /api/works/{id}/inconsistencies lists a Work's verified continuity
  errors enriched with chapter titles/seq for BOTH sides, plus per-status
  counts covering all dispositions regardless of the filter.
- PATCH /api/works/{id}/inconsistencies/{iid} sets the disposition; only
  the four legal statuses pass; the update is scoped to the Work so one
  Work can never re-disposition another Work's finding.
- Status changes remove items from the open list (and reopening restores).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
    return app, db


def _seed_chapter(db, work_id, seq, title):
    from orivellum.database.db import _now

    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?, 'chapter text', 'draft','{}',?,?)""",
            (oid, work_id, seq, title, _now(), _now()),
        )
        db._conn.commit()
    return oid


class ContinuityErrorRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)
        self.work_id = self.db.create_work(title="Novel", work_type="writing")["id"]
        self.ch1 = _seed_chapter(self.db, self.work_id, 1, "The Departure")
        self.ch2 = _seed_chapter(self.db, self.work_id, 2, "The Return")

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_inconsistency(self, **overrides):
        kwargs = dict(
            work_id=self.work_id,
            chapter_id=self.ch2,
            description="Eye color changes between chapters",
            current_quote="her green eyes flashed",
            current_offset=10,
            prior_chapter_id=self.ch1,
            prior_quote="his gaze met her brown eyes",
            prior_offset=5,
            reasoning="Same character, different eye color.",
        )
        kwargs.update(overrides)
        return self.db.create_graph_inconsistency(**kwargs)

    # ── listing ──────────────────────────────────────────────────────────

    def test_404_unknown_work(self):
        r = self.client.get("/api/works/nope/inconsistencies")
        self.assertEqual(r.status_code, 404)

    def test_empty_list(self):
        r = self.client.get(f"/api/works/{self.work_id}/inconsistencies")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["inconsistencies"], [])
        self.assertEqual(body["counts"], {})

    def test_list_enriched_with_chapter_refs(self):
        iid = self._seed_inconsistency()
        body = self.client.get(f"/api/works/{self.work_id}/inconsistencies").json()
        self.assertEqual(len(body["inconsistencies"]), 1)
        item = body["inconsistencies"][0]
        self.assertEqual(item["id"], iid)
        self.assertEqual(item["status"], "open")
        self.assertEqual(item["chapter_title"], "The Return")
        self.assertEqual(item["chapter_seq"], 2)
        self.assertEqual(item["prior_chapter_title"], "The Departure")
        self.assertEqual(item["prior_chapter_seq"], 1)
        self.assertEqual(item["current_quote"], "her green eyes flashed")
        self.assertEqual(item["prior_quote"], "his gaze met her brown eyes")
        self.assertEqual(body["counts"], {"open": 1})

    def test_status_filter_and_counts_cover_all_dispositions(self):
        a = self._seed_inconsistency()
        b = self._seed_inconsistency(
            description="Timeline error", current_quote="three days later"
        )
        self.db.update_graph_inconsistency_status(b, "fixed")
        body = self.client.get(
            f"/api/works/{self.work_id}/inconsistencies", params={"status": "open"}
        ).json()
        self.assertEqual([i["id"] for i in body["inconsistencies"]], [a])
        # counts always aggregate every disposition, not just the filtered one
        self.assertEqual(body["counts"], {"open": 1, "fixed": 1})

    def test_invalid_status_filter_rejected(self):
        r = self.client.get(
            f"/api/works/{self.work_id}/inconsistencies", params={"status": "bogus"}
        )
        self.assertEqual(r.status_code, 422)

    # ── disposition updates ──────────────────────────────────────────────

    def test_patch_sets_status_and_removes_from_open(self):
        iid = self._seed_inconsistency()
        r = self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/{iid}",
            json={"status": "intentional", "note": "The narrator is unreliable here."},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()["inconsistency"]
        self.assertEqual(body["status"], "intentional")
        self.assertEqual(body["disposition_by"], "author")
        self.assertEqual(body["disposition_note"], "The narrator is unreliable here.")
        self.assertTrue(body["disposition_at"])
        open_items = self.client.get(
            f"/api/works/{self.work_id}/inconsistencies", params={"status": "open"}
        ).json()["inconsistencies"]
        self.assertEqual(open_items, [])

    def test_intentional_requires_note(self):
        iid = self._seed_inconsistency()
        r = self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/{iid}",
            json={"status": "intentional"},
        )
        self.assertEqual(r.status_code, 422)
        row = self.db.list_graph_inconsistencies(work_id=self.work_id)[0]
        self.assertEqual(row["status"], "open")

    def test_disposition_is_audited(self):
        iid = self._seed_inconsistency()
        self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/{iid}", json={"status": "fixed"}
        )
        rows = (
            self.db.read_conn()
            .execute("SELECT * FROM audit_log WHERE operation='continuity.dispositioned'")
            .fetchall()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"], "author")

    def test_patch_reopen_clears_provenance(self):
        iid = self._seed_inconsistency()
        self.db.update_graph_inconsistency_status(iid, "wontfix")
        r = self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/{iid}", json={"status": "open"}
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()["inconsistency"]
        self.assertEqual(body["status"], "open")
        self.assertIsNone(body["disposition_by"])
        self.assertIsNone(body["disposition_at"])
        self.assertEqual(body["disposition_note"], "")

    def test_patch_invalid_status_422(self):
        iid = self._seed_inconsistency()
        r = self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/{iid}", json={"status": "resolved"}
        )
        self.assertEqual(r.status_code, 422)
        # untouched
        row = self.db.list_graph_inconsistencies(work_id=self.work_id)[0]
        self.assertEqual(row["status"], "open")

    def test_patch_unknown_id_404(self):
        r = self.client.patch(
            f"/api/works/{self.work_id}/inconsistencies/ghost", json={"status": "fixed"}
        )
        self.assertEqual(r.status_code, 404)

    def test_patch_scoped_to_work(self):
        """A finding in Work A must not be updatable through Work B's route."""
        iid = self._seed_inconsistency()
        other = self.db.create_work(title="Other", work_type="writing")["id"]
        r = self.client.patch(f"/api/works/{other}/inconsistencies/{iid}", json={"status": "fixed"})
        self.assertEqual(r.status_code, 404)
        row = self.db.list_graph_inconsistencies(work_id=self.work_id)[0]
        self.assertEqual(row["status"], "open")

    # ── db-level guard ───────────────────────────────────────────────────

    def test_db_rejects_invalid_status_before_write(self):
        iid = self._seed_inconsistency()
        with self.assertRaises(ValueError):
            self.db.update_graph_inconsistency_status(iid, "nope")

    def test_db_returns_none_for_missing_row(self):
        self.assertIsNone(self.db.update_graph_inconsistency_status("ghost", "fixed"))

    # ── durability across ATLAS-O re-verification ────────────────────────

    def test_dispositions_survive_reverify(self):
        """The rerun path (delete open + recreate staged) must never resurrect
        a dismissed finding as a fresh open duplicate."""
        iid = self._seed_inconsistency()
        self.db.update_graph_inconsistency_status(
            iid, "intentional", note="Deliberate misdirection."
        )
        # Simulate ATLAS-O reverify of ch2: delete pass + recreate same finding
        self.db.delete_graph_inconsistencies_for_chapter(self.ch2)
        again = self._seed_inconsistency()
        self.assertEqual(again, iid)  # dedupe returned the preserved row
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "intentional")
        self.assertEqual(rows[0]["disposition_note"], "Deliberate misdirection.")

    def test_reverify_replaces_open_findings(self):
        """Open (undispositioned) findings ARE machine output — the rerun
        replaces them wholesale."""
        self._seed_inconsistency(description="stale finding")
        self.db.delete_graph_inconsistencies_for_chapter(self.ch2)
        self.assertEqual(self.db.list_graph_inconsistencies(work_id=self.work_id), [])
        fresh = self._seed_inconsistency(
            description="new finding", current_quote="a different quote"
        )
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        self.assertEqual([r["id"] for r in rows], [fresh])

    def test_chapter_purge_preserves_dispositioned(self):
        """Full graph purge (chapter re-extraction) also keeps author decisions."""
        iid = self._seed_inconsistency()
        self.db.update_graph_inconsistency_status(iid, "wontfix")
        self.db.delete_graph_for_chapter(self.ch2)
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        self.assertEqual([r["id"] for r in rows], [iid])
        self.assertEqual(rows[0]["status"], "wontfix")

    def test_create_dedupes_regardless_of_offsets(self):
        """Identity is chapters+quotes; shifted offsets alone don't duplicate."""
        a = self._seed_inconsistency(current_offset=10, prior_offset=5)
        b = self._seed_inconsistency(current_offset=99, prior_offset=42)
        self.assertEqual(a, b)
        self.assertEqual(len(self.db.list_graph_inconsistencies(work_id=self.work_id)), 1)


if __name__ == "__main__":
    unittest.main()
