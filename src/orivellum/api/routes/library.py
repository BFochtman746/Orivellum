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

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.capabilities.pipeline import (
    process_document,
    release_extraction,
    try_reserve_extraction,
)

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

                now_iso = _dt.datetime.now(_dt.UTC).isoformat()
                meta_payload = json.dumps(
                    {
                        "doc_a_id": other["id"],
                        "doc_b_id": new_doc_id,
                        "doc_a_title": other.get("title", ""),
                        "doc_b_title": filename,
                        "similarity_basis": "filename_stem",
                    }
                )
                text_label = (
                    f'"{other.get("title") or other_stem}" and "{filename}" '
                    f"may be versions of the same document"
                )
                db._conn.execute(
                    """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        str(_uuid_mod.uuid4()),
                        work_id,
                        "version_relationship",
                        text_label,
                        meta_payload,
                        now_iso,
                    ),
                )
                db._conn.commit()
                logger.debug(
                    "Version suggestion created: %s ↔ %s in work %s",
                    other["id"],
                    new_doc_id,
                    work_id,
                )
    except Exception as exc:  # noqa: BLE001
        logger.debug("Version suggestion check failed: %s", exc)


router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
_KIND_MAP = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "csv",
    ".pptx": "pptx",
    ".ppt": "pptx",
    ".txt": "text",
    ".md": "markdown",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".java": "code",
    ".cpp": "code",
    ".c": "code",
    ".cs": "code",
    ".go": "code",
    ".rs": "code",
    ".rb": "code",
    ".html": "html",
    ".htm": "html",
    ".json": "json",
    ".zip": "zip",
    ".eml": "email",  # RFC 2822 email file — stdlib email module parses
    ".msg": "email",  # Outlook compound document — extract_msg or raw fallback
    ".rtf": "file",  # handled by markitdown fallback
    ".epub": "file",  # handled by markitdown fallback
    ".xml": "file",  # handled by markitdown fallback
}


def _kind_for(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return _KIND_MAP.get(ext, "file")


# Magic-byte signatures for MIME validation.
# Each entry: (extension_set, expected_magic_bytes, max_offset).
# We check that the first `max_offset + len(sig)` bytes contain the signature
# at position `max_offset` (or 0 for fixed-offset checks).
_MIME_SIGNATURES: list[tuple[frozenset[str], bytes, int]] = [
    (frozenset({".pdf"}), b"%PDF", 0),
    (frozenset({".png"}), b"\x89PNG\r\n\x1a\n", 0),
    (frozenset({".jpg", ".jpeg"}), b"\xff\xd8\xff", 0),
    (frozenset({".zip", ".docx", ".xlsx", ".pptx", ".epub"}), b"PK\x03\x04", 0),
    (frozenset({".ogg"}), b"OggS", 0),
    (frozenset({".wav"}), b"RIFF", 0),
    (frozenset({".mp3"}), b"ID3", 0),
    (frozenset({".mp3"}), b"\xff\xfb", 0),  # MP3 without ID3
    (frozenset({".mp3"}), b"\xff\xfa", 0),  # MP3 without ID3 (MPEG-1 Layer 3)
    (frozenset({".flac"}), b"fLaC", 0),
    (frozenset({".m4a", ".mp4"}), b"ftyp", 4),  # ISO base media container
    (frozenset({".webm"}), b"\x1a\x45\xdf\xa3", 0),  # EBML (WebM/Matroska)
    (frozenset({".webp"}), b"WEBP", 8),  # RIFF????WEBP
    (frozenset({".gif"}), b"GIF8", 0),
]
_MIME_CHECK_BYTES = 32  # how many bytes to read


def _validate_mime_signature(file_path: Path, original_name: str = "") -> None:
    """Raise HTTPException 415 when file magic bytes don't match the extension.

    Non-fatal for extensions without a known signature — those pass through.
    Uses *original_name* (the user-supplied filename) to determine the expected
    extension, not *file_path* which may be a temp file with a .part suffix.
    """
    ext = Path(original_name).suffix.lower() if original_name else file_path.suffix.lower()
    # Find relevant signatures for this extension
    relevant = [(sig_bytes, offset) for exts, sig_bytes, offset in _MIME_SIGNATURES if ext in exts]
    if not relevant:
        return  # No known magic for this type — allow through

    try:
        with open(file_path, "rb") as fh:
            header = fh.read(_MIME_CHECK_BYTES + 16)
    except OSError:
        return  # Can't read — let the extractor handle it

    for sig_bytes, offset in relevant:
        window = header[offset : offset + len(sig_bytes)]
        if window == sig_bytes:
            return  # Signature matched — OK

    raise HTTPException(
        415,
        f"File content does not match its extension ({ext}). "
        "The file may be corrupt or misnamed. Please verify and re-upload.",
    )


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
def library_list(
    work_id: str | None = None,
    kind: str | None = None,
    readiness: str | None = None,
    lifecycle: str | None = None,
    limit: int = 200,
):
    db = get_db()
    docs = db.list_documents(
        work_id=work_id, kind=kind, readiness=readiness, lifecycle=lifecycle, limit=min(limit, 1000)
    )
    # Attach extraction warnings to failed documents so the list UI can surface
    # them without a separate per-document request.
    _FAILED = {"error", "no_text"}
    for doc in docs:
        if doc.get("readiness") in _FAILED:
            doc["warnings"] = db.get_extraction_warnings(doc["id"])
        else:
            doc["warnings"] = []
    return {"documents": docs, "count": len(docs)}


_SEARCH_MODES = {"keyword", "semantic", "hybrid"}


@router.get("/library/search")
def library_search(q: str, work_id: str | None = None, limit: int = 10, mode: str = "hybrid"):
    """Document search — keyword (BM25), semantic (cosine), or hybrid (RRF).

    Queries chunks, then aggregates to document-level results so each
    document appears at most once.  Each result has the same shape as a
    ``GET /api/library`` document entry, with extra fields:
    - ``snippet``     – best-matching excerpt (keyword hits carry ``[[``/``]]``
                        markers around matched terms)
    - ``bm25_score``  – raw BM25 relevance score (keyword/hybrid, lower = better)
    - ``score``       – cosine similarity (semantic/hybrid when available)
    - ``match_type``  – "keyword" | "semantic" | "both" (hybrid/semantic modes)

    Semantic and hybrid modes degrade gracefully to keyword-only results when
    the local embeddings endpoint is unavailable.
    """
    if not q:
        raise HTTPException(400, "q parameter required")
    if mode not in _SEARCH_MODES:
        raise HTTPException(400, f"mode must be one of: {', '.join(sorted(_SEARCH_MODES))}")
    db = get_db()
    query = q.strip()
    fetch = min(limit * 4, 100)

    # Over-fetch chunks so doc-level dedup still returns enough unique docs
    if mode == "keyword":
        chunk_results = db.search_chunks(query, work_id=work_id, limit=fetch)
    elif mode == "semantic":
        from orivellum.capabilities.embeddings import semantic_search

        chunk_results = semantic_search(query, db, "chunk", limit=fetch, work_id=work_id)
        if not chunk_results:
            # Degrade to keyword results (embeddings unavailable, or nothing
            # above the similarity floor) rather than returning an empty page.
            chunk_results = db.search_chunks(query, work_id=work_id, limit=fetch)
            for c in chunk_results:
                c.setdefault("match_type", "keyword")
    else:  # hybrid
        from orivellum.capabilities.embeddings import hybrid_search_chunks

        chunk_results = hybrid_search_chunks(query, db, limit=fetch, work_id=work_id)

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
        if chunk.get("score") is not None:
            doc["score"] = chunk.get("score")
        if chunk.get("match_type"):
            doc["match_type"] = chunk.get("match_type")
        seen[doc_id] = doc
    results = list(seen.values())[:limit]
    return {"query": q, "results": results, "count": len(results), "mode": mode}


# ── Duplicates (must be registered BEFORE /{doc_id} so the literal segment wins) ─


@router.post("/library/scan-duplicates")
async def library_scan_duplicates(background_tasks: BackgroundTasks):
    """Backfill MinHash signatures for all ready documents that don't yet have one.

    Runs in the background and returns immediately. The caller can poll
    GET /api/library/duplicates to see new pairs as they are found.

    Returns {"queued": N, "already_indexed": M} so the UI can give feedback
    even when the library was already fully indexed.
    """
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.extracted_text
               FROM documents d
               LEFT JOIN minhash_sig ms ON ms.doc_id = d.id
               WHERE d.readiness = 'ready'
                 AND COALESCE(d.quarantined, 0) = 0
                 AND ms.doc_id IS NULL
                 AND d.extracted_text IS NOT NULL
                 AND d.extracted_text != ''"""
        ).fetchall()
        already_count = db._conn.execute("SELECT COUNT(*) FROM minhash_sig").fetchone()[0]

    pending = [(r["id"], r["extracted_text"]) for r in rows]

    def _backfill(doc_list: list[tuple[str, str]]) -> None:
        from orivellum.capabilities.dedup import (
            compute_and_store,
            find_and_record_near_duplicates,
        )

        for doc_id, text in doc_list:
            try:
                sig = compute_and_store(doc_id, text, db)
                if sig:
                    find_and_record_near_duplicates(doc_id, sig, db)
            except Exception as exc:  # noqa: BLE001
                logger.debug("scan-duplicates: error on %s: %s", doc_id[:8], exc)

    background_tasks.add_task(_backfill, pending)
    return {
        "queued": len(pending),
        "already_indexed": already_count,
        "message": (
            f"Scanning {len(pending)} document(s) — poll /api/library/duplicates for results."
            if pending
            else "All documents are already indexed."
        ),
    }


