"""Tests for the unified governance review queue (/api/review/*).

Covers:
- GET /api/review/queue aggregates knowledge (ai_auto), pending_reclassify,
  unexpired suggestions, and unresolved doc_dupes; sorted confidence ASC.
- Expired suggestions and resolved dupes are excluded.
- POST /api/review/{item_id}/resolve — approve/reject/defer for each type.
- Defer snoozes an item (excluded from the queue) via review_deferrals.
- work_assignment suggestion approval creates a Work and links docs.
- Validation: bad decision → 400, unknown type → 400, missing item → 404.
- ZIP explode auto-populates a work_assignment suggestion for >2 children.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.database.db import OrivellumDB
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _make_doc(db: OrivellumDB, title: str = "Doc", work_id: str | None = None) -> dict:
    return db.create_document(
        title=title,
        source="/tmp/x.pdf",
        sha256=uuid.uuid4().hex,
        kind="pdf",
        work_id=work_id,
    )


def _add_suggestion(db, kind: str, text: str, meta: dict, expires_at: str | None = None) -> str:
    sid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO suggestions(id, work_id, kind, text, meta, created_at, expires_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (sid, None, kind, text, json.dumps(meta), _now(), expires_at),
        )
        db._conn.commit()
    return sid


def _add_reclassify(db, doc_id: str, reason: str = "looks misfiled") -> str:
    rid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO pending_reclassify(id, doc_id, reason, created_at) VALUES(?,?,?,?)",
            (rid, doc_id, reason, _now()),
        )
        db._conn.commit()
    return rid


def _add_dupe(db, doc_a: str, doc_b: str, similarity: float = 0.9) -> str:
    did = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO doc_dupes(id, doc_a_id, doc_b_id, similarity, kind, created_at)"
            " VALUES(?,?,?,?, 'near_duplicate', ?)",
            (did, doc_a, doc_b, similarity, _now()),
        )
        db._conn.commit()
    return did


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    # ── queue aggregation ─────────────────────────────────────────────────────

    def test_queue_aggregates_all_four_types(self):
        kid = self.db.create_knowledge_item(
            None, "claim", "AI thinks X", confidence=0.3, review_status="ai_auto"
        )
        doc = _make_doc(self.db, "Reclass me")
        rid = _add_reclassify(self.db, doc["id"])
        sid = _add_suggestion(
            self.db,
            "version_relationship",
            "A derives from B",
            {"doc_a_id": "a", "doc_b_id": "b", "confidence": 0.7},
        )
        d1, d2 = _make_doc(self.db, "Dup A"), _make_doc(self.db, "Dup B")
        did = _add_dupe(self.db, d1["id"], d2["id"], 0.92)

        r = self.client.get("/api/review/queue")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = {i["id"] for i in body["items"]}
        self.assertIn(f"knowledge:{kid}", ids)
        self.assertIn(f"reclassify:{rid}", ids)
        self.assertIn(f"suggestion:{sid}", ids)
        self.assertIn(f"duplicate:{did}", ids)
        self.assertEqual(body["count"], 4)
        self.assertEqual(
            body["counts_by_type"],
            {"knowledge": 1, "reclassify": 1, "suggestion": 1, "duplicate": 1},
        )

    def test_queue_sorted_confidence_asc(self):
        self.db.create_knowledge_item(None, "claim", "low", confidence=0.1, review_status="ai_auto")
        self.db.create_knowledge_item(
            None, "claim", "high", confidence=0.9, review_status="ai_auto"
        )
        r = self.client.get("/api/review/queue").json()
        confs = [i["confidence"] for i in r["items"]]
        self.assertEqual(confs, sorted(confs))

    def test_queue_excludes_expired_suggestions_and_resolved_dupes(self):
        past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        _add_suggestion(self.db, "note", "expired", {}, expires_at=past)
        d1, d2 = _make_doc(self.db), _make_doc(self.db)
        did = _add_dupe(self.db, d1["id"], d2["id"])
        self.db.resolve_near_duplicate(did, "keep_both")
        r = self.client.get("/api/review/queue").json()
        self.assertEqual(r["count"], 0)

    def test_queue_excludes_approved_knowledge(self):
        kid = self.db.create_knowledge_item(None, "claim", "done", review_status="ai_auto")
        self.db.update_knowledge_review_status(kid, "approved")
        r = self.client.get("/api/review/queue").json()
        self.assertEqual(r["count"], 0)

    # ── resolve: knowledge ────────────────────────────────────────────────────

    def test_resolve_knowledge_approve_and_reject(self):
        k1 = self.db.create_knowledge_item(None, "claim", "a", review_status="ai_auto")
        k2 = self.db.create_knowledge_item(None, "claim", "b", review_status="ai_auto")
        r = self.client.post(f"/api/review/knowledge:{k1}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["review_status"], "approved")
        r = self.client.post(f"/api/review/knowledge:{k2}/resolve", json={"decision": "reject"})
        self.assertEqual(r.json()["review_status"], "rejected")
        self.assertEqual(self.client.get("/api/review/queue").json()["count"], 0)

    def test_cross_endpoint_knowledge_resolution_is_final(self):
        # Queue decision first → legacy PATCH cannot overturn it without force.
        k1 = self.db.create_knowledge_item(None, "claim", "x", review_status="ai_auto")
        self.client.post(f"/api/review/knowledge:{k1}/resolve", json={"decision": "approve"})
        r = self.client.patch(f"/api/knowledge/{k1}/review", json={"review_status": "rejected"})
        self.assertEqual(r.status_code, 409)
        # Same status again is idempotent, not a conflict
        r = self.client.patch(f"/api/knowledge/{k1}/review", json={"review_status": "approved"})
        self.assertEqual(r.status_code, 200)
        # Deliberate flip works with force=true
        r = self.client.patch(
            f"/api/knowledge/{k1}/review", json={"review_status": "rejected", "force": True}
        )
        self.assertEqual(r.status_code, 200)

        # Legacy PATCH first → queue resolve loses.
        k2 = self.db.create_knowledge_item(None, "claim", "y", review_status="ai_auto")
        r = self.client.patch(f"/api/knowledge/{k2}/review", json={"review_status": "approved"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/review/knowledge:{k2}/resolve", json={"decision": "reject"})
        self.assertEqual(r.status_code, 409)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT review_status FROM knowledge WHERE id=?", (k2,)
            ).fetchone()
        self.assertEqual(row["review_status"], "approved")

    def test_resolve_knowledge_stale_card_conflicts(self):
        # A decision already made elsewhere must not be overturned by a stale card.
        kid = self.db.create_knowledge_item(None, "claim", "x", review_status="ai_auto")
        self.db.update_knowledge_review_status(kid, "approved")
        r = self.client.post(f"/api/review/knowledge:{kid}/resolve", json={"decision": "reject"})
        self.assertEqual(r.status_code, 409)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT review_status FROM knowledge WHERE id=?", (kid,)
            ).fetchone()
        self.assertEqual(row["review_status"], "approved")

    # ── resolve: defer ────────────────────────────────────────────────────────

    def test_defer_snoozes_item_from_queue(self):
        kid = self.db.create_knowledge_item(None, "claim", "later", review_status="ai_auto")
        r = self.client.post(
            f"/api/review/knowledge:{kid}/resolve",
            json={"decision": "defer", "reason": "need context"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("deferred_until", r.json())
        self.assertEqual(self.client.get("/api/review/queue").json()["count"], 0)
        # Expire the deferral → item returns
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE review_deferrals SET deferred_until=?",
                ((datetime.now(UTC) - timedelta(hours=1)).isoformat(),),
            )
            self.db._conn.commit()
        self.assertEqual(self.client.get("/api/review/queue").json()["count"], 1)

    def test_defer_nonexistent_or_resolved_item_404(self):
        # nonexistent
        r = self.client.post(
            f"/api/review/knowledge:{uuid.uuid4()}/resolve", json={"decision": "defer"}
        )
        self.assertEqual(r.status_code, 404)
        # already resolved → no orphaned deferral
        kid = self.db.create_knowledge_item(None, "claim", "done", review_status="ai_auto")
        self.db.update_knowledge_review_status(kid, "approved")
        r = self.client.post(f"/api/review/knowledge:{kid}/resolve", json={"decision": "defer"})
        self.assertEqual(r.status_code, 404)
        with self.db._lock:
            n = self.db._conn.execute("SELECT COUNT(*) c FROM review_deferrals").fetchone()["c"]
        self.assertEqual(n, 0)

    # ── resolve: reclassify ───────────────────────────────────────────────────

    def test_resolve_reclassify_reject_deletes_row(self):
        doc = _make_doc(self.db)
        rid = _add_reclassify(self.db, doc["id"])
        r = self.client.post(f"/api/review/reclassify:{rid}/resolve", json={"decision": "reject"})
        self.assertEqual(r.status_code, 200)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM pending_reclassify WHERE id=?", (rid,)
            ).fetchone()
        self.assertIsNone(row)

    # ── resolve: suggestion ───────────────────────────────────────────────────

    def test_resolve_work_assignment_creates_work_and_links(self):
        d1, d2, d3 = (_make_doc(self.db, f"child {i}") for i in range(3))
        sid = _add_suggestion(
            self.db,
            "work_assignment",
            "Group archive docs",
            {"doc_ids": [d1["id"], d2["id"], d3["id"]], "proposed_title": "My Archive"},
        )
        r = self.client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 200)
        applied = r.json()["applied"]
        self.assertEqual(applied["linked"], 3)
        work = self.db.get_work(applied["work_id"])
        self.assertEqual(work["title"], "My Archive")
        self.assertEqual(self.db.get_document(d1["id"])["work_id"], applied["work_id"])

    def test_resolve_suggestion_reject_deletes(self):
        sid = _add_suggestion(self.db, "note", "meh", {})
        r = self.client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "reject"})
        self.assertEqual(r.status_code, 200)
        with self.db._lock:
            row = self.db._conn.execute("SELECT 1 FROM suggestions WHERE id=?", (sid,)).fetchone()
        self.assertIsNone(row)

    # ── resolve: duplicate ────────────────────────────────────────────────────

    def test_resolve_duplicate_approve_and_reject(self):
        d1, d2 = _make_doc(self.db, "A"), _make_doc(self.db, "B")
        did = _add_dupe(self.db, d1["id"], d2["id"])
        r = self.client.post(f"/api/review/duplicate:{did}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["action"], "mark_superseded")

        d3, d4 = _make_doc(self.db, "C"), _make_doc(self.db, "D")
        did2 = _add_dupe(self.db, d3["id"], d4["id"])
        r = self.client.post(f"/api/review/duplicate:{did2}/resolve", json={"decision": "reject"})
        self.assertEqual(r.json()["action"], "keep_both")
        self.assertEqual(self.client.get("/api/review/queue").json()["count"], 0)

    def test_resolve_duplicate_canonical_doc_b(self):
        d1, d2 = _make_doc(self.db, "A"), _make_doc(self.db, "B")
        did = _add_dupe(self.db, d1["id"], d2["id"])
        r = self.client.post(
            f"/api/review/duplicate:{did}/resolve",
            json={"decision": "approve", "canonical_doc_id": d2["id"]},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["canonical_doc_id"], d2["id"])
        # doc_a (the non-canonical one) got superseded
        self.assertEqual(self.db.get_document(d1["id"])["lifecycle"], "superseded")

    def test_resolve_duplicate_invalid_canonical_400(self):
        d1, d2 = _make_doc(self.db), _make_doc(self.db)
        did = _add_dupe(self.db, d1["id"], d2["id"])
        r = self.client.post(
            f"/api/review/duplicate:{did}/resolve",
            json={"decision": "approve", "canonical_doc_id": "not-in-pair"},
        )
        self.assertEqual(r.status_code, 400)
        # Pair must remain unresolved after the rejected request
        self.assertEqual(len(self.db.list_near_duplicates(resolved=False)), 1)

    def test_cross_endpoint_duplicate_resolution_is_final(self):
        # Resolving via the review queue must block the legacy library route
        # from overturning the decision or re-applying side effects — and
        # vice versa. Both surfaces go through the same claim-first primitive.
        d1, d2 = _make_doc(self.db, "A"), _make_doc(self.db, "B")
        did = _add_dupe(self.db, d1["id"], d2["id"])
        r = self.client.post(
            f"/api/review/duplicate:{did}/resolve", json={"decision": "reject"}
        )  # keep_both
        self.assertEqual(r.status_code, 200)
        # Legacy route now loses the race → 409, no lifecycle change
        r = self.client.post(
            f"/api/library/duplicates/{did}/resolve", json={"action": "mark_superseded"}
        )
        self.assertEqual(r.status_code, 409)
        self.assertNotEqual(self.db.get_document(d2["id"])["lifecycle"], "superseded")
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT resolution FROM doc_dupes WHERE id=?", (did,)
            ).fetchone()
        self.assertEqual(row["resolution"], "keep_both")

        # And the reverse: legacy route first, review route loses.
        d3, d4 = _make_doc(self.db, "C"), _make_doc(self.db, "D")
        did2 = _add_dupe(self.db, d3["id"], d4["id"])
        r = self.client.post(
            f"/api/library/duplicates/{did2}/resolve", json={"action": "keep_both"}
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/api/review/duplicate:{did2}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)
        self.assertNotEqual(self.db.get_document(d4["id"])["lifecycle"], "superseded")

    def test_double_resolve_conflicts(self):
        # duplicate: second resolve → 409
        d1, d2 = _make_doc(self.db), _make_doc(self.db)
        did = _add_dupe(self.db, d1["id"], d2["id"])
        self.client.post(f"/api/review/duplicate:{did}/resolve", json={"decision": "reject"})
        r = self.client.post(f"/api/review/duplicate:{did}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)
        # suggestion: second resolve → 404 (row already deleted by claimant)
        sid = _add_suggestion(self.db, "note", "once", {})
        self.client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "approve"})
        r = self.client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 404)

    # ── validation ────────────────────────────────────────────────────────────

    def test_bad_decision_400(self):
        r = self.client.post("/api/review/knowledge:x/resolve", json={"decision": "maybe"})
        self.assertEqual(r.status_code, 400)

    def test_unknown_type_400(self):
        r = self.client.post("/api/review/banana:x/resolve", json={"decision": "approve"})
        self.assertEqual(r.status_code, 400)

    def test_missing_item_404(self):
        for t in ("knowledge", "reclassify", "suggestion", "duplicate"):
            r = self.client.post(
                f"/api/review/{t}:{uuid.uuid4()}/resolve", json={"decision": "approve"}
            )
            self.assertEqual(r.status_code, 404, t)

    def test_auth_required(self):
        bare = TestClient(self.app, raise_server_exceptions=True)
        self.assertEqual(bare.get("/api/review/queue").status_code, 401)


class ZipSuggestionTests(unittest.TestCase):
    """ZIP explode should auto-create a work_assignment suggestion for >2 docs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _make_zip(self, n: int) -> Path:
        import zipfile

        p = Path(self._tmp.name) / "archive.zip"
        with zipfile.ZipFile(p, "w") as zf:
            for i in range(n):
                zf.writestr(f"note_{i}.txt", f"content of file {i} " * 20)
        return p

    def _explode(self, path: Path, work_id: str | None = None) -> list[str]:
        from orivellum.capabilities.pipeline import _explode_zip_into_documents

        parent = _make_doc(self.db, "archive.zip")
        return _explode_zip_into_documents(parent["id"], path, work_id, "archive.zip", self.db)

    def _suggestions(self) -> list:
        with self.db._lock:
            return self.db._conn.execute(
                "SELECT * FROM suggestions WHERE kind='work_assignment'"
            ).fetchall()

    def test_zip_with_three_children_creates_suggestion(self):
        children = self._explode(self._make_zip(3))
        self.assertEqual(len(children), 3)
        rows = self._suggestions()
        self.assertEqual(len(rows), 1)
        meta = json.loads(rows[0]["meta"])
        self.assertEqual(set(meta["doc_ids"]), set(children))
        self.assertEqual(meta["proposed_title"], "archive")

    def test_zip_with_two_children_no_suggestion(self):
        self._explode(self._make_zip(2))
        self.assertEqual(len(self._suggestions()), 0)

    def test_zip_with_work_id_no_suggestion(self):
        work = self.db.create_work("Existing")
        self._explode(self._make_zip(4), work_id=work["id"])
        self.assertEqual(len(self._suggestions()), 0)

    def test_reexplode_does_not_duplicate_suggestion(self):
        z = self._make_zip(3)
        from orivellum.capabilities.pipeline import _explode_zip_into_documents

        parent = _make_doc(self.db, "archive.zip")
        _explode_zip_into_documents(parent["id"], z, None, "archive.zip", self.db)
        _explode_zip_into_documents(parent["id"], z, None, "archive.zip", self.db)
        self.assertEqual(len(self._suggestions()), 1)


if __name__ == "__main__":
    unittest.main()
