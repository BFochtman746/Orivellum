"""Library domain routes — /api/library/*"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re as _re
import uuid as _uuid_mod
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config
from orivellum.capabilities.pipeline import process_document

logger = logging.getLogger(__name__)


# ── Version-suggestion helpers ─────────────────────────────────────────────────

def _stems_similar(a: str, b: str) -> bool:
    """Return True if two filename stems look like versions of the same document."""
    if not a or not b:
        return False
    a = a.lower().strip()
    b = b.lower().strip()
    if a == b:
        return True
    # Strip common version/edition suffixes, then compare cleaned bases
    _VER = _re.compile(
        r"[_\s\-]*(v\d+[\d.]*|draft\d*|rev\d*|copy\d*|\d+|final|interim|updated?)$",
        _re.I,
    )
    a_base = _VER.sub("", a).strip()
    b_base = _VER.sub("", b).strip()
    if a_base and b_base and a_base == b_base:
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.75


def _maybe_suggest_version(db, new_doc_id: str, work_id: str, filename: str) -> None:
    """After importing a doc to a Work, check for similar-titled existing docs
    and create a version-relationship suggestion if one is found."""
    try:
        stem = Path(filename).stem
        existing = db.list_documents(work_id=work_id, limit=200)
        for other in existing:
            if other.get("id") == new_doc_id:
                continue
            other_stem = Path(other.get("title") or "").stem
            if not other_stem:
                continue
            if not _stems_similar(stem, other_stem):
                continue
            # Avoid creating duplicate suggestions for the same pair
            with db._lock:
                already = db._conn.execute(
                    """SELECT id FROM suggestions
                       WHERE work_id=? AND kind='version_relationship'
                       AND (
                           (json_extract(meta,'$.doc_a_id')=? AND json_extract(meta,'$.doc_b_id')=?)
                        OR (json_extract(meta,'$.doc_a_id')=? AND json_extract(meta,'$.doc_b_id')=?)
                       )""",
                    (work_id, other["id"], new_doc_id, new_doc_id, other["id"]),
                ).fetchone()
                if already:
                    continue
                import datetime as _dt
                now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
                meta_payload = json.dumps({
                    "doc_a_id": other["id"],
                    "doc_b_id": new_doc_id,
                    "doc_a_title": other.get("title", ""),
                    "doc_b_title": filename,
                    "similarity_basis": "filename_stem",
                })
                text_label = (
                    f'"{other.get("title") or other_stem}" and "{filename}" '
                    f"may be versions of the same document"
                )
                db._conn.execute(
                    """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        str(_uuid_mod.uuid4()), work_id, "version_relationship",
                        text_label, meta_payload, now_iso,
                    ),
                )
                db._conn.commit()
                logger.debug(
                    "Version suggestion created: %s ↔ %s in work %s",
                    other["id"], new_doc_id, work_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Version suggestion check failed: %s", exc)

router = APIRouter(prefix="/api")

_KIND_MAP = {
    ".pdf": "pdf", ".docx": "docx", ".doc": "docx",
    ".xlsx": "excel", ".xls": "excel", ".csv": "csv",
    ".pptx": "pptx", ".ppt": "pptx",
    ".txt": "text", ".md": "markdown",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".gif": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".py": "code", ".js": "code", ".ts": "code",
    ".jsx": "code", ".tsx": "code", ".java": "code", ".cpp": "code",
    ".c": "code", ".cs": "code", ".go": "code", ".rs": "code", ".rb": "code",
    ".html": "html", ".htm": "html",
    ".json": "json",
    ".zip": "zip",
    ".rtf": "file",   # handled by markitdown fallback
    ".epub": "file",  # handled by markitdown fallback
    ".xml": "file",   # handled by markitdown fallback
}


def _kind_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _KIND_MAP.get(ext, "file")


def _library_root() -> Path:
    cfg = get_config()
    p = Path(cfg.data_dir) / "library"
    p.mkdir(parents=True, exist_ok=True)
    return p


class LibraryImport(BaseModel):
    filename: str
    content_b64: str
    work_id: str | None = None
    meta: dict[str, Any] = {}
    force: bool = False  # Bypass dedup and re-queue extraction (e.g. after an error)


class LibraryActiveWork(BaseModel):
    work_id: str | None = None


@router.get("/library")
def library_list(work_id: str | None = None, kind: str | None = None,
                 readiness: str | None = None, lifecycle: str | None = None,
                 limit: int = 200):
    db = get_db()
    docs = db.list_documents(work_id=work_id, kind=kind, readiness=readiness,
                             lifecycle=lifecycle, limit=min(limit, 1000))
    # Attach extraction warnings to failed documents so the list UI can surface
    # them without a separate per-document request.
    _FAILED = {"error", "no_text"}
    for doc in docs:
        if doc.get("readiness") in _FAILED:
            doc["warnings"] = db.get_extraction_warnings(doc["id"])
        else:
            doc["warnings"] = []
    return {"documents": docs, "count": len(docs)}


@router.get("/library/search")
def library_search(q: str, work_id: str | None = None, limit: int = 10):
    """BM25-ranked document search.

    Queries chunks FTS, then aggregates to document-level results so each
    document appears at most once.  Each result has the same shape as a
    ``GET /api/library`` document entry, with two extra fields:
    - ``snippet``     – the best-matching excerpt from that doc (may contain
                        ``[[`` / ``]]`` markers around matched terms)
    - ``bm25_score``  – raw BM25 relevance score (lower = more relevant)
    """
    if not q:
        raise HTTPException(400, "q parameter required")
    db = get_db()
    # Over-fetch chunks so dedup still returns enough unique docs
    chunk_results = db.search_chunks(q.strip(), work_id=work_id, limit=min(limit * 4, 100))
    seen: dict[str, dict] = {}
    for chunk in chunk_results:
        doc_id = chunk.get("doc_id")
        if not doc_id or doc_id in seen:
            continue
        doc = db.get_document(doc_id)
        if not doc:
            continue
        raw_snip = chunk.get("snippet") or (chunk.get("text") or "")[:300]
        doc["snippet"] = raw_snip
        doc["bm25_score"] = chunk.get("bm25_score")
        seen[doc_id] = doc
    results = list(seen.values())[:limit]
    return {"query": q, "results": results, "count": len(results)}


# ── Duplicates (must be registered BEFORE /{doc_id} so the literal segment wins) ─

@router.get("/library/duplicates")
def library_duplicates(resolved: bool = False):
    """Return all detected near-duplicate / likely-revision document pairs."""
    db = get_db()
    pairs = db.list_near_duplicates(resolved=resolved)
    return {"pairs": pairs, "count": len(pairs)}


_VALID_RESOLVE_ACTIONS = {"keep_both", "mark_versions", "mark_superseded"}


class DupeResolveBody(BaseModel):
    action: str                         # keep_both | mark_versions | mark_superseded
    canonical_doc_id: str | None = None  # when mark_superseded: the doc that should SURVIVE


@router.post("/library/duplicates/{dupe_id}/resolve")
def library_resolve_duplicate(dupe_id: str, body: DupeResolveBody):
    """Resolve a near-duplicate pair.

    action: keep_both — dismiss the alert, no structural change
            mark_versions — create a DERIVED_FROM relationship between the pair
            mark_superseded — set the non-canonical doc lifecycle to superseded;
                              pass canonical_doc_id to specify which survives
                              (defaults to doc_a if not supplied)
    """
    if body.action not in _VALID_RESOLVE_ACTIONS:
        raise HTTPException(
            400,
            f"action must be one of: {', '.join(sorted(_VALID_RESOLVE_ACTIONS))}",
        )
    db = get_db()
    result = db.resolve_near_duplicate(dupe_id, body.action,
                                       canonical_doc_id=body.canonical_doc_id)
    if result is None:
        raise HTTPException(404, f"Duplicate pair {dupe_id!r} not found")
    return {"ok": True, "dupe_id": dupe_id, "action": body.action}


@router.get("/library/{doc_id}")
def library_get(doc_id: str):
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    # Always include warnings array; populated for any failure state.
    _FAILED = {"error", "no_text"}
    doc["warnings"] = (
        db.get_extraction_warnings(doc_id)
        if doc.get("readiness") in _FAILED
        else []
    )
    return {"document": doc}


class DocumentUpdate(BaseModel):
    work_id: str | None = None


@router.patch("/library/{doc_id}")
def library_update(doc_id: str, body: DocumentUpdate):
    db = get_db()
    if not db.get_document(doc_id):
        raise HTTPException(404, f"Document {doc_id!r} not found")
    db.update_document_work(doc_id, body.work_id)
    return {"document": db.get_document(doc_id)}


_VALID_DOC_LIFECYCLES = {"draft", "canonical", "superseded", "reference"}


class LifecycleUpdate(BaseModel):
    lifecycle: str


@router.patch("/library/{doc_id}/lifecycle")
def library_set_lifecycle(doc_id: str, body: LifecycleUpdate):
    """Declare a document's authority state: draft | canonical | superseded | reference."""
    if body.lifecycle not in _VALID_DOC_LIFECYCLES:
        raise HTTPException(
            400,
            f"lifecycle must be one of: {', '.join(sorted(_VALID_DOC_LIFECYCLES))}",
        )
    db = get_db()
    ok = db.update_document_lifecycle(doc_id, body.lifecycle)
    if not ok:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    return {"ok": True, "lifecycle": body.lifecycle, "document": db.get_document(doc_id)}


@router.delete("/library/{doc_id}")
def library_delete(doc_id: str):
    db = get_db()
    ok = db.delete_document(doc_id)
    if not ok:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    return {"ok": True}


@router.get("/library/{doc_id}/knowledge")
def library_doc_knowledge(doc_id: str, limit: int = 200):
    """Return all knowledge items sourced from this document."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    with db._lock:
        rows = db._conn.execute(
            """SELECT * FROM knowledge
               WHERE source_doc_id=?
               ORDER BY confidence DESC, created_at DESC
               LIMIT ?""",
            (doc_id, min(limit, 500)),
        ).fetchall()
    items = [db._k_dict(r) for r in rows]
    return {"knowledge": items, "count": len(items), "doc_id": doc_id}


@router.get("/library/{doc_id}/versions")
def library_versions(doc_id: str):
    db = get_db()
    if not db.get_document(doc_id):
        raise HTTPException(404, f"Document {doc_id!r} not found")
    versions = db.list_document_versions(doc_id)
    return {"versions": versions, "count": len(versions), "doc_id": doc_id}


@router.post("/library/{doc_id}/versions")
def library_create_version(doc_id: str, body: dict = Body(default={})):
    """Snapshot current document state as a new version."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    version = db.create_document_version(
        doc_id=doc_id,
        sha256=doc.get("sha256"),
        word_count=doc.get("word_count") or 0,
        notes=body.get("notes"),
        is_canonical=bool(body.get("is_canonical", False)),
    )
    # audit is emitted inside db.create_document_version
    return {"version": version}


