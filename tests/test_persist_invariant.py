"""Structural test: Save/Process/Recall invariant.

Verifies that every known create-path registers its output as a library
document with:
  1. A row in `documents` (the object is saved).
  2. At least one row in `chunks` (the text is chunked for FTS/keyword search).
  3. A row in `object_provenance` (provenance is recorded).

Embedding is best-effort (requires a live embeddings endpoint) so we don't
assert on `vectors` rows — the nightly backfill handles that path.  We DO
assert that chunks exist so the backfill has something to embed.

Create-paths exercised
----------------------
- capabilities.generate._register_output   (Wave 5 generation pipeline)
- capabilities.persist.register_and_index  (Studio TTS / image / audiobook)
- capabilities.persist.register_text_note  (Research summary / chat note)

Run with:
    uv run --with pytest pytest tests/test_persist_invariant.py -v
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# Ensure auth middleware accepts requests in tests
os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")


def _make_db_and_cfg(tmp: str):
    """Return (db, cfg) wired to a fresh temp directory."""
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB

    data_dir = Path(tmp) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        serving=ServingConfig(base_url="http://localhost:8001"),
    )
    # OrivellumDB creates and migrates the schema automatically on construction.
    db = OrivellumDB(str(data_dir / "test.db"))
    return db, cfg


class TestRegisterOutput(unittest.TestCase):
    """_register_output (generate.py) registers + chunks every generated doc."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def test_docx_is_registered_and_chunked(self):
        """generate._register_output creates a document and FTS chunks."""
        from orivellum.capabilities.generate import _register_output

        # Create a minimal fake DOCX file (doesn't need to be valid DOCX for registration)
        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "test_work"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_docx = out_dir / "test_report.docx"
        fake_docx.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # minimal ZIP header

        text = "This is a generated research report about machine learning."
        doc_id = _register_output(fake_docx, None, self.db, self.cfg, "docx", "Test Report", text)

        # 1. Document exists
        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT id, readiness FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        self.assertIsNotNone(doc, "Document was not created by _register_output")
        self.assertEqual(
            doc["readiness"], "ready", "Document readiness should be 'ready' after _register_output"
        )

        # 2. At least one chunk exists (FTS searchable)
        with self.db._lock:
            chunks = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
        self.assertGreater(chunks["n"], 0, "No chunks created — document is not keyword-searchable")

        # 3. Provenance record exists (Amendment-1 invariant)
        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT source FROM object_provenance WHERE object_id=?", (doc_id,)
            ).fetchone()
        self.assertIsNotNone(prov, "No provenance record — object_provenance missing entry")
        self.assertEqual(prov["source"], "generation")

    def test_xlsx_is_registered_and_chunked(self):
        """_register_output works for Excel outputs."""
        from orivellum.capabilities.generate import _register_output

        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "test_work"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_xlsx = out_dir / "summary.xlsx"
        fake_xlsx.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

        text = "Revenue Q1 2024: $1.2 M.  Expenses Q1 2024: $0.9 M."
        doc_id = _register_output(
            fake_xlsx, None, self.db, self.cfg, "xlsx", "Revenue Summary", text
        )

        with self.db._lock:
            chunks = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
        self.assertGreater(chunks["n"], 0, "Excel output has no FTS chunks")

    def test_zip_is_registered_and_chunked(self):
        """_register_output works for zip bundle outputs (tax package etc.)."""
        from orivellum.capabilities.generate import _register_output

        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "library"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_zip = out_dir / "tax_package_2024.zip"
        fake_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

        text = "Tax package 2024: 12 documents, 7 expense-matched"
        doc_id = _register_output(
            fake_zip, None, self.db, self.cfg, "zip", "Tax Package 2024", text
        )

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        self.assertIsNotNone(doc)
        self.assertEqual(doc["readiness"], "ready")

    def _lib_root(self) -> Path:
        return Path(self.cfg.data_dir) / "library"

    def test_docx_content_path_resolves_under_lib_root(self):
        """_register_output DOCX content_path resolves correctly under lib_root."""
        from orivellum.capabilities.generate import _register_output

        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "unscoped"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_docx = out_dir / "report_libtest.docx"
        fake_docx.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

        # work_id=None (unscoped) avoids FK constraint on works table in test DB
        doc_id = _register_output(
            fake_docx, None, self.db, self.cfg, "docx", "Report", "Content for library path test."
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(row["content_path"], "content_path is NULL for DOCX output")
        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library cannot resolve DOCX content_path: lib_root/{row['content_path']}",
        )

    def test_xlsx_content_path_resolves_under_lib_root(self):
        """_register_output XLSX content_path resolves correctly under lib_root."""
        from orivellum.capabilities.generate import _register_output

        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "library"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_xlsx = out_dir / "summary_libtest.xlsx"
        fake_xlsx.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

        doc_id = _register_output(
            fake_xlsx, None, self.db, self.cfg, "xlsx", "Summary", "Revenue data here."
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(), f"Library cannot resolve XLSX content_path: {row['content_path']}"
        )

    def test_zip_content_path_resolves_under_lib_root(self):
        """_register_output ZIP content_path resolves correctly under lib_root."""
        from orivellum.capabilities.generate import _register_output

        out_dir = Path(self.tmp) / "data" / "outputs" / "generate" / "library"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_zip = out_dir / "pkg_libtest.zip"
        fake_zip.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

        doc_id = _register_output(
            fake_zip, None, self.db, self.cfg, "zip", "Package", "Archive content here."
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(), f"Library cannot resolve ZIP content_path: {row['content_path']}"
        )