@router.get("/library/duplicates")
def library_duplicates(resolved: bool = False):
    """Return all detected near-duplicate / likely-revision document pairs."""
    db = get_db()
    pairs = db.list_near_duplicates(resolved=resolved)
    return {"pairs": pairs, "count": len(pairs)}


_VALID_RESOLVE_ACTIONS = {"keep_both", "mark_versions", "mark_superseded"}


class DupeResolveBody(BaseModel):
    action: str  # keep_both | mark_versions | mark_superseded
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
    result = db.resolve_near_duplicate(dupe_id, body.action, canonical_doc_id=body.canonical_doc_id)
    if result is None:
        raise HTTPException(404, f"Duplicate pair {dupe_id!r} not found")
    if result.get("already_resolved"):
        raise HTTPException(409, f"Duplicate pair {dupe_id!r} was already resolved")
    return {"ok": True, "dupe_id": dupe_id, "action": body.action}


def _resolve_doc_file(lib_root: Path, content_path: str | None, source: str | None) -> Path | None:
    """Resolve a document's on-disk file the same way reprocess does."""
    if content_path:
        return lib_root / content_path
    if source:
        return Path(source)
    return None


@router.get("/library/missing-files")
def library_missing_files():
    """List documents whose source file is missing from disk.

    Scoped to documents that actually NEED their file — those a reprocess run
    would try to re-extract (not finished: imported/error/no_text/reprocessing,
    plus all ZIPs). A 'ready' document with no file keeps working from its
    extracted text, so it is not reported here.

    Each entry is a standard document dict. Recovery paths: re-upload the file
    via POST /api/library/{doc_id}/restore-file, or delete the dead record.
    """
    db = get_db()
    lib_root = _library_root()
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, source, content_path FROM documents
               WHERE readiness IN ('imported','error','no_text',?)
                  OR kind='zip'
               ORDER BY created_at DESC
               LIMIT 2000""",
            (_REPROCESS_RESERVED,),
        ).fetchall()
    missing: list[dict] = []
    for row in rows:
        file_path = _resolve_doc_file(lib_root, row["content_path"], row["source"])
        if file_path is not None and file_path.exists():
            continue
        doc = db.get_document(row["id"])
        if doc:
            doc["file_missing"] = True
            missing.append(doc)
    return {"documents": missing, "count": len(missing)}


@router.get("/library/{doc_id}")
def library_get(doc_id: str):
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    # Always include warnings array; populated for any failure state.
    _FAILED = {"error", "no_text"}
    doc["warnings"] = db.get_extraction_warnings(doc_id) if doc.get("readiness") in _FAILED else []
    return {"document": doc}


class DocumentUpdate(BaseModel):
    work_id: str | None = None
    title: str | None = None


@router.patch("/library/{doc_id}")
def library_update(doc_id: str, body: DocumentUpdate):
    db = get_db()
    if not db.get_document(doc_id):
        raise HTTPException(404, f"Document {doc_id!r} not found")
    if body.work_id is not None:
        db.update_document_work(doc_id, body.work_id)
    if body.title is not None:
        with db._lock:
            db._conn.execute(
                "UPDATE documents SET title=? WHERE id=?", (body.title.strip(), doc_id)
            )
            db._conn.commit()
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


class ReadPositionUpdate(BaseModel):
    part: int
    time: float
    part_count: int
    saved_at: int


@router.get("/library/{doc_id}/read-position")
def library_get_read_position(doc_id: str):
    """Return the server-synced Read Aloud listening position, or null.

    Not gated on the document existing: positions may briefly outlive a
    document, and the client only uses this to offer a resume point.
    """
    db = get_db()
    return {"position": db.get_read_position(doc_id)}


@router.put("/library/{doc_id}/read-position")
def library_set_read_position(doc_id: str, body: ReadPositionUpdate):
    """Upsert the Read Aloud listening position for a document.

    Called debounced by the client (~every 30s or on part change) so listening
    resumes at the same spot on any device.
    """
    if body.part < 0 or body.time < 0 or body.part_count <= 0:
        raise HTTPException(400, "part/time must be >= 0 and part_count > 0")
    db = get_db()
    db.set_read_position(doc_id, body.part, body.time, body.part_count, body.saved_at)
    return {"ok": True}


@router.delete("/library/{doc_id}/read-position")
def library_clear_read_position(doc_id: str):
    """Clear the server-synced listening position (finished or declined resume)."""
    db = get_db()
    db.delete_read_position(doc_id)
    return {"ok": True}


@router.get("/library/{doc_id}/versions")
def library_versions(doc_id: str):
    db = get_db()
    if not db.get_document(doc_id):
        raise HTTPException(404, f"Document {doc_id!r} not found")
    versions = db.list_document_versions(doc_id)
    return {"versions": versions, "count": len(versions), "doc_id": doc_id}


class VersionCreate(BaseModel):
    """FA-09: typed request body for creating a document version snapshot."""

    model_config = ConfigDict(extra="forbid")
    notes: str | None = Field(default=None, max_length=2000)
    is_canonical: bool = False


@router.post("/library/{doc_id}/versions")
def library_create_version(doc_id: str, body: VersionCreate = VersionCreate()):
    """Snapshot current document state as a new version."""
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")
    version = db.create_document_version(
        doc_id=doc_id,
        sha256=doc.get("sha256"),
        word_count=doc.get("word_count") or 0,
        notes=body.notes,
        is_canonical=bool(body.is_canonical),
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
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY page, created_at LIMIT 500", (doc_id,)
        ).fetchall()
    return {"chunks": [dict(r) for r in rows], "count": len(rows)}


@router.get("/library/{doc_id}/progress")
def library_doc_progress(doc_id: str):
    """SSE stream — pushes processing progress events until the document
    reaches a terminal state (``ready`` / ``error`` / ``no_text``) or the
    5-minute session timeout expires.

    Event shape::

        data: {"stage": "extracting|chunking|indexing|harvesting|transcribing|complete",
               "pct": 0-100, "items_found": <knowledge_count>,
               "readiness": "<current>", "chunk_count": <n>}

    Clients that cannot use SSE or whose connection drops should fall back
    to the existing 4-second polling on ``GET /api/library/{doc_id}``.
    """
    import json as _js
    import time as _t

    from fastapi.responses import StreamingResponse as _SR

    db = get_db()
    if not db.get_document(doc_id):
        raise HTTPException(404, f"Document {doc_id!r} not found")

    _TERMINAL = frozenset({"ready", "error", "no_text"})
    _KEEPALIVE_INTERVAL = 10.0  # seconds between keepalive comments
    _MAX_LIFETIME = 300.0  # 5-minute hard ceiling per stream

    def _stream():
        deadline = _t.monotonic() + _MAX_LIFETIME
        last_payload: str | None = None
        last_keepalive = _t.monotonic()

        while _t.monotonic() < deadline:
            try:
                snap = db.get_document(doc_id)
                if not snap:
                    break

                readiness: str = snap.get("readiness") or "imported"
                word_count: int = snap.get("word_count") or 0

                with db._lock:
                    chunk_count: int = db._conn.execute(
                        "SELECT COUNT(*) FROM chunks WHERE doc_id=?",
                        (doc_id,),
                    ).fetchone()[0]
                    knowledge_count: int = db._conn.execute(
                        "SELECT COUNT(*) FROM knowledge WHERE source_doc_id=?",
                        (doc_id,),
                    ).fetchone()[0]

                # Map pipeline state to a human-readable stage + percentage.
                if readiness == "transcribing":
                    stage, pct = "transcribing", 25
                elif readiness == "ready":
                    stage, pct = "complete", 100
                elif readiness in ("error", "no_text"):
                    stage, pct = readiness, 0
                elif word_count == 0 and chunk_count == 0:
                    stage, pct = "extracting", 10
                elif chunk_count == 0:
                    stage, pct = "chunking", 45
                elif knowledge_count == 0:
                    stage, pct = "indexing", 70
                else:
                    # Knowledge items accumulating: advance 70 → 95 as they grow
                    stage = "harvesting"
                    pct = min(95, 70 + knowledge_count)

                payload = _js.dumps(
                    {
                        "stage": stage,
                        "pct": pct,
                        "items_found": knowledge_count,
                        "readiness": readiness,
                        "chunk_count": chunk_count,
                    }
                )

                now = _t.monotonic()
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                    last_keepalive = now
                elif now - last_keepalive >= _KEEPALIVE_INTERVAL:
                    # Keep the TCP connection alive through idle proxy timeouts
                    yield ": keepalive\n\n"
                    last_keepalive = now

                if readiness in _TERMINAL:
                    break

            except Exception:
                # Never crash the client connection — just stop the stream
                break

            _t.sleep(0.5)

    return _SR(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _ingest_file(
    db,
    background_tasks: BackgroundTasks,
    *,
    tmp_path: Path,
    name: str,
    sha256: str,
    work_id: str | None,
    meta: dict[str, Any],
    force: bool,
) -> dict:
    """Shared ingestion for both the base64 and multipart import paths.

    ``tmp_path`` holds the uploaded bytes on disk. On the duplicate path the
    temp file is deleted; otherwise it is moved into the library tree.
    Returns the JSON-serialisable response body.
    """
    with db._lock:
        existing = db._conn.execute("SELECT id FROM documents WHERE sha256=?", (sha256,)).fetchone()
    if existing:
        doc = db.get_document(existing["id"])
        readiness = doc.get("readiness", "")
        _FAILED = {"error", "no_text"}

        # If force=True, re-queue regardless of current readiness (including ready).
        # If NOT forced, only re-queue when the doc is in a failed state.
        # Temp file no longer needed — the stored copy is used.
        tmp_path.unlink(missing_ok=True)

        should_requeue = force or (readiness in _FAILED)
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
                _EXTRACTABLE = {
                    "pdf",
                    "docx",
                    "excel",
                    "csv",
                    "pptx",
                    "text",
                    "markdown",
                    "code",
                    "html",
                    "json",
                    "zip",
                    "file",
                    "audio",
                    "image",
                }
                if kind in _EXTRACTABLE:
                    logger.info(
                        "Re-queuing extraction for duplicate doc=%s kind=%s (force=%s readiness_was=%s)",
                        doc["id"],
                        kind,
                        force,
                        readiness,
                    )
                    background_tasks.add_task(
                        process_document,
                        doc_id=doc["id"],
                        file_path=str(file_path_existing),
                        kind=kind,
                        work_id=doc.get("work_id") or work_id,
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
    # Different-content files can share a shard dir + filename; never overwrite
    # another document's stored bytes — disambiguate with a sha prefix.
    if file_path.exists():
        file_path = dest / f"{sha256[:12]}_{name}"
    # Move (rename when possible) instead of rewriting — the bytes are already
    # on disk in the temp file, so no second copy is held in RAM.
    import shutil as _shutil

    _shutil.move(str(tmp_path), str(file_path))

    import sqlite3 as _sqlite3

    try:
        doc = db.create_document(
            title=name,
            source=str(file_path),
            sha256=sha256,
            kind=kind,
            work_id=work_id,
            content_path=str(file_path.relative_to(lib_root)),
            meta=meta,
        )
    except _sqlite3.IntegrityError:
        # A concurrent identical upload won the race on the unique sha256
        # constraint. Keep the winner's record; drop our copy if it is a
        # different file on disk, then respond as a duplicate.
        with db._lock:
            row = db._conn.execute(
                "SELECT id, content_path FROM documents WHERE sha256=?", (sha256,)
            ).fetchone()
        if not row:
            file_path.unlink(missing_ok=True)
            raise
        winner_path = (lib_root / row["content_path"]) if row["content_path"] else None
        if winner_path is None or file_path.resolve() != winner_path.resolve():
            file_path.unlink(missing_ok=True)
        doc = db.get_document(row["id"])
        doc["warnings"] = []
        return {
            "document": doc,
            "duplicate": True,
            "readiness": doc.get("readiness"),
            "warnings": [],
        }

    # Fire extraction + chunking + knowledge harvest in the background.
    # BackgroundTasks runs after the response is sent — safe with SQLite WAL mode.
    _EXTRACTABLE = {
        "pdf",
        "docx",
        "excel",
        "csv",
        "pptx",
        "text",
        "markdown",
        "code",
        "html",
        "json",
        "zip",
        "file",
        "audio",
        "image",
    }
    if kind in _EXTRACTABLE:
        logger.info("Queuing extraction for doc=%s kind=%s", doc["id"], kind)
        background_tasks.add_task(
            process_document,
            doc_id=doc["id"],
            file_path=str(file_path),
            kind=kind,
            work_id=work_id,
            title=name,
            db=db,
        )

    # Automatic workbook review: every uploaded .xlsx also lands in the
    # Workbench with a findings report (setting-gated inside the helper).
    if kind == "excel" and name.lower().endswith(".xlsx"):
        background_tasks.add_task(_auto_workbench_review, str(file_path), name)

    # Version suggestion: check for similar-named docs in the same Work
    if work_id:
        _maybe_suggest_version(db, doc["id"], work_id, name)

    return {"document": doc, "duplicate": False}


def _auto_workbench_review(file_path: str, name: str) -> None:
    from orivellum.api._deps import get_config, get_db
    from orivellum.capabilities.workbench import auto_review_upload

    auto_review_upload(get_db(), get_config(), Path(file_path), name)


def _cleanup_stale_parts(lib_root: Path, max_age_s: int = 3600) -> None:
    """Remove orphaned .part temp files left by a crash mid-upload."""
    import time as _time

    now = _time.time()
    try:
        for p in lib_root.glob("*.part"):
            try:
                if now - p.stat().st_mtime > max_age_s:
                    p.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


# FA-01: hard ceiling for a single library file. The streaming upload route is
# (correctly) exempt from the in-RAM body-size middleware, so it must enforce
# its own cap or a single request can fill the disk. 1 GiB comfortably covers
# large audiobooks/ZIPs while bounding the damage of a runaway upload.
_MAX_LIBRARY_FILE_BYTES = 1024 * 1024 * 1024


def _validate_import_target(db, filename: str, work_id: str | None) -> str:
    """Validate filename + work_id; returns the sanitised basename."""
    name = Path(filename).name
    if not name or name.startswith("."):
        raise HTTPException(400, f"Bad filename: {filename!r}")
    if work_id and not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    return name


@router.post("/library/import")
def library_import(body: LibraryImport, background_tasks: BackgroundTasks):
    """Legacy JSON/base64 import path — kept for backward compatibility.

    Prefer POST /api/library/upload (multipart) which streams to disk without
    the base64 2× RAM overhead.
    """
    db = get_db()
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")
    if len(data) > _MAX_LIBRARY_FILE_BYTES:
        raise HTTPException(
            413,
            f"File too large (limit {_MAX_LIBRARY_FILE_BYTES // (1024 * 1024)} MB)",
        )

    name = _validate_import_target(db, body.filename, body.work_id)
    sha256 = hashlib.sha256(data).hexdigest()

    import tempfile as _tempfile

    lib_root = _library_root()
    _cleanup_stale_parts(lib_root)
    tmp = _tempfile.NamedTemporaryFile(delete=False, dir=lib_root, suffix=".part")
    try:
        try:
            tmp.write(data)
        finally:
            tmp.close()
        return _ingest_file(
            db,
            background_tasks,
            tmp_path=Path(tmp.name),
            name=name,
            sha256=sha256,
            work_id=body.work_id,
            meta=body.meta,
            force=body.force,
        )
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise


@router.post("/library/upload")
async def library_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    work_id: str | None = Form(None),
    force: bool = Form(False),
):
    """Streaming multipart import — no base64, constant memory.

    The file is streamed to a temp file in 1 MB chunks while the SHA-256 is
    computed incrementally, then ingested exactly like /library/import.
    """
    db = get_db()
    name = _validate_import_target(db, file.filename or "", work_id or None)

    import tempfile as _tempfile

    lib_root = _library_root()
    _cleanup_stale_parts(lib_root)
    tmp = _tempfile.NamedTemporaryFile(delete=False, dir=lib_root, suffix=".part")
    hasher = hashlib.sha256()
    size = 0
    try:
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
                if size > _MAX_LIBRARY_FILE_BYTES:
                    # Stop writing immediately — the partial file is deleted by
                    # the outer BaseException handler.
                    raise HTTPException(
                        413,
                        f"File too large (limit {_MAX_LIBRARY_FILE_BYTES // (1024 * 1024)} MB)",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()
        if size == 0:
            Path(tmp.name).unlink(missing_ok=True)
            raise HTTPException(400, "Uploaded file is empty")
        _validate_mime_signature(Path(tmp.name), original_name=name)
        return _ingest_file(
            db,
            background_tasks,
            tmp_path=Path(tmp.name),
            name=name,
            sha256=hasher.hexdigest(),
            work_id=work_id or None,
            meta={},
            force=force,
        )
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        raise


@router.post("/library/{doc_id}/restore-file")
async def library_restore_file(
    doc_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Re-attach a source file to a document whose file is missing from disk.

    Recovery path for records whose stored file was deleted or moved: the user
    re-uploads the file, we store it in the library tree, point the document at
    it, and queue extraction. Refused (409) when the document's current file is
    still on disk — use /reprocess for that case.

    The re-uploaded bytes may differ from the original (e.g. a fresh export of
    the same document): the record's sha256 is updated. If the bytes match a
    DIFFERENT existing document we refuse (409) rather than create two records
    with the same content.
    """
    db = get_db()
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"Document {doc_id!r} not found")

    lib_root = _library_root()
    current = _resolve_doc_file(lib_root, doc.get("content_path"), doc.get("source"))
    if current is not None and current.exists():
        raise HTTPException(
            409,
            "This document's source file is still on disk — use reprocess instead.",
        )

    name = _validate_import_target(db, file.filename or "", None)

    # Atomically reserve the document (CAS on readiness) so a concurrent
    # reprocess-all can't select it mid-restore and vice versa: reprocess-all
    # excludes docs at the reservation marker, and if IT reserved first we
    # refuse here instead of double-queuing extraction.
    prior_readiness = doc.get("readiness") or "error"
    _BUSY = HTTPException(
        409,
        "This document is being reprocessed right now — try again in a moment.",
    )
    if prior_readiness == _REPROCESS_RESERVED:
        raise _BUSY
    with db._lock:
        cur = db._conn.execute(
            "UPDATE documents SET readiness=? WHERE id=? AND readiness=?",
            (_REPROCESS_RESERVED, doc_id, prior_readiness),
        )
        db._conn.commit()
    if cur.rowcount != 1:
        raise _BUSY

    import shutil as _shutil
    import sqlite3 as _sqlite3
    import tempfile as _tempfile

    _cleanup_stale_parts(lib_root)
    tmp = _tempfile.NamedTemporaryFile(delete=False, dir=lib_root, suffix=".part")
    hasher = hashlib.sha256()
    size = 0
    moved_path: Path | None = None  # set once bytes live in the library tree
    committed = False  # set once the DB record points at them
    try:
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
                if size > _MAX_LIBRARY_FILE_BYTES:
                    raise HTTPException(
                        413,
                        f"File too large (limit {_MAX_LIBRARY_FILE_BYTES // (1024 * 1024)} MB)",
                    )
                tmp.write(chunk)
        finally:
            tmp.close()
        if size == 0:
            raise HTTPException(400, "Uploaded file is empty")
        tmp_path = Path(tmp.name)
        _validate_mime_signature(tmp_path, original_name=name)
        sha256 = hasher.hexdigest()

        # Store in the library tree (same sharded layout as uploads).
        dest = lib_root / sha256[:2] / sha256[2:4]
        dest.mkdir(parents=True, exist_ok=True)
        file_path = dest / name
        if file_path.exists():
            file_path = dest / f"{sha256[:12]}_{name}"
        _shutil.move(str(tmp_path), str(file_path))
        moved_path = file_path

        # Claim the sha256 and point the record at the new file in ONE
        # transaction: the duplicate pre-check and the UPDATE happen under the
        # same lock, and a concurrent import that claims the hash between them
        # surfaces as IntegrityError — handled as the same 409, never a 500.
        kind = doc.get("kind") or _kind_for(name)
        conflict = False
        try:
            with db._lock:
                other = db._conn.execute(
                    "SELECT id FROM documents WHERE sha256=? AND id!=?",
                    (sha256, doc_id),
                ).fetchone()
                if other is None:
                    db._conn.execute(
                        """UPDATE documents
                           SET source=?, content_path=?, sha256=?,
                               readiness='imported', error_message=NULL
                           WHERE id=?""",
                        (str(file_path), str(file_path.relative_to(lib_root)), sha256, doc_id),
                    )
                    db._conn.commit()
                    committed = True
                else:
                    conflict = True
        except _sqlite3.IntegrityError:
            conflict = True
        if conflict:
            # Never delete the winning document's bytes if the paths coincide.
            with db._lock:
                winner = db._conn.execute(
                    "SELECT content_path FROM documents WHERE sha256=? AND id!=?",
                    (sha256, doc_id),
                ).fetchone()
            winner_path = (
                (lib_root / winner["content_path"]) if winner and winner["content_path"] else None
            )
            if winner_path is None or file_path.resolve() != winner_path.resolve():
                file_path.unlink(missing_ok=True)
            moved_path = None
            raise HTTPException(
                409,
                "That file already exists in the library as another document. "
                "Delete this record and use the existing document instead.",
            )

        db.delete_extraction_warnings(doc_id)

        background_tasks.add_task(
            process_document,
            doc_id=doc_id,
            file_path=str(file_path),
            kind=kind,
            work_id=doc.get("work_id"),
            title=doc.get("title") or name,
            db=db,
        )
        logger.info(
            "restore-file: doc=%s re-attached %s (%d bytes), extraction queued", doc_id, name, size
        )
        return {"ok": True, "document": db.get_document(doc_id)}
    except BaseException:
        Path(tmp.name).unlink(missing_ok=True)
        if not committed:
            # Release the reservation so the record never sticks at the
            # transient marker, and drop any orphaned staged file.
            if moved_path is not None:
                moved_path.unlink(missing_ok=True)
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness=? WHERE id=? AND readiness=?",
                    (prior_readiness, doc_id, _REPROCESS_RESERVED),
                )
                db._conn.commit()
        raise


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

    # Claim the document-level extraction reservation BEFORE any destructive
    # step (warning wipe, readiness reset).  A competing run — another
    # reprocess, reprocess-all, nightshift recovery, or a Studio
    # re-transcribe — gets a clean 409 instead of interleaving.
    token = try_reserve_extraction(doc_id)
    if token is None:
        raise HTTPException(
            409, "This document is already being processed — wait for it to finish, then try again"
        )
    try:
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
            reservation_token=token,
        )
    except Exception:
        # The queued pipeline never got ownership — release so the doc
        # isn't locked out until restart.
        release_extraction(doc_id, token)
        raise
    logger.info("Queued reprocess for doc=%s kind=%s", doc_id, kind)
    return {"ok": True, "doc_id": doc_id, "message": "Reprocessing queued"}


