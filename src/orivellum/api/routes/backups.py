"""Backups routes — /api/backups/*"""
from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from orivellum.api._deps import get_config, get_db, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
def _backup_dir() -> Path:
    cfg = get_config()
    p = Path(cfg.data_dir) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/backups")
def list_backups():
    bd = _backup_dir()
    files = sorted(bd.glob("*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
    result = []
    for f in files[:50]:
        stat = f.stat()
        result.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        })
    return {"backups": result, "count": len(result)}


@router.post("/backups")
def create_backup():
    cfg = get_config()
    bd = _backup_dir()
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    zip_path = bd / f"orivellum_backup_{ts}.zip"

    db = get_db()
    # Checkpoint WAL before backup
    with db._lock:
        db._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    data_dir = Path(cfg.data_dir)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        db_path = Path(cfg.db_path)
        if db_path.exists():
            zf.write(db_path, "orivellum.db")
        # Sidecar databases (Studio / Pressworks) — part of a full restore.
        # Snapshot via SQLite's online backup API rather than a raw file copy:
        # a raw copy can race a concurrent transaction (and would silently
        # omit -wal contents if WAL is ever enabled on these files).
        import sqlite3
        import tempfile
        for side in ("atelier.db", "press.db"):
            sp = data_dir / side
            if not sp.exists():
                continue
            with tempfile.TemporaryDirectory() as td:
                snap = Path(td) / side
                src = sqlite3.connect(str(sp))
                try:
                    dst = sqlite3.connect(str(snap))
                    try:
                        src.backup(dst)
                    finally:
                        dst.close()
                finally:
                    src.close()
                zf.write(snap, side)
        # Serving configuration — restoring without it loses model endpoints.
        # Resolve against the repo ROOT (same anchor load_config uses), never
        # the process CWD, which differs under workflow runners.
        from orivellum.configuration.config import ROOT as _root
        cfg_file = _root / "config.yaml"
        if cfg_file.exists():
            zf.write(cfg_file, "config.yaml")
        lib_dir = data_dir / "library"
        if lib_dir.exists():
            for f in lib_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f"library/{f.relative_to(lib_dir)}")

    stat = zip_path.stat()
    return {
        "backup": {
            "name": zip_path.name,
            "size_bytes": stat.st_size,
            "created_at": datetime.now(UTC).isoformat(),
        }
    }


@router.post("/backups/{name}/restore")
def stage_restore(name: str):
    """Stage a backup for restore. The actual swap happens at the next server
    start, before the database is opened — a live SQLite file can't be safely
    replaced under a running app. The current data is snapshotted to a safety
    folder before anything is overwritten, so a restore is itself reversible."""
    bd = _backup_dir()
    src = bd / name
    if not src.exists() or not src.name.endswith(".zip") or "/" in name or ".." in name:
        raise HTTPException(404, "Backup not found")
    try:
        with zipfile.ZipFile(src) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise HTTPException(422, f"Backup archive is corrupt (bad entry: {bad})")
            names = zf.namelist()
            if "orivellum.db" not in names:
                raise HTTPException(422, "Backup does not contain a database — cannot restore")
    except zipfile.BadZipFile:
        raise HTTPException(422, "Backup file is not a valid zip archive") from None

    cfg = get_config()
    pending = Path(cfg.data_dir) / "restore-pending.zip"
    import shutil
    shutil.copy2(src, pending)
    return {"staged": True, "backup": name,
            "detail": "Restore staged. It will be applied the next time the server starts. "
                      "Current data will be kept in a safety snapshot."}


@router.get("/backups/restore/pending")
def restore_pending():
    cfg = get_config()
    return {"pending": (Path(cfg.data_dir) / "restore-pending.zip").exists()}


@router.delete("/backups/restore/pending")
def cancel_restore():
    cfg = get_config()
    pending = Path(cfg.data_dir) / "restore-pending.zip"
    if pending.exists():
        pending.unlink()
        return {"cancelled": True}
    return {"cancelled": False}


@router.get("/backups/{name}/download")
def download_backup(name: str):
    bd = _backup_dir()
    safe_name = Path(name).name
    path = bd / safe_name
    if not path.exists() or not safe_name.endswith(".zip"):
        raise HTTPException(404, f"Backup {name!r} not found")
    return FileResponse(
        path=str(path),
        media_type="application/zip",
        filename=safe_name,
    )


@router.get("/backups/{name}/verify")
def verify_backup(name: str):
    bd = _backup_dir()
    safe_name = Path(name).name
    path = bd / safe_name
    if not path.exists() or not safe_name.endswith(".zip"):
        raise HTTPException(404, f"Backup {name!r} not found")
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            members = zf.namelist()
        return {
            "ok": bad is None,
            "first_bad_file": bad,
            "member_count": len(members),
            "has_db": "orivellum.db" in members,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
