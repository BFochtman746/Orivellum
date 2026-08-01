"""Library domain routes — /api/library/*"""
from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config
from orivellum.capabilities.pipeline import process_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

_KIND_MAP = {
    ".pdf": "pdf", ".docx": "docx", ".doc": "docx",
    ".xlsx": "excel", ".xls": "excel", ".csv": "csv",
    ".pptx": "pptx", ".ppt": "pptx",
    ".txt": "text", ".md": "markdown",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".py": "code", ".js": "code", ".ts": "code",
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


class LibraryActiveWork(BaseModel):
    work_id: str | None = None


@router.get("/library")
def library_list(work_id: str | None = None, kind: str | None = None,
                 readiness: str | None = None, limit: int = 200):
    db = get_db()
    docs = db.list_documents(work_id=work_id, kind=kind, readiness=readiness,
                             limit=min(limit, 1000))
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
    if not q:
        raise HTTPException(400, "q parameter required")
    db = get_db()
    results = db.search_chunks(q.strip(), work_id=work_id, limit=min(limit, 50))
    return {"query": q, "results": results, "count": len(results)}


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
        return {"document": doc, "duplicate": True}

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
    _EXTRACTABLE = {"pdf", "docx", "excel", "csv", "pptx", "text", "markdown", "code"}
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

    return {"document": doc, "duplicate": False}


@router.post("/library/{doc_id}/extract")
def library_extract(doc_id: str, background_tasks: BackgroundTasks):
    """Alias for /reprocess — re-queues extraction for a document in error state."""
    return library_reprocess(doc_id, background_tasks)


@router.post("/library/{doc_id}/reprocess")
def library_reprocess(doc_id: str, background_tasks: BackgroundTasks):
    """Re-run extraction on a document that previously failed or produced no text.

    Resolves the file from the stored content_path so this works after a
    server restart even if the original absolute path has changed.
    """
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")

    current = doc.get("readiness", "")
    if current == "ready":
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
    db.set_setting("active_work_id", body.work_id or "")
    return {"ok": True, "work_id": body.work_id}
