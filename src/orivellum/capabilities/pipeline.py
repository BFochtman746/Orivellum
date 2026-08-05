"""End-to-end document processing pipeline.

Call `process_document()` after a file is imported to the library.
Runs: extract → chunk → harvest → update document readiness.

This function is designed to be called in a background thread so the
HTTP response returns immediately after file storage.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orivellum.capabilities.extraction import extract
from orivellum.capabilities.chunking import chunk_and_store
from orivellum.capabilities.knowledge_harvest import harvest, llm_harvest

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)


def _explode_zip_into_documents(
    doc_id: str, path: Path, work_id: str | None, zip_title: str, db: "OrivellumDB"
) -> list[str]:
    """Extract each supported file inside a ZIP as its own library document.

    Returns the list of child document IDs created (or found via dedup).
    Each child document is immediately queued for the normal processing pipeline
    in a daemon thread so the ZIP handler returns quickly.
    """
    import hashlib
    import threading
    import zipfile

    from orivellum.api._deps import get_config

    cfg = get_config()
    lib_root = Path(cfg.data_dir) / "library"

    _EXT_KIND = {
        ".pdf": "pdf", ".docx": "docx", ".doc": "docx",
        ".xlsx": "excel", ".xls": "excel", ".csv": "csv",
        ".pptx": "pptx", ".ppt": "pptx",
        ".txt": "text", ".md": "markdown",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
        ".html": "html", ".htm": "html",
        ".json": "json",
        ".rtf": "file", ".epub": "file",
    }

    children: list[str] = []

    try:
        with zipfile.ZipFile(path, "r") as zf:
            members = [n for n in zf.namelist() if not n.endswith("/")]
            for name in members:
                basename = Path(name).name
                # Skip macOS metadata and hidden files
                if name.startswith("__MACOSX/") or basename.startswith("._") or basename.startswith("."):
                    continue
                # Skip nested ZIPs (only go one level deep)
                if Path(name).suffix.lower() == ".zip":
                    continue

                ext = Path(name).suffix.lower()
                kind = _EXT_KIND.get(ext)
                if not kind:
                    continue

                try:
                    content = zf.read(name)
                except Exception as exc:
                    logger.warning("ZIP explode: cannot read %s from %s: %s", name, path.name, exc)
                    continue

                if not content:
                    continue

                sha256 = hashlib.sha256(content).hexdigest()

                # Dedup: if this file is already in the library, link it and skip
                with db._lock:
                    existing_row = db._conn.execute(
                        "SELECT id FROM documents WHERE sha256=?", (sha256,)
                    ).fetchone()
                if existing_row:
                    children.append(existing_row["id"])
                    continue

                # Derive clean title from filename (strip leading index digits like "01_")
                stem = Path(basename).stem
                import re as _re
                title = _re.sub(r"^(\d+[_\-\s]+)", "", stem).strip() or stem
                if not title:
                    title = stem

                # Folder hint for grouping (the path inside the ZIP minus filename)
                folder_hint = str(Path(name).parent)
                if folder_hint == ".":
                    folder_hint = ""

                # Save to sharded library directory
                subdir = lib_root / sha256[:2] / sha256[2:4]
                subdir.mkdir(parents=True, exist_ok=True)
                file_path = subdir / basename
                if file_path.exists():
                    file_path = subdir / f"{sha256[:8]}_{basename}"
                file_path.write_bytes(content)

                from orivellum.capabilities.classify import (
                    classify_object as _classify, EXCLUDED_FROM_WORKS as _EFW,
                )
                _child_clf = _classify(basename, kind=kind, source_path=name)
                _child_tier = _child_clf.tier.value

                doc = db.create_document(
                    title=title,
                    source=str(file_path),
                    sha256=sha256,
                    kind=kind,
                    work_id=work_id,
                    content_path=str(file_path.relative_to(lib_root)),
                    meta={
                        "from_zip": doc_id,
                        "zip_name": zip_title,
                        "zip_path": name,
                        "zip_folder": folder_hint,
                    },
                    tier=_child_tier,
                )
                children.append(doc["id"])

                # BUG-005 fix: every ZIP child must be recorded in object_provenance
                # so it is visible to the recall index ("find everything I've imported").
                try:
                    from orivellum.capabilities.persist import record_provenance as _rp
                    _rp(doc["id"], "zip_extract", db, origin_id=doc_id)
                except Exception as _prov_exc:
                    logger.warning("ZIP provenance record failed for %s: %s", doc["id"], _prov_exc)

                # Skip knowledge harvest for ARTIFACT/SYSTEM children — they
                # must never become knowledge nodes or Works.
                if _child_clf.tier in _EFW:
                    logger.debug("ZIP child %s is tier=%s — skipping harvest", basename, _child_tier)
                    continue

                # Queue processing via tracked executor so work is bounded,
                # visible in the job dashboard, and never spawns unlimited threads.
                try:
                    from orivellum.api.executor import _tracked_submit as _ts_zip
                    _ts_zip(
                        process_document, doc["id"], str(file_path), kind, work_id, title, db,
                        kind="pipeline", label=f"process:{doc['id'][:8]}",
                    )
                except Exception as _exc_zip:
                    # Executor not yet initialised (e.g. tests) — fall back to thread
                    logger.warning("Executor unavailable for ZIP child %s, falling back to thread: %s",
                                   doc["id"][:8], _exc_zip)
                    threading.Thread(
                        target=process_document,
                        args=(doc["id"], str(file_path), kind, work_id, title, db),
                        daemon=True,
                    ).start()

    except zipfile.BadZipFile as exc:
        logger.error("ZIP explode: bad archive %s: %s", path.name, exc)

    # Review-queue auto-populate (MONARCH #151): when an archive with no Work
    # produces a group of >2 child docs, suggest assigning them to a new Work
    # named after the archive.  A human approves/rejects it on /review.
    # Only propose when the archive itself is CANON — never for ARTIFACT/SYSTEM
    # batches (migration dumps, run reports, etc.).
    # Propose a Work for SOURCE/CANON archives — never for ARTIFACT/SYSTEM batches
    # (migration dumps, build outputs, run reports).  This uses EXCLUDED_FROM_WORKS
    # rather than an exact CANON check, so user-uploaded archives ("archive.zip",
    # "documents.zip") also get a suggestion, which matches the original intent.
    from orivellum.capabilities.classify import (
        classify_object as _clf_arch,
        EXCLUDED_FROM_WORKS as _EFW,
    )
    _archive_tier = _clf_arch(zip_title, source_path=zip_title).tier
    if work_id is None and len(children) > 2 and _archive_tier not in _EFW:
        try:
            import json as _json
            import uuid as _uuid_mod
            from datetime import datetime as _dt, timezone as _tz

            proposed = Path(zip_title).stem.replace("_", " ").replace("-", " ").strip() or zip_title
            with db._lock:
                exists = db._conn.execute(
                    """SELECT 1 FROM suggestions
                       WHERE kind='work_assignment'
                         AND json_extract(meta,'$.archive_doc_id')=?""",
                    (doc_id,),
                ).fetchone()
                if not exists:
                    db._conn.execute(
                        """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            str(_uuid_mod.uuid4()),
                            None,
                            "work_assignment",
                            f"Archive \u201c{zip_title}\u201d produced {len(children)} documents. "
                            f"Group them into a new Work \u201c{proposed}\u201d?",
                            _json.dumps({
                                "archive_doc_id": doc_id,
                                "doc_ids": children,
                                "proposed_title": proposed,
                                "confidence": 0.6,
                            }),
                            _dt.now(_tz.utc).isoformat(),
                        ),
                    )
                    db._conn.commit()
        except Exception as exc:  # noqa: BLE001 — suggestion is best-effort
            logger.warning("ZIP explode: could not create work_assignment suggestion: %s", exc)

    return children


