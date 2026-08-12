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


def _make_extraction_result(pages: list, full_text: str | None = None):
    """Return a minimal ExtractionResult stub for llm_harvest.

    If *full_text* is None, it is synthesised from the page text so stubs
    created without full_text still exercise the stride-based chunker.
    """
    if full_text is None:
        full_text = " ".join(p.text for p in pages)
    return SimpleNamespace(pages=pages, full_text=full_text)


def _make_db_and_cfg(tmp: str):
    """Create a real OrivellumDB and OrivellumConfig wired to *tmp*."""
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

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
        payload = {
            "entities": [{"name": "Alice", "description": ""}],
            "claims": [],
            "relationships": [],
        }
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

_GOOD_LLM_RESPONSE = json.dumps(
    {
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
    }
)


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
        mock_resp.json.return_value = {"choices": [{"message": {"content": llm_text}}]}

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

    # -- gap cache invalidation ------------------------------------------------

    def test_warm_gap_cache_dropped_when_items_created(self):
        """LLM harvest adds knowledge → the Work's cached coverage is stale
        and must be dropped so the next read recomputes (task: keep the book
        coverage report accurate after overnight AI extraction)."""
        work = self.db.create_work(title="Cache Work")
        self.db.cache_work_gaps(
            work["id"], gaps=[{"kind": "test"}], coverage={"overall": {"completeness": 0.42}}
        )

        count = self._run_llm_harvest(_GOOD_LLM_RESPONSE, work_id=work["id"])
        self.assertGreater(count, 0)

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM work_gap_cache WHERE work_id=?", (work["id"],)
            ).fetchone()
        self.assertIsNone(row, "warm gap cache survived an LLM harvest that added items")

    def test_gap_cache_kept_when_nothing_created(self):
        """An LLM harvest that produces no items must not throw away a
        perfectly valid cached coverage result."""
        work = self.db.create_work(title="Cache Work 2")
        self.db.cache_work_gaps(
            work["id"], gaps=[{"kind": "test"}], coverage={"overall": {"completeness": 0.42}}
        )

        count = self._run_llm_harvest(json.dumps({}), work_id=work["id"])
        self.assertEqual(count, 0)

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM work_gap_cache WHERE work_id=?", (work["id"],)
            ).fetchone()
        self.assertIsNotNone(row, "gap cache dropped even though no items were created")

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
                self.assertEqual(
                    r["meta"].get("source"), "llm", f"meta={r['meta']!r} — expected source=llm"
                )

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
            "entities": [
                {"name": None, "description": "Ghost"},
                {"name": "", "description": "Empty"},
            ],
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

        result = _make_extraction_result(pages=[_make_page("Some content.")])
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
                    self.assertEqual(
                        doc["readiness"],
                        "ready",
                        f"toggle={toggle}: readiness={doc['readiness']!r}",
                    )
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
                doc["readiness"],
                "ready",
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

    def _make_doc(self, db, tmp: str, filename: str = "doc.txt", write_content: bool = True):
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
            db.update_document_extracted(
                doc_err["id"], "", 0, readiness="error", error_message="forced error"
            )
            db.add_extraction_warning(doc_err["id"], kind="file_not_found", detail="forced error")

            doc_nt = db.create_document(title="nt.txt", kind="text", work_id=None)
            db.update_document_extracted(
                doc_nt["id"], "", 0, readiness="no_text", error_message="no text"
            )
            db.add_extraction_warning(doc_nt["id"], kind="no_readable_text", detail="no text")

            for doc_id in (doc_err["id"], doc_nt["id"]):
                warnings = db.get_extraction_warnings(doc_id)
                self.assertEqual(len(warnings), 1, f"Expected 1 warning for {doc_id[:8]}")
                self.assertIsNotNone(warnings[0]["kind"])
                self.assertIsNotNone(warnings[0]["detail"])


# ---------------------------------------------------------------------------
# Full-document indexing — confirms caps are removed / raised correctly
# ---------------------------------------------------------------------------


