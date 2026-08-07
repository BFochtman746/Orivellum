"""Tests for Evidence Before Belief memory capture (Task #848).

Covers:
 - Schema v99 migration: memory_evidence table exists; source_evidence_id on user_memory
 - create_memory_evidence: returns an ID; row is retrievable via get_memory_evidence
 - upsert_memory_fact with source_evidence_id: FK stored; retrievable via include_evidence
 - get_current_memory_facts(include_evidence=True): LEFT JOIN returns evidence fields
 - Facts without evidence: include_evidence=True gracefully returns NULL evidence fields
 - Capture ordering: evidence row must be written before the fact row (FK constraint hold)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


def _make_db(path: str) -> "OrivellumDB":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from orivellum.database.db import OrivellumDB
    return OrivellumDB(path)


# ─── Schema v99 ───────────────────────────────────────────────────────────────

class TestSchemaV99(unittest.TestCase):
    """v99 creates memory_evidence table and adds source_evidence_id to user_memory."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_memory_evidence_table_exists(self):
        tables = {r[0] for r in self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        self.assertIn("memory_evidence", tables,
                      "memory_evidence table must exist after v99 migration")

    def test_memory_evidence_columns(self):
        cols = {r[1] for r in self.db._conn.execute(
            "PRAGMA table_info(memory_evidence)"
        ).fetchall()}
        for col in ("id", "raw_text", "source_type", "source_id",
                    "conversation_id", "message_id", "created_at"):
            self.assertIn(col, cols, f"Column '{col}' missing from memory_evidence")

    def test_user_memory_has_source_evidence_id(self):
        cols = {r[1] for r in self.db._conn.execute(
            "PRAGMA table_info(user_memory)"
        ).fetchall()}
        self.assertIn("source_evidence_id", cols,
                      "user_memory must have source_evidence_id after v99")

    def test_evidence_indexes_created(self):
        indexes = {r[0] for r in self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()}
        self.assertIn("me_conv", indexes)
        self.assertIn("um_evidence", indexes)


# ─── create_memory_evidence ───────────────────────────────────────────────────

class TestCreateMemoryEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_returns_non_empty_id(self):
        eid = self.db.create_memory_evidence("User: I like dark mode\n\nAssistant: Noted.")
        self.assertTrue(eid, "create_memory_evidence must return a non-empty ID")
        self.assertIsInstance(eid, str)

    def test_row_is_retrievable(self):
        raw = "User: I prefer Python\n\nAssistant: Understood."
        eid = self.db.create_memory_evidence(raw, source_type="conversation",
                                              source_id="conv-1", conversation_id="conv-1")
        row = self.db.get_memory_evidence(eid)
        self.assertIsNotNone(row)
        self.assertEqual(row["raw_text"], raw)
        self.assertEqual(row["source_type"], "conversation")
        self.assertEqual(row["conversation_id"], "conv-1")

    def test_nonexistent_evidence_returns_none(self):
        result = self.db.get_memory_evidence("nonexistent-uuid")
        self.assertIsNone(result)

    def test_raw_text_truncated_to_2000_chars(self):
        long_text = "x" * 5000
        eid = self.db.create_memory_evidence(long_text)
        row = self.db.get_memory_evidence(eid)
        self.assertLessEqual(len(row["raw_text"]), 2000,
                             "raw_text must be truncated to at most 2000 characters")

    def test_default_source_type_is_conversation(self):
        eid = self.db.create_memory_evidence("Some passage")
        row = self.db.get_memory_evidence(eid)
        self.assertEqual(row["source_type"], "conversation")

    def test_optional_fields_can_be_none(self):
        eid = self.db.create_memory_evidence("Text without IDs")
        row = self.db.get_memory_evidence(eid)
        self.assertIsNone(row["source_id"])
        self.assertIsNone(row["conversation_id"])
        self.assertIsNone(row["message_id"])


# ─── upsert_memory_fact with source_evidence_id ───────────────────────────────

class TestUpsertWithEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_source_evidence_id_stored_in_row(self):
        eid = self.db.create_memory_evidence("Exchange text", conversation_id="c1")
        self.db.upsert_memory_fact("lang", "Python", memory_type="semantic",
                                   source_evidence_id=eid)
        row = self.db._conn.execute(
            "SELECT source_evidence_id FROM user_memory WHERE key='lang' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_evidence_id"], eid)

    def test_fact_without_evidence_has_null_source_evidence_id(self):
        self.db.upsert_memory_fact("theme", "dark")
        row = self.db._conn.execute(
            "SELECT source_evidence_id FROM user_memory WHERE key='theme' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["source_evidence_id"],
                          "source_evidence_id must be NULL when no evidence passed")

    def test_evidence_id_persisted_across_update(self):
        """The new current row after an update should carry a fresh evidence ID."""
        eid1 = self.db.create_memory_evidence("First exchange", conversation_id="c1")
        self.db.upsert_memory_fact("lang", "Python", source_evidence_id=eid1)
        eid2 = self.db.create_memory_evidence("Second exchange", conversation_id="c2")
        self.db.upsert_memory_fact("lang", "Rust", source_evidence_id=eid2)
        current = self.db._conn.execute(
            "SELECT source_evidence_id FROM user_memory WHERE key='lang' AND valid_to IS NULL"
        ).fetchone()
        self.assertEqual(current["source_evidence_id"], eid2,
                         "Current row must reference the most recent evidence ID")


