"""Shared dependency injection for route handlers.

Call init() once during application startup to wire the database and config
into all route modules. Routes import get_db() / get_config() — never globals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

_DB: OrivellumDB | None = None
_CFG: OrivellumConfig | None = None


def init(db: OrivellumDB, cfg: OrivellumConfig) -> None:
    global _DB, _CFG
    _DB = db
    _CFG = cfg


def get_db() -> OrivellumDB:
    if _DB is None:
        raise RuntimeError("Database not initialized — call init() first")
    return _DB


def get_config() -> OrivellumConfig:
    if _CFG is None:
        raise RuntimeError("Config not initialized — call init() first")
    return _CFG


def require_auth(request: Request) -> None:
    """FastAPI dependency that enforces authentication (FA-10 defense in depth).

    Applied at the router level on privileged routers so authorization no
    longer relies solely on the global path-prefix middleware in app.py: a
    mounting/path-normalization regression that bypasses the middleware still
    hits this second layer.

    It honours the SAME auth sources the middleware accepts, in the same
    order, using the same helpers (``orivellum.api.auth_keys``):

      1. an authenticated session cookie (``request.session['authenticated']``);
      2. a ``Bearer`` token or ``X-Api-Key`` header, compared constant-time.

    When the middleware already authenticated the request (e.g. via cookie or
    a matching token), this dependency re-runs the same cheap check and passes
    — a no-op in the happy path.  Raises HTTP 401 when no valid credential is
    present, and HTTP 503 (fail closed) when no credential is configured at
    all.
    """
    from fastapi import HTTPException
    from fastapi.security.utils import get_authorization_scheme_param

    from orivellum.api.auth_keys import key_matches, resolve_login_key

    # ── Session cookie (web browser) ──────────────────────────────────────
    # SessionMiddleware runs outermost, so request.session is populated here
    # exactly as it is inside the middleware.  Mirror the middleware's check.
    try:
        if request.session.get("authenticated"):
            return
    except (AssertionError, KeyError):
        # SessionMiddleware not installed (e.g. isolated unit test) — fall
        # through to token-based auth rather than erroring.
        pass

    expected_key = resolve_login_key()
    if not expected_key:
        # Fail CLOSED — no configured credential must never mean open access.
        raise HTTPException(status_code=503,
                            detail="Service not ready — no API key configured")

    auth_header = request.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(auth_header)
    if scheme.lower() != "bearer":
        token = request.headers.get("x-api-key", "")

    if not key_matches(token, expected_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
