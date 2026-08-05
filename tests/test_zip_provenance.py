"""Tests for ZIP child document provenance recording (task #368).

Verifies:
1. _explode_zip_into_documents() writes an object_provenance row per child
   immediately after create_document(), with source='zip_extract' and
   origin_id pointing to the parent ZIP document.
2. The nightshift backfill pass (_pass_zip_provenance_backfill) back-fills
   provenance rows for existing ZIP children that have a 'from_zip' meta key
   but no provenance row.
3. The backfill is idempotent — running it twice doesn't double-insert.
"""
from __future__ import annotations

import io
import json
import os
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Allow importing orivellum from the source tree without installation
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orivellum.database.db import OrivellumDB


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_db(tmp_path: Path) -> OrivellumDB:
    db = OrivellumDB(str(tmp_path / "test.db"))
    return db


def _count_provenance(db: OrivellumDB, object_id: str, source: str = "zip_extract") -> int:
    with db._lock:
        return db._conn.execute(
            "SELECT COUNT(*) FROM object_provenance WHERE object_id=? AND source=?",
            (object_id, source),
        ).fetchone()[0]


def _provenance_rows(db: OrivellumDB, source: str = "zip_extract") -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM object_provenance WHERE source=?",
            (source,),
        ).fetchall()
    return [dict(r) for r in rows]


def _make_zip_bytes(members: dict[str, bytes]) -> bytes:
    """Create an in-memory ZIP archive with the given {name: content} members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _create_parent_zip_doc(db: OrivellumDB, tmp_path: Path) -> tuple[str, Path]:
    """Create a parent ZIP document record and write a real ZIP file to disk."""
    zip_bytes = _make_zip_bytes({
        "chapter1.txt": b"The quick brown fox jumps over the lazy dog.",
        "chapter2.txt": b"Pack my box with five dozen liquor jugs.",
    })
    zip_path = tmp_path / "archive.zip"
    zip_path.write_bytes(zip_bytes)

    parent_doc = db.create_document(
        title="archive.zip",
        source=str(zip_path),
        sha256="aabbcc",
        kind="zip",
        work_id=None,
        content_path=str(zip_path),
    )
    return parent_doc["id"], zip_path


# ── 1. Pipeline — immediate provenance on explosion ────────────────────────────

class TestZipExplodeProvenance:
    """_explode_zip_into_documents must write provenance rows immediately."""

    def _explode(self, db: OrivellumDB, tmp_path: Path,
                 parent_id: str, zip_path: Path, title: str = "archive.zip") -> list[str]:
        """Run _explode_zip_into_documents with mocked config/executor."""
        (tmp_path / "library").mkdir(exist_ok=True)
        cfg_mock = MagicMock()
        cfg_mock.data_dir = str(tmp_path)

        # get_config and _tracked_submit are imported locally inside the function,
        # so patch them at their definition sites.
        with patch("orivellum.api._deps.get_config", return_value=cfg_mock), \
             patch("orivellum.api.executor._tracked_submit",
                   side_effect=Exception("no executor in tests")):
            from orivellum.capabilities.pipeline import _explode_zip_into_documents
            return _explode_zip_into_documents(parent_id, zip_path, None, title, db)

    def test_each_child_gets_provenance_row(self, tmp_path: Path) -> None:
        """After ZIP explosion, every child document has a zip_extract provenance row."""
        db = _make_db(tmp_path)
        parent_id, zip_path = _create_parent_zip_doc(db, tmp_path)
        children = self._explode(db, tmp_path, parent_id, zip_path)

        assert len(children) >= 1, "At least one child must be created"

        rows = _provenance_rows(db)
        child_ids_with_prov = {r["object_id"] for r in rows}
        for child_id in children:
            assert child_id in child_ids_with_prov, (
                f"Child {child_id} has no zip_extract provenance row"
            )

    def test_provenance_origin_id_points_to_parent(self, tmp_path: Path) -> None:
        """Each provenance row's origin_id must equal the parent ZIP document id."""
        db = _make_db(tmp_path)
        parent_id, zip_path = _create_parent_zip_doc(db, tmp_path)
        children = self._explode(db, tmp_path, parent_id, zip_path)

        rows = _provenance_rows(db)
        for row in rows:
            if row["object_id"] in children:
                assert row["origin_id"] == parent_id, (
                    f"Provenance origin_id {row['origin_id']!r} != parent_id {parent_id!r}"
                )

    def test_provenance_source_is_zip_extract(self, tmp_path: Path) -> None:
        """Provenance rows for ZIP children must carry source='zip_extract'."""
        db = _make_db(tmp_path)
        parent_id, zip_path = _create_parent_zip_doc(db, tmp_path)
        children = self._explode(db, tmp_path, parent_id, zip_path)

        rows = _provenance_rows(db, source="zip_extract")
        child_ids_with_prov = {r["object_id"] for r in rows}
        for child_id in children:
            assert child_id in child_ids_with_prov, (
                f"Child {child_id} missing zip_extract provenance row"
            )

    def test_provenance_survives_dedup_reuse(self, tmp_path: Path) -> None:
        """When a ZIP child is a dedup hit, the existing child id is collected
        without creating a new document — no crash should occur."""
        db = _make_db(tmp_path)
        import hashlib as _hl
        txt_content = b"Pre-existing document content."
        sha = _hl.sha256(txt_content).hexdigest()
        preexist = db.create_document(
            title="preexist.txt", source="manual", sha256=sha,
            kind="text", work_id=None, content_path="preexist.txt",
        )

        zip_bytes = _make_zip_bytes({"preexist.txt": txt_content})
        zip_path = tmp_path / "dedup.zip"
        zip_path.write_bytes(zip_bytes)

        parent_doc = db.create_document(
            title="dedup.zip", source=str(zip_path),
            sha256="dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
            kind="zip", work_id=None, content_path=str(zip_path),
        )

        children = self._explode(db, tmp_path, parent_doc["id"], zip_path, "dedup.zip")
        assert preexist["id"] in children, "Dedup hit must be returned as a child id"