class TestFullDocumentIndexing(unittest.TestCase):
    """Verify that llm_harvest covers the ENTIRE document, not just the first
    few pages.  These tests are the regression guard for the silent truncation
    that limited non-chapter documents to the first 5 pages (~10 k chars).
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db, self.cfg = _make_db_and_cfg(self._tmpdir.name)
        doc = self.db.create_document(title="Long Novel", kind="text")
        self.doc_id = doc["id"]

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    # -- _EXTRACTED_TEXT_CAP is large enough for full novels ------------------

    def test_extracted_text_cap_is_at_least_2_million_chars(self):
        """_EXTRACTED_TEXT_CAP must accommodate novels ≥ 300 k words (~2 M chars)."""
        from orivellum.capabilities.chunking import _EXTRACTED_TEXT_CAP

        self.assertGreaterEqual(
            _EXTRACTED_TEXT_CAP,
            2_000_000,
            f"_EXTRACTED_TEXT_CAP={_EXTRACTED_TEXT_CAP} is too small for full novels; "
            "must be ≥ 2,000,000",
        )

    # -- Chunking strategy: full-text stride covers entire document -----------

    def test_short_document_full_coverage(self):
        """A document shorter than _MAX_HARVEST_CHUNKS × _MAX_CHUNK_CHARS must
        be covered completely — every 2 000-char window is sent."""
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHUNK_CHARS,
            _MAX_HARVEST_CHUNKS,
        )

        # Build a 20 000-char document (10 sequential windows at 2 000 each)
        # Well within the "short doc" threshold
        doc_text = "word " * 4_000  # ~20 000 chars
        self.assertLess(len(doc_text), _MAX_HARVEST_CHUNKS * _MAX_CHUNK_CHARS)

        call_offsets: list[int] = []

        def _capture_llm(prompt: str, *args, **kwargs):
            # Extract the chunk from the prompt; record its start offset in doc
            marker = "Chunk:\n"
            idx = prompt.find(marker)
            if idx != -1:
                chunk = prompt[idx + len(marker) :][:50]
                # Find where this chunk appears in the document
                pos = doc_text.find(chunk[:20])
                call_offsets.append(pos if pos >= 0 else -1)
            return  # empty response — we only care about CALL pattern

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            side_effect=_capture_llm,
        ):
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            result = _make_extraction_result(pages=[], full_text=doc_text)
            llm_harvest(
                result, doc_id=self.doc_id, work_id=None, doc_title="Long Novel", db=self.db
            )

        # Must have called LLM at least once per 2 000-char window
        expected_calls = max(1, len(doc_text) // _MAX_CHUNK_CHARS)
        self.assertGreaterEqual(
            len(call_offsets),
            expected_calls,
            f"Short document: expected ≥{expected_calls} LLM calls, got {len(call_offsets)}",
        )

    def test_long_document_samples_beyond_first_5_pages(self):
        """A long document must produce LLM calls sampling from well past the
        first 10 000 characters — proving the old pages[:5] cap is gone.

        Strategy: build a document where each 2 000-char section is uniquely
        tagged with its section number (SEC_0000, SEC_0001, …).  Record which
        section numbers appear in LLM prompts.  Assert that at least one call
        samples from the second half of the document.
        """
        from orivellum.capabilities.knowledge_harvest import _MAX_CHUNK_CHARS

        # Each section is exactly _MAX_CHUNK_CHARS chars.
        # Build 60 sections so the document clearly exceeds 50-chunk cap.
        n_sections = 60
        section_size = _MAX_CHUNK_CHARS
        # Each section: unique tag + padding to reach exactly section_size chars
        sections: list[str] = []
        for i in range(n_sections):
            tag = f"SEC_{i:04d}_"  # e.g. "SEC_0000_" (9 chars)
            padding = "x" * (section_size - len(tag))
            sections.append(tag + padding)
        doc_text = "".join(sections)

        sampled_sections: set[int] = set()

        def _recording_llm(prompt: str, *args, **kwargs):
            # Find which SEC_NNNN tags appear in the prompt chunk
            import re as _re

            for m in _re.finditer(r"SEC_(\d{4})_", prompt):
                sampled_sections.add(int(m.group(1)))
            return

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            side_effect=_recording_llm,
        ):
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            result = _make_extraction_result(pages=[], full_text=doc_text)
            llm_harvest(
                result, doc_id=self.doc_id, work_id=None, doc_title="Long Novel", db=self.db
            )

        # At least one sampled section must be from the second half (section ≥ 30)
        second_half = {s for s in sampled_sections if s >= n_sections // 2}
        self.assertGreater(
            len(second_half),
            0,
            f"No LLM call sampled from the second half of the document — "
            f"sampled sections: {sorted(sampled_sections)} "
            "(expected at least one from section ≥ 30). "
            "The old pages[:5] cap may still be active.",
        )

    def test_long_document_tail_always_covered(self):
        """Endpoint-inclusive stride: the LAST LLM window must end at the very
        last character of the document (no tail dropped).

        Build a document whose final _MAX_CHUNK_CHARS characters carry a unique
        marker (TAIL_MARKER).  Assert that marker appears in at least one
        LLM prompt.  A plain stride that does not include the final window
        would leave up to (N % stride) characters uncovered and fail this test.
        """
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHUNK_CHARS,
            _MAX_HARVEST_CHUNKS,
        )

        # Document longer than _MAX_HARVEST_CHUNKS * _MAX_CHUNK_CHARS so the
        # endpoint-inclusive interpolation path is exercised (not sequential).
        long_body = "a" * (_MAX_HARVEST_CHUNKS * _MAX_CHUNK_CHARS + 5_000)
        tail_marker = "TAIL_MARKER_XYZ"
        # Pad tail_marker out to exactly _MAX_CHUNK_CHARS chars so it matches
        # the final window precisely.
        tail_window = tail_marker + "b" * (_MAX_CHUNK_CHARS - len(tail_marker))
        doc_text = long_body + tail_window

        tail_seen = [False]

        def _check_tail(prompt: str, *a, **k):
            if tail_marker in prompt:
                tail_seen[0] = True
            return

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            side_effect=_check_tail,
        ):
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            result = _make_extraction_result(pages=[], full_text=doc_text)
            llm_harvest(
                result, doc_id=self.doc_id, work_id=None, doc_title="Long Novel", db=self.db
            )

        self.assertTrue(
            tail_seen[0],
            f"TAIL_MARKER_XYZ was never seen in any LLM prompt — "
            f"the final ~{_MAX_CHUNK_CHARS} chars of the document were dropped. "
            "Endpoint-inclusive stride must include a window that ends at len(text).",
        )

    def test_long_document_call_count_does_not_exceed_max_harvest_chunks(self):
        """Stride-based sampling must never exceed _MAX_HARVEST_CHUNKS LLM calls
        for any document length — prevents runaway API usage on 300 k-word novels."""
        from orivellum.capabilities.knowledge_harvest import _MAX_HARVEST_CHUNKS

        # Simulate a very long document: 1 000 000 chars (~150 k words)
        doc_text = "x" * 1_000_000

        call_count = [0]

        def _count_llm(*args, **kwargs):
            call_count[0] += 1
            return

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            side_effect=_count_llm,
        ):
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            result = _make_extraction_result(pages=[], full_text=doc_text)
            llm_harvest(
                result, doc_id=self.doc_id, work_id=None, doc_title="Long Novel", db=self.db
            )

        self.assertLessEqual(
            call_count[0],
            _MAX_HARVEST_CHUNKS,
            f"LLM was called {call_count[0]} times for a 1 M-char document; "
            f"must not exceed _MAX_HARVEST_CHUNKS={_MAX_HARVEST_CHUNKS}",
        )


# ---------------------------------------------------------------------------
# Chapter-aware harvest — long chapters must be covered beyond 6 000 chars
# ---------------------------------------------------------------------------


class TestLlmHarvestByChapters(unittest.TestCase):
    """llm_harvest_by_chapters must sample content from EVERY part of a chapter,
    not only the first 6 000 characters.  These tests are the regression guard
    for the [:_MAX_CHAPTER_CHARS] slice that silently dropped the remainder of
    any chapter longer than one prompt window.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db, self.cfg = _make_db_and_cfg(self._tmpdir.name)
        doc = self.db.create_document(title="Long Novel", kind="text")
        self.doc_id = doc["id"]

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def _insert_chapters(self, chapters: list[dict]) -> None:
        """Upsert a list of chapters for self.doc_id via the proper DB API.

        Each dict must contain: seq, text, and optionally title.
        Replaces any previously inserted chapters for this document.
        """
        self.db.upsert_book_chapters(
            doc_id=self.doc_id,
            work_id=None,
            chapters=[
                {
                    "seq": ch["seq"],
                    "level": 1,
                    "title": ch.get("title") or f"Chapter {ch['seq'] + 1}",
                    "text": ch["text"],
                }
                for ch in chapters
            ],
        )

    def _insert_chapter(self, seq: int, text: str, title: str = "") -> None:
        """Convenience wrapper for a single chapter."""
        self._insert_chapters([{"seq": seq, "text": text, "title": title}])

    # -- gap cache invalidation (chapter path) ---------------------------------

    _FICTION_RESPONSE = json.dumps(
        {
            "characters": [{"name": "Mara", "role": "protagonist", "description": "A sailor"}],
            "events": [{"text": "Mara sets sail at dawn.", "significance": "major"}],
            "settings": [],
            "relationships": [],
            "themes": [],
            "foreshadowing": [],
        }
    )

    def test_warm_gap_cache_dropped_after_chapter_harvest(self):
        """The chapter-first LLM harvest is the production path for novels —
        it must drop the Work's warm coverage cache when it adds items."""
        from orivellum.capabilities.knowledge_harvest import llm_harvest_by_chapters

        work = self.db.create_work(title="Chapter Cache Work")
        self.db.cache_work_gaps(
            work["id"], gaps=[{"kind": "test"}], coverage={"overall": {"completeness": 0.42}}
        )
        self._insert_chapter(0, "Mara set sail at dawn. " * 40, "Chapter 1")

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync",
            return_value=self._FICTION_RESPONSE,
        ):
            created = llm_harvest_by_chapters(
                doc_id=self.doc_id, work_id=work["id"], doc_title="Test", db=self.db
            )

        self.assertGreater(created, 0)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM work_gap_cache WHERE work_id=?", (work["id"],)
            ).fetchone()
        self.assertIsNone(row, "warm gap cache survived a chapter harvest that added items")

    def test_gap_cache_kept_when_chapter_harvest_creates_nothing(self):
        """A chapter harvest that yields no items must not evict a valid cache."""
        from orivellum.capabilities.knowledge_harvest import llm_harvest_by_chapters

        work = self.db.create_work(title="Chapter Cache Work 2")
        self.db.cache_work_gaps(
            work["id"], gaps=[{"kind": "test"}], coverage={"overall": {"completeness": 0.42}}
        )
        self._insert_chapter(0, "Mara set sail at dawn. " * 40, "Chapter 1")

        with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", return_value=None):
            created = llm_harvest_by_chapters(
                doc_id=self.doc_id, work_id=work["id"], doc_title="Test", db=self.db
            )

        self.assertEqual(created, 0)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM work_gap_cache WHERE work_id=?", (work["id"],)
            ).fetchone()
        self.assertIsNotNone(row, "gap cache dropped though no chapter items were created")

    def test_short_chapter_gets_one_llm_call(self):
        """A chapter shorter than _MAX_CHAPTER_CHARS needs exactly one LLM call."""
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHAPTER_CHARS,
            llm_harvest_by_chapters,
        )

        short_text = "x" * (_MAX_CHAPTER_CHARS - 100)
        self._insert_chapter(0, short_text)

        call_count = [0]

        def _count(*a, **k):
            call_count[0] += 1
            return

        with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_count):
            llm_harvest_by_chapters(doc_id=self.doc_id, work_id=None, doc_title="Test", db=self.db)

        self.assertEqual(
            call_count[0], 1, f"Short chapter expected 1 LLM call, got {call_count[0]}"
        )

    def test_long_chapter_samples_beyond_first_6000_chars(self):
        """A chapter longer than _MAX_CHAPTER_CHARS must produce ≥2 LLM calls,
        covering text beyond the first 6 000 characters.

        Strategy: build a chapter with uniquely-tagged sections (SEC_0000,
        SEC_0001, …).  Assert that at least one LLM call includes a tag from
        the second half of the chapter text.
        """
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHAPTER_CHARS,
            llm_harvest_by_chapters,
        )

        # Build a chapter of 3 × _MAX_CHAPTER_CHARS chars with unique section tags
        n_sections = 3
        sections = []
        for i in range(n_sections):
            tag = f"CHSEC_{i:04d}_"
            padding = "y" * (_MAX_CHAPTER_CHARS - len(tag))
            sections.append(tag + padding)
        chapter_text = "".join(sections)
        self.assertGreater(
            len(chapter_text), _MAX_CHAPTER_CHARS, "Chapter must be longer than one prompt window"
        )

        self._insert_chapter(0, chapter_text, "Chapter 1")

        sampled_sections: set[int] = set()

        def _record_prompt(prompt: str, *a, **k):
            import re as _re

            for m in _re.finditer(r"CHSEC_(\d{4})_", prompt):
                sampled_sections.add(int(m.group(1)))
            return

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_record_prompt
        ):
            llm_harvest_by_chapters(doc_id=self.doc_id, work_id=None, doc_title="Test", db=self.db)

        # Must have seen sections from at least the first AND last parts
        self.assertIn(0, sampled_sections, "Section 0 (first 6k chars) was never sampled")
        second_half = {s for s in sampled_sections if s >= n_sections // 2}
        self.assertGreater(
            len(second_half),
            0,
            f"No LLM call sampled from the second half of the chapter — "
            f"sampled sections: {sorted(sampled_sections)}. "
            "The [:_MAX_CHAPTER_CHARS] truncation may still be active.",
        )

    def test_long_chapter_tail_always_covered(self):
        """Endpoint-inclusive stride: the LAST window for a long chapter must
        end at the very last character — no tail dropped.

        Build a chapter whose final _MAX_CHAPTER_CHARS characters carry a
        unique marker (CH_TAIL_MARKER).  Assert that marker appears in at least
        one LLM prompt.  A plain stride without endpoint inclusion would leave
        the tail uncovered and fail this test.
        """
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHAPTER_CALLS,
            _MAX_CHAPTER_CHARS,
            llm_harvest_by_chapters,
        )

        # Chapter long enough to trigger the endpoint-inclusive path
        long_body = "c" * (_MAX_CHAPTER_CALLS * _MAX_CHAPTER_CHARS + 5_000)
        tail_marker = "CH_TAIL_MARKER_XYZ"
        tail_window = tail_marker + "d" * (_MAX_CHAPTER_CHARS - len(tail_marker))
        chapter_text = long_body + tail_window

        self._insert_chapter(0, chapter_text, "Chapter 1")

        tail_seen = [False]

        def _check_tail(prompt: str, *a, **k):
            if tail_marker in prompt:
                tail_seen[0] = True
            return

        with patch(
            "orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_check_tail
        ):
            llm_harvest_by_chapters(doc_id=self.doc_id, work_id=None, doc_title="Test", db=self.db)

        self.assertTrue(
            tail_seen[0],
            "CH_TAIL_MARKER_XYZ was never seen in any LLM prompt — "
            "the tail of the chapter was dropped. "
            "Endpoint-inclusive stride must include a window ending at len(chapter_text).",
        )

    def test_call_count_per_chapter_respects_max_chapter_calls(self):
        """A very long chapter must not produce more than _MAX_CHAPTER_CALLS
        LLM calls, regardless of its length."""
        from orivellum.capabilities.knowledge_harvest import (
            _MAX_CHAPTER_CALLS,
            _MAX_CHAPTER_CHARS,
            llm_harvest_by_chapters,
        )

        # Chapter longer than _MAX_CHAPTER_CALLS × _MAX_CHAPTER_CHARS
        long_text = "z" * (_MAX_CHAPTER_CALLS * _MAX_CHAPTER_CHARS + 5_000)
        self._insert_chapter(0, long_text)

        call_count = [0]

        def _count(*a, **k):
            call_count[0] += 1
            return

        with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_count):
            llm_harvest_by_chapters(doc_id=self.doc_id, work_id=None, doc_title="Test", db=self.db)

        self.assertLessEqual(
            call_count[0],
            _MAX_CHAPTER_CALLS,
            f"Expected ≤{_MAX_CHAPTER_CALLS} LLM calls for a very long chapter, "
            f"got {call_count[0]}",
        )

    def test_multiple_chapters_all_processed(self):
        """All chapters in a document must be sent to the LLM, not just the first."""
        from orivellum.capabilities.knowledge_harvest import llm_harvest_by_chapters

        n_chapters = 5
        self._insert_chapters(
            [
                {"seq": i, "text": f"Chapter_{i} content here " * 50, "title": f"Ch {i}"}
                for i in range(n_chapters)
            ]
        )

        chapters_seen: set[int] = set()

        def _record(prompt: str, *a, **k):
            import re as _re

            for m in _re.finditer(r"Chapter_(\d+)", prompt):
                chapters_seen.add(int(m.group(1)))
            return

        with patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_record):
            llm_harvest_by_chapters(doc_id=self.doc_id, work_id=None, doc_title="Test", db=self.db)

        self.assertEqual(
            len(chapters_seen),
            n_chapters,
            f"Expected all {n_chapters} chapters to be processed; "
            f"only saw chapters: {sorted(chapters_seen)}",
        )


if __name__ == "__main__":
    unittest.main()