def _suggest_version_relationships(
    doc_id: str,
    hits: list[tuple[str, float, str]],
    db: "OrivellumDB",
) -> None:
    """Create version_relationship suggestions for likely_revision pairs.

    Near-duplicate pairs (≥0.85 Jaccard) are already surfaced in the Review
    Queue as duplicate alerts.  The 0.60–0.85 band is a different signal: the
    documents share substantial text but are not copies.  They are more likely
    versions — draft vs final, chapter vs complete manuscript, etc. — and should
    become version_relationship suggestions so users can declare a DERIVED_FROM
    link in one click.

    Dedup guard: checks both (a,b) and (b,a) orderings so a pair is never
    proposed twice regardless of which document was processed first.
    """
    import json as _json
    import uuid as _uuid_mod
    from datetime import datetime as _dt, timezone as _tz

    for other_id, sim, kind in hits:
        if kind != "likely_revision":
            continue
        try:
            doc_a_row = db.get_document(doc_id)
            doc_b_row = db.get_document(other_id)
            title_a = (doc_a_row or {}).get("title") or doc_id[:8]
            title_b = (doc_b_row or {}).get("title") or other_id[:8]

            with db._lock:
                exists = db._conn.execute(
                    """SELECT 1 FROM suggestions
                       WHERE kind='version_relationship'
                         AND (
                           (json_extract(meta,'$.doc_a_id')=?
                            AND json_extract(meta,'$.doc_b_id')=?)
                           OR
                           (json_extract(meta,'$.doc_a_id')=?
                            AND json_extract(meta,'$.doc_b_id')=?)
                         )""",
                    (doc_id, other_id, other_id, doc_id),
                ).fetchone()
                if not exists:
                    db._conn.execute(
                        """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (
                            str(_uuid_mod.uuid4()),
                            None,
                            "version_relationship",
                            (
                                f"\u201c{title_a}\u201d and \u201c{title_b}\u201d "
                                f"share {round(sim * 100)}\u202f% of content. "
                                "Is one derived from the other?"
                            ),
                            _json.dumps({
                                "doc_a_id": doc_id,
                                "doc_b_id": other_id,
                                "doc_a_title": title_a,
                                "doc_b_title": title_b,
                                "confidence": round(sim, 4),
                                "similarity": round(sim, 4),
                            }),
                            _dt.now(_tz.utc).isoformat(),
                        ),
                    )
                    db._conn.commit()
                    logger.info(
                        "version_relationship suggestion created: %s \u2194 %s  sim=%.2f",
                        doc_id[:8], other_id[:8], sim,
                    )
        except Exception as exc:  # noqa: BLE001 — suggestion is best-effort
            logger.debug(
                "version_relationship suggestion failed %s\u2194%s: %s",
                doc_id[:8], other_id[:8], exc,
            )


def resolve_file_path(file_path: str, doc_id: str, db: "OrivellumDB") -> Path | None:
    """Return the file as a Path, falling back to content_path from the DB.

    This makes reprocessing after a server restart safe: even if the original
    absolute path is stale, content_path (relative to lib_root) still works.
    """
    p = Path(file_path)
    if p.exists():
        return p

    # Fallback: resolve from content_path stored in the document record
    doc = db.get_document(doc_id)
    if not doc:
        return None
    content_path = doc.get("content_path")
    if not content_path:
        return None

    # content_path is relative to data_dir/library
    try:
        from orivellum.api._deps import get_config
        cfg = get_config()
        lib_root = Path(cfg.data_dir) / "library"
        fallback = lib_root / content_path
        if fallback.exists():
            logger.info("Resolved %s via content_path fallback: %s", doc_id[:8], fallback)
            return fallback
    except Exception:
        pass
    return None


def process_document(doc_id: str, file_path: str, kind: str,
                     work_id: str | None, title: str,
                     db: "OrivellumDB") -> None:
    """Extract, chunk, and harvest a single document.

    Safe to call from a daemon thread — catches and logs all exceptions.
    Stores a descriptive error_message on the document when anything fails
    so callers can surface the reason in the UI.
    """
    logger.info("Processing doc %s (%s) kind=%s", doc_id, title, kind)
    try:
        path = resolve_file_path(file_path, doc_id, db)
        if path is None:
            msg = f"File not found: {file_path}"
            logger.warning("Doc %s — %s", doc_id, msg)
            db.add_extraction_warning(doc_id, kind="file_not_found", detail=msg)
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="error",
                                         error_message=msg)
            return

        # ZIP archives: explode into individual child documents instead of
        # concatenating everything into one blob.  Each file inside the ZIP
        # becomes its own library document with its own processing pipeline.
        if kind == "zip":
            children = _explode_zip_into_documents(doc_id, path, work_id, title, db)
            summary = (
                f"Archive extracted: {len(children)} document(s) added to your library."
                if children
                else "Archive is empty or contains no supported file types."
            )
            db.update_document_extracted(doc_id, summary, 0, readiness="ready")
            try:
                import json as _jz
                with db._lock:
                    db._conn.execute(
                        "UPDATE documents SET meta=? WHERE id=?",
                        (_jz.dumps({
                            "zip_exploded": True,
                            "zip_child_count": len(children),
                            "zip_children": children,
                        }), doc_id),
                    )
                    db._conn.commit()
                try:
                    db.audit("document.meta_updated", object_id=doc_id,
                             object_type="document", actor="system",
                             detail=f"zip meta {len(children)} children")
                except Exception:
                    pass
            except Exception:
                pass
            logger.info("ZIP %s exploded → %d child docs", doc_id, len(children))
            return

        # Step 1: extract text
        result = extract(path, kind, db=db)
        if not result.ok:
            # Use the extractor's own diagnostic when available (e.g. ZIP manifest)
            # Guard against test mocks: ensure meta is a real dict before calling .get()
            _meta = result.meta if isinstance(result.meta, dict) else {}
            meta_msg = _meta.get("user_message") or ""
            msg = str(meta_msg) if meta_msg else f"Extraction produced no readable text (kind={kind})"
            logger.warning("Doc %s — %s", doc_id, msg)
            db.add_extraction_warning(doc_id, kind="no_readable_text", detail=msg)
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="no_text",
                                         error_message=msg)
            return

        # Store ZIP manifest / extractor meta so the UI can show per-member status
        if result.meta:
            try:
                import json as _json
                with db._lock:
                    db._conn.execute(
                        "UPDATE documents SET meta=? WHERE id=?",
                        (_json.dumps(result.meta), doc_id),
                    )
                    db._conn.commit()
                db.audit("document.meta_updated", object_id=doc_id, object_type="document",
                         actor="system", detail="extraction meta")
            except Exception as meta_exc:
                logger.debug("Could not persist extraction meta for %s: %s", doc_id, meta_exc)

        # Step 2: chunk and index
        chunk_and_store(result, doc_id, db)

        # Step 3: harvest knowledge (rule-based, always runs)
        harvest(result, doc_id=doc_id, work_id=work_id,
                doc_title=title, db=db)

        # Step 4: mark document ready — happens BEFORE the optional LLM step so
        # the document is usable even if the AI service is slow or unavailable.
        db.update_document_extracted(
            doc_id,
            extracted_text=result.full_text[:100_000],  # cap stored text
            word_count=result.word_count,
            readiness="ready",
        )
        logger.info("Doc %s processed — %d words, ready", doc_id, result.word_count)

        # Step 4.4: chunk embeddings — daemon thread so semantic search picks
        # up new documents quickly without ever blocking readiness.  When the
        # embeddings endpoint is down this exits quietly; the nightly backfill
        # catches up later.
        try:
            from orivellum.capabilities.embeddings import embed_chunks_for_doc
            from orivellum.api.executor import get_executor as _gex_emb
            _gex_emb().submit(embed_chunks_for_doc, doc_id, db)
        except Exception as _emb_exc:
            logger.debug("Embedding kickoff non-fatal for %s: %s", doc_id, _emb_exc)

        # Step 4.5: chapter/section extraction — runs after readiness so it
        # never delays the document appearing as usable.  Non-fatal: failure
        # is logged but does not change readiness.
        try:
            from orivellum.capabilities.chapters import extract_chapters
            _text_for_chapters = result.full_text
            if _text_for_chapters:
                _chapters = extract_chapters(_text_for_chapters)
                if _chapters:
                    _chapter_dicts = [
                        {"seq": c.seq, "level": c.level,
                         "title": c.title, "text": c.text}
                        for c in _chapters
                    ]
                    _n = db.upsert_book_chapters(doc_id, work_id, _chapter_dicts)
                    logger.info("Doc %s: %d chapter(s) extracted", doc_id, _n)
        except Exception as _ch_exc:
            logger.debug("Chapter extraction non-fatal for %s: %s", doc_id, _ch_exc)

        # Step 4.6: near-duplicate detection — compare against all stored sketches.
        # Completely non-fatal; results land in doc_dupes for the UI to surface.
        # likely_revision pairs (0.60–0.85) are also promoted to version_relationship
        # suggestions in the Review Queue so users can declare DERIVED_FROM links.
        try:
            from orivellum.capabilities.dedup import compute_and_store, find_and_record_near_duplicates
            _text_for_dedup = result.full_text
            if _text_for_dedup:
                _sig = compute_and_store(doc_id, _text_for_dedup, db)
                if _sig is not None:
                    _hits = find_and_record_near_duplicates(doc_id, _sig, db, work_id=work_id)
                    if _hits:
                        logger.info("Doc %s: %d near-duplicate(s) found", doc_id, len(_hits))
                        _suggest_version_relationships(doc_id, _hits, db)
        except Exception as _dd_exc:
            logger.debug("Dedup step non-fatal for %s: %s", doc_id, _dd_exc)

        # Audit: document became ready
        try:
            db.audit("document.ready", object_id=doc_id, object_type="document",
                     actor="pipeline", result="ok",
                     detail=f"words={result.word_count}")
        except Exception:
            pass

        # Step 5 (optional): LLM-powered harvest — runs after readiness is set so
        # latency here never blocks the document from appearing as ready.
        if db.get_setting("ai_extraction_enabled", "false").lower() == "true":
            logger.info("AI extraction enabled — running llm_harvest for doc %s", doc_id)
            try:
                llm_harvest(result, doc_id=doc_id, work_id=work_id,
                            doc_title=title, db=db)
            except Exception as llm_exc:
                # Never let an LLM failure touch the ready document
                logger.warning(
                    "llm_harvest failed for doc %s (non-fatal): %s", doc_id, llm_exc
                )

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Pipeline failed for doc %s: %s", doc_id, msg, exc_info=True)
        try:
            db.add_extraction_warning(doc_id, kind="pipeline_exception", detail=msg)
        except Exception:
            pass
        try:
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="error",
                                         error_message=msg)
        except Exception:
            pass
