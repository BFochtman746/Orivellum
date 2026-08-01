"""Library domain routes — /api/library/*"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

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
    return {"document": doc}


@router.delete("/library/{doc_id}")
def library_delete(doc_id: str):
    db = get_db()
    ok = db.delete_document(doc_id)
    if not ok:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    return {"ok": True}


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
def library_import(body: LibraryImport):
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
    return {"document": doc, "duplicate": False}


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
