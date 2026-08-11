"""GENESIS self-tests, restored into the suite (audit D-08).

Covers the tamper-evident ledger chain, stage-status helpers, and the
seal preconditions (G0–G8 passed, no <<FILL>> placeholders in G9).
Runs against a real OrivellumDB temp instance so the schema is authoritative.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from orivellum.capabilities.genesis.gates import (
    STAGE_CODES,
    get_stage_status,
    ledger_append,
    next_open_stage,
)
from orivellum.capabilities.genesis.seal import compute_seal, verify_ledger
from orivellum.database.db import OrivellumDB, _now


class GenesisTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.conn = self.db._conn
        work = self.db.create_work(title="Ash and Silence")
        self.book_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO genesis_books (id, work_id, mode, length, acts, state, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self.book_id, work["id"], "cold", 80, 4, "G0", _now(), _now()),
        )
        self.conn.commit()

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────

    def _pass_stage(self, code: str):
        self.conn.execute(
            "INSERT INTO genesis_stages (id, book_id, stage_code, status) VALUES (?,?,?,?) "
            "ON CONFLICT(book_id, stage_code) DO UPDATE SET status=excluded.status",
            (str(uuid.uuid4()), self.book_id, code, "PASSED"),
        )

    def _set_artifact(self, code: str, content: str):
        import hashlib

        self.conn.execute(
            "INSERT INTO genesis_artifacts (id, book_id, stage_code, content, sha256, "
            "updated_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(book_id, stage_code) DO UPDATE SET "
            "content=excluded.content, sha256=excluded.sha256, updated_at=excluded.updated_at",
            (
                str(uuid.uuid4()),
                self.book_id,
                code,
                content,
                hashlib.sha256(content.encode()).hexdigest(),
                _now(),
            ),
        )

    # ── ledger chain ─────────────────────────────────────────────────────

    def test_ledger_chain_verifies(self):
        ledger_append(self.conn, self.book_id, "gate.pass", {"stage": "G0"})
        ledger_append(self.conn, self.book_id, "gate.pass", {"stage": "G1"})
        ledger_append(self.conn, self.book_id, "note", {"text": "canon locked"})
        ok, msg = verify_ledger(self.conn, self.book_id)
        self.assertTrue(ok, msg)

    def test_tampered_payload_breaks_verification(self):
        ledger_append(self.conn, self.book_id, "gate.pass", {"stage": "G0"})
        ledger_append(self.conn, self.book_id, "gate.pass", {"stage": "G1"})
        self.conn.execute(
            "UPDATE genesis_ledger SET payload=? WHERE book_id=? AND seq=0",
            ('{"stage":"G9"}', self.book_id),
        )
        ok, msg = verify_ledger(self.conn, self.book_id)
        self.assertFalse(ok)
        self.assertIn("seq=0", msg)

    def test_empty_ledger_is_valid(self):
        ok, _ = verify_ledger(self.conn, self.book_id)
        self.assertTrue(ok)

    # ── stage status ─────────────────────────────────────────────────────

    def test_stage_status_and_next_open(self):
        status = get_stage_status(self.conn, self.book_id)
        self.assertEqual(set(status), set(STAGE_CODES))
        self.assertEqual(next_open_stage(status), "G0")
        for code in ("G0", "G1"):
            self._pass_stage(code)
        status = get_stage_status(self.conn, self.book_id)
        self.assertEqual(next_open_stage(status), "G2")

    # ── seal preconditions ───────────────────────────────────────────────

    def test_seal_refused_until_g0_g8_passed(self):
        with self.assertRaises(ValueError):
            compute_seal(self.conn, self.book_id, "Ash and Silence", 80, 4, "Author X")

    def test_seal_refused_while_g9_has_fill_placeholders(self):
        for code in STAGE_CODES[:-1]:
            self._pass_stage(code)
        self._set_artifact("G9", "Blueprint\n<<FILL>> chapter 3 value shift")
        with self.assertRaises(ValueError):
            compute_seal(self.conn, self.book_id, "Ash and Silence", 80, 4, "Author X")

    def test_seal_happy_path_produces_manifest_and_valid_ledger(self):
        for code in STAGE_CODES[:-1]:
            self._pass_stage(code)
            self._set_artifact(code, f"# {code} artifact\ncomplete")
        self._set_artifact("G9", "# Chapter Blueprint\nAll rows complete.")
        manifest = compute_seal(self.conn, self.book_id, "Ash and Silence", 80, 4, "Author X")
        self.assertEqual(manifest["author_signoff"], "Author X")
        self.assertTrue(manifest["package_sha256"])
        self.assertEqual(manifest["handoff_target"], "BPOS:B0")
        # Seal + handoff entries landed on a verifiable chain
        ok, msg = verify_ledger(self.conn, self.book_id)
        self.assertTrue(ok, msg)
        status = get_stage_status(self.conn, self.book_id)
        self.assertEqual(status["G9"], "PASSED")


if __name__ == "__main__":
    unittest.main()
