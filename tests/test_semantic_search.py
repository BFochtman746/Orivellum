"""Semantic + hybrid search tests (task: find documents by meaning).

Covers:
- embed_chunks_for_doc stores vectors for a document's chunks
- hybrid_search_chunks: RRF fusion, keyword-only fallback, semantic-only fallback
- GET /api/library/search mode param (keyword / semantic / hybrid / invalid)
- chat context builder survives embeddings being down (hybrid degrades to FTS)

The embeddings endpoint is always mocked — no network calls.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


def _seed_doc(db, title: str, text: str) -> str:
    doc = db.create_document(title=title, kind="text", work_id=None)
    db.add_chunk(doc_id=doc["id"], text=text, page=0)
    db.update_document_extracted(doc["id"], text, len(text.split()), readiness="ready")
    return doc["id"]


def _fake_embedder(mapping: dict[str, list[float]], default: list[float]):
    """Return an embed_texts stand-in keyed on substring matches."""

    def _embed(texts, timeout=None):
        out = []
        for t in texts:
            vec = default
            for key, v in mapping.items():
                if key in t:
                    vec = v
                    break
            out.append(vec)
        return out

    return _embed


class SemanticSearchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)

    def tearDown(self):
        self._tmp.cleanup()

    # ── embedding storage ────────────────────────────────────────────────

    def test_embed_chunks_for_doc_stores_vectors(self):
        from orivellum.capabilities import embeddings as emb

        doc_id = _seed_doc(self.db, "cats.txt", "Felines are graceful nocturnal hunters " * 5)
        with patch.object(emb, "embed_texts", side_effect=lambda ts: [[1.0, 0.0, 0.0]] * len(ts)):
            n = emb.embed_chunks_for_doc(doc_id, self.db)
        self.assertEqual(n, 1)
        self.assertEqual(self.db.count_vectors("chunk"), 1)
        # Re-running embeds nothing new (idempotent)
        with patch.object(emb, "embed_texts", side_effect=lambda ts: [[1.0, 0.0, 0.0]] * len(ts)):
            self.assertEqual(emb.embed_chunks_for_doc(doc_id, self.db), 0)

    def test_embed_chunks_for_doc_endpoint_down(self):
        from orivellum.capabilities import embeddings as emb

        doc_id = _seed_doc(self.db, "dogs.txt", "Canines are loyal companions " * 5)
        with patch.object(emb, "embed_texts", return_value=None):
            self.assertEqual(emb.embed_chunks_for_doc(doc_id, self.db), 0)
        self.assertEqual(self.db.count_vectors("chunk"), 0)

    # ── hybrid fusion ────────────────────────────────────────────────────

    def _seed_two_docs_with_vectors(self):
        """Doc A matches 'quantum' by keyword; doc B is about the same concept
        but shares no keywords ('subatomic physics')."""
        from orivellum.capabilities import embeddings as emb

        a = _seed_doc(self.db, "a.txt", "quantum mechanics explains particle behaviour " * 10)
        b = _seed_doc(self.db, "b.txt", "subatomic physics describes tiny matter waves " * 10)
        embedder = _fake_embedder(
            {"quantum": [1.0, 0.1, 0.0], "subatomic": [0.9, 0.2, 0.0]},
            default=[0.0, 0.0, 1.0],
        )
        with patch.object(emb, "embed_texts", side_effect=embedder):
            emb.embed_chunks_for_doc(a, self.db)
            emb.embed_chunks_for_doc(b, self.db)
        return a, b, embedder

    def test_hybrid_finds_conceptual_match_without_keywords(self):
        from orivellum.capabilities import embeddings as emb

        a, b, embedder = self._seed_two_docs_with_vectors()
        with patch.object(emb, "embed_texts", side_effect=embedder):
            hits = emb.hybrid_search_chunks("quantum", self.db, limit=10)
        doc_ids = [h.get("doc_id") for h in hits]
        self.assertIn(a, doc_ids)  # keyword match
        self.assertIn(b, doc_ids)  # semantic-only match
        for h in hits:
            self.assertIn("rrf_score", h)
            self.assertIn(h["match_type"], ("keyword", "semantic", "both"))
        # Doc A matched both ways → should rank first via RRF
        self.assertEqual(doc_ids[0], a)
        a_hit = hits[0]
        self.assertEqual(a_hit["match_type"], "both")

    def test_hybrid_falls_back_to_keyword_when_embeddings_down(self):
        from orivellum.capabilities import embeddings as emb

        a, b, _ = self._seed_two_docs_with_vectors()
        with patch.object(emb, "embed_texts", return_value=None):
            hits = emb.hybrid_search_chunks("quantum", self.db, limit=10)
        self.assertTrue(hits)
        self.assertTrue(all(h["match_type"] == "keyword" for h in hits))
        self.assertNotIn(b, [h.get("doc_id") for h in hits])

    def test_hybrid_semantic_only_when_fts_finds_nothing(self):
        from orivellum.capabilities import embeddings as emb

        a, b, embedder = self._seed_two_docs_with_vectors()
        # Query shares zero keywords with either doc but embeds near them
        q_embedder = _fake_embedder({}, default=[1.0, 0.15, 0.0])
        with patch.object(emb, "embed_texts", side_effect=q_embedder):
            hits = emb.hybrid_search_chunks("zzzunmatchedtoken", self.db, limit=10)
        self.assertTrue(hits)
        self.assertTrue(all(h["match_type"] == "semantic" for h in hits))

    # ── API modes ────────────────────────────────────────────────────────

    def test_search_api_keyword_mode(self):
        self._seed_two_docs_with_vectors()
        r = self.client.get("/api/library/search", params={"q": "quantum", "mode": "keyword"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["mode"], "keyword")
        self.assertEqual(len(body["results"]), 1)

    def test_search_api_semantic_mode(self):
        from orivellum.capabilities import embeddings as emb

        a, b, embedder = self._seed_two_docs_with_vectors()
        with patch.object(emb, "embed_texts", side_effect=embedder):
            r = self.client.get(
                "/api/library/search", params={"q": "quantum theory", "mode": "semantic"}
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["mode"], "semantic")
        ids = [d["id"] for d in body["results"]]
        self.assertIn(a, ids)
        self.assertIn(b, ids)
        self.assertTrue(all("score" in d for d in body["results"]))

    def test_search_api_hybrid_default_and_degraded(self):
        from orivellum.capabilities import embeddings as emb

        a, b, embedder = self._seed_two_docs_with_vectors()
        # Default mode is hybrid
        with patch.object(emb, "embed_texts", side_effect=embedder):
            r = self.client.get("/api/library/search", params={"q": "quantum theory"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "hybrid")
        self.assertEqual(len(r.json()["results"]), 2)
        # Embeddings down → still returns keyword results, no error
        with patch.object(emb, "embed_texts", return_value=None):
            r = self.client.get("/api/library/search", params={"q": "quantum"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["results"]), 1)

    def test_search_api_invalid_mode(self):
        r = self.client.get("/api/library/search", params={"q": "x", "mode": "bogus"})
        self.assertEqual(r.status_code, 400)

    # ── availability regression guards ───────────────────────────────────

    def test_unreachable_endpoint_opens_cooldown_and_stays_fast(self):
        """A failed embeddings call must open a cooldown so subsequent
        searches skip the network entirely (BM25-level latency)."""
        import urllib.request

        from orivellum.capabilities import embeddings as emb

        emb._reset_circuit_breaker()
        self._seed_two_docs_with_vectors()
        calls = {"n": 0}

        def _boom(*a, **kw):
            calls["n"] += 1
            raise OSError("connection refused")

        try:
            with patch.object(urllib.request, "urlopen", side_effect=_boom):
                # First call attempts the network once, fails, opens cooldown
                r = self.client.get("/api/library/search", params={"q": "quantum"})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(len(r.json()["results"]), 1)  # keyword fallback
                first = calls["n"]
                self.assertEqual(first, 1)
                # Cooldown active → no further network attempts
                r = self.client.get("/api/library/search", params={"q": "quantum"})
                self.assertEqual(r.status_code, 200)
                r = self.client.get(
                    "/api/library/search", params={"q": "quantum", "mode": "semantic"}
                )
                self.assertEqual(r.status_code, 200)
                self.assertEqual(len(r.json()["results"]), 1)  # semantic → keyword fallback
                self.assertEqual(calls["n"], first)
        finally:
            emb._reset_circuit_breaker()

    def test_semantic_mode_falls_back_to_keyword(self):
        from orivellum.capabilities import embeddings as emb

        self._seed_two_docs_with_vectors()
        with patch.object(emb, "embed_texts", return_value=None):
            r = self.client.get("/api/library/search", params={"q": "quantum", "mode": "semantic"})
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["match_type"], "keyword")

    # ── chat context ─────────────────────────────────────────────────────

    def test_chat_context_builder_survives_embeddings_down(self):
        from orivellum.api.routes.conversations import _build_system_prompt
        from orivellum.capabilities import embeddings as emb

        _seed_two_docs_with_vectors = self._seed_two_docs_with_vectors()
        conv = {"id": "c1", "work_id": None}
        with patch.object(emb, "embed_texts", return_value=None):
            prompt = _build_system_prompt(self.db, conv, user_query="quantum")
        self.assertIn("quantum", prompt.lower())


if __name__ == "__main__":
    unittest.main()