# ─── get_current_memory_facts(include_evidence=True) ─────────────────────────

class TestGetCurrentWithEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_include_evidence_returns_evidence_fields(self):
        raw = "User: I code in Python\n\nAssistant: Noted."
        eid = self.db.create_memory_evidence(raw, conversation_id="conv-99")
        self.db.upsert_memory_fact("lang", "Python", source_evidence_id=eid)
        facts = self.db.get_current_memory_facts(include_evidence=True)
        fact = next((f for f in facts if f["key"] == "lang"), None)
        self.assertIsNotNone(fact)
        self.assertEqual(fact["evidence_text"], raw)
        self.assertEqual(fact["evidence_conversation_id"], "conv-99")
        self.assertEqual(fact["evidence_source_type"], "conversation")

    def test_fact_without_evidence_has_null_evidence_fields(self):
        """Facts with no source_evidence_id must return NULL for all evidence fields."""
        self.db.upsert_memory_fact("theme", "dark")
        facts = self.db.get_current_memory_facts(include_evidence=True)
        fact = next((f for f in facts if f["key"] == "theme"), None)
        self.assertIsNotNone(fact)
        self.assertIsNone(fact.get("evidence_text"),
                          "evidence_text must be NULL for facts without evidence")
        self.assertIsNone(fact.get("evidence_conversation_id"))

    def test_include_evidence_false_omits_evidence_fields(self):
        """Without include_evidence, the response must not contain evidence columns."""
        eid = self.db.create_memory_evidence("Exchange", conversation_id="c1")
        self.db.upsert_memory_fact("x", "y", source_evidence_id=eid)
        facts = self.db.get_current_memory_facts(include_evidence=False)
        fact = next((f for f in facts if f["key"] == "x"), None)
        self.assertIsNotNone(fact)
        self.assertNotIn("evidence_text", fact,
                         "evidence_text must not appear when include_evidence=False")

    def test_multiple_facts_each_get_their_own_evidence(self):
        eid_a = self.db.create_memory_evidence("Exchange A", conversation_id="ca")
        eid_b = self.db.create_memory_evidence("Exchange B", conversation_id="cb")
        self.db.upsert_memory_fact("ka", "va", source_evidence_id=eid_a)
        self.db.upsert_memory_fact("kb", "vb", source_evidence_id=eid_b)
        facts = self.db.get_current_memory_facts(include_evidence=True)
        by_key = {f["key"]: f for f in facts}
        self.assertEqual(by_key["ka"]["evidence_conversation_id"], "ca")
        self.assertEqual(by_key["kb"]["evidence_conversation_id"], "cb")


# ─── Evidence-before-belief ordering ─────────────────────────────────────────

