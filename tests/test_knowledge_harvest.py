"""Tests for AI knowledge extraction: _parse_extraction and llm_harvest.

Covers:
- _parse_extraction: valid JSON, JSON in markdown fences, malformed JSON,
  empty / null fields, non-dict top-level values.
- llm_harvest: entities, claims, and relationships written to the DB with
  correct kind, review_status='ai_auto', and meta={'source': 'llm'}.
- llm_harvest skipped entirely when ai_extraction_enabled setting is 'false'.
- llm_harvest skips a chunk gracefully when the LLM call returns None.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build lightweight stubs
# ---------------------------------------------------------------------------

def _make_page(text: str):
    """Return a minimal page/segment stub."""
    return SimpleNamespace(text=text)


def _make_extraction_result(pages: list):
    """Return a minimal ExtractionResult stub for llm_harvest."""
    return SimpleNamespace(pages=pages)


def _make_db_and_cfg(tmp: str):
    """Create a real OrivellumDB and OrivellumConfig wired to *tmp*."""
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return db, cfg


def _knowledge_rows(db, doc_id: str) -> list[dict]:
    """Return all knowledge rows for *doc_id* as plain dicts."""
    import json as _json
    with db._lock:
        rows = db._conn.execute(
            "SELECT kind, text, subject, predicate, object, confidence, "
            "review_status, meta FROM knowledge WHERE source_doc_id=?",
            (doc_id,),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if d.get("meta"):
            try:
                d["meta"] = _json.loads(d["meta"])
            except Exception:
                pass
        result.append(d)
    return result


def _set_toggle(db, value: str) -> None:
    """Set ai_extraction_enabled in the settings table."""
    db.set_setting("ai_extraction_enabled", value)


# ---------------------------------------------------------------------------
# Unit tests — _parse_extraction
# ---------------------------------------------------------------------------

class TestParseExtraction(unittest.TestCase):
    """_parse_extraction must handle all LLM response shapes gracefully."""

    def _parse(self, raw: str) -> dict:
        from orivellum.capabilities.knowledge_harvest import _parse_extraction
        return _parse_extraction(raw)

    # -- happy path ----------------------------------------------------------

    def test_valid_json(self):
        payload = {
            "entities": [{"name": "Orivellum", "description": "A knowledge tool"}],
            "claims": [{"text": "Orivellum is powerful."}],
            "relationships": [{"subject": "Orivellum", "predicate": "is", "object": "tool"}],
        }
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["entities"][0]["name"], "Orivellum")
        self.assertEqual(result["claims"][0]["text"], "Orivellum is powerful.")
        self.assertEqual(result["relationships"][0]["predicate"], "is")

    def test_json_with_leading_trailing_whitespace(self):
        payload = {"entities": [], "claims": [], "relationships": []}
        result = self._parse("   " + json.dumps(payload) + "\n\n")
        self.assertIsInstance(result, dict)

    # -- markdown fence variants ---------------------------------------------

    def test_json_in_triple_backtick_fence(self):
        payload = {"entities": [{"name": "Alice", "description": ""}], "claims": [], "relationships": []}
        raw = "```\n" + json.dumps(payload) + "\n```"
        result = self._parse(raw)
        self.assertEqual(result["entities"][0]["name"], "Alice")

    def test_json_in_json_labelled_fence(self):
        payload = {"entities": [], "claims": [{"text": "Test claim."}], "relationships": []}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        result = self._parse(raw)
        self.assertEqual(result["claims"][0]["text"], "Test claim.")

    def test_json_fence_without_closing_backticks(self):
        """Opening fence but no closing fence — inner lines still parsed."""
        payload = {"entities": [], "claims": [], "relationships": []}
        raw = "```json\n" + json.dumps(payload)
        result = self._parse(raw)
        self.assertIsInstance(result, dict)

    # -- error / edge cases --------------------------------------------------

    def test_malformed_json_returns_empty_dict(self):
        result = self._parse("{not valid json}")
        self.assertEqual(result, {})

    def test_empty_string_returns_empty_dict(self):
        result = self._parse("")
        self.assertEqual(result, {})

    def test_json_array_top_level_returns_empty_dict(self):
        """LLM returned a JSON array instead of object — must not crash."""
        result = self._parse("[1, 2, 3]")
        self.assertEqual(result, {})

    def test_null_json_returns_empty_dict(self):
        result = self._parse("null")
        self.assertEqual(result, {})

    def test_plain_text_returns_empty_dict(self):
        result = self._parse("Sorry, I cannot extract anything.")
        self.assertEqual(result, {})

    def test_empty_lists_in_fields(self):
        """All fields present but empty — valid, not an error."""
        payload = {"entities": [], "claims": [], "relationships": []}
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["relationships"], [])

    def test_null_fields_in_entity(self):
        """Entity with null name should be handled without crashing by caller."""
        payload = {
            "entities": [{"name": None, "description": None}],
            "claims": [],
            "relationships": [],
        }
        result = self._parse(json.dumps(payload))
        # _parse_extraction just returns the dict; null handling is in llm_harvest
        self.assertIsNone(result["entities"][0]["name"])

    def test_missing_optional_keys_in_entity(self):
        """Entity missing 'description' key — must not crash."""
        payload = {"entities": [{"name": "Bob"}], "claims": [], "relationships": []}
        result = self._parse(json.dumps(payload))
        self.assertEqual(result["entities"][0]["name"], "Bob")
        self.assertNotIn("description", result["entities"][0])


# ---------------------------------------------------------------------------
# Integration tests — llm_harvest writes to the DB
# ---------------------------------------------------------------------------

_GOOD_LLM_RESPONSE = json.dumps({
    "entities": [
        {"name": "Orivellum", "description": "A knowledge management platform"},
        {"name": "Alice", "description": "Lead researcher"},
    ],
    "claims": [
        {"text": "Orivellum improves productivity."},
        {"text": "Alice designed the extraction pipeline."},
    ],
    "relationships": [
        {"subject": "Orivellum", "predicate": "built by", "object": "Alice"},
    ],
})


class TestLlmHarvestDBWrites(unittest.TestCase):
    """llm_harvest must write entities, claims, and relationships to the DB."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db, self.cfg = _make_db_and_cfg(self._tmpdir.name)

        # Create a minimal document record so source_doc_id FK references are valid
        doc = self.db.create_document(title="Test Document", kind="text")
        self.doc_id = doc["id"]

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def _run_llm_harvest(self, llm_text: str, work_id=None):
        """Patch httpx.Client so _call_llm_sync returns *llm_text* without a network call."""
        from orivellum.capabilities.knowledge_harvest import llm_harvest

        result = _make_extraction_result(
            pages=[_make_page("Orivellum is a knowledge platform built by Alice.")]
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": llm_text}}]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_client):
            count = llm_harvest(
                result,
                doc_id=self.doc_id,
                work_id=work_id,
                doc_title="Test Document",
                db=self.db,
            )
        return count

    # -- entities written correctly ------------------------------------------

    def test_entity_rows_created(self):
        count = self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        entity_rows = [r for r in rows if r["kind"] == "entity"]
        self.assertEqual(len(entity_rows), 2, f"Expected 2 entities, got {entity_rows}")

    def test_entity_text_contains_name_and_description(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        entity_texts = [r["text"] for r in rows if r["kind"] == "entity"]
        self.assertTrue(
            any("Orivellum" in t and "knowledge management platform" in t for t in entity_texts),
            f"Entity text not found in {entity_texts}",
        )

    def test_entity_review_status_is_ai_auto(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        for r in rows:
            if r["kind"] == "entity":
                self.assertEqual(r["review_status"], "ai_auto")

    def test_entity_meta_source_is_llm(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        for r in rows:
            if r["kind"] == "entity":
                self.assertEqual(r["meta"].get("source"), "llm",
                                 f"meta={r['meta']!r} — expected source=llm")

    # -- claims written correctly --------------------------------------------

    def test_claim_rows_created(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        claim_rows = [r for r in rows if r["kind"] == "claim"]
        self.assertEqual(len(claim_rows), 2)

    def test_claim_text_matches_llm_output(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        claim_texts = [r["text"] for r in rows if r["kind"] == "claim"]
        self.assertIn("Orivellum improves productivity.", claim_texts)

    def test_claim_review_status_is_ai_auto(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        for r in rows:
            if r["kind"] == "claim":
                self.assertEqual(r["review_status"], "ai_auto")

    # -- relationships written correctly -------------------------------------

    def test_relationship_rows_created(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        rel_rows = [r for r in rows if r["kind"] == "relationship"]
        self.assertEqual(len(rel_rows), 1)

    def test_relationship_text_is_triple(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        rel_rows = [r for r in rows if r["kind"] == "relationship"]
        self.assertEqual(rel_rows[0]["text"], "Orivellum built by Alice")

    def test_relationship_subject_predicate_object_columns(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        rel = next(r for r in rows if r["kind"] == "relationship")
        self.assertEqual(rel["subject"], "Orivellum")
        self.assertEqual(rel["predicate"], "built by")
        self.assertEqual(rel["object"], "Alice")

    def test_relationship_review_status_is_ai_auto(self):
        self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        for r in rows:
            if r["kind"] == "relationship":
                self.assertEqual(r["review_status"], "ai_auto")

    # -- return value --------------------------------------------------------

    def test_return_count_matches_items_written(self):
        count = self._run_llm_harvest(_GOOD_LLM_RESPONSE)
        rows = _knowledge_rows(self.db, self.doc_id)
        ai_rows = [r for r in rows if r["review_status"] == "ai_auto"]
        self.assertEqual(count, len(ai_rows))

    # -- degenerate LLM responses --------------------------------------------

    def test_malformed_json_produces_zero_items(self):
        count = self._run_llm_harvest("{not valid}")
        rows = _knowledge_rows(self.db, self.doc_id)
        ai_rows = [r for r in rows if r["review_status"] == "ai_auto"]
        self.assertEqual(count, 0)
        self.assertEqual(len(ai_rows), 0)

    def test_empty_lists_produce_zero_items(self):
        count = self._run_llm_harvest(
            json.dumps({"entities": [], "claims": [], "relationships": []})
        )
        self.assertEqual(count, 0)

    def test_entity_with_null_name_skipped(self):
        """Entities where name is null/empty must be silently dropped."""
        payload = {
            "entities": [{"name": None, "description": "Ghost"}, {"name": "", "description": "Empty"}],
            "claims": [],
            "relationships": [],
        }
        count = self._run_llm_harvest(json.dumps(payload))
        rows = _knowledge_rows(self.db, self.doc_id)
        entity_rows = [r for r in rows if r["kind"] == "entity"]
        self.assertEqual(len(entity_rows), 0)
        self.assertEqual(count, 0)

    def test_relationship_missing_fields_skipped(self):
        """Relationships without all three fields must be skipped."""
        payload = {
            "entities": [],
            "claims": [],
            "relationships": [
                {"subject": "A", "predicate": "relates to"},  # missing object
                {"subject": "", "predicate": "is", "object": "B"},  # empty subject
            ],
        }
        count = self._run_llm_harvest(json.dumps(payload))
        rows = _knowledge_rows(self.db, self.doc_id)
        rel_rows = [r for r in rows if r["kind"] == "relationship"]
        self.assertEqual(len(rel_rows), 0)
        self.assertEqual(count, 0)

    def test_markdown_fenced_response_is_parsed_correctly(self):
        """llm_harvest must strip markdown fences before parsing."""
        fenced = "```json\n" + _GOOD_LLM_RESPONSE + "\n```"
        count = self._run_llm_harvest(fenced)
        self.assertGreater(count, 0, "Fenced JSON must still produce knowledge items")

    def test_llm_returning_none_produces_zero_items(self):
        """When _call_llm_sync returns None (network error etc.), no items are written."""
        from orivellum.capabilities.knowledge_harvest import llm_harvest
        result = _make_extraction_result(
            pages=[_make_page("Some content.")]
        )
        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            return_value=None,
        ):
            count = llm_harvest(
                result,
                doc_id=self.doc_id,
                work_id=None,
                doc_title="Test Document",
                db=self.db,
            )
        self.assertEqual(count, 0)
        rows = _knowledge_rows(self.db, self.doc_id)
        ai_rows = [r for r in rows if r["review_status"] == "ai_auto"]
        self.assertEqual(len(ai_rows), 0)


# ---------------------------------------------------------------------------
# Toggle gate — ai_extraction_enabled
# ---------------------------------------------------------------------------

class TestAiExtractionToggle(unittest.TestCase):
    """pipeline.process_document must skip llm_harvest when toggle is false."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db, self.cfg = _make_db_and_cfg(self._tmpdir.name)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def _run_process_document(self, text: str, toggle_value: str):
        """Write *text* as a plain-text file, call process_document, return mock_llm."""
        from orivellum.capabilities.pipeline import process_document

        # Write the file
        file_path = Path(self._tmpdir.name) / "toggle_test.txt"
        file_path.write_text(text, encoding="utf-8")

        # Create a DB document record using the proper API
        doc = self.db.create_document(title="Toggle Test Doc", kind="text")
        doc_id = doc["id"]

        # Set the toggle via the proper settings API
        _set_toggle(self.db, toggle_value)

        # Patch llm_harvest so we can track calls without a real LLM
        with patch(
            "orivellum.capabilities.pipeline.llm_harvest",
            wraps=None,
            return_value=0,
        ) as mock_llm:
            process_document(
                doc_id=doc_id,
                file_path=str(file_path),
                kind="text",
                work_id=None,
                title="Toggle Test Doc",
                db=self.db,
            )
            return mock_llm

    def test_llm_harvest_skipped_when_toggle_false(self):
        """With ai_extraction_enabled=false, llm_harvest must never be called."""
        mock_llm = self._run_process_document(
            text="This is a long enough sentence for extraction to work properly here.",
            toggle_value="false",
        )
        mock_llm.assert_not_called()

    def test_llm_harvest_called_when_toggle_true(self):
        """With ai_extraction_enabled=true, llm_harvest must be invoked."""
        mock_llm = self._run_process_document(
            text="This is a long enough sentence for extraction to work properly here.",
            toggle_value="true",
        )
        mock_llm.assert_called_once()

    def test_document_is_ready_regardless_of_toggle(self):
        """Document readiness must be 'ready' whether llm_harvest runs or not."""
        from orivellum.capabilities.pipeline import process_document

        for toggle in ("true", "false"):
            with self.subTest(toggle=toggle):
                tmpdir = tempfile.TemporaryDirectory()
                db, _ = _make_db_and_cfg(tmpdir.name)
                try:
                    file_path = Path(tmpdir.name) / "doc.txt"
                    file_path.write_text(
                        "A sufficiently long sentence that qualifies for extraction.",
                        encoding="utf-8",
                    )
                    doc = db.create_document(title="Ready Test", kind="text")
                    doc_id = doc["id"]
                    _set_toggle(db, toggle)

                    with patch("orivellum.capabilities.pipeline.llm_harvest", return_value=0):
                        process_document(doc_id, str(file_path), "text", None, "Ready Test", db)

                    doc = db.get_document(doc_id)
                    self.assertEqual(doc["readiness"], "ready",
                                     f"toggle={toggle}: readiness={doc['readiness']!r}")
                finally:
                    db.close()
                    tmpdir.cleanup()


# ---------------------------------------------------------------------------
# Pipeline: document reaches ready even when llm_harvest raises
# ---------------------------------------------------------------------------

class TestPipelineReadinessOnLlmFailure(unittest.TestCase):
    """process_document must leave the document 'ready' even when llm_harvest raises."""

    def test_document_ready_when_llm_harvest_raises(self):
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            db.set_setting("ai_extraction_enabled", "true")

            p = Path(tmp) / "doc.txt"
            p.write_text("Some content for testing LLM failure isolation.", encoding="utf-8")

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
            self.assertIsNotNone(doc, "Document record must exist after llm_harvest failure")
            self.assertEqual(
                doc["readiness"], "ready",
                f"Document must be ready even when llm_harvest raises; "
                f"readiness={doc['readiness']!r}, error_message={doc.get('error_message')!r}",
            )
            db.close()


# ---------------------------------------------------------------------------
# Settings API: default and toggle persistence
# ---------------------------------------------------------------------------

class TestSettingsApi(unittest.TestCase):
    """get_setting / set_setting must behave correctly for the AI toggle."""

    def test_default_setting_is_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            val = db.get_setting("ai_extraction_enabled", "false")
            self.assertEqual(val, "false")
            db.close()

    def test_setting_persists_after_toggle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            db.set_setting("ai_extraction_enabled", "true")
            self.assertEqual(db.get_setting("ai_extraction_enabled", "false"), "true")
            db.set_setting("ai_extraction_enabled", "false")
            self.assertEqual(db.get_setting("ai_extraction_enabled", "false"), "false")
            db.close()


# ---------------------------------------------------------------------------
# Extraction warnings: persisted at each pipeline failure point
# ---------------------------------------------------------------------------

class TestExtractionWarnings(unittest.TestCase):
    """add_extraction_warning is called and persisted for every failure path."""

    def _make_doc(self, db, tmp: str, filename: str = "doc.txt",
                  write_content: bool = True):
        p = Path(tmp) / filename
        if write_content:
            p.write_text("Some content for testing.", encoding="utf-8")
        doc = db.create_document(
            title=filename,
            source=str(p),
            kind="text",
            work_id=None,
            content_path=str(p),
        )
        return doc["id"], p

    def test_file_not_found_stores_warning(self):
        """A missing file produces a file_not_found warning and readiness=error."""
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            doc_id, p = self._make_doc(db, tmp, write_content=False)

            pipe_module.process_document(
                doc_id=doc_id,
                file_path=str(p),  # file was never written
                kind="text",
                work_id=None,
                title="Missing",
                db=db,
            )

            doc = db.get_document(doc_id)
            self.assertEqual(doc["readiness"], "error")

            warnings = db.get_extraction_warnings(doc_id)
            self.assertEqual(len(warnings), 1, "Exactly one warning expected")
            self.assertEqual(warnings[0]["kind"], "file_not_found")
            self.assertIn(str(p), warnings[0]["detail"])
            db.close()

    def test_no_readable_text_stores_warning(self):
        """When extraction returns ok=False, a no_readable_text warning is stored."""
        from unittest.mock import MagicMock as _MM
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            doc_id, p = self._make_doc(db, tmp)

            failed_result = _MM()
            failed_result.ok = False

            with patch.object(pipe_module, "extract", return_value=failed_result):
                pipe_module.process_document(
                    doc_id=doc_id,
                    file_path=str(p),
                    kind="text",
                    work_id=None,
                    title="Empty",
                    db=db,
                )

            doc = db.get_document(doc_id)
            self.assertEqual(doc["readiness"], "no_text")

            warnings = db.get_extraction_warnings(doc_id)
            self.assertEqual(len(warnings), 1, "Exactly one warning expected")
            self.assertEqual(warnings[0]["kind"], "no_readable_text")
            db.close()

    def test_pipeline_exception_stores_warning(self):
        """An unexpected exception in the pipeline stores a pipeline_exception warning."""
        from orivellum.capabilities import pipeline as pipe_module

        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)
            doc_id, p = self._make_doc(db, tmp)

            def bad_extract(*args, **kwargs):
                raise RuntimeError("Corrupt file header")

            with patch.object(pipe_module, "extract", side_effect=bad_extract):
                pipe_module.process_document(
                    doc_id=doc_id,
                    file_path=str(p),
                    kind="text",
                    work_id=None,
                    title="Corrupt",
                    db=db,
                )

            doc = db.get_document(doc_id)
            self.assertEqual(doc["readiness"], "error")

            warnings = db.get_extraction_warnings(doc_id)
            self.assertEqual(len(warnings), 1, "Exactly one warning expected")
            self.assertEqual(warnings[0]["kind"], "pipeline_exception")
            self.assertIn("RuntimeError", warnings[0]["detail"])
            db.close()

    def test_warnings_present_for_error_and_no_text_docs(self):
        """get_extraction_warnings returns rows for both error and no_text docs."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _ = _make_db_and_cfg(tmp)

            doc_err = db.create_document(title="err.txt", kind="text", work_id=None)
            db.update_document_extracted(doc_err["id"], "", 0,
                                         readiness="error",
                                         error_message="forced error")
            db.add_extraction_warning(doc_err["id"], kind="file_not_found",
                                      detail="forced error")

            doc_nt = db.create_document(title="nt.txt", kind="text", work_id=None)
            db.update_document_extracted(doc_nt["id"], "", 0,
                                         readiness="no_text",
                                         error_message="no text")
            db.add_extraction_warning(doc_nt["id"], kind="no_readable_text",
                                      detail="no text")

            for doc_id in (doc_err["id"], doc_nt["id"]):
                warnings = db.get_extraction_warnings(doc_id)
                self.assertEqual(len(warnings), 1,
                                 f"Expected 1 warning for {doc_id[:8]}")
                self.assertIsNotNone(warnings[0]["kind"])
                self.assertIsNotNone(warnings[0]["detail"])

            db.close()


if __name__ == "__main__":
    unittest.main()
