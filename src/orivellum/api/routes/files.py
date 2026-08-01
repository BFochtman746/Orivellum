"""File browser routes — /api/files/* and /api/upload, /api/download/*"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from orivellum.api._deps import get_config

router = APIRouter(prefix="/api")


def _data_dir() -> Path:
    cfg = get_config()
    return Path(cfg.data_dir)


@router.get("/files")
def list_files(subdir: str = ""):
    data_dir = _data_dir()
    target = (data_dir / subdir).resolve()
    if not str(target).startswith(str(data_dir)):
        raise HTTPException(403, "Path traversal not allowed")
    if not target.exists():
        return {"files": [], "dirs": [], "path": subdir}

    files = []
    dirs = []
    for item in sorted(target.iterdir()):
        if item.name.startswith("."):
            continue
        rel = str(item.relative_to(data_dir))
        if item.is_dir():
            dirs.append({"name": item.name, "path": rel})
        else:
            stat = item.stat()
            files.append({
                "name": item.name,
                "path": rel,
                "size_bytes": stat.st_size,
                "mime": mimetypes.guess_type(item.name)[0] or "application/octet-stream",
            })
    return {"files": files, "dirs": dirs, "path": subdir}


class UploadRequest(BaseModel):
    filename: str
    content_b64: str
    subdir: str = "intake"


@router.post("/upload")
def upload_file(body: UploadRequest):
    data_dir = _data_dir()
    try:
        data = base64.b64decode(body.content_b64, validate=True)
    except Exception:
        raise HTTPException(400, "content_b64 is not valid base64")

    name = Path(body.filename).name
    if not name or name.startswith("."):
        raise HTTPException(400, f"Bad filename: {body.filename!r}")

    target_dir = (data_dir / body.subdir).resolve()
    if not str(target_dir).startswith(str(data_dir)):
        raise HTTPException(403, "Path traversal not allowed")
    target_dir.mkdir(parents=True, exist_ok=True)

    dest = target_dir / name
    # Handle filename collision
    counter = 1
    while dest.exists():
        stem = Path(name).stem
        suffix = Path(name).suffix
        dest = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1

    dest.write_bytes(data)
    return {
        "ok": True,
        "filename": dest.name,
        "path": str(dest.relative_to(data_dir)),
        "size_bytes": len(data),
    }


@router.get("/download/{path:path}")
def download_file(path: str):
    data_dir = _data_dir()
    target = (data_dir / path).resolve()
    if not str(target).startswith(str(data_dir)):
        raise HTTPException(403, "Path traversal not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"File not found: {path}")
    return FileResponse(str(target), filename=target.name)
