"""Backups routes — /api/backups/*"""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")


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
            "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return {"backups": result, "count": len(result)}


@router.post("/backups")
def create_backup():
    cfg = get_config()
    bd = _backup_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
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
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    }


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