class TestEvidenceBeliefOrdering(unittest.TestCase):
    """Verify that _infer_memory_facts writes the evidence row BEFORE invoking
    the LLM (_call_sync), not merely before writing the fact row.

    The test intercepts _call_sync and checks that the memory_evidence table
    already contains a row for the exchange at the moment derivation begins.
    """

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def _run_infer(self, user_text: str, assistant_text: str,
                   llm_response: str = '{"facts": []}') -> list[dict]:
        """Call _infer_memory_facts with a mocked LLM and return evidence rows
        that existed at the moment _call_sync was invoked."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes import conversations as conv_mod
        from orivellum.api.routes.conversations import _infer_memory_facts
        from unittest.mock import patch, MagicMock

        evidence_at_call_time: list[dict] = []

        def capture_evidence_then_return(*args, **kwargs):
            # At this point derivation is happening — evidence must already exist
            rows = self.db._conn.execute(
                "SELECT * FROM memory_evidence"
            ).fetchall()
            evidence_at_call_time.extend(dict(r) for r in rows)
            return llm_response

        mock_cfg = MagicMock()
        mock_cfg.serving.base_url = "http://fake"
        mock_cfg.serving.workhorse_model = "fake-model"

        with patch.object(conv_mod, "get_config", return_value=mock_cfg), \
             patch("orivellum.capabilities.cognition._call_sync",
                   side_effect=capture_evidence_then_return):
            _infer_memory_facts(self.db, "conv-test", user_text, assistant_text)

        return evidence_at_call_time

    def test_evidence_committed_before_llm_invoked(self):
        """The memory_evidence table must have a row at the moment _call_sync fires."""
        evidence_rows = self._run_infer(
            "I really prefer Python over Go for backend work.",
            "Understood, I'll remember that."
        )
        self.assertGreater(len(evidence_rows), 0,
                           "Evidence row must be committed before _call_sync is called")

    def test_evidence_text_matches_exchange(self):
        """The committed evidence must contain the user text from the exchange."""
        evidence_rows = self._run_infer(
            "My favourite language is Rust.",
            "Got it, noted."
        )
        self.assertTrue(
            any("Rust" in r["raw_text"] for r in evidence_rows),
            "Evidence raw_text must include the user's message text"
        )

    def test_evidence_conversation_id_set_before_llm(self):
        """Evidence row must reference the correct conversation_id before inference."""
        evidence_rows = self._run_infer(
            "I work mostly on distributed systems.",
            "Good to know."
        )
        self.assertTrue(
            any(r["conversation_id"] == "conv-test" for r in evidence_rows),
            "Evidence conversation_id must be set at LLM call time"
        )

    def test_short_exchange_writes_no_evidence(self):
        """Exchanges shorter than 15 chars must not create any evidence row."""
        self._run_infer("Hi", "Hello!")  # user_text < 15 chars, returns early
        count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_evidence"
        ).fetchone()["n"]
        self.assertEqual(count, 0, "No evidence row should be written for too-short exchanges")

    def test_evidence_write_failure_prevents_fact_creation(self):
        """If evidence write fails, no fact should be written (abort-on-evidence-fail)."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes import conversations as conv_mod
        from orivellum.api.routes.conversations import _infer_memory_facts
        from unittest.mock import patch, MagicMock

        mock_cfg = MagicMock()
        mock_cfg.serving.base_url = "http://fake"
        mock_cfg.serving.workhorse_model = "fake-model"

        llm_response = '{"facts": [{"key": "lang", "value": "Python", "confidence": 0.9, "memory_type": "semantic"}]}'

        with patch.object(conv_mod, "get_config", return_value=mock_cfg), \
             patch("orivellum.capabilities.cognition._call_sync",
                   return_value=llm_response), \
             patch.object(self.db, "create_memory_evidence",
                          side_effect=RuntimeError("DB locked")):
            # Must not raise
            _infer_memory_facts(self.db, "conv-test",
                                "I prefer Python for all my work.", "Understood.")

        # No fact should have been written because evidence write failed
        facts = self.db.get_current_memory_facts()
        self.assertEqual(len(facts), 0,
                         "No fact must be written when evidence creation fails")


# ─── _handle_remember evidence-before-belief ordering ────────────────────────