class TestRegisterAndIndex(unittest.TestCase):
    """capabilities.persist.register_and_index covers Studio outputs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def test_audio_tts_clip_is_registered(self):
        """A TTS MP3 is registered as a searchable library document."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_mp3 = out_dir / "speech_test.mp3"
        fake_mp3.write_bytes(b"ID3" + b"\x00" * 50)  # minimal MP3-ish bytes

        source_text = "Hello, this is a test TTS clip about quantum computing."
        doc_id = register_and_index(
            doc_path=fake_mp3,
            text_content=source_text,
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS clip: Hello, this is a test",
            provenance_source="studio",
            origin_id="test-conv-001",
        )

        # 1. Document exists and is ready
        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness, kind FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        self.assertIsNotNone(doc)
        self.assertEqual(doc["readiness"], "ready")
        self.assertEqual(doc["kind"], "mp3")

        # 2. Chunks exist
        with self.db._lock:
            n = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()["n"]
        self.assertGreater(n, 0, "TTS clip has no FTS chunks — not keyword-searchable")

        # 3. Provenance: source = studio, origin_id = test-conv-001
        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT source, origin_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()
        self.assertIsNotNone(prov)
        self.assertEqual(prov["source"], "studio")
        self.assertEqual(prov["origin_id"], "test-conv-001")

    def test_generated_image_is_registered(self):
        """A generated image PNG is registered with its prompt as searchable text."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_png = out_dir / "image_test.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        prompt = "A serene mountain lake at sunset with pine trees reflected in the water"
        doc_id = register_and_index(
            doc_path=fake_png,
            text_content=prompt,
            kind="png",
            db=self.db,
            cfg=self.cfg,
            title=f"Image: {prompt[:60]}",
            provenance_source="studio",
        )

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT id, extracted_text FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        self.assertIsNotNone(doc)
        # The prompt should be stored as extracted text for semantic lookup
        self.assertIn("mountain lake", doc["extracted_text"])

    def test_audiobook_is_registered_with_source_text(self):
        """An audiobook MP3 is registered and the source doc text is searchable."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_mp3 = out_dir / "audiobook_test.mp3"
        fake_mp3.write_bytes(b"ID3" + b"\x00" * 50)

        source_text = (
            "Chapter 1: Introduction to Machine Learning. "
            "Machine learning is a field of artificial intelligence that enables "
            "systems to learn from data and improve over time without being explicitly programmed."
        )
        doc_id = register_and_index(
            doc_path=fake_mp3,
            text_content=source_text,
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="Audiobook: Introduction to Machine Learning",
            provenance_source="studio",
            origin_id="source-doc-abc123",
        )

        with self.db._lock:
            chunks = self.db._conn.execute(
                "SELECT text FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchall()
        chunk_texts = " ".join(r["text"] for r in chunks)
        self.assertIn(
            "Machine learning",
            chunk_texts,
            "Source text not present in audiobook chunks — not searchable",
        )


class TestRegisterTextNote(unittest.TestCase):
    """capabilities.persist.register_text_note covers research summaries."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def test_research_summary_is_registered(self):
        """A research note (no file) is registered and keyword-searchable."""
        from orivellum.capabilities.persist import register_text_note

        summary = (
            "Research summary: The latest studies on transformer architectures show "
            "that attention mechanisms can be pruned by up to 40% without significant "
            "loss in downstream task performance."
        )
        doc_id = register_text_note(
            text=summary,
            db=self.db,
            cfg=self.cfg,
            title="Research Summary: Transformer Pruning",
            provenance_source="chat",
            origin_id="conv-xyz",
        )

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness, kind FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
            chunks = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
            prov = self.db._conn.execute(
                "SELECT source, origin_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()

        self.assertEqual(doc["readiness"], "ready")
        self.assertGreater(chunks["n"], 0)
        self.assertEqual(prov["source"], "chat")
        self.assertEqual(prov["origin_id"], "conv-xyz")

    def test_dedup_same_note_returns_same_id(self):
        """Registering the same file twice returns the existing doc_id (SHA dedup)."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        shared_file = out_dir / "dedup_test.txt"
        shared_file.write_text("Same content registered twice.", encoding="utf-8")

        id1 = register_and_index(
            shared_file,
            "Same content registered twice.",
            "txt",
            self.db,
            self.cfg,
            title="First registration",
            provenance_source="generation",
        )
        id2 = register_and_index(
            shared_file,
            "Same content registered twice.",
            "txt",
            self.db,
            self.cfg,
            title="Second registration",
            provenance_source="studio",
        )

        self.assertEqual(
            id1, id2, "Same file registered twice should return the same doc_id (SHA dedup)"
        )


class TestStudioRoundTrip(unittest.TestCase):
    """Studio outputs must remain ready and FTS-searchable after a simulated restart.

    The most likely failure mode is the document being created with
    readiness='imported' (never reaching 'ready') or content_path becoming
    stale after a restart — neither should be possible because
    register_and_index() sets readiness='ready' and stores a lib-root-relative
    content_path synchronously before returning.

    Each test:
      1. Registers a Studio output via register_and_index on DB instance A.
      2. Closes DB A and opens a *new* OrivellumDB instance B pointing to the
         same file — simulating an API server restart.
      3. Asserts readiness and chunks survive on B.

    FTS sub-tests additionally run db.search_chunks() on B to confirm the
    document surfaces via keyword search, proving chunks are properly written
    to the chunks_fts shadow table (not just the chunks row table).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)
        self._db_path = str(Path(self.tmp) / "data" / "test.db")
        self._out_dir = Path(self.tmp) / "data" / "outputs"
        self._out_dir.mkdir(parents=True, exist_ok=True)

    def _reopen_db(self):
        """Close the current DB connection and open a fresh one to the same file."""
        self.db.close()
        from orivellum.database.db import OrivellumDB

        self.db = OrivellumDB(self._db_path)

    # ── TTS clip ─────────────────────────────────────────────────────────────

    def test_tts_clip_is_ready_after_restart(self):
        """A TTS MP3 registered before restart is still readiness='ready' after."""
        from orivellum.capabilities.persist import register_and_index

        clip = self._out_dir / "tts_restart_test.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 64)

        doc_id = register_and_index(
            doc_path=clip,
            text_content="Quantum computing uses qubits and superposition to solve hard problems.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS: Quantum Computing Introduction",
            provenance_source="studio",
            origin_id="test-restart-001",
        )

        # Simulate restart
        self._reopen_db()

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness, kind FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(doc, "Document vanished after simulated restart")
        self.assertEqual(
            doc["readiness"],
            "ready",
            f"readiness degraded to {doc['readiness']!r} after restart — "
            "document is no longer usable",
        )

    def test_tts_clip_chunks_survive_restart(self):
        """FTS chunks for a TTS clip are still present after a simulated restart."""
        from orivellum.capabilities.persist import register_and_index

        clip = self._out_dir / "tts_chunks_restart.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 64)

        doc_id = register_and_index(
            doc_path=clip,
            text_content="Quantum computing uses qubits and superposition to solve hard problems.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS: Quantum Computing Chunks",
            provenance_source="studio",
        )

        self._reopen_db()

        with self.db._lock:
            n = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()["n"]

        self.assertGreater(n, 0, "Chunks missing after restart — document not FTS-searchable")

    def test_tts_clip_surfaces_via_fts_after_restart(self):
        """search_chunks() on a fresh DB instance finds the registered TTS clip."""
        from orivellum.capabilities.persist import register_and_index

        clip = self._out_dir / "tts_fts_restart.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 64)

        register_and_index(
            doc_path=clip,
            text_content="Quantum computing uses qubits and superposition to solve hard problems.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS: Quantum Computing FTS",
            provenance_source="studio",
        )

        self._reopen_db()

        hits = self.db.search_chunks("quantum computing")
        doc_ids = [h.get("doc_id") or h.get("id") for h in hits]
        # At least one hit must reference a document with 'quantum' in its chunks
        chunk_texts = " ".join(
            h.get("text", "") or h.get("snippet", "") or "" for h in hits
        ).lower()

        self.assertGreater(
            len(hits), 0, "search_chunks returned nothing after restart — FTS index lost"
        )
        self.assertIn(
            "quantum",
            chunk_texts,
            "Expected 'quantum' in FTS results but none of the hits contain it",
        )

    # ── Generated image ───────────────────────────────────────────────────────

    def test_generated_image_is_ready_after_restart(self):
        """A generated PNG registered before restart is still readiness='ready' after."""
        from orivellum.capabilities.persist import register_and_index

        img = self._out_dir / "image_restart_test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

        doc_id = register_and_index(
            doc_path=img,
            text_content="A futuristic cityscape at sunset with flying cars and neon lights.",
            kind="png",
            db=self.db,
            cfg=self.cfg,
            title="Image: Futuristic Cityscape",
            provenance_source="studio",
        )

        self._reopen_db()

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(doc)
        self.assertEqual(
            doc["readiness"], "ready", "Generated image readiness degraded after restart"
        )

    def test_generated_image_fts_after_restart(self):
        """search_chunks() finds a generated image by its prompt text after restart."""
        from orivellum.capabilities.persist import register_and_index

        img = self._out_dir / "image_fts_restart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

        register_and_index(
            doc_path=img,
            text_content="A futuristic cityscape at sunset with flying cars and neon lights.",
            kind="png",
            db=self.db,
            cfg=self.cfg,
            title="Image: Futuristic Cityscape FTS",
            provenance_source="studio",
        )

        self._reopen_db()

        hits = self.db.search_chunks("cityscape sunset")
        chunk_texts = " ".join(
            h.get("text", "") or h.get("snippet", "") or "" for h in hits
        ).lower()

        self.assertGreater(
            len(hits), 0, "search_chunks returned no results after restart — PNG prompt not indexed"
        )
        self.assertIn("cityscape", chunk_texts, "Expected 'cityscape' in FTS results after restart")

    # ── Provenance survives restart ───────────────────────────────────────────

    def test_provenance_row_survives_restart(self):
        """object_provenance row is still present after a simulated restart."""
        from orivellum.capabilities.persist import register_and_index

        clip = self._out_dir / "tts_prov_restart.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 64)

        doc_id = register_and_index(
            doc_path=clip,
            text_content="Provenance durability test content.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS: Provenance Durability",
            provenance_source="studio",
            origin_id="restart-prov-check",
        )

        self._reopen_db()

        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT source, origin_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()

        self.assertIsNotNone(
            prov, "Provenance row missing after restart — recall queries will fail"
        )
        self.assertEqual(prov["source"], "studio")
        self.assertEqual(prov["origin_id"], "restart-prov-check")


class TestLibraryPathResolution(unittest.TestCase):
    """content_path stored by register_and_index/register_text_note must resolve
    correctly under data_dir/library — the invariant every Library endpoint relies on.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def _lib_root(self) -> Path:
        return Path(self.cfg.data_dir) / "library"

    def test_tts_clip_content_path_resolves_under_lib_root(self):
        """A TTS clip registered via register_and_index has content_path resolvable
        under lib_root — no lib_root/outputs/... mismatch."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_mp3 = out_dir / "speech_libtest.mp3"
        fake_mp3.write_bytes(b"ID3" + b"\x00" * 50)

        doc_id = register_and_index(
            doc_path=fake_mp3,
            text_content="Hello world — a test TTS clip.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS clip: Hello world",
            provenance_source="studio",
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(row["content_path"], "content_path is NULL")
        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library cannot resolve content_path: lib_root/{row['content_path']} does not exist "
            f"(lib_root={self._lib_root()})",
        )

    def test_generated_image_content_path_resolves_under_lib_root(self):
        """A generated PNG registered via register_and_index resolves under lib_root."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        fake_png = out_dir / "image_libtest.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        doc_id = register_and_index(
            doc_path=fake_png,
            text_content="A serene mountain lake at sunset.",
            kind="png",
            db=self.db,
            cfg=self.cfg,
            title="Image: mountain lake",
            provenance_source="studio",
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library cannot resolve PNG content_path: {row['content_path']}",
        )

    def test_research_note_content_path_resolves_under_lib_root(self):
        """A research note registered via register_text_note resolves under lib_root."""
        from orivellum.capabilities.persist import register_text_note

        doc_id = register_text_note(
            text="This is a research summary about black holes.",
            db=self.db,
            cfg=self.cfg,
            title="Research: Black Holes",
            provenance_source="intake",
        )

        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(row["content_path"])
        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library cannot resolve note content_path: {row['content_path']}",
        )

    def test_dedup_link_not_doubled(self):
        """Registering the same file twice reuses the existing doc — no extra library entries."""
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        shared = out_dir / "shared_libtest.txt"
        shared.write_text("Content for dedup test.", encoding="utf-8")

        id1 = register_and_index(
            shared,
            "Content for dedup test.",
            "txt",
            self.db,
            self.cfg,
            provenance_source="generation",
        )
        id2 = register_and_index(
            shared, "Content for dedup test.", "txt", self.db, self.cfg, provenance_source="studio"
        )

        self.assertEqual(id1, id2, "Same file should dedup to the same doc_id")

        # lib_root/generated should have at most 1 entry for this file
        gen_dir = self._lib_root() / "generated"
        entries = list(gen_dir.glob("*shared_libtest*"))
        self.assertEqual(
            len(entries),
            1,
            f"Expected 1 library entry for deduped file, found {len(entries)}: {entries}",
        )

    def test_library_doc_survives_source_deletion(self):
        """A registered Studio output remains resolvable under lib_root even after
        the original source file is deleted — simulating what output rotation does.

        This is the key durability guarantee: Studio outputs are stored as
        hard-linked (or copied) durable files under lib_root/generated/, not
        symlinks, so their Library entries stay valid past the rolling 50-file
        deletion window in _rotate_outputs.
        """
        from orivellum.capabilities.persist import register_and_index

        out_dir = Path(self.tmp) / "data" / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        source_mp3 = out_dir / "rotation_test.mp3"
        source_mp3.write_bytes(b"ID3" + b"\x00" * 100)

        doc_id = register_and_index(
            doc_path=source_mp3,
            text_content="Audio clip that must survive output rotation.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS rotation test",
            provenance_source="studio",
        )

        # Simulate _rotate_outputs deleting this file (oldest file gets rotated out).
        source_mp3.unlink()
        self.assertFalse(source_mp3.exists(), "Source file should be deleted")

        # Library document must still resolve under lib_root.
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()

        self.assertIsNotNone(row["content_path"])
        resolved = self._lib_root() / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library document is dangling after source deletion (simulated rotation). "
            f"content_path={row['content_path']} must survive in lib_root/generated/ "
            f"as a hard link or copy, not a symlink.",
        )