@router.patch("/library/{doc_id}/versions/{version_id}/canonical")
def library_set_canonical(doc_id: str, version_id: str):
    """Mark a version as canonical (the authoritative version)."""
    db = get_db()
    ok = db.set_canonical_version(doc_id, version_id)
    if not ok:
        raise HTTPException(404, "Version not found")
    # audit is emitted inside db.set_canonical_version
    return {"ok": True}


@router.get("/library/{doc_id}/chapters")
def library_chapters(doc_id: str):
    """Return all extracted chapter/section records for a document."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    chapters = db.get_book_chapters(doc_id)
    return {"chapters": chapters, "count": len(chapters), "doc_id": doc_id}


@router.get("/library/{doc_id}/chunks")
def library_chunks(doc_id: str):
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY page, created_at LIMIT 500",
            (doc_id,)
        ).fetchall()
    return {"chunks": [dict(r) for r in rows], "count": len(rows)}


@router.post("/library/import")
def library_import(body: LibraryImport, background_tasks: BackgroundTasks):
    db = get_db()
    # Validate and decode
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")

    name = Path(body.filename).name
    if not name or name.startswith("."):
        raise HTTPException(400, f"Bad filename: {body.filename!r}")

    if body.work_id:
        if not db.get_work(body.work_id):
            raise HTTPException(404, f"Work {body.work_id!r} not found")

    # SHA-256 dedup
    sha256 = hashlib.sha256(data).hexdigest()
    with db._lock:
        existing = db._conn.execute(
            "SELECT id FROM documents WHERE sha256=?", (sha256,)
        ).fetchone()
    if existing:
        doc = db.get_document(existing["id"])
        readiness = doc.get("readiness", "")
        _FAILED = {"error", "no_text"}

        # If force=True, re-queue regardless of current readiness (including ready).
        # If NOT forced, only re-queue when the doc is in a failed state.
        should_requeue = body.force or (readiness in _FAILED)
        if should_requeue:
            content_path = doc.get("content_path")
            lib_root = _library_root()
            file_path_existing = (lib_root / content_path) if content_path else None

            if file_path_existing and file_path_existing.exists():
                db.delete_extraction_warnings(doc["id"])
                db.update_document_extracted(
                    doc["id"], "", 0, readiness="imported", error_message=None
                )
                kind = doc.get("kind") or _kind_for(name)
                _EXTRACTABLE = {"pdf", "docx", "excel", "csv", "pptx", "text", "markdown",
                               "code", "html", "json", "zip", "file"}
                if kind in _EXTRACTABLE:
                    logger.info(
                        "Re-queuing extraction for duplicate doc=%s kind=%s (force=%s readiness_was=%s)",
                        doc["id"], kind, body.force, readiness,
                    )
                    background_tasks.add_task(
                        process_document,
                        doc_id=doc["id"],
                        file_path=str(file_path_existing),
                        kind=kind,
                        work_id=doc.get("work_id") or body.work_id,
                        title=doc.get("title", name),
                        db=db,
                    )
                doc = db.get_document(doc["id"])

        # Always surface readiness and warnings at the top level so callers know
        # the state without a second request.
        current_readiness = doc.get("readiness")
        warnings = (
            db.get_extraction_warnings(doc["id"])
            if current_readiness in {"error", "no_text"}
            else []
        )
        doc["warnings"] = warnings
        return {
            "document": doc,
            "duplicate": True,
            "readiness": current_readiness,
            "warnings": warnings,
        }

    # Store file
    kind = _kind_for(name)
    lib_root = _library_root()
    dest = lib_root / sha256[:2] / sha256[2:4]
    dest.mkdir(parents=True, exist_ok=True)
    file_path = dest / name
    file_path.write_bytes(data)

    doc = db.create_document(
        title=name,
        source=str(file_path),
        sha256=sha256,
        kind=kind,
        work_id=body.work_id,
        content_path=str(file_path.relative_to(lib_root)),
        meta=body.meta,
    )

    # Fire extraction + chunking + knowledge harvest in the background.
    # BackgroundTasks runs after the response is sent — safe with SQLite WAL mode.
    _EXTRACTABLE = {"pdf", "docx", "excel", "csv", "pptx", "text", "markdown",
                   "code", "html", "json", "zip", "file"}
    if kind in _EXTRACTABLE:
        logger.info("Queuing extraction for doc=%s kind=%s", doc["id"], kind)
        background_tasks.add_task(
            process_document,
            doc_id=doc["id"],
            file_path=str(file_path),
            kind=kind,
            work_id=body.work_id,
            title=name,
            db=db,
        )

    # Version suggestion: check for similar-named docs in the same Work
    if body.work_id:
        _maybe_suggest_version(db, doc["id"], body.work_id, name)

    return {"document": doc, "duplicate": False}


@router.post("/library/{doc_id}/extract")
def library_extract(doc_id: str, background_tasks: BackgroundTasks):
    """Alias for /reprocess — re-queues extraction for a document in error state."""
    return library_reprocess(doc_id, background_tasks)


@router.post("/library/{doc_id}/reprocess")
def library_reprocess(
    doc_id: str,
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """Re-run extraction on a document that previously failed or produced no text.

    Pass ``force=true`` to re-queue extraction even when the document is already
    ``ready`` (e.g. to recover from missing chapter structure).

    Resolves the file from the stored content_path so this works after a
    server restart even if the original absolute path has changed.
    """
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")

    current = doc.get("readiness", "")
    if current == "ready" and not force:
        return {"ok": True, "message": "Document is already ready — skipping reprocess"}

    # Resolve the file path
    content_path = doc.get("content_path")
    if not content_path:
        raise HTTPException(400, "Document has no stored file path (content_path is empty)")

    lib_root = _library_root()
    file_path = lib_root / content_path
    if not file_path.exists():
        raise HTTPException(
            404,
            f"File not found at {file_path}. The file may have been moved or deleted.",
        )

    # Clear prior warnings so a fresh run isn't presented alongside stale history
    db.delete_extraction_warnings(doc_id)
    # Reset status so the UI shows processing
    db.update_document_extracted(doc_id, "", 0, readiness="imported", error_message=None)

    kind = doc.get("kind") or _kind_for(doc.get("title", ""))
    background_tasks.add_task(
        process_document,
        doc_id=doc_id,
        file_path=str(file_path),
        kind=kind,
        work_id=doc.get("work_id"),
        title=doc.get("title", ""),
        db=db,
    )
    logger.info("Queued reprocess for doc=%s kind=%s", doc_id, kind)
    return {"ok": True, "doc_id": doc_id, "message": "Reprocessing queued"}


@router.get("/library/{doc_id}/download")
def download_document(doc_id: str):
    """Serve the original stored file as a download."""
    from fastapi.responses import FileResponse
    import mimetypes
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    content_path = doc.get("content_path")
    if not content_path:
        raise HTTPException(400, "Document has no stored file (content_path is empty)")
    lib_root = _library_root()
    file_path = lib_root / content_path
    if not file_path.exists():
        raise HTTPException(404, "Stored file not found — it may have been deleted")
    filename = doc.get("title") or file_path.name
    # Ensure filename has the right extension
    if not Path(filename).suffix:
        filename = filename + file_path.suffix
    mime, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(
        str(file_path),
        media_type=mime or "application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/library/active-work")
def library_get_active_work():
    db = get_db()
    work_id = db.get_setting("active_work_id")
    work = db.get_work(work_id) if work_id else None
    return {"work_id": work_id or None, "work": work}


@router.post("/library/active-work")
def library_set_active_work(body: LibraryActiveWork):
    db = get_db()
    if body.work_id and not db.get_work(body.work_id):
        raise HTTPException(404, f"Work {body.work_id!r} not found")
    db.set_setting("active_work_id", body.work_id or "", actor="user")
    return {"ok": True, "work_id": body.work_id}


@router.post("/library/explode-zips")
def library_explode_zips(background_tasks: BackgroundTasks):
    """Re-process every ZIP document, exploding each archive into individual
    child documents so users see content rather than opaque zip containers.
    Safe to call multiple times — already-exploded archives will just
    re-enumerate (dedup by SHA-256 prevents duplicate child docs).
    """
    db = get_db()
    lib_root = _library_root()

    with db._lock:
        rows = db._conn.execute(
            "SELECT id, source, content_path, work_id, title FROM documents WHERE kind='zip'"
        ).fetchall()

    queued = 0
    for row in rows:
        content_path = row["content_path"]
        if content_path:
            file_path = lib_root / content_path
        else:
            file_path = Path(row["source"]) if row["source"] else None

        if not file_path or not file_path.exists():
            logger.warning("ZIP explode: file missing for doc %s — skipping", row["id"])
            continue

        db.delete_extraction_warnings(row["id"])
        db.update_document_extracted(row["id"], "", 0,
                                     readiness="imported", error_message=None)
        background_tasks.add_task(
            process_document,
            doc_id=row["id"],
            file_path=str(file_path),
            kind="zip",
            work_id=row["work_id"],
            title=row["title"] or file_path.name,
            db=db,
        )
        queued += 1
        logger.info("Queued ZIP explosion for doc=%s", row["id"])

    return {
        "queued": queued,
        "message": (
            f"Queued {queued} ZIP archive(s) for extraction. "
            "Each file inside will become its own library document."
        ),
    }


@router.post("/library/reprocess-all")
def library_reprocess_all(
    background_tasks: BackgroundTasks,
    force: bool = False,
):
    """Bulk re-extract every document that hasn't finished processing.

    Pass ``force=true`` to also re-run documents that are already ``ready``
    (useful after an extraction engine upgrade).

    Strategy per document kind:
    - ``zip`` — always re-exploded into individual child documents.
    - Any other kind stuck in ``imported`` / ``error`` / ``no_text`` — queued
      for normal extraction using the stored ``content_path`` and ``kind``.

    Already-exploded ZIPs are safe to re-run: SHA-256 dedup prevents duplicates.
    Documents whose source file is no longer on disk are skipped with a warning.

    Returns a summary: total queued, breakdown by category, and any skipped.
    """
    db      = get_db()
    lib_root = _library_root()

    # ── Collect candidates ─────────────────────────────────────────────────────
    readiness_filter: tuple[str, ...]
    if force:
        readiness_filter = ("imported", "error", "no_text", "ready")
    else:
        readiness_filter = ("imported", "error", "no_text")

    placeholders = ",".join("?" * len(readiness_filter))
    with db._lock:
        rows = db._conn.execute(
            f"""SELECT id, source, content_path, work_id, title, kind, readiness
                FROM documents
                WHERE readiness IN ({placeholders})
                ORDER BY created_at DESC""",
            readiness_filter,
        ).fetchall()

    # Also always include ZIPs (even 'ready' ones — exploding is idempotent)
    with db._lock:
        zip_rows = db._conn.execute(
            "SELECT id, source, content_path, work_id, title, kind, readiness "
            "FROM documents WHERE kind='zip'",
        ).fetchall()

    # Merge; use doc id as dedup key so ZIPs only appear once
    seen: set[str] = set()
    candidates: list[Any] = []
    for row in list(rows) + list(zip_rows):
        if row["id"] not in seen:
            seen.add(row["id"])
            candidates.append(row)

    # ── Queue each candidate ───────────────────────────────────────────────────
    queued_zips    = 0
    queued_stuck   = 0
    skipped        = 0

    for row in candidates:
        doc_id       = row["id"]
        content_path = row["content_path"]
        kind         = row["kind"] or "text"

        # Resolve file on disk
        if content_path:
            file_path = lib_root / content_path
        elif row["source"]:
            file_path = Path(row["source"])
        else:
            file_path = None

        if not file_path or not file_path.exists():
            logger.warning(
                "reprocess-all: file missing for doc %s (kind=%s) — skipping",
                doc_id, kind,
            )
            skipped += 1
            continue

        db.delete_extraction_warnings(doc_id)
        db.update_document_extracted(doc_id, "", 0,
                                     readiness="imported", error_message=None)

        background_tasks.add_task(
            process_document,
            doc_id=doc_id,
            file_path=str(file_path),
            kind=kind,
            work_id=row["work_id"],
            title=row["title"] or file_path.name,
            db=db,
        )

        if kind == "zip":
            queued_zips += 1
        else:
            queued_stuck += 1

        logger.info(
            "reprocess-all: queued doc=%s kind=%s previous_readiness=%s",
            doc_id, kind, row["readiness"],
        )

    total = queued_zips + queued_stuck
    parts: list[str] = []
    if queued_zips:
        parts.append(f"{queued_zips} ZIP archive(s) will be exploded into child documents")
    if queued_stuck:
        parts.append(f"{queued_stuck} document(s) re-queued for extraction")
    if skipped:
        parts.append(f"{skipped} skipped (source file not found on disk)")

    return {
        "queued":       total,
        "queued_zips":  queued_zips,
        "queued_stuck": queued_stuck,
        "skipped":      skipped,
        "message":      ". ".join(parts) if parts else "Nothing to reprocess.",
    }


@router.post("/library/smart-organize")
def library_smart_organize():
    """Group unassigned documents into Works based on their ZIP origin.

    Documents extracted from the same ZIP archive are grouped under a Work
    named after that archive.  Documents already linked to a Work are skipped.
    Returns a summary of Works created and documents organised.
    """
    import json as _json
    import re as _re

    db = get_db()

    with db._lock:
        rows = db._conn.execute(
            """SELECT id, title, meta, work_id
               FROM documents
               WHERE readiness = 'ready'
               ORDER BY created_at DESC
               LIMIT 2000"""
        ).fetchall()

    # Group docs without a Work by their zip_name origin
    groups: dict[str, list[str]] = {}
    for row in rows:
        if row["work_id"]:
            continue  # Already organised
        try:
            meta = _json.loads(row["meta"] or "{}")
        except Exception:
            meta = {}

        zip_name = meta.get("zip_name", "")
        zip_folder = meta.get("zip_folder", "")

        if zip_name:
            # Use zip filename stem, cleaned up
            key = Path(zip_name).stem
            key = _re.sub(r"[_\-\s]+(archive|files|docs|documents|library|collection|pack)$",
                          "", key, flags=_re.I).strip() or key
        elif zip_folder and zip_folder not in (".", ""):
            key = zip_folder.split("/")[0]
        else:
            continue  # No grouping hint

        if key:
            groups.setdefault(key, []).append(row["id"])

    # Create / reuse a Work for each group with at least 2 documents
    created_works: list[dict] = []
    total_assigned = 0
    for key, doc_ids in groups.items():
        if len(doc_ids) < 2:
            continue

        # Reuse existing work with same title (case-insensitive)
        with db._lock:
            existing = db._conn.execute(
                "SELECT id FROM works WHERE lower(title)=lower(?)", (key,)
            ).fetchone()

        if existing:
            work_id = existing["id"]
        else:
            work = db.create_work(title=key)
            work_id = work["id"]
            created_works.append({"id": work_id, "title": key, "doc_count": len(doc_ids)})

        for doc_id in doc_ids:
            db.update_document_work(doc_id, work_id)
        total_assigned += len(doc_ids)

    return {
        "works_created": len(created_works),
        "works": created_works,
        "docs_organised": total_assigned,
        "message": (
            f"Created {len(created_works)} Work(s) and organised "
            f"{total_assigned} document(s)."
        ),
    }
