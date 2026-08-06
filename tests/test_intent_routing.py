"""Integration tests for intent routing — classify, dispatch, tool failures, persistence."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ─── Helper: minimal in-memory DB ────────────────────────────────────────────

def _make_db(tmp_path: str) -> "OrivellumDB":
    from orivellum.database.db import OrivellumDB
    return OrivellumDB(tmp_path)


# ─── Intent classifier ────────────────────────────────────────────────────────

class TestIntentClassifier(unittest.TestCase):

    def test_fast_path_remember(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("remember that I prefer APA citations", "http://x", "m")
        self.assertEqual(r["intent"], "remember")

    def test_fast_path_remember_my_name(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("my name is Alice", "http://x", "m")
        self.assertEqual(r["intent"], "remember")

    def test_fast_path_weather_returns_location(self):
        from orivellum.capabilities.intent import classify_intent, _extract_weather_location
        # Location extraction without LLM
        loc = _extract_weather_location("what's the weather in London?")
        self.assertIsNotNone(loc)
        self.assertIn("London", loc)

    def test_fast_path_weather_for_city(self):
        from orivellum.capabilities.intent import _extract_weather_location
        loc = _extract_weather_location("weather in New York today")
        self.assertIsNotNone(loc)
        self.assertIn("New York", loc)

    def test_fast_path_image_gen(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("generate an image of a mountain at dusk", "http://x", "m")
        self.assertEqual(r["intent"], "image_gen")

    def test_fast_path_web_search(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("search for recent papers on attention mechanisms", "http://x", "m")
        self.assertEqual(r["intent"], "web_search")

    def test_llm_failure_falls_back_to_chat(self):
        """Classifier must return 'chat' when the LLM call fails."""
        from orivellum.capabilities.intent import classify_intent
        # No pattern match + LLM unavailable
        with patch("orivellum.capabilities.cognition._call_sync", side_effect=Exception("offline")):
            r = classify_intent("explain quantum entanglement simply", "http://bad", "m")
        self.assertEqual(r["intent"], "chat")

    def test_llm_bad_json_falls_back_to_chat(self):
        from orivellum.capabilities.intent import classify_intent
        with patch("orivellum.capabilities.cognition._call_sync", return_value="not json at all"):
            r = classify_intent("tell me about black holes", "http://x", "m")
        self.assertEqual(r["intent"], "chat")

    def test_llm_unknown_intent_coerced_to_chat(self):
        from orivellum.capabilities.intent import classify_intent
        bad = json.dumps({"intent": "hacking", "query": "x", "location": None})
        with patch("orivellum.capabilities.cognition._call_sync", return_value=bad):
            r = classify_intent("do something weird", "http://x", "m")
        self.assertEqual(r["intent"], "chat")


# ─── Web search ───────────────────────────────────────────────────────────────

class TestWebSearch(unittest.TestCase):
    """Tests use the real DDG Instant Answers JSON format (mocked)."""

    # Realistic DDG Instant Answers API response for an abstract query
    _DDG_ABSTRACT = json.dumps({
        "Heading":     "Attention (machine learning)",
        "AbstractText": "Attention mechanisms allow neural networks to focus on relevant parts of inputs.",
        "AbstractURL": "https://en.wikipedia.org/wiki/Attention_(machine_learning)",
        "RelatedTopics": [
            {
                "Text":     "Transformer (machine learning model) — Architecture using self-attention.",
                "FirstURL": "https://en.wikipedia.org/wiki/Transformer_(machine_learning_model)",
            },
            {
                "Text":     "BERT (language model) — Pre-trained attention-based model by Google.",
                "FirstURL": "https://en.wikipedia.org/wiki/BERT_(language_model)",
            },
        ],
        "Answer": "",
        "AnswerType": "",
        "Definition": "",
        "DefinitionURL": "",
    })

    # Empty response (no instant answer available for this query)
    _DDG_EMPTY = json.dumps({
        "Heading": "", "AbstractText": "", "AbstractURL": "",
        "RelatedTopics": [], "Answer": "", "AnswerType": "",
        "Definition": "", "DefinitionURL": "",
    })

    def _mock_urlopen(self, body: str):
        mock_resp = MagicMock()
        mock_resp.read.return_value = body.encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_markdown_with_abstract_result(self):
        """Tavily results are formatted as a numbered markdown block."""
        from orivellum.capabilities.websearch import web_search
        tavily_resp = json.dumps({
            "results": [
                {
                    "title": "Attention (machine learning)",
                    "url": "https://en.wikipedia.org/wiki/Attention_(machine_learning)",
                    "content": "Attention is a mechanism in neural networks. Transformer models use it extensively.",
                    "score": 0.95,
                },
                {
                    "title": "Transformer architecture",
                    "url": "https://en.wikipedia.org/wiki/Transformer_(machine_learning_model)",
                    "content": "The Transformer uses self-attention to process sequences in parallel.",
                    "score": 0.88,
                },
            ]
        })
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(tavily_resp)):
            result = web_search("attention mechanisms")

        self.assertIn("🌐", result)
        self.assertIn("Attention (machine learning)", result)
        self.assertIn("wikipedia.org", result)
        self.assertIn("Transformer", result)

    def test_returns_all_related_topics(self):
        """Multiple Tavily results each appear as numbered markdown entries."""
        from orivellum.capabilities.websearch import web_search
        tavily_resp = json.dumps({
            "results": [
                {
                    "title": "Attention mechanisms overview",
                    "url": "https://example.com/attention",
                    "content": "Attention in deep learning allows models to focus on relevant parts.",
                    "score": 0.9,
                },
                {
                    "title": "Self-attention explained",
                    "url": "https://example.com/self-attention",
                    "content": "Self-attention relates different positions of a single sequence.",
                    "score": 0.8,
                },
            ]
        })
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(tavily_resp)):
            result = web_search("attention mechanisms")
        # Numbered entries use **[1]** format
        self.assertGreaterEqual(result.count("**[1]"), 1)

    def test_graceful_no_results_includes_direct_link(self):
        """When Tavily returns no results, user gets a DuckDuckGo fallback link."""
        from orivellum.capabilities.websearch import web_search
        empty_resp = json.dumps({"results": []})
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(empty_resp)):
            result = web_search("recent papers on quantum computing 2025")

        self.assertIn("🌐", result)
        self.assertIn("duckduckgo.com", result)
        self.assertIsInstance(result, str)
        # Must not claim success when no results returned
        self.assertNotIn("**[1]", result)

    def test_graceful_network_failure(self):
        """API failure must return a user-visible string, never raise."""
        from orivellum.capabilities.websearch import web_search
        with patch("urllib.request.urlopen", side_effect=Exception("connection refused")):
            result = web_search("anything")

        self.assertIn("🌐", result)
        self.assertIn("duckduckgo.com", result)
        self.assertIsInstance(result, str)

    def test_tavily_results_numbered(self):
        """Tavily results are formatted as **[N] [title](url)** entries."""
        from orivellum.capabilities.websearch import web_search
        tavily_resp = json.dumps({
            "results": [
                {
                    "title": "42 miles to kilometres",
                    "url": "https://www.unitconverters.net/length/miles-to-km.htm",
                    "content": "42 miles = 67.59 kilometres. Use this converter for distance.",
                    "score": 0.99,
                },
            ]
        })
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(tavily_resp)):
            result = web_search("42 miles in km")

        self.assertIn("**[1]", result)
        self.assertIn("42 miles", result)


# ─── Weather ─────────────────────────────────────────────────────────────────

class TestWeather(unittest.TestCase):

    def test_returns_card_for_known_city(self):
        from orivellum.capabilities.weather import get_weather

        geo_data = {"results": [{"latitude": 51.5, "longitude": -0.12, "name": "London",
                                  "country_code": "GB"}]}
        wx_data = {
            "current_weather": {"temperature": 15.3, "windspeed": 12.0,
                                 "winddirection": 270, "weathercode": 2},
            "hourly": {"relativehumidity_2m": [65], "apparent_temperature": [13.1]},
        }

        def fake_fetch(url, params):
            if "geocoding" in url:
                return geo_data
            return wx_data

        with patch("orivellum.capabilities.weather._fetch_json", side_effect=fake_fetch):
            result = get_weather("London")

        self.assertIn("📍", result)
        self.assertIn("London", result)
        self.assertIn("15", result)  # temperature

    def test_graceful_error_missing_location(self):
        from orivellum.capabilities.weather import get_weather
        result = get_weather("")
        self.assertIn("📍", result)
        self.assertIsInstance(result, str)

    def test_graceful_error_geocoding_failure(self):
        from orivellum.capabilities.weather import get_weather
        with patch("orivellum.capabilities.weather._geocode", return_value=None):
            result = get_weather("NoSuchPlace XYZ")
        self.assertIn("📍", result)
        self.assertNotIn("Traceback", result)

    def test_graceful_error_weather_fetch_failure(self):
        from orivellum.capabilities.weather import get_weather

        geo = {"latitude": 51.5, "longitude": -0.12, "name": "London", "country_code": "GB"}
        with patch("orivellum.capabilities.weather._geocode", return_value=geo):
            with patch("orivellum.capabilities.weather._fetch_json", side_effect=Exception("network")):
                result = get_weather("London")
        self.assertIn("📍", result)
        self.assertIsInstance(result, str)


# ─── Remember tool ────────────────────────────────────────────────────────────

class TestRemember(unittest.TestCase):

    def _db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        db = _make_db(path)
        return db

    def test_stores_fact_and_returns_confirmation(self):
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        llm_reply = json.dumps({"key": "citation_style", "value": "APA"})
        with patch("orivellum.capabilities.cognition._call_sync", return_value=llm_reply):
            result = _handle_remember(db, "remember that I prefer APA citations", "http://x", "m")

        self.assertIn("📌", result)
        self.assertIn("Remembered", result)
        self.assertIn("APA", result)

        # Fact must be persisted
        with db._lock:
            row = db._conn.execute("SELECT value FROM user_memory WHERE key='citation_style'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "APA")

    def test_honest_failure_when_nothing_extracted(self):
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        llm_reply = json.dumps({"key": None, "value": None})
        with patch("orivellum.capabilities.cognition._call_sync", return_value=llm_reply):
            result = _handle_remember(db, "hello world", "http://x", "m")

        self.assertIn("Nothing stored", result)
        self.assertNotIn("Remembered", result)

        # DB must be empty — no false write
        with db._lock:
            count = db._conn.execute("SELECT COUNT(*) FROM user_memory").fetchone()[0]
        self.assertEqual(count, 0)

    def test_honest_failure_when_llm_offline(self):
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        with patch("orivellum.capabilities.cognition._call_sync", side_effect=Exception("offline")):
            result = _handle_remember(db, "remember my birthday is June 5", "http://x", "m")

        self.assertIn("Could not save", result)
        self.assertNotIn("Remembered", result)

        with db._lock:
            count = db._conn.execute("SELECT COUNT(*) FROM user_memory").fetchone()[0]
        self.assertEqual(count, 0)

    def test_upsert_updates_existing_key(self):
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        for value in ("APA", "MLA"):
            llm_reply = json.dumps({"key": "citation_style", "value": value})
            with patch("orivellum.capabilities.cognition._call_sync", return_value=llm_reply):
                _handle_remember(db, f"I prefer {value}", "http://x", "m")

        with db._lock:
            row = db._conn.execute("SELECT value FROM user_memory WHERE key='citation_style'").fetchone()
        self.assertEqual(row[0], "MLA")  # latest wins

    def test_my_name_is_stores_fact(self):
        """'my name is X' must go through _handle_remember and write only once."""
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        llm_reply = json.dumps({"key": "user_name", "value": "Alice"})
        with patch("orivellum.capabilities.cognition._call_sync", return_value=llm_reply):
            result = _handle_remember(db, "my name is Alice", "http://x", "m")

        self.assertIn("Remembered", result)
        with db._lock:
            row = db._conn.execute("SELECT value FROM user_memory WHERE key='user_name'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Alice")

    def test_i_prefer_failure_leaves_db_empty(self):
        """'I prefer X' with LLM offline must NOT write anything (honest failure)."""
        from orivellum.api.routes.conversations import _handle_remember
        db = self._db()

        with patch("orivellum.capabilities.cognition._call_sync", side_effect=Exception("offline")):
            result = _handle_remember(db, "I prefer APA citations", "http://x", "m")

        self.assertIn("Could not save", result)
        self.assertNotIn("Remembered", result)
        with db._lock:
            count = db._conn.execute("SELECT COUNT(*) FROM user_memory").fetchone()[0]
        self.assertEqual(count, 0)


class TestExplicitRememberSuppression(unittest.TestCase):
    """Verify that _EXPLICIT_REMEMBER_RE covers all phrases routed to remember intent."""

    def _matches(self, text: str) -> bool:
        from orivellum.api.routes.conversations import _EXPLICIT_REMEMBER_RE
        return bool(_EXPLICIT_REMEMBER_RE.search(text))

    def test_remember_that(self):
        self.assertTrue(self._matches("remember that I prefer APA"))

    def test_remember_my(self):
        self.assertTrue(self._matches("remember my birthday please"))

    def test_my_name_is(self):
        self.assertTrue(self._matches("my name is Alice"))

    def test_i_prefer(self):
        self.assertTrue(self._matches("I prefer dark mode"))

    def test_i_like(self):
        self.assertTrue(self._matches("I like concise answers"))

    def test_i_dislike(self):
        self.assertTrue(self._matches("I dislike verbose responses"))

    def test_my_email(self):
        self.assertTrue(self._matches("my email is alice@example.com"))

    def test_my_phone(self):
        self.assertTrue(self._matches("my phone is 555-1234"))

    def test_unrelated_does_not_match(self):
        self.assertFalse(self._matches("what is the weather in Paris?"))
        self.assertFalse(self._matches("explain quantum entanglement"))
        self.assertFalse(self._matches("search for recent AI papers"))


# ─── Intent dispatch (meta) ───────────────────────────────────────────────────

class TestIntentDispatch(unittest.TestCase):
    """Light tests that _maybe_dispatch_intent returns (text, meta) with correct intent."""

    def _db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        return _make_db(path)

    def _run(self, coro):
        import asyncio
        # Use a fresh event loop rather than get_event_loop(): other test
        # modules (e.g. anyio-marked async tests) may have closed the global
        # loop, which would otherwise make run_until_complete fail here.
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_chat_intent_returns_none(self):
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        db = self._db()
        classification = {"intent": "chat", "query": "hello", "location": None}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            result = self._run(_maybe_dispatch_intent(db, "hello", "http://x", "m"))
        self.assertIsNone(result)

    def test_weather_intent_returns_meta(self):
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        db = self._db()
        classification = {"intent": "weather", "query": "weather in Paris", "location": "Paris"}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            with patch("orivellum.capabilities.weather.get_weather", return_value="📍 Paris: 20°C"):
                result = self._run(_maybe_dispatch_intent(db, "weather in Paris", "http://x", "m"))
        self.assertIsNotNone(result)
        text, meta = result
        self.assertEqual(meta["intent"], "weather")
        self.assertIn("Paris", text)

    def test_web_search_intent_returns_meta(self):
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        db = self._db()
        classification = {"intent": "web_search", "query": "quantum computing", "location": None}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            with patch("orivellum.capabilities.websearch.web_search", return_value="🌐 Results..."):
                result = self._run(_maybe_dispatch_intent(db, "search for quantum computing", "http://x", "m"))
        self.assertIsNotNone(result)
        text, meta = result
        self.assertEqual(meta["intent"], "web_search")

    def test_classifier_exception_returns_none(self):
        """If classify_intent raises, dispatch must return None (fall through to chat)."""
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        db = self._db()
        with patch("orivellum.capabilities.intent.classify_intent", side_effect=Exception("boom")):
            result = self._run(_maybe_dispatch_intent(db, "any message", "http://x", "m"))
        self.assertIsNone(result)

    def test_tool_meta_includes_intent_key(self):
        """Persisted meta must always include 'intent' key so the frontend badge works."""
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        db = self._db()
        classification = {"intent": "weather", "query": "weather in Tokyo", "location": "Tokyo"}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            with patch("orivellum.capabilities.weather.get_weather", return_value="📍 Tokyo: 28°C"):
                result = self._run(_maybe_dispatch_intent(db, "weather in Tokyo", "http://x", "m"))
        _, meta = result
        self.assertIn("intent", meta)


class TestRecallOutputIntent(unittest.TestCase):
    """recall_output intent: fast-path classification + dispatch + handler."""

    # ── intent classifier ────────────────────────────────────────────────────

    def test_fast_path_find_report_i_made(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("find the report I made about taxes", "http://x", "m")
        self.assertEqual(r["intent"], "recall_output")

    def test_fast_path_show_me_tts_clip(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("show me the TTS clip I generated last week", "http://x", "m")
        self.assertEqual(r["intent"], "recall_output")

    def test_fast_path_find_document_i_uploaded(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("find the document I uploaded about machine learning", "http://x", "m")
        self.assertEqual(r["intent"], "recall_output")

    def test_fast_path_show_my_generated_files(self):
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("show my generated files", "http://x", "m")
        self.assertEqual(r["intent"], "recall_output")

    def test_fast_path_does_not_match_regular_recall(self):
        """'where are we on X' is recall, not recall_output."""
        from orivellum.capabilities.intent import classify_intent
        r = classify_intent("where are we on the project", "http://x", "m")
        self.assertNotEqual(r["intent"], "recall_output")

    # ── db.search_provenance ─────────────────────────────────────────────────

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        from orivellum.database.db import OrivellumDB
        from orivellum.configuration.config import OrivellumConfig, ServingConfig
        from pathlib import Path
        data_dir = Path(self.tmp) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db = OrivellumDB(str(data_dir / "test.db"))
        self.cfg = OrivellumConfig(
            data_dir=str(data_dir),
            serving=ServingConfig(base_url="http://localhost:8001"),
        )

    def _register_output(self, title: str, kind: str = "pdf",
                         source: str = "generation") -> str:
        """Create a document + provenance row and return doc_id."""
        from orivellum.capabilities.persist import record_provenance
        doc = self.db.create_document(title=title, source=source, kind=kind)
        doc_id = doc["id"]
        record_provenance(doc_id, source, self.db)
        return doc_id

    def test_search_provenance_finds_matching_title(self):
        self._register_output("Tax Report 2024", kind="pdf", source="generation")
        self._register_output("Recipe Collection", kind="docx", source="upload")

        results = self.db.search_provenance("tax report")
        titles = [r["title"] for r in results]
        self.assertIn("Tax Report 2024", titles)
        self.assertNotIn("Recipe Collection", titles)

    def test_search_provenance_empty_query_returns_recent(self):
        self._register_output("Alpha", source="upload")
        self._register_output("Beta", source="generation")

        results = self.db.search_provenance("")
        self.assertGreaterEqual(len(results), 2)

    def test_search_provenance_source_filter(self):
        self._register_output("Studio Clip", kind="mp3", source="studio")
        self._register_output("Uploaded PDF", kind="pdf", source="upload")

        results = self.db.search_provenance("", source="studio")
        sources = {r["source"] for r in results}
        self.assertEqual(sources, {"studio"})

    # ── handler ──────────────────────────────────────────────────────────────

    def test_handler_returns_markdown_list(self):
        from orivellum.api.routes.conversations import _handle_recall_output
        self._register_output("Tax Report 2024", kind="pdf", source="generation")

        text, meta = _handle_recall_output(self.db, "find the tax report I generated")
        self.assertIn("Tax Report 2024", text)
        self.assertIn("/library/", text)
        self.assertEqual(meta["intent"], "recall_output")
        self.assertGreater(meta["count"], 0)

    def test_handler_no_results_returns_friendly_message(self):
        from orivellum.api.routes.conversations import _handle_recall_output
        text, meta = _handle_recall_output(self.db, "find the nonexistent dragon document")
        self.assertIn("No matching", text)
        self.assertEqual(meta["count"], 0)

    # ── dispatch integration ─────────────────────────────────────────────────

    def _run(self, coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_dispatch_recall_output_returns_meta(self):
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        self._register_output("Quantum Report", kind="pdf", source="generation")
        classification = {"intent": "recall_output", "query": "quantum report I generated",
                          "location": None}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            result = self._run(
                _maybe_dispatch_intent(self.db, "find the quantum report I generated", "http://x", "m")
            )
        self.assertIsNotNone(result)
        text, meta = result
        self.assertEqual(meta["intent"], "recall_output")
        self.assertIsInstance(text, str)

    def test_dispatch_recall_output_handler_exception_returns_fallback(self):
        from orivellum.api.routes.conversations import _maybe_dispatch_intent
        classification = {"intent": "recall_output", "query": "find report", "location": None}
        with patch("orivellum.capabilities.intent.classify_intent", return_value=classification):
            with patch(
                "orivellum.api.routes.conversations._handle_recall_output",
                side_effect=Exception("db error"),
            ):
                result = self._run(
                    _maybe_dispatch_intent(self.db, "find the report I made", "http://x", "m")
                )
        self.assertIsNotNone(result)
        _, meta = result
        self.assertEqual(meta["intent"], "recall_output")


if __name__ == "__main__":
    unittest.main()