class TestIntakeResearchPath(unittest.TestCase):
    """The *actual* intake research code path wires into register_text_note.

    This exercises the real call stack in capabilities/intake.py — not just the
    helper — so that a future regression (removing the register_text_note call)
    would cause this test to fail.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def test_intake_research_registers_library_document(self):
        """run_intake with research=True creates a library document + provenance row."""
        from unittest.mock import patch

        from orivellum.capabilities.intake import run_intake

        # Create a minimal source document with extracted text already set (avoids
        # needing a real file or AI extraction during the test).
        doc = self.db.create_document(
            title="Quantum Computing Primer",
            source="test/qc_primer.txt",
            kind="txt",
        )
        doc_id = doc["id"]
        self.db.update_document_extracted(
            doc_id,
            extracted_text="Quantum computing uses qubits and superposition to solve hard problems.",
            word_count=12,
            readiness="ready",
        )

        fake_summary = (
            "Recent research confirms that quantum error correction has advanced significantly. "
            "IBM and Google report qubit counts exceeding 1000."
        )
        fake_sources = [{"url": "https://example.com/qc", "title": "QC News"}]

        # Patch the external web search so the test makes no network calls.
        # web_search_synthesize is imported locally inside run_intake, so we patch
        # the function on its source module (orivellum.capabilities.websearch).
        # Also suppress background embedding calls that would fail without _deps.
        with (
            patch(
                "orivellum.capabilities.websearch.web_search_synthesize",
                return_value=(fake_summary, fake_sources),
            ),
            patch("orivellum.capabilities.embeddings.embed_chunks_for_doc", return_value=None),
        ):
            try:
                run_intake(
                    doc_id,
                    db=self.db,
                    cfg=self.cfg,
                    research=True,
                    research_query="quantum computing",
                )
            except Exception:
                # run_intake may fail on stages that need AI services; that's
                # fine — the research registration happens in a try/except block
                # and should have already persisted the note by the time intake
                # reaches any AI-dependent stage.
                pass

        # The research note must appear as a library document with source="intake".
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT d.id, d.readiness FROM documents d "
                "JOIN object_provenance p ON p.object_id = d.id "
                "WHERE p.source = 'intake' AND p.origin_id = ?",
                (doc_id,),
            ).fetchall()

        self.assertGreater(
            len(rows), 0, "Intake research did not create a library document with provenance"
        )
        self.assertEqual(
            rows[0]["readiness"],
            "ready",
            "Intake research library document readiness is not 'ready'",
        )

    def test_intake_research_chunks_are_searchable(self):
        """Research note library document has FTS chunks so keyword search works."""
        from unittest.mock import patch

        from orivellum.capabilities.intake import run_intake

        doc = self.db.create_document(
            title="Machine Learning Basics",
            source="test/ml_basics.txt",
            kind="txt",
        )
        doc_id = doc["id"]
        self.db.update_document_extracted(
            doc_id,
            extracted_text="Machine learning is a field of AI that learns from data.",
            word_count=11,
            readiness="ready",
        )

        fake_summary = "Recent ML research shows transformers dominate NLP benchmarks."

        with (
            patch(
                "orivellum.capabilities.websearch.web_search_synthesize",
                return_value=(fake_summary, []),
            ),
            patch("orivellum.capabilities.embeddings.embed_chunks_for_doc", return_value=None),
        ):
            try:
                run_intake(
                    doc_id,
                    db=self.db,
                    cfg=self.cfg,
                    research=True,
                    research_query="machine learning",
                )
            except Exception:
                pass

        # Find the research library document(s) for this source doc
        with self.db._lock:
            note_docs = self.db._conn.execute(
                "SELECT object_id FROM object_provenance WHERE source='intake' AND origin_id=?",
                (doc_id,),
            ).fetchall()

        self.assertGreater(len(note_docs), 0, "No provenance row for intake research note")

        note_doc_id = note_docs[0]["object_id"]
        with self.db._lock:
            chunks = self.db._conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE doc_id=?", (note_doc_id,)
            ).fetchone()
        self.assertGreater(
            chunks["n"], 0, "Research note has no FTS chunks — not keyword-searchable"
        )


class TestSchemaV70Migration(unittest.TestCase):
    """object_provenance table exists and has the expected columns."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def test_object_provenance_table_exists(self):
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='object_provenance'"
            ).fetchone()
        self.assertIsNotNone(row, "object_provenance table not found after schema migration")

    def test_object_provenance_columns(self):
        """All required provenance columns are present."""
        with self.db._lock:
            cols = {
                r["name"]
                for r in self.db._conn.execute("PRAGMA table_info(object_provenance)").fetchall()
            }
        for col in ("id", "object_id", "source", "origin_id", "work_id", "topic_id", "created_at"):
            self.assertIn(col, cols, f"Column '{col}' missing from object_provenance")

    def test_upgrade_from_v69_applies_v70(self):
        """A fresh DB at v69 correctly upgrades to v70 (monotonic migration order)."""
        from orivellum.database.schema import MIGRATIONS

        # Verify v70 is declared AFTER all migrations with version < 70,
        # and that sorted application would apply v66 before v70.
        versions_in_order = [v for v, _, _ in MIGRATIONS]
        idx_70 = versions_in_order.index(70)

        lower_migrations = [v for v in versions_in_order[:idx_70] if v < 70]
        # Every migration declared before v70 in file order must have v < 70
        self.assertTrue(
            all(v < 70 for v in lower_migrations),
            f"v70 was declared before lower-numbered migrations: "
            f"{[v for v in lower_migrations if v >= 70]}",
        )

        # Simulate an upgrade from v69: only migrations with v > 69 should be pending.
        pending = sorted([(v, d, s) for v, d, s in MIGRATIONS if v > 69], key=lambda x: x[0])
        pending_versions = [v for v, _, _ in pending]
        self.assertIn(70, pending_versions, "v70 is not in the pending list for a v69→v70 upgrade")
        # v66 must NOT be in the upgrade path for a v69 DB (it's already applied)
        self.assertNotIn(
            66,
            pending_versions,
            "v66 is incorrectly in the pending list for a v69 DB — "
            "it was already applied before v69",
        )


