"""Shared dependency injection for route handlers.

Call init() once during application startup to wire the database and config
into all route modules. Routes import get_db() / get_config() — never globals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fastapi
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

_DB: "OrivellumDB | None" = None
_CFG: "OrivellumConfig | None" = None


def init(db: "OrivellumDB", cfg: "OrivellumConfig") -> None:
    global _DB, _CFG
    _DB = db
    _CFG = cfg


def get_db() -> "OrivellumDB":
    if _DB is None:
        raise RuntimeError("Database not initialized — call init() first")
    return _DB


def get_config() -> "OrivellumConfig":
    if _CFG is None:
        raise RuntimeError("Config not initialized — call init() first")
    return _CFG


def require_auth(request: "fastapi.Request") -> None:
    """FastAPI dependency that enforces API key authentication.

    Raises HTTP 401 when the request does not carry a valid bearer token or
    X-Api-Key header.  Import and include as a router dependency where you
    need per-route granularity; the global middleware in app.py covers all
    routes by default.
    """
    import os
    from fastapi import HTTPException  # noqa: F401
    from fastapi.security.utils import get_authorization_scheme_param

    # SESSION_SECRET takes priority — same source of truth as the middleware.
    expected_key = os.environ.get("SESSION_SECRET", "")
    if not expected_key:
        if _DB is None:
            raise RuntimeError("Database not initialized")
        expected_key = _DB.get_setting("api_key", "")

    auth_header = request.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(auth_header)
    if scheme.lower() != "bearer":
        token = request.headers.get("x-api-key", "")

    if not expected_key or token != expected_key:
        raise HTTPException(status_code=401, detail="Unauthorized")
