"""Tests for LLM-powered knowledge extraction.

Covers:
- _parse_extraction: valid JSON, markdown-fenced JSON, malformed input, empty/null fields
- llm_harvest: entities/claims/relationships written to DB with a mocked AI response
- llm_harvest: gracefully produces zero items when AI returns malformed JSON
- llm_harvest: gracefully produces zero items when AI call fails (connection error)
- pipeline: llm_harvest is skipped when ai_extraction_enabled is 'false'
- pipeline: document reaches 'ready' even when llm_harvest raises
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: str):
    """Open a real OrivellumDB backed by a temp file."""
    from orivellum.database.db import OrivellumDB
    return OrivellumDB(str(Path(tmp_path) / "test.db"))


def _make_extraction_result(text: str = "Hello world. This is a test sentence. Orivellum Corp was founded in 2020."):
    """Return a minimal ExtractionResult-like object."""
    page = MagicMock()
    page.text = text

    result = MagicMock()
    result.kind = "text"
    result.word_count = len(text.split())
    result.full_text = text
    result.headings = []
    result.pages = [page]
    result.ok = True
    return result


VALID_JSON = json.dumps({
    "entities": [{"name": "Orivellum Corp", "description": "A software company"}],
    "claims": [{"text": "Orivellum Corp was founded in 2020."}],
    "relationships": [{"subject": "Orivellum Corp", "predicate": "founded in", "object": "2020"}],
})

FENCED_JSON = f"```json\n{VALID_JSON}\n```"

PARTIAL_JSON = json.dumps({
    "entities": [{"name": "Alice"}],  # no description
    "claims": [],
    # no relationships key
})


# ---------------------------------------------------------------------------
# _parse_extraction
# ---------------------------------------------------------------------------

class TestParseExtraction(unittest.TestCase):

    def _parse(self, text: str) -> dict:
        from orivellum.capabilities.knowledge_harvest import _parse_extraction
        return _parse_extraction(text)

    def test_valid_json(self):
        result = self._parse(VALID_JSON)
        self.assertEqual(result["entities"][0]["name"], "Orivellum Corp")
        self.assertEqual(result["claims"][0]["text"], "Orivellum Corp was founded in 2020.")
        self.assertEqual(result["relationships"][0]["predicate"], "founded in")

    def test_markdown_fenced_json(self):
        result = self._parse(FENCED_JSON)
        self.assertEqual(result["entities"][0]["name"], "Orivellum Corp")

    def test_malformed_json_returns_empty(self):
        result = self._parse("not json at all")
        self.assertEqual(result, {})

    def test_empty_string_returns_empty(self):
        result = self._parse("")
        self.assertEqual(result, {})

    def test_non_dict_json_returns_empty(self):
        result = self._parse("[1, 2, 3]")
        self.assertEqual(result, {})

    def test_partial_keys_are_tolerated(self):
        result = self._parse(PARTIAL_JSON)
        # entities present, no description — name still accessible
        self.assertEqual(result["entities"][0]["name"], "Alice")
        self.assertIsNone(result["entities"][0].get("description"))

    def test_null_fields_handled(self):
        data = json.dumps({"entities": None, "claims": None, "relationships": None})
        result = self._parse(data)
        self.assertIsNone(result.get("entities"))


# ---------------------------------------------------------------------------
# llm_harvest — happy path
# ---------------------------------------------------------------------------

class TestLlmHarvestHappyPath(unittest.TestCase):

    def test_entities_claims_relationships_written_to_db(self):
        from orivellum.capabilities.knowledge_harvest import llm_harvest

        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            result = _make_extraction_result()

            # Pre-create a document record so FK constraints on knowledge.source_doc_id pass
            doc = db.create_document(title="Test Doc", kind="text")
            doc_id = doc["id"]

            # Patch config and httpx so no real network call is made
            mock_cfg = MagicMock()
            mock_cfg.serving.base_url = "http://127.0.0.1:13305/api/v1"
            mock_cfg.serving.workhorse_model = "test-model"

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": VALID_JSON}}]
            }
            mock_response.raise_for_status = MagicMock()

            # get_config is imported inside llm_harvest — patch at the source module
            with patch("orivellum.api._deps.get_config", return_value=mock_cfg), \
                 patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.post.return_value = mock_response
                mock_client_cls.return_value = mock_client

                count = llm_harvest(result, doc_id=doc_id, work_id=None,
                                    doc_title="Test Doc", db=db)

            self.assertGreater(count, 0, "Expected at least one item created")

            items = db.list_knowledge(limit=100)
            kinds = {k["kind"] for k in items}
            self.assertIn("entity", kinds)
            self.assertIn("claim", kinds)
            self.assertIn("relationship", kinds)

            entity = next(k for k in items if k["kind"] == "entity")
            self.assertAlmostEqual(entity["confidence"], 0.85)

            claim = next(k for k in items if k["kind"] == "claim")
            self.assertAlmostEqual(claim["confidence"], 0.80)

            rel = next(k for k in items if k["kind"] == "relationship")
            self.assertAlmostEqual(rel["confidence"], 0.75)

            db.close()


# ---------------------------------------------------------------------------
# llm_harvest — failure resilience
# ---------------------------------------------------------------------------

class TestLlmHarvestResilience(unittest.TestCase):

    def _run_harvest(self, llm_response_content: str | None):
        from orivellum.capabilities.knowledge_harvest import llm_harvest

        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            result = _make_extraction_result()

            mock_cfg = MagicMock()
            mock_cfg.serving.base_url = "http://127.0.0.1:13305/api/v1"
            mock_cfg.serving.workhorse_model = "test-model"

            # get_config is imported inline — patch at the source module
            if llm_response_content is None:
                # Simulate connection error
                with patch("orivellum.api._deps.get_config", return_value=mock_cfg), \
                     patch("httpx.Client") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_client.__enter__ = MagicMock(return_value=mock_client)
                    mock_client.__exit__ = MagicMock(return_value=False)
                    mock_client.post.side_effect = ConnectionError("refused")
                    mock_client_cls.return_value = mock_client

                    count = llm_harvest(result, doc_id="doc-2", work_id=None,
                                       doc_title="Test", db=db)
            else:
                mock_response = MagicMock()
                mock_response.json.return_value = {
                    "choices": [{"message": {"content": llm_response_content}}]
                }
                mock_response.raise_for_status = MagicMock()

                with patch("orivellum.api._deps.get_config", return_value=mock_cfg), \
                     patch("httpx.Client") as mock_client_cls:
                    mock_client = MagicMock()
                    mock_client.__enter__ = MagicMock(return_value=mock_client)
                    mock_client.__exit__ = MagicMock(return_value=False)
                    mock_client.post.return_value = mock_response
                    mock_client_cls.return_value = mock_client

                    count = llm_harvest(result, doc_id="doc-3", work_id=None,
                                       doc_title="Test", db=db)

            db.close()
            return count

    def test_malformed_llm_response_yields_zero_items(self):
        count = self._run_harvest("this is not JSON {{{")
        self.assertEqual(count, 0)

    def test_connection_error_yields_zero_items(self):
        count = self._run_harvest(None)
        self.assertEqual(count, 0)

    def test_empty_llm_response_yields_zero_items(self):
        count = self._run_harvest("{}")
        self.assertEqual(count, 0)


# ---------------------------------------------------------------------------
# Pipeline: AI extraction toggle
# ---------------------------------------------------------------------------

class TestPipelineAiToggle(unittest.TestCase):

    def _run_pipeline(self, ai_enabled: bool):
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)

            # Write a real temp file the pipeline can find
            p = Path(tmp) / "test.txt"
            p.write_text("Orivellum Corp was founded in 2020. This is a test document.", encoding="utf-8")

            # Pre-create the document record so FK constraints are satisfied
            doc = db.create_document(
                title="Test",
                source=str(p),
                kind="text",
                work_id=None,
                content_path=str(p),
            )
            doc_id = doc["id"]

            db.set_setting("ai_extraction_enabled", "true" if ai_enabled else "false")

            llm_harvest_calls = []

            def fake_llm_harvest(*args, **kwargs):
                llm_harvest_calls.append(1)
                return 0

            with patch.object(pipe_module, "llm_harvest", side_effect=fake_llm_harvest):
                pipe_module.process_document(
                    doc_id=doc_id,
                    file_path=str(p),
                    kind="text",
                    work_id=None,
                    title="Test",
                    db=db,
                )

            doc = db.get_document(doc_id)
            readiness = doc["readiness"] if doc else None
            db.close()
            return readiness, len(llm_harvest_calls)

    def test_llm_harvest_skipped_when_disabled(self):
        readiness, llm_calls = self._run_pipeline(ai_enabled=False)
        self.assertEqual(readiness, "ready")
        self.assertEqual(llm_calls, 0, "llm_harvest should not be called when disabled")

    def test_llm_harvest_called_when_enabled(self):
        readiness, llm_calls = self._run_pipeline(ai_enabled=True)
        self.assertEqual(readiness, "ready")
        self.assertEqual(llm_calls, 1, "llm_harvest should be called once when enabled")


# ---------------------------------------------------------------------------
# Pipeline: document reaches ready even when llm_harvest raises
# ---------------------------------------------------------------------------

class TestPipelineReadinessOnLlmFailure(unittest.TestCase):

    def test_document_ready_when_llm_harvest_raises(self):
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            db.set_setting("ai_extraction_enabled", "true")

            p = Path(tmp) / "doc.txt"
            p.write_text("Some content for testing.", encoding="utf-8")

            # Pre-create the document so FK constraints are satisfied
            doc = db.create_document(
                title="Fail Doc",
                source=str(p),
                kind="text",
                work_id=None,
                content_path=str(p),
            )
            doc_id = doc["id"]

            def exploding_harvest(*args, **kwargs):
                raise RuntimeError("LLM service timed out")

            with patch.object(pipe_module, "llm_harvest", side_effect=exploding_harvest):
                pipe_module.process_document(
                    doc_id=doc_id,
                    file_path=str(p),
                    kind="text",
                    work_id=None,
                    title="Fail Doc",
                    db=db,
                )

            doc = db.get_document(doc_id)
            self.assertIsNotNone(doc, "Document record must exist")
            self.assertEqual(doc["readiness"], "ready",
                             "Document must be ready even when llm_harvest raises")
            db.close()


# ---------------------------------------------------------------------------
# Settings API: default and toggle
# ---------------------------------------------------------------------------

class TestSettingsApi(unittest.TestCase):

    def test_default_setting_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            val = db.get_setting("ai_extraction_enabled", "false")
            self.assertEqual(val, "false")
            db.close()

    def test_setting_persists_after_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _make_db(tmp)
            db.set_setting("ai_extraction_enabled", "true")
            self.assertEqual(db.get_setting("ai_extraction_enabled", "false"), "true")
            db.set_setting("ai_extraction_enabled", "false")
            self.assertEqual(db.get_setting("ai_extraction_enabled", "false"), "false")
            db.close()


if __name__ == "__main__":
    unittest.main()