class TestDeleteMemoryEvidence(unittest.TestCase):
    """delete_memory_evidence removes orphaned evidence rows immediately."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def test_delete_existing_row_returns_true(self):
        eid = self.db.create_memory_evidence("Some passage")
        result = self.db.delete_memory_evidence(eid)
        self.assertTrue(result)

    def test_deleted_row_not_retrievable(self):
        eid = self.db.create_memory_evidence("Some passage")
        self.db.delete_memory_evidence(eid)
        self.assertIsNone(self.db.get_memory_evidence(eid))

    def test_delete_nonexistent_id_returns_false(self):
        self.assertFalse(self.db.delete_memory_evidence("nonexistent-uuid"))


class TestNoFactEvidenceCleanup(unittest.TestCase):
    """When inference produces no qualifying facts the evidence row must be deleted."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def _run_infer(self, user_text: str, llm_response: str):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes import conversations as conv_mod
        from orivellum.api.routes.conversations import _infer_memory_facts
        from unittest.mock import patch, MagicMock

        mock_cfg = MagicMock()
        mock_cfg.serving.base_url = "http://fake"
        mock_cfg.serving.workhorse_model = "fake-model"

        with patch.object(conv_mod, "get_config", return_value=mock_cfg), \
             patch("orivellum.capabilities.cognition._call_sync",
                   return_value=llm_response):
            _infer_memory_facts(self.db, "conv-test", user_text, "Got it.")

    def test_no_facts_deletes_evidence_row(self):
        """When LLM returns empty facts list, evidence row must be deleted."""
        self._run_infer(
            "I think the weather is nice today.",
            '{"facts": []}'
        )
        count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_evidence"
        ).fetchone()["n"]
        self.assertEqual(count, 0,
                         "Evidence row must be deleted when no facts are derived")

    def test_below_threshold_facts_deletes_evidence(self):
        """Facts below confidence 0.75 don't qualify; evidence must be deleted."""
        self._run_infer(
            "I might perhaps sometimes prefer dark mode.",
            '{"facts": [{"key": "ui_theme", "value": "dark", "confidence": 0.5, "memory_type": "semantic"}]}'
        )
        count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_evidence"
        ).fetchone()["n"]
        self.assertEqual(count, 0,
                         "Evidence must be deleted when all facts are below threshold")

    def test_qualifying_fact_retains_evidence(self):
        """When a fact is written, the evidence row must be kept."""
        self._run_infer(
            "I prefer Python for all backend work.",
            '{"facts": [{"key": "lang", "value": "Python", "confidence": 0.9, "memory_type": "semantic"}]}'
        )
        count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_evidence"
        ).fetchone()["n"]
        self.assertEqual(count, 1,
                         "Evidence row must be retained when at least one fact is written")

    def test_llm_empty_response_deletes_evidence(self):
        """If LLM returns empty string, evidence row must be deleted."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes import conversations as conv_mod
        from orivellum.api.routes.conversations import _infer_memory_facts
        from unittest.mock import patch, MagicMock

        mock_cfg = MagicMock()
        mock_cfg.serving.base_url = "http://fake"
        mock_cfg.serving.workhorse_model = "fake-model"

        with patch.object(conv_mod, "get_config", return_value=mock_cfg), \
             patch("orivellum.capabilities.cognition._call_sync", return_value=""):
            _infer_memory_facts(self.db, "conv-test",
                                "I occasionally use dark mode sometimes.", "Got it.")

        count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM memory_evidence"
        ).fetchone()["n"]
        self.assertEqual(count, 0,
                         "Evidence must be deleted when LLM returns empty response")


class TestHandleRememberEvidenceOrdering(unittest.TestCase):
    """Verify that _handle_remember also writes evidence BEFORE invoking the LLM.

    The explicit "remember" path is user-directed and must satisfy the same
    Evidence-Before-Belief guarantee as the automatic inference path.
    """

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        Path(self.tmp).unlink(missing_ok=True)

    def _run_remember(self, user_text: str,
                      llm_response: str = '{"key": "lang", "value": "Python"}'):
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes.conversations import _handle_remember
        from unittest.mock import patch

        evidence_at_call_time: list[dict] = []

        def capture_then_return(*args, **kwargs):
            rows = self.db._conn.execute(
                "SELECT * FROM memory_evidence"
            ).fetchall()
            evidence_at_call_time.extend(dict(r) for r in rows)
            return llm_response

        with patch("orivellum.capabilities.cognition._call_sync",
                   side_effect=capture_then_return):
            reply = _handle_remember(self.db, user_text, "http://fake", "fake-model")

        return reply, evidence_at_call_time

    def test_evidence_committed_before_llm_in_remember(self):
        """Evidence must be committed before _call_sync fires in _handle_remember."""
        _, evidence_rows = self._run_remember("Please remember I prefer Python.")
        self.assertGreater(len(evidence_rows), 0,
                           "Evidence row must exist before LLM invocation in _handle_remember")

    def test_evidence_text_contains_user_message(self):
        _, evidence_rows = self._run_remember("Remember that I use APA citations.")
        self.assertTrue(
            any("APA" in r["raw_text"] for r in evidence_rows),
            "Evidence raw_text must contain the user's message"
        )

    def test_stored_fact_references_evidence_id(self):
        """Fact written by _handle_remember must have source_evidence_id set."""
        self._run_remember("Remember I prefer dark mode.")
        row = self.db._conn.execute(
            "SELECT source_evidence_id FROM user_memory WHERE valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(row, "A memory fact must be written")
        self.assertIsNotNone(row["source_evidence_id"],
                             "source_evidence_id must be set on facts from _handle_remember")

    def test_evidence_write_failure_aborts_remember_capture(self):
        """If evidence write fails, no fact must be written and reply must indicate failure."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from orivellum.api.routes.conversations import _handle_remember
        from unittest.mock import patch

        with patch.object(self.db, "create_memory_evidence",
                          side_effect=RuntimeError("DB locked")), \
             patch("orivellum.capabilities.cognition._call_sync",
                   return_value='{"key": "lang", "value": "Python"}') as mock_llm:
            reply = _handle_remember(self.db, "Remember I like Python.",
                                     "http://fake", "fake-model")

        # LLM must NOT have been called (evidence failure aborts before LLM)
        mock_llm.assert_not_called()
        # No fact must be written
        facts = self.db.get_current_memory_facts()
        self.assertEqual(len(facts), 0, "No fact must be written when evidence fails")
        # Reply must indicate failure, not success
        self.assertIn("Could not save", reply)

    def test_successful_remember_reply_contains_key(self):
        reply, _ = self._run_remember(
            "Remember I prefer Rust.",
            '{"key": "lang", "value": "Rust"}'
        )
        self.assertIn("Remembered", reply)


if __name__ == "__main__":
    unittest.main()