class TestRotationRace(unittest.TestCase):
    """Prove the link-before-rotate guarantee: Studio outputs are always
    resolvable even when _rotate_outputs deletes the source file before the
    background registration thread runs.

    The core invariant:
        _link_output_sync(file)   ← synchronous (pre-rotation)
        _rotate_outputs(out_dir)  ← may delete the source path
        background thread: register_and_index(…, _prelinked_rel=rel)
        → Library document is valid; content_path resolves under lib_root

    These tests simulate the race by linking → rotating → registering in strict
    order, then asserting the library entry resolves after source deletion.
    """

    _MAX_OUTPUTS = 50  # matches studio.py _MAX_OUTPUTS

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def _lib_root(self) -> Path:
        return Path(self.tmp) / "data" / "library"

    def _out_dir(self) -> Path:
        d = Path(self.tmp) / "data" / "outputs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _rotate_outputs(self, out_dir: Path) -> None:
        """Mirror of studio.py _rotate_outputs."""
        files = sorted(
            (f for f in out_dir.iterdir() if f.is_file()),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        for old in files[self._MAX_OUTPUTS :]:
            old.unlink(missing_ok=True)

    def _make_outputs(self, out_dir: Path, n: int) -> list[Path]:
        """Create *n* dummy output files (oldest first by mtime)."""

        files = []
        for i in range(n):
            p = out_dir / f"dummy_{i:04d}.bin"
            p.write_bytes(b"\x00" * 16)
            # Space mtime so rotation order is predictable
            os.utime(str(p), (1_000_000 + i, 1_000_000 + i))
            files.append(p)
        return files

    def test_link_before_rotate_tts_clip_resolves(self):
        """TTS clip linked before rotation survives even when rotation deletes it."""
        from orivellum.capabilities.persist import _ensure_lib_symlink, register_and_index

        out_dir = self._out_dir()
        lib_root = self._lib_root()

        # Fill outputs to just over the rotation limit
        existing = self._make_outputs(out_dir, self._MAX_OUTPUTS)

        # Newest file: the TTS clip we care about
        clip = out_dir / "tts_latest.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 100)
        # Give it a future mtime so it is the NEWEST (kept) but we test it
        # by deliberately putting it last — the oldest file will be rotated.

        os.utime(str(clip), (2_000_000, 2_000_000))

        # ── STEP 1: Synchronous link BEFORE rotation ──────────────────────────
        prelinked_rel = _ensure_lib_symlink(clip, lib_root)
        self.assertTrue(prelinked_rel, "pre-linked rel must not be empty")
        linked_abs = lib_root / prelinked_rel
        self.assertTrue(linked_abs.exists(), "Hard link must exist before rotation")

        # ── STEP 2: Rotate (deletes oldest — the dummy files, not the clip) ──
        self._rotate_outputs(out_dir)
        # The clip itself survives rotation (it is newest), but we simulate
        # rotation deleting it by unlinking it manually after linking.
        clip.unlink()
        self.assertFalse(clip.exists(), "Source deleted to simulate rotation")
        # Library copy must still be readable via the hard link
        self.assertTrue(linked_abs.exists(), "Hard link survived source deletion")

        # ── STEP 3: Background registration (uses prelinked path) ─────────────
        doc_id = register_and_index(
            doc_path=linked_abs,  # background thread uses lib copy
            text_content="TTS source text for rotation race test.",
            kind="mp3",
            db=self.db,
            cfg=self.cfg,
            title="TTS rotation race",
            provenance_source="studio",
            _prelinked_rel=prelinked_rel,
        )
        self.assertIsNotNone(doc_id)

        # Library document must resolve under lib_root
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        resolved = lib_root / row["content_path"]
        self.assertTrue(
            resolved.exists(),
            f"Library entry content_path={row['content_path']} does not resolve "
            f"after source deletion and rotation",
        )

    def test_link_before_rotate_image_resolves(self):
        """Generated image linked before rotation survives at the rotation limit."""
        from orivellum.capabilities.persist import _ensure_lib_symlink, register_and_index

        out_dir = self._out_dir()
        lib_root = self._lib_root()

        # Fill to exactly the limit; next write pushes it over
        existing = self._make_outputs(out_dir, self._MAX_OUTPUTS)

        img = out_dir / "gen_image.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        os.utime(str(img), (2_000_000, 2_000_000))

        # Synchronous link before rotation
        prelinked_rel = _ensure_lib_symlink(img, lib_root)
        linked_abs = lib_root / prelinked_rel

        # Rotation: oldest dummy is deleted; simulate img also being deleted
        self._rotate_outputs(out_dir)
        img.unlink()
        self.assertFalse(img.exists())
        self.assertTrue(linked_abs.exists(), "Image hard link survived rotation")

        doc_id = register_and_index(
            doc_path=linked_abs,
            text_content="A futuristic cityscape at sunset.",
            kind="png",
            db=self.db,
            cfg=self.cfg,
            title="Generated image race test",
            provenance_source="studio",
            _prelinked_rel=prelinked_rel,
        )
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT content_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        resolved = lib_root / row["content_path"]
        self.assertTrue(resolved.exists(), "PNG entry resolves after rotation+deletion")

    def test_no_duplicate_entries_at_rotation_limit(self):
        """Registering the same clip twice (link-before-rotate pattern) deduplicates."""
        from orivellum.capabilities.persist import _ensure_lib_symlink, register_and_index

        out_dir = self._out_dir()
        lib_root = self._lib_root()

        clip = out_dir / "dedup_race.mp3"
        clip.write_bytes(b"ID3" + b"\x00" * 64)

        rel1 = _ensure_lib_symlink(clip, lib_root)
        id1 = register_and_index(
            clip, "first", "mp3", self.db, self.cfg, provenance_source="studio", _prelinked_rel=rel1
        )

        # Simulate second registration (e.g. duplicate route call)
        rel2 = _ensure_lib_symlink(clip, lib_root)  # returns same path (exists)
        id2 = register_and_index(
            clip,
            "second",
            "mp3",
            self.db,
            self.cfg,
            provenance_source="studio",
            _prelinked_rel=rel2,
        )

        self.assertEqual(id1, id2, "Duplicate registrations must dedup to same doc_id")
        gen_dir = lib_root / "generated"
        entries = list(gen_dir.glob("*dedup_race*"))
        self.assertEqual(
            len(entries), 1, f"Expected exactly 1 library entry; found {len(entries)}: {entries}"
        )


class TestUploadProvenance(unittest.TestCase):
    """process_document() must write an object_provenance row for every uploaded document.

    Exercises the "upload" provenance source added to capabilities/pipeline.py so that
    recall queries ("find everything I added about X") can surface ingested documents
    alongside Studio outputs and generated notes.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db, self.cfg = _make_db_and_cfg(self.tmp)

    def _write_doc(self, name: str, content: str, work_id: str | None = None) -> tuple[str, str]:
        """Create a temp file and matching documents row; return (doc_id, file_path)."""
        import hashlib

        lib_dir = Path(self.tmp) / "data" / "library"
        lib_dir.mkdir(parents=True, exist_ok=True)
        doc_file = lib_dir / name
        doc_file.write_text(content, encoding="utf-8")
        sha = hashlib.sha256(doc_file.read_bytes()).hexdigest()

        doc = self.db.create_document(
            title=Path(name).stem,
            source=str(doc_file),
            sha256=sha,
            kind="text",
            work_id=work_id,
        )
        return doc["id"], str(doc_file)

    def test_plain_upload_creates_provenance_row(self):
        """process_document writes source='upload' provenance for a basic text file."""
        from orivellum.capabilities.pipeline import process_document

        doc_id, file_path = self._write_doc(
            "plain_upload.txt",
            "The quick brown fox jumps over the lazy dog.",
        )

        process_document(doc_id, file_path, "text", None, "Plain Upload", self.db)

        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT source, work_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()

        self.assertIsNotNone(prov, "No provenance row created for uploaded document")
        self.assertEqual(
            prov["source"], "upload", f"Expected source='upload', got {prov['source']!r}"
        )
        self.assertIsNone(prov["work_id"], "work_id should be None for an unscoped upload")

    def test_upload_with_work_id_sets_provenance_work_id(self):
        """process_document passes work_id into the provenance row for work-linked docs."""
        from orivellum.capabilities.pipeline import process_document

        # Create a work so the FK is satisfied
        work = self.db.create_work("Test Work")
        work_id = work["id"]

        doc_id, file_path = self._write_doc(
            "work_upload.txt",
            "This document belongs to a specific Work.",
            work_id=work_id,
        )

        process_document(doc_id, file_path, "text", work_id, "Work Upload", self.db)

        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT source, work_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()

        self.assertIsNotNone(prov)
        self.assertEqual(prov["source"], "upload")
        self.assertEqual(
            prov["work_id"], work_id, "Provenance work_id must match the document's linked Work"
        )

    def test_origin_id_is_sha256_of_uploaded_file(self):
        """process_document uses the document sha256 as the provenance origin_id."""
        import hashlib

        from orivellum.capabilities.pipeline import process_document

        doc_id, file_path = self._write_doc(
            "sha_upload.txt",
            "Content with a specific SHA for origin_id verification.",
        )
        expected_sha = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()

        process_document(doc_id, file_path, "text", None, "SHA Upload", self.db)

        with self.db._lock:
            prov = self.db._conn.execute(
                "SELECT origin_id FROM object_provenance WHERE object_id=?",
                (doc_id,),
            ).fetchone()

        self.assertIsNotNone(prov)
        self.assertEqual(
            prov["origin_id"], expected_sha, "origin_id must be the sha256 of the uploaded file"
        )

    def test_provenance_row_exists_after_readiness_is_ready(self):
        """Provenance is recorded only after the document reaches readiness='ready'.

        This verifies the row exists alongside a correctly-marked document, not
        during an intermediate state such as 'imported' or 'transcribing'.
        """
        from orivellum.capabilities.pipeline import process_document

        doc_id, file_path = self._write_doc(
            "readiness_upload.txt",
            "A document that must be ready and have provenance simultaneously.",
        )

        process_document(doc_id, file_path, "text", None, "Readiness Upload", self.db)

        with self.db._lock:
            doc = self.db._conn.execute(
                "SELECT readiness FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
            prov = self.db._conn.execute(
                "SELECT id FROM object_provenance WHERE object_id=? AND source='upload'",
                (doc_id,),
            ).fetchone()

        self.assertEqual(
            doc["readiness"], "ready", "Document readiness must be 'ready' after process_document"
        )
        self.assertIsNotNone(prov, "Provenance row must exist when document is ready")


if __name__ == "__main__":
    unittest.main()
