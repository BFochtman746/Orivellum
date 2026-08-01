"""Orivellum FastAPI application.

Startup order (enforced here — no shortcuts):
  1. Load configuration
  2. Resolve DB path
  3. Open orivellum.db
  4. Run migrations
  5. Wire _deps
  6. Start API

A clean boot must create ONLY orivellum.db — no monarch.db, no legacy stores.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orivellum import __version__
from orivellum.configuration.config import load_config
from orivellum.database.db import OrivellumDB
from orivellum.api import _deps

logging.basicConfig(
    level=os.environ.get("ORIVELLUM_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("orivellum")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Governed startup: config → db → migrations → deps → serve."""
    # Step 1-2: Config and DB path
    cfg = load_config()
    logger.info("Orivellum %s starting — data_dir=%s", __version__, cfg.data_dir)

    # Step 3-4: Open DB and run migrations
    db = OrivellumDB.open(cfg.db_path)
    logger.info("Database ready: %s (schema v%s)", cfg.db_path,
                db.get_setting("schema_version", "0"))

    # Step 4b: Ensure an API key is configured.
    # Priority: SESSION_SECRET env var > DB-stored key > newly generated key.
    # The env-var path never persists the value to disk; it is assumed the
    # operator has placed SESSION_SECRET in a secrets manager (e.g. Replit
    # Secrets) and that the Vite dev server also reads it from there.
    if os.environ.get("SESSION_SECRET"):
        # Env var takes precedence; no key needs to be written anywhere.
        logger.info("API key sourced from SESSION_SECRET environment variable")
    else:
        if not db.get_setting("api_key"):
            api_key = secrets.token_hex(32)
            db.set_setting("api_key", api_key)
            # Write to a local file so the Vite dev server can read it without
            # an API call.  This file must NOT be committed or exposed publicly.
            key_file = cfg.data_dir / "api_key.txt"
            try:
                key_file.write_text(api_key, encoding="utf-8")
                logger.info("API key generated — stored in %s", key_file)
            except OSError as e:
                logger.warning("Could not write api_key.txt: %s", e)
        else:
            logger.info("API key loaded from database")

    # Step 5: Wire deps
    _deps.init(db=db, cfg=cfg)

    # Step 6: Start background daemons
    try:
        from orivellum.capabilities.nightshift import start_nightshift_daemon
        start_nightshift_daemon(db=db, cfg=cfg)
        logger.info("Nightshift daemon started")
    except Exception as ns_exc:
        logger.warning("Could not start nightshift daemon: %s", ns_exc)

    logger.info("API ready — serving on %s:%d", cfg.server.host, cfg.server.port)
    yield

    # Shutdown: close DB
    logger.info("Shutting down...")
    db.close()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Orivellum",
        version=__version__,
        description="Local-first sovereign AI workspace API",
        lifespan=lifespan,
    )

    # CORS — restricted to configured origins + only this repl's Replit domain.
    # With allow_credentials=True (needed for session cookies), the regex must
    # be exact rather than a wildcard — otherwise any *.replit.dev origin can
    # make credentialed requests using the user's session cookie.
    allowed_origins = os.environ.get(
        "ORIVELLUM_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://localhost:80",
    ).split(",")

    # Build an exact regex for THIS repl's Replit dev domain only.
    # REPLIT_DEV_DOMAIN is set by the Replit runtime; for self-hosted
    # deployments it will be empty and the regex is not added.
    _replit_domain = os.environ.get("REPLIT_DEV_DOMAIN", "").strip()
    _origin_regex: str | None = (
        r"https://" + re.escape(_replit_domain)
        if _replit_domain
        else None
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=_origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Authentication middleware ─────────────────────────────────────────────
    # All routes except the exempted paths below require authentication.
    #
    # The web UI uses session cookies (set via POST /api/auth/login).
    # API / mobile clients may supply: Authorization: Bearer <key>
    # or X-Api-Key: <key>.
    _AUTH_EXEMPT = frozenset({
        "/api/healthz",
        "/api/version",
        # Auth endpoints — the login and status checks must be reachable
        # before a session is established.
        "/api/auth/login",
        "/api/auth/me",
    })

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        # CORS preflight and explicitly exempt paths skip auth.
        if request.method == "OPTIONS" or request.url.path in _AUTH_EXEMPT:
            return await call_next(request)

        # ── Session cookie (web browser) ──────────────────────────────────
        # SessionMiddleware (outermost) has already parsed the cookie by the
        # time this middleware runs, so request.session is populated.
        if request.session.get("authenticated"):
            return await call_next(request)

        # ── Bearer token (API clients / mobile) ───────────────────────────
        expected_key = os.environ.get("SESSION_SECRET", "")
        if not expected_key:
            try:
                db = _deps.get_db()
                expected_key = db.get_setting("api_key", "")
            except RuntimeError:
                return JSONResponse({"detail": "Service unavailable"}, status_code=503)

        if not expected_key:
            # Key not configured yet (startup window); let through so health
            # checks still work.
            return await call_next(request)

        token: str = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("x-api-key", "").strip()

        if token and token == expected_key:
            return await call_next(request)

        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    # ── In-memory sliding-window rate limiter ─────────────────────────────────
    # Keyed by (client_ip, route_prefix) → deque of request timestamps.
    # Limits are intentionally generous for a single-user local workspace.
    _rl_windows: dict[tuple[str, str], Deque[float]] = defaultdict(deque)

    _RATE_LIMITS: dict[str, tuple[int, int]] = {
        # path prefix           max_requests  window_seconds
        "/api/studio/tts":      (20,  60),   # 20 TTS per minute
        "/api/studio/ocr":      (30,  60),   # 30 OCR per minute
        "/api/studio/image":    (10,  60),   # 10 image gen per minute
        # chat send is /api/conversations/<id>/messages
        "/api/conversations":   (60,  60),   # 60 chat messages per minute
    }

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        path = request.url.path
        if request.method in ("POST", "PUT", "PATCH"):
            for prefix, (limit, window) in _RATE_LIMITS.items():
                if path.startswith(prefix):
                    ip = request.client.host if request.client else "unknown"
                    key = (ip, prefix)
                    now = time.monotonic()
                    dq = _rl_windows[key]
                    # Drop timestamps outside the window
                    while dq and dq[0] < now - window:
                        dq.popleft()
                    if len(dq) >= limit:
                        return JSONResponse(
                            {"detail": f"Rate limit exceeded — max {limit} requests per {window}s"},
                            status_code=429,
                            headers={"Retry-After": str(window)},
                        )
                    dq.append(now)
                    break
        return await call_next(request)

    # ── Request size limit ────────────────────────────────────────────────────
    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        if request.headers.get("content-length"):
            try:
                cfg = _deps.get_config()
                size = int(request.headers["content-length"])
                if size > cfg.server.max_body_bytes:
                    return JSONResponse(
                        {"detail": f"Request body too large ({size} bytes)"},
                        status_code=413,
                    )
            except Exception:
                pass
        return await call_next(request)

    # Register routers
    from orivellum.api.routes import (
        auth, health, works, conversations, library, knowledge,
        projects, backups, studio, files, system, dashboard, learning,
    )
    for module in [auth, health, works, conversations, library, knowledge,
                   projects, backups, studio, files, system, dashboard, learning]:
        app.include_router(module.router)

    # 404 handler for /api/* paths
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Session middleware ────────────────────────────────────────────────────
    # Added LAST so it ends up OUTERMOST in the chain (processes requests
    # first), ensuring request.session is populated when auth_middleware runs.
    from starlette.middleware.sessions import SessionMiddleware
    app.add_middleware(
        SessionMiddleware,
        secret_key=_init_session_secret(),
        session_cookie="orivellum_session",
        max_age=86400 * 30,   # 30 days
        https_only=False,     # Allow HTTP in local development
        same_site="lax",
    )

    return app


def _init_session_secret() -> str:
    """Return a stable, non-public session-signing secret.

    Priority:
      1. SESSION_SECRET env var (Replit Secrets / operator-provided)
      2. ``data/.session_secret`` file (written on first run, stable across restarts)
      3. Fresh ``secrets.token_hex(32)`` if the file cannot be written

    This function runs at ``create_app()`` time — before the lifespan starts
    the DB — so it reads/writes a plain text sidecar file rather than the DB.
    The value is cryptographically random in every case; there is no known
    fallback literal that an attacker could use to forge session cookies.

    The session-signing secret is deliberately separate from the API bearer-token
    credential (SESSION_SECRET / DB ``api_key``).  When SESSION_SECRET is set,
    the same value happens to serve both roles; for local deployments without
    SESSION_SECRET the two secrets are independently random.
    """
    if env_secret := os.environ.get("SESSION_SECRET"):
        return env_secret

    # Locate the data directory using the same env var the config uses so we
    # don't need to call load_config() here.
    data_dir = os.environ.get("ORIVELLUM_DATA_DIR", "data")
    secret_file = Path(data_dir) / ".session_secret"

    # Try to load a previously-generated secret from disk.
    try:
        if secret_file.exists():
            stored = secret_file.read_text(encoding="utf-8").strip()
            if len(stored) >= 32:  # Sanity check — must be a real key
                return stored
    except OSError:
        pass

    # Generate a fresh, cryptographically random secret.
    fresh = secrets.token_hex(32)
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(fresh, encoding="utf-8")
    except OSError:
        # Cannot persist; the secret is still random for this process lifetime.
        # Cookies will be invalidated on next restart — acceptable for a
        # local-first single-user app.
        pass
    return fresh


app = create_app()
