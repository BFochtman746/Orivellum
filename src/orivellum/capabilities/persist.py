"""Save/Process/Recall invariant — central registration and indexing hook.

Every object that enters or leaves Orivellum (uploaded doc, generated file,
TTS clip, audiobook, generated image, research note) should call
``register_and_index`` so it is:

1. Saved as a library document with provenance metadata.
2. Chunked for FTS keyword search.
3. Embedded for semantic search (background daemon thread — never blocks caller).
4. Tracked in ``object_provenance`` for audit / recall queries.

Callers
-------
- capabilities/generate.py  → _register_output (generation pipeline)
- api/routes/studio.py      → TTS / image-gen / audiobook routes
- Any future create-path
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.persist")

_CHUNK_SIZE = 1_000  # chars per chunk (matches generate.py)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Provenance recording ───────────────────────────────────────────────────────


def record_provenance(
    object_id: str,
    source: str,
    db: OrivellumDB,
    origin_id: str | None = None,
    work_id: str | None = None,
    topic_id: str | None = None,
) -> None:
    """Write a row to ``object_provenance``.  Best-effort — never raises."""
    try:
        with db._lock:
            db._conn.execute(
                """INSERT OR IGNORE INTO object_provenance
                   (id, object_id, source, origin_id, work_id, topic_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (_uuid(), object_id, source, origin_id, work_id, topic_id, _now()),
            )
            db._conn.commit()
    except Exception as exc:
        logger.debug("record_provenance failed (non-fatal): %s", exc)


# ── Chunk helpers ──────────────────────────────────────────────────────────────


def _ensure_lib_symlink(doc_path: Path, lib_root: Path) -> str:
    """Return a lib-root-relative content_path that Library can resolve.

    If *doc_path* is already under *lib_root*, return the relative path
    directly.  Otherwise create a symbolic link inside ``lib_root/generated/``
    pointing to the absolute file and return that relative path.

    Falls back to the raw relative-to-lib_root string on any OS error so the
    caller can still proceed (search works; reprocess may degrade gracefully).
    """
    try:
        # Already inside lib_root — just relativise.
        return str(doc_path.resolve().relative_to(lib_root.resolve()))
    except ValueError:
        pass

    # File is outside lib_root — symlink it in.
    gen_dir = lib_root / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)

    link_name = doc_path.name
    link_path = gen_dir / link_name
    # Avoid collision: if name is taken by a *different* file, prefix with a short id.
    # Use inode comparison (not resolved path) so two hard links to the same file
    # are correctly detected as "same file" and we return the existing link path
    # instead of creating a duplicate entry under a UUID-prefixed name.
    if link_path.exists() or link_path.is_symlink():
        try:
            link_stat = link_path.stat()
            source_stat = doc_path.stat()
            same_inode = (
                link_stat.st_ino == source_stat.st_ino and link_stat.st_dev == source_stat.st_dev
            )
            if same_inode:
                # Link already points to our file — return it directly.
                return str(link_path.relative_to(lib_root))
            # Name collision with a different file — add short prefix.
            link_name = f"{_uuid()[:8]}_{doc_path.name}"
            link_path = gen_dir / link_name
        except Exception:
            link_name = f"{_uuid()[:8]}_{doc_path.name}"
            link_path = gen_dir / link_name

    if not link_path.exists() and not link_path.is_symlink():
        # Hard link first: zero disk cost AND durable past source deletion.
        # If the original is later removed (e.g. by _rotate_outputs), the
        # library copy survives because hard links share the inode.
        # Falls back to a full copy on cross-filesystem or permission error.
        try:
            os.link(str(doc_path.resolve()), str(link_path))
        except OSError:
            try:
                import shutil

                shutil.copy2(str(doc_path.resolve()), str(link_path))
            except Exception as exc:
                logger.debug("_ensure_lib_symlink: copy failed (%s) — using abs path", exc)
                # Absolute path as last resort: Python's Path('/a') / '/b' == Path('/b')
                return str(doc_path.resolve())

    return str(link_path.relative_to(lib_root))