@router.get("/library/{doc_id}/download")
def download_document(doc_id: str):
    """Serve the original stored file as a download."""
    import mimetypes

    from fastapi.responses import FileResponse

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

    Each archive is reserved through the shared document-level extraction
    reservation BEFORE any state is touched — an archive that is already
    extracting is skipped entirely (no warning wipe, no readiness reset) and
    counted in ``skipped`` rather than mutating the active run's document.
    """
    db = get_db()
    lib_root = _library_root()

    with db._lock:
        rows = db._conn.execute(
            "SELECT id, source, content_path, work_id, title FROM documents WHERE kind='zip'"
        ).fetchall()

    queued = 0
    skipped = 0
    for row in rows:
        content_path = row["content_path"]
        if content_path:
            file_path = lib_root / content_path
        else:
            file_path = Path(row["source"]) if row["source"] else None

        if not file_path or not file_path.exists():
            logger.warning("ZIP explode: file missing for doc %s — skipping", row["id"])
            continue

        # Reserve BEFORE any destructive step so a ZIP mid-extraction is left
        # completely untouched by this call.
        token = try_reserve_extraction(row["id"])
        if token is None:
            skipped += 1
            logger.info("ZIP explode: doc %s already extracting — skipped", row["id"])
            continue

        try:
            db.delete_extraction_warnings(row["id"])
            db.update_document_extracted(row["id"], "", 0, readiness="imported", error_message=None)
            background_tasks.add_task(
                process_document,
                doc_id=row["id"],
                file_path=str(file_path),
                kind="zip",
                work_id=row["work_id"],
                title=row["title"] or file_path.name,
                db=db,
                reservation_token=token,
            )
        except Exception:
            # The queued pipeline never got ownership — release the claim.
            release_extraction(row["id"], token)
            raise
        queued += 1
        logger.info("Queued ZIP explosion for doc=%s", row["id"])

    return {
        "queued": queued,
        "skipped": skipped,
        "message": (
            f"Queued {queued} ZIP archive(s) for extraction. "
            "Each file inside will become its own library document."
            + (f" Skipped {skipped} already being processed." if skipped else "")
        ),
    }


# Reservation marker: a doc selected for reprocessing is flipped to this state
# *atomically* under the same lock as the candidate SELECT, so a second
# concurrent call selects nothing (candidates exclude this state) and can never
# double-enqueue the same doc.  The pipeline ignores the initial readiness and
# drives the doc to a terminal state, so this marker is safe as a transient.
_REPROCESS_RESERVED = "reprocessing"

# In-flight (non-terminal) readiness states — a doc mid-pipeline sits in
# 'imported' or the transient reservation marker until the extractor drives it
# to a terminal state (ready / error / no_text).  Shared so other routes (e.g.
# MCOS rag reprocess-status) agree on what "still processing" means.
REPROCESS_INFLIGHT_STATES: tuple[str, ...] = ("imported", _REPROCESS_RESERVED)


def queue_library_reprocess(db, background_tasks: BackgroundTasks, force: bool = False) -> dict:
    """Shared bulk re-extraction machinery used by both the library
    ``/reprocess-all`` route and the MCOS ``/rag/apply?reprocess_library`` path.

    Collects every not-finished document (and all ZIPs, which are idempotent to
    re-explode), claims the shared document-level extraction reservation for
    each BEFORE touching any document state, then flips readiness to the
    transient ``reprocessing`` marker (so concurrent callers can't re-select
    it) and queues ``process_document`` on ``background_tasks``.  Documents
    whose reservation is held by another entry point are skipped with zero
    mutation.  Returns the same summary dict the route exposes.
    """
    lib_root = _library_root()

    # ── Atomically select + reserve candidates ──────────────────────────────────
    # force=True also re-runs 'ready' docs (e.g. after a chunk-settings change).
    # The reservation marker itself is NEVER a candidate, so a concurrent call
    # (double rag/apply) selects only docs the first call hasn't grabbed.
    readiness_filter: tuple[str, ...]
    if force:
        readiness_filter = ("imported", "error", "no_text", "ready")
    else:
        readiness_filter = ("imported", "error", "no_text")

    placeholders = ",".join("?" * len(readiness_filter))
    seen: set[str] = set()
    candidates: list[Any] = []
    with db._lock:
        rows = db._conn.execute(
            f"""SELECT id, source, content_path, work_id, title, kind, readiness
                FROM documents
                WHERE readiness IN ({placeholders})
                  AND readiness != ?
                ORDER BY created_at DESC""",
            (*readiness_filter, _REPROCESS_RESERVED),
        ).fetchall()
        # Also always include ZIPs (even 'ready' ones — exploding is idempotent),
        # but skip any already reserved by a concurrent call.
        zip_rows = db._conn.execute(
            "SELECT id, source, content_path, work_id, title, kind, readiness "
            "FROM documents WHERE kind='zip' AND readiness != ?",
            (_REPROCESS_RESERVED,),
        ).fetchall()
        # Merge; use doc id as dedup key so ZIPs only appear once.
        for row in list(rows) + list(zip_rows):
            if row["id"] not in seen:
                seen.add(row["id"])
                candidates.append(row)
    # NOTE: readiness is NOT flipped here.  The shared extraction reservation
    # (claimed per candidate below, before any mutation) is what serializes
    # against every entry point — including a concurrent reprocess-all, whose
    # try_reserve_extraction for the same doc fails and skips it.  Only after
    # ownership is acquired does the doc get the _REPROCESS_RESERVED marker.

    # ── Queue each reserved candidate ───────────────────────────────────────────
    queued_zips = 0
    queued_stuck = 0
    skipped = 0
    skipped_docs: list[dict] = []

    for row in candidates:
        doc_id = row["id"]
        content_path = row["content_path"]
        kind = row["kind"] or "text"

        # Resolve file on disk
        if content_path:
            file_path = lib_root / content_path
        elif row["source"]:
            file_path = Path(row["source"])
        else:
            file_path = None

        if not file_path or not file_path.exists():
            # Nothing was reserved or mutated for this doc — pure skip.
            logger.warning(
                "reprocess-all: file missing for doc %s (kind=%s) — skipping",
                doc_id,
                kind,
            )
            skipped += 1
            skipped_docs.append(
                {
                    "id": doc_id,
                    "title": row["title"] or doc_id[:8],
                    "kind": kind,
                    "readiness": row["readiness"],
                    "reason": "file_missing",
                }
            )
            continue

        # Claim the document-level extraction reservation BEFORE any mutation.
        # The reservation is shared with EVERY pipeline entry point (single
        # reprocess, Studio re-transcribe, nightshift recovery, uploads), so a
        # doc mid-extraction is skipped here with its state left untouched.
        token = try_reserve_extraction(doc_id)
        if token is None:
            skipped += 1
            skipped_docs.append(
                {
                    "id": doc_id,
                    "title": row["title"] or doc_id[:8],
                    "kind": kind,
                    "readiness": row["readiness"],
                    "reason": "already_processing",
                }
            )
            logger.info("reprocess-all: doc %s already extracting — skipped", doc_id)
            continue

        try:
            # We own the doc now: mark it with the transient reservation
            # readiness (so a concurrent reprocess-all's SELECT won't re-pick
            # it) and clear stale warnings.  The pipeline drives readiness to
            # a terminal state when it finishes.
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness=? WHERE id=?", (_REPROCESS_RESERVED, doc_id)
                )
                db._conn.commit()
            db.delete_extraction_warnings(doc_id)

            background_tasks.add_task(
                process_document,
                doc_id=doc_id,
                file_path=str(file_path),
                kind=kind,
                work_id=row["work_id"],
                title=row["title"] or file_path.name,
                db=db,
                reservation_token=token,
            )
        except Exception:
            # The queued pipeline never got ownership — restore the prior
            # readiness and release the claim.
            prior = row["readiness"] or "error"
            if prior == _REPROCESS_RESERVED:
                prior = "error"
            with db._lock:
                db._conn.execute("UPDATE documents SET readiness=? WHERE id=?", (prior, doc_id))
                db._conn.commit()
            release_extraction(doc_id, token)
            raise

        if kind == "zip":
            queued_zips += 1
        else:
            queued_stuck += 1

        logger.info(
            "reprocess-all: queued doc=%s kind=%s previous_readiness=%s",
            doc_id,
            kind,
            row["readiness"],
        )

    total = queued_zips + queued_stuck
    parts: list[str] = []
    if queued_zips:
        parts.append(f"{queued_zips} ZIP archive(s) will be exploded into child documents")
    if queued_stuck:
        parts.append(f"{queued_stuck} document(s) re-queued for extraction")
    n_missing = sum(1 for d in skipped_docs if d.get("reason") == "file_missing")
    n_busy = sum(1 for d in skipped_docs if d.get("reason") == "already_processing")
    if n_missing:
        parts.append(f"{n_missing} skipped (source file not found on disk)")
    if n_busy:
        parts.append(f"{n_busy} skipped (already being processed)")

    return {
        "queued": total,
        "queued_zips": queued_zips,
        "queued_stuck": queued_stuck,
        "skipped": skipped,
        "skipped_docs": skipped_docs,
        "message": ". ".join(parts) if parts else "Nothing to reprocess.",
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
    db = get_db()
    return queue_library_reprocess(db, background_tasks, force=force)


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
            key = (
                _re.sub(
                    r"[_\-\s]+(archive|files|docs|documents|library|collection|pack)$",
                    "",
                    key,
                    flags=_re.I,
                ).strip()
                or key
            )
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
            f"Created {len(created_works)} Work(s) and organised {total_assigned} document(s)."
        ),
    }