# ── 2. Nightshift backfill pass ───────────────────────────────────────────────

class TestZipProvenanceBackfill:
    """_pass_zip_provenance_backfill must fill rows for pre-existing ZIP children."""

    def _insert_zip_child(
        self, db: OrivellumDB, parent_id: str, work_id: str | None = None
    ) -> str:
        """Insert a document that looks like a ZIP child (has from_zip in meta)."""
        import json as _json
        # Use create_document so the objects row is also created correctly,
        # then patch in the from_zip meta directly.
        doc = db.create_document(
            title="child_doc",
            source="/path/to/child",
            sha256=str(uuid.uuid4()).replace("-", ""),  # unique sha to avoid dedup
            kind="text",
            work_id=work_id,
            content_path="/path/to/child",
            meta={"from_zip": parent_id, "zip_name": "archive.zip"},
        )
        return doc["id"]

    def _insert_parent_zip(self, db: OrivellumDB) -> str:
        doc = db.create_document(
            title="archive.zip",
            source="/p/archive.zip",
            sha256=str(uuid.uuid4()).replace("-", ""),
            kind="zip",
            work_id=None,
            content_path="/p/archive.zip",
        )
        return doc["id"]

    def test_backfill_writes_provenance_for_missing_rows(self, tmp_path: Path) -> None:
        """The backfill pass must insert provenance rows for ZIP children that lack them."""
        db = _make_db(tmp_path)
        parent_id = self._insert_parent_zip(db)
        child1 = self._insert_zip_child(db, parent_id)
        child2 = self._insert_zip_child(db, parent_id)

        # Confirm no provenance yet
        assert _count_provenance(db, child1) == 0
        assert _count_provenance(db, child2) == 0

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        report: list[str] = []
        _pass_zip_provenance_backfill(db, report)

        assert _count_provenance(db, child1) == 1, "child1 must get a provenance row"
        assert _count_provenance(db, child2) == 1, "child2 must get a provenance row"

    def test_backfill_sets_correct_origin_id(self, tmp_path: Path) -> None:
        """Backfilled rows must have origin_id = the parent ZIP doc id from meta."""
        db = _make_db(tmp_path)
        parent_id = self._insert_parent_zip(db)
        child_id = self._insert_zip_child(db, parent_id)

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        _pass_zip_provenance_backfill(db, [])

        with db._lock:
            row = db._conn.execute(
                "SELECT origin_id FROM object_provenance WHERE object_id=? AND source='zip_extract'",
                (child_id,),
            ).fetchone()
        assert row is not None
        assert row["origin_id"] == parent_id

    def test_backfill_is_idempotent(self, tmp_path: Path) -> None:
        """Running the backfill pass twice must not produce duplicate provenance rows."""
        db = _make_db(tmp_path)
        parent_id = self._insert_parent_zip(db)
        child_id = self._insert_zip_child(db, parent_id)

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        _pass_zip_provenance_backfill(db, [])
        _pass_zip_provenance_backfill(db, [])

        assert _count_provenance(db, child_id) == 1, (
            "Running backfill twice must not insert duplicate provenance rows "
            "(INSERT OR IGNORE prevents duplicates by object_id + source)"
        )

    def test_backfill_skips_already_covered_children(self, tmp_path: Path) -> None:
        """Children that already have a zip_extract provenance row are left alone."""
        from orivellum.capabilities.persist import record_provenance

        db = _make_db(tmp_path)
        parent_id = self._insert_parent_zip(db)
        child_id = self._insert_zip_child(db, parent_id)

        # Pre-seed the provenance row (as if the pipeline already wrote it)
        record_provenance(child_id, "zip_extract", db, origin_id=parent_id)
        assert _count_provenance(db, child_id) == 1

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        report: list[str] = []
        _pass_zip_provenance_backfill(db, report)

        # Still exactly one row — backfill did not add a duplicate
        assert _count_provenance(db, child_id) == 1
        # No work to report
        assert not any("backfill" in line for line in report), (
            "Backfill pass must not report work if all children already have provenance"
        )

    def test_backfill_does_not_touch_non_zip_documents(self, tmp_path: Path) -> None:
        """Documents without a 'from_zip' meta key must not receive a provenance row."""
        db = _make_db(tmp_path)
        normal_doc = db.create_document(
            title="normal.txt",
            source="/p/normal.txt",
            sha256=str(uuid.uuid4()).replace("-", ""),
            kind="text",
            work_id=None,
            content_path="/p/normal.txt",
            meta={"foo": "bar"},
        )

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        _pass_zip_provenance_backfill(db, [])

        assert _count_provenance(db, normal_doc["id"]) == 0, (
            "Non-ZIP documents must not receive a zip_extract provenance row"
        )

    def test_backfill_report_line_mentions_count(self, tmp_path: Path) -> None:
        """When backfill writes rows, the report must mention the count."""
        db = _make_db(tmp_path)
        parent_id = self._insert_parent_zip(db)
        self._insert_zip_child(db, parent_id)
        self._insert_zip_child(db, parent_id)

        from orivellum.capabilities.nightshift import _pass_zip_provenance_backfill
        report: list[str] = []
        _pass_zip_provenance_backfill(db, report)

        assert any("2" in line for line in report), (
            f"Report must mention 2 backfilled rows; got: {report}"
        )