def _chunk_text(text: str) -> list[str]:
    """Split *text* into ~_CHUNK_SIZE-char word-boundary chunks."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        wl = len(word) + 1
        if current_len + wl > _CHUNK_SIZE and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(word)
        current_len += wl
    if current:
        chunks.append(" ".join(current))
    return chunks


# ── Main registration hook ────────────────────────────────────────────────────


def register_and_index(
    doc_path: Path,
    text_content: str,
    kind: str,
    db: OrivellumDB,
    cfg: OrivellumConfig,
    *,
    title: str | None = None,
    work_id: str | None = None,
    provenance_source: str = "generation",
    origin_id: str | None = None,
    topic_id: str | None = None,
    tier: str = "artifact",
    _prelinked_rel: str | None = None,
) -> str:
    """Register *doc_path* as a library document, chunk it, embed it, record provenance.

    Parameters
    ----------
    doc_path:
        Absolute path to the file.  Must exist on disk.
    text_content:
        Searchable text for the object (generated doc body, TTS source text,
        image caption / prompt, etc.).  Written to ``extracted_text`` and split
        into FTS chunks.
    kind:
        File type label (e.g. ``"mp3"``, ``"png"``, ``"docx"``).
    db, cfg:
        Standard DB / config dependencies.
    title:
        Human-readable title for the library entry.  Defaults to the file stem.
    work_id:
        Owning Work, if applicable.
    provenance_source:
        One of ``upload``, ``chat``, ``generation``, ``studio``, ``intake``.
    origin_id:
        Conversation ID, job ID, or other creator identifier.
    topic_id:
        Topic area, if applicable.
    tier:
        Document tier (default ``"artifact"`` for generated/studio outputs).

    Returns
    -------
    str
        The new (or existing) ``doc_id``.
    """
    data_dir = Path(cfg.data_dir)
    lib_root = data_dir / "library"

    # ── Create library document ────────────────────────────────────────────────
    sha: str | None = None
    if doc_path.exists():
        try:
            sha = hashlib.sha256(doc_path.read_bytes()).hexdigest()
        except Exception:
            pass

    doc_title = title or doc_path.stem

    # ── SHA dedup: return existing document without creating a second library entry ──
    if sha:
        with db._lock:
            existing = db._conn.execute(
                "SELECT id FROM documents WHERE sha256=? LIMIT 1", (sha,)
            ).fetchone()
        if existing:
            doc_id: str = existing["id"]
            # Still record provenance in case the source/caller changed.
            record_provenance(
                doc_id,
                provenance_source,
                db,
                origin_id=origin_id,
                work_id=work_id,
                topic_id=topic_id,
            )
            return doc_id

    # ── Create library-root-relative content path ──────────────────────────────
    # Studio callers pass _prelinked_rel when they already called _ensure_lib_symlink
    # synchronously *before* _rotate_outputs ran.  That guarantees the hard link is
    # durable even if rotation deleted the source file before this background thread
    # starts.  When _prelinked_rel is absent (non-Studio callers), fall through to
    # the standard _ensure_lib_symlink path which creates the link on demand.
    if _prelinked_rel:
        rel = _prelinked_rel
    else:
        rel = _ensure_lib_symlink(doc_path, lib_root)

    try:
        doc = db.create_document(
            title=doc_title,
            source=f"{provenance_source}/{kind}",
            sha256=sha,
            kind=kind,
            work_id=work_id,
            content_path=rel,
            meta={
                "provenance": provenance_source,
                "origin_id": origin_id,
                "generated_at": _now(),
            },
            tier=tier,
        )
        doc_id = doc["id"]
    except Exception as exc:
        logger.warning("register_and_index: could not create document: %s", exc)
        raise

    # ── Store extracted text + FTS chunks ─────────────────────────────────────
    body = text_content.strip() or f"{provenance_source} {kind} output"
    try:
        db.update_document_extracted(
            doc_id,
            extracted_text=body,
            word_count=max(len(body.split()), 1),
            readiness="ready",
        )
    except Exception as exc:
        logger.warning("register_and_index: update_document_extracted failed: %s", exc)

    for page, chunk in enumerate(_chunk_text(body)):
        try:
            db.add_chunk(doc_id, chunk, page=page)
        except Exception as exc:
            logger.debug("register_and_index: add_chunk page %d failed: %s", page, exc)

    # ── Background embedding ───────────────────────────────────────────────────
    def _embed() -> None:
        try:
            from orivellum.capabilities.embeddings import embed_chunks_for_doc

            embed_chunks_for_doc(doc_id, db)
        except Exception as exc:
            logger.debug("register_and_index background embed failed: %s", exc)

    # Route through the tracked executor so embedding work is bounded and
    # appears in the job dashboard.  Falls back to a daemon thread only when
    # the executor has not been initialised yet (test harnesses, CLI scripts).
    try:
        from orivellum.api.executor import _tracked_submit as _ts_embed

        _ts_embed(_embed, kind="embed", label=f"embed:{doc_id[:8]}")
    except Exception as _exc_embed:
        logger.warning(
            "Executor unavailable for embed:%s, falling back to thread: %s", doc_id[:8], _exc_embed
        )
        threading.Thread(target=_embed, daemon=True).start()

    # ── Provenance record ─────────────────────────────────────────────────────
    record_provenance(
        doc_id, provenance_source, db, origin_id=origin_id, work_id=work_id, topic_id=topic_id
    )

    logger.debug(
        "register_and_index: registered doc %s (%s) source=%s", doc_id[:8], kind, provenance_source
    )
    return doc_id


# ── Convenience wrapper for in-memory text content (no file) ──────────────────


def register_text_note(
    text: str,
    db: OrivellumDB,
    cfg: OrivellumConfig,
    *,
    title: str = "Generated note",
    work_id: str | None = None,
    provenance_source: str = "generation",
    origin_id: str | None = None,
    topic_id: str | None = None,
) -> str:
    """Register a plain-text snippet (no file) as a searchable library note.

    Useful for research summaries, AI-generated notes, and other text that has
    no backing file but should still be recalled semantically.

    Returns the new ``doc_id``.
    """
    Path(cfg.data_dir)
    lib_root = Path(cfg.data_dir) / "library"
    # Write directly into lib_root/generated/notes/ so content_path is
    # naturally lib-root-relative without needing a symlink.
    note_dir = lib_root / "generated" / "notes"
    note_dir.mkdir(parents=True, exist_ok=True)

    # Write to a small .txt file so the library document has a content_path.
    note_path = note_dir / f"{_uuid()[:8]}.txt"
    note_path.write_text(text, encoding="utf-8")

    return register_and_index(
        doc_path=note_path,
        text_content=text,
        kind="txt",
        db=db,
        cfg=cfg,
        title=title,
        work_id=work_id,
        provenance_source=provenance_source,
        origin_id=origin_id,
        topic_id=topic_id,
        tier="artifact",
    )
