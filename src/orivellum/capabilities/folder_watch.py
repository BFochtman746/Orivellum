"""Folder watch — auto-import new files from watched directories.

Orivellum can watch one or more local directories for new files and
automatically import them into the library.  This is useful for users who save
documents to a specific folder on their device (e.g. Downloads, Obsidian vault).

The watcher runs as a single background thread.  It polls every
``_POLL_INTERVAL_SEC`` seconds with no inotify/FSEvents dependency — works on
all platforms including Windows.

Configuration (stored in settings table):
  watch_dirs   — JSON array of ``{"path": str, "work_id": str|null, "enabled": bool}``
                 (multi-dir config; preferred over legacy keys below)
  watch_dirs_status — JSON written after each scan:
                 ``{"scanned_at": ISO, "dirs": [{"path", "files_imported", "error"}]}``

Legacy single-dir keys (read-only compat; replaced when watch_dirs is written):
  folder_watch_path     — absolute path to watch
  folder_watch_enabled  — "true" / "false"
  folder_watch_work_id  — optional Work id

Seen-file registry (prevents re-import):
  folder_watch_seen — JSON array of canonical absolute paths already imported;
                      capped at 10 000 entries.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.folder_watch")

_POLL_INTERVAL_SEC = 60  # within-60-second guarantee
_SUPPORTED_EXTS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".csv",
    ".pptx",
    ".ppt",
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".html",
    ".htm",
    ".rtf",
    ".epub",
    ".xml",
    ".zip",
    ".mp3",
    ".wav",
    ".m4a",
    ".ogg",
    ".flac",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ─── Watch-dirs config helpers ────────────────────────────────────────────────


def get_watch_dirs(db: OrivellumDB) -> list[dict]:
    """Return the list of configured watch directories.

    Prefers the ``watch_dirs`` setting (multi-dir JSON array) and falls back to
    the legacy single-dir keys if ``watch_dirs`` is not set.
    """
    raw = db.get_setting("watch_dirs", "")
    if raw:
        try:
            dirs = json.loads(raw)
            if isinstance(dirs, list):
                return dirs
        except Exception:
            pass

    # Legacy single-dir compat
    legacy_path = db.get_setting("folder_watch_path", "").strip()
    if legacy_path:
        return [
            {
                "path": legacy_path,
                "work_id": db.get_setting("folder_watch_work_id", "").strip() or None,
                "enabled": db.get_setting("folder_watch_enabled", "false").lower() == "true",
            }
        ]
    return []


def set_watch_dirs(dirs: list[dict], db: OrivellumDB) -> None:
    """Persist the watch-dirs list and clear the legacy single-dir keys."""
    db.set_setting("watch_dirs", json.dumps(dirs), actor="user")
    # Clear legacy keys so the UI and watcher read from watch_dirs only.
    db.set_setting("folder_watch_path", "", actor="system")
    db.set_setting("folder_watch_enabled", "false", actor="system")
    db.set_setting("folder_watch_work_id", "", actor="system")


def get_watch_status(db: OrivellumDB) -> dict:
    """Return the status written by the last watcher scan cycle."""
    raw = db.get_setting("watch_dirs_status", "")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {"scanned_at": None, "dirs": []}


# ─── Seen-file registry ────────────────────────────────────────────────────────


def _get_seen_paths(db: OrivellumDB) -> set[str]:
    try:
        raw = db.get_setting("folder_watch_seen", "")
        return set(json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _mark_seen(paths: list[str], db: OrivellumDB) -> None:
    try:
        seen = _get_seen_paths(db)
        seen.update(paths)
        if len(seen) > 10_000:
            seen = set(list(seen)[-8_000:])
        db.set_setting("folder_watch_seen", json.dumps(list(seen)), actor="system")
    except Exception as exc:
        logger.debug("folder_watch: could not mark seen: %s", exc)


# ─── File importer ────────────────────────────────────────────────────────────

_KIND_MAP: dict[str, str] = {
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
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".zip": "zip",
}


def _collection_for_watch_dir(dir_path: str, db: OrivellumDB) -> str | None:
    """Get-or-create the provenance collection for a watched directory.

    Keyed on the directory path (source_ref) so every scan of the same
    directory lands documents in the same collection.  Returns None on any
    failure — provenance must never block an import.
    """
    try:
        source_ref = f"folder:{dir_path}"
        coll = db.find_collection_by_source_ref(source_ref)
        if coll is None:
            coll = db.create_collection(
                label=Path(dir_path).name or dir_path,
                source_kind="folder",
                source_ref=source_ref,
            )
        return coll["id"]
    except Exception as exc:
        logger.warning("folder_watch: collection lookup failed for %s: %s", dir_path, exc)
        return None


def _import_file(
    file_path: Path,
    work_id: str | None,
    db: OrivellumDB,
    collection_id: str | None = None,
) -> bool:
    """Copy a file into the library and queue it for processing.

    Returns True on success (including dedup skip).
    """
    try:
        import hashlib
        import shutil

        sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()

        from orivellum.api._deps import get_config

        cfg = get_config()
        lib_root = Path(cfg.data_dir) / "library"
        lib_root.mkdir(parents=True, exist_ok=True)

        with db._lock:
            existing = db._conn.execute(
                "SELECT id FROM documents WHERE sha256=?", (sha256,)
            ).fetchall()
        if existing:
            logger.info("folder_watch: skipping %s (duplicate SHA)", file_path.name)
            return True

        dest = lib_root / file_path.name
        if dest.exists():
            dest = lib_root / f"{file_path.stem}_{sha256[:8]}{file_path.suffix}"
        shutil.copy2(file_path, dest)

        kind = _KIND_MAP.get(file_path.suffix.lower(), "file")
        from orivellum.capabilities.classify import classify_doc_type, classify_object

        _tier = classify_object(file_path.name, kind=kind, source_path=str(file_path))
        _dt = classify_doc_type(file_path.name, kind=kind, source_path=str(file_path))
        doc = db.create_document(
            source=str(dest.relative_to(lib_root)),
            title=file_path.name,
            kind=kind,
            content_path=str(dest.relative_to(lib_root)),
            sha256=sha256,
            work_id=work_id,
            collection_id=collection_id,
            tier=_tier.tier.value,
            doc_type=_dt.doc_type.value,
            doc_type_by=f"rule:{_dt.rule}",
        )
        if collection_id:
            try:
                db.refresh_collection_count(collection_id)
            except Exception as count_exc:  # count sync is best-effort
                logger.debug("folder_watch: collection count refresh failed: %s", count_exc)

        from orivellum.api.executor import get_executor
        from orivellum.capabilities.pipeline import process_document

        get_executor().submit(
            process_document,
            doc_id=doc["id"],
            file_path=str(dest),
            kind=kind,
            work_id=work_id,
            title=file_path.name,
            db=db,
        )
        logger.info("folder_watch: imported %s → id=%s", file_path.name, doc["id"])
        return True

    except Exception as exc:
        logger.error("folder_watch: error importing %s: %s", file_path.name, exc)
        return False


# ─── Main polling loop ────────────────────────────────────────────────────────


def _watch_loop(db: OrivellumDB) -> None:
    logger.info("folder_watch: daemon started (interval=%ds)", _POLL_INTERVAL_SEC)
    while not _stop_event.is_set():
        dir_statuses: list[dict] = []
        try:
            dirs = get_watch_dirs(db)
            seen = _get_seen_paths(db)
            newly_seen: list[str] = []

            for entry in dirs:
                path_str = (entry.get("path") or "").strip()
                enabled = bool(entry.get("enabled", True))
                work_id = entry.get("work_id") or None

                status: dict = {"path": path_str, "files_imported": 0, "error": None}
                dir_statuses.append(status)

                if not enabled or not path_str:
                    continue

                p = Path(path_str)
                try:
                    if not p.is_dir():
                        status["error"] = "directory not found"
                        logger.warning("folder_watch: path does not exist: %s", path_str)
                        continue

                    collection_id: str | None = None
                    for f in sorted(p.iterdir()):
                        if (
                            f.is_file()
                            and f.suffix.lower() in _SUPPORTED_EXTS
                            and str(f) not in seen
                        ):
                            if collection_id is None:
                                collection_id = _collection_for_watch_dir(path_str, db)
                            if _import_file(f, work_id, db, collection_id=collection_id):
                                newly_seen.append(str(f))
                                seen.add(str(f))
                                status["files_imported"] += 1
                except OSError as dir_exc:
                    status["error"] = str(dir_exc)
                    logger.warning("folder_watch: could not scan %s: %s", path_str, dir_exc)

            if newly_seen:
                _mark_seen(newly_seen, db)

        except Exception as exc:
            logger.error("folder_watch: poll cycle error: %s", exc)

        # Write scan status so the UI can display last-scan info
        try:
            from datetime import datetime

            scan_status = {
                "scanned_at": datetime.now(UTC).isoformat(),
                "dirs": dir_statuses,
            }
            db.set_setting("watch_dirs_status", json.dumps(scan_status), actor="system")
        except Exception:
            pass

        _stop_event.wait(timeout=_POLL_INTERVAL_SEC)

    logger.info("folder_watch: daemon stopped")


# ─── Public API ───────────────────────────────────────────────────────────────


def start_watcher(db: OrivellumDB) -> None:
    """Start the folder-watch background thread.  Idempotent."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    # Deliberately a dedicated daemon thread, NOT the shared executor: this is
    # a long-lived polling loop that would otherwise permanently occupy one of
    # the bounded pool workers.  Same pattern as the nightshift scheduler loop.
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
