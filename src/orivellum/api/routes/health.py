"""Health, version, and diagnostics endpoints."""
from __future__ import annotations

import platform
import socket
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

import psutil
from fastapi import APIRouter

from orivellum import __version__
from orivellum.api._deps import get_db, get_config

router = APIRouter()


def _probe_ai(base_url: str, timeout: float = 1.0) -> dict:
    """TCP probe to the AI server — no HTTP call, sub-millisecond if up."""
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "ok", "url": base_url}
    except OSError:
        return {"status": "unavailable", "url": base_url}


@router.get("/api/healthz")
def health():
    db = get_db()
    cfg = get_config()
    db_health = db.health()
    ai_health = _probe_ai(cfg.serving.base_url)
    overall = "ok" if db_health["status"] == "ok" else "degraded"
    return {
        "status": overall,
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": {
            "database": db_health,
            "ai": ai_health,
        },
    }


@router.get("/api/version")
def version():
    return {
        "version": __version__,
        "product": "Orivellum",
        "python": sys.version,
        "platform": platform.platform(),
    }


@router.get("/api/diagnostics")
def diagnostics():
    db = get_db()
    cfg = get_config()
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "version": __version__,
        "database": db.health(),
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "memory_used_mb": round(mem.used / 1024 / 1024),
            "memory_total_mb": round(mem.total / 1024 / 1024),
            "disk_used_gb": round(disk.used / 1024 / 1024 / 1024, 2),
            "disk_total_gb": round(disk.total / 1024 / 1024 / 1024, 2),
        },
        "configuration": cfg.effective(redact=True),
    }


@router.get("/api/configuration/effective")
def effective_config():
    cfg = get_config()
    return cfg.effective(redact=True)
