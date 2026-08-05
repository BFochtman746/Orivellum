"""Folder watch — auto-import new files from a watched directory.

Orivellum can watch a local directory for new files and automatically import
them into the library.  This is useful for users who save documents to a
specific folder on their device (e.g. Downloads, Dropbox, Obsidian vault).

The watcher runs as a background thread managed by the nightshift daemon.
It polls the watched directory every ``_POLL_INTERVAL_SEC`` seconds (no
inotify/FSEvents dependency — works on all platforms).

Configuration (stored in db_settings):
  folder_watch_path  — absolute path to watch (empty = disabled)
  folder_watch_enabled — "true" / "false"
  folder_watch_work_id — optional Work to link imported documents to

Files are identified by path.  Once a file has been imported its path is
stored in folder_watch_seen so it is never re-imported.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.folder_watch")

_POLL_INTERVAL_SEC = 15
_SUPPORTED_EXTS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv",
    ".pptx", ".ppt", ".txt", ".md", ".py", ".js", ".ts",
    ".jsx", ".tsx", ".json", ".html", ".htm", ".rtf",
    ".epub", ".xml", ".zip",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _get_seen_paths(db: "OrivellumDB") -> set[str]:
    """Return the set of file paths already imported by the watcher."""
    try:
        raw = db.get_setting("folder_watch_seen", "")
        if not raw:
            return set()
        import json
        return set(json.loads(raw))
    except Exception:
        return set()


def _mark_seen(path: str, db: "OrivellumDB") -> None:
    """Record a path as imported so it is not reimported next cycle."""
    try:
        import json
        seen = _get_seen_paths(db)
        seen.add(path)
        # Keep at most 5000 entries to avoid unbounded growth
        if len(seen) > 5000:
            seen = set(list(seen)[-4000:])
        db.set_setting("folder_watch_seen", json.dumps(list(seen)), actor="system")
    except Exception as exc:
        logger.debug("folder_watch: could not mark seen: %s", exc)


def _import_file(file_path: Path, work_id: str | None, db: "OrivellumDB") -> bool:
    """Import a single file into the library.  Returns True on success."""
    try:
        import hashlib
        import shutil
        import tempfile

        # Compute SHA-256 to detect duplicates
        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()

        from orivellum.api._deps import get_config
        cfg = get_config()
        lib_root = Path(cfg.data_dir) / "library"
        lib_root.mkdir(parents=True, exist_ok=True)

        # Check for existing document with the same SHA
        existing_rows = []
        with db._lock:
            existing_rows = db._conn.execute(
                "SELECT id FROM documents WHERE sha256=?", (sha256,)
            ).fetchall()
        if existing_rows:
            logger.info("folder_watch: skipping %s (duplicate SHA)", file_path.name)
            return True  # treated as success — file was seen

        # Copy to library storage
        dest = lib_root / file_path.name
        if dest.exists():
            dest = lib_root / f"{file_path.stem}_{sha256[:8]}{file_path.suffix}"
        shutil.copy2(file_path, dest)

        # Determine kind
        ext = file_path.suffix.lower()
        kind_map = {
            ".pdf": "pdf", ".docx": "docx", ".doc": "docx",
            ".xlsx": "excel", ".xls": "excel", ".csv": "csv",
            ".pptx": "pptx", ".ppt": "pptx",
            ".txt": "text", ".md": "markdown",
            ".png": "image", ".jpg": "image", ".jpeg": "image",
            ".webp": "image", ".gif": "image",
            ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
            ".py": "code", ".js": "code", ".ts": "code",
            ".jsx": "code", ".tsx": "code",
            ".json": "json", ".html": "html", ".htm": "html",
            ".zip": "zip",
        }
        kind = kind_map.get(ext, "file")

        # Create document
        doc = db.create_document(
            source=str(dest.relative_to(lib_root)),
            title=file_path.name,
            kind=kind,
            content_path=str(dest.relative_to(lib_root)),
            sha256=sha256,
            work_id=work_id,
        )
        doc_id = doc["id"]

        # Queue processing
        from orivellum.capabilities.pipeline import process_document
        from orivellum.api.executor import get_executor
        get_executor().submit(
            process_document,
            doc_id=doc_id, file_path=str(dest), kind=kind,
            work_id=work_id, title=file_path.name, db=db,
        )
        logger.info("folder_watch: imported %s (id=%s)", file_path.name, doc_id)
        return True

    except Exception as exc:
        logger.error("folder_watch: error importing %s: %s", file_path.name, exc)
        return False


def _watch_loop(db: "OrivellumDB") -> None:
    """Main polling loop — runs in the background thread."""
    logger.info("folder_watch: daemon started")
    while not _stop_event.is_set():
        try:
            enabled = db.get_setting("folder_watch_enabled", "false").lower() == "true"
            watch_path = db.get_setting("folder_watch_path", "").strip()
            work_id = db.get_setting("folder_watch_work_id", "").strip() or None

            if enabled and watch_path:
                p = Path(watch_path)
                if p.is_dir():
                    seen = _get_seen_paths(db)
                    for f in sorted(p.iterdir()):
                        if (f.is_file()
                                and f.suffix.lower() in _SUPPORTED_EXTS
                                and str(f) not in seen):
                            success = _import_file(f, work_id, db)
                            if success:
                                _mark_seen(str(f), db)
                elif watch_path:
                    logger.warning("folder_watch: configured path does not exist: %s", watch_path)
        except Exception as exc:
            logger.error("folder_watch: poll error: %s", exc)

        _stop_event.wait(timeout=_POLL_INTERVAL_SEC)

    logger.info("folder_watch: daemon stopped")


def start_watcher(db: "OrivellumDB") -> None:
    """Start the folder watch background thread.  Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_watch_loop,
        args=(db,),
        daemon=True,
        name="folder-watcher",
    )
    _thread.start()


def stop_watcher() -> None:
    """Signal the watcher to stop (non-blocking)."""
    _stop_event.set()
