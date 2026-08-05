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
            logger.info("API key generated and stored in database")
        else:
            api_key = db.get_setting("api_key")
            logger.info("API key loaded from database")
        # Always (re)write api_key.txt so the user can find it on disk.
        key_file = Path(cfg.data_dir) / "api_key.txt"
        try:
            key_file.write_text(api_key, encoding="utf-8")
            logger.info("API key written to %s", key_file)
        except OSError as e:
            logger.warning("Could not write api_key.txt: %s", e)

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

    # CORS — restricted to explicitly configured origins only.
    # With allow_credentials=True (needed for session cookies), the origin list
    # must be exact — a regex covering broad IP ranges would let any service on
    # a matching LAN address make credentialed requests with the user's session
    # cookie.  Users who need LAN access should add their origin to
    # ORIVELLUM_ALLOWED_ORIGINS instead.
    allowed_origins = os.environ.get(
        "ORIVELLUM_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://localhost:80",
    ).split(",")

    # Also allow this repl's exact Replit dev domains (https only) — the web
    # preview domain and the Expo web preview domain (mobile app in a browser).
    for _env in ("REPLIT_DEV_DOMAIN", "REPLIT_EXPO_DEV_DOMAIN"):
        _domain = os.environ.get(_env, "").strip()
        if _domain:
            _origin = f"https://{_domain}"
            if _origin not in allowed_origins:
                allowed_origins.append(_origin)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
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
        path = request.url.path

        # PWA API calls arrive with BASE_URL prefix (/orivellum-ui/api/…).
        # Strip it so the exempt-list and API-path checks both work correctly.
        effective_path = (
            path[len("/orivellum-ui"):] if path.startswith("/orivellum-ui/api/") else path
        )

        # CORS preflight and explicitly exempt paths skip auth.
        if request.method == "OPTIONS" or effective_path in _AUTH_EXEMPT:
            return await call_next(request)

        # Static UI assets (served by the StaticFiles mount) need no auth.
        # Only enforce auth on /api/* requests (both bare and /orivellum-ui/-prefixed).
        if not effective_path.startswith("/api/"):
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
            # No key is available even after startup completed — this should
            # never happen in normal operation (lifespan generates one).
            # Fail CLOSED: return 503 rather than opening the API to anyone.
            # Health/auth exempt paths are already handled above.
            import logging as _log
            _log.getLogger(__name__).error(
                "auth_middleware: no API key found after startup — request denied (503). "
                "Set SESSION_SECRET or check database accessibility."
            )
            return JSONResponse({"detail": "Service not ready — no API key configured"}, status_code=503)

        token: str = ""
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("x-api-key", "").strip()

        if token and token == expected_key:
            return await call_next(request)

        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    # ── Path normalizer ───────────────────────────────────────────────────────
    # PWA requests arrive with the BASE_URL prefix (/orivellum-ui/api/…).
    # Strip it once here so every policy check (rate-limit, body-size, auth)
    # works identically regardless of whether the caller is the PWA or a
    # direct API client.
    def _canonical_path(raw: str) -> str:
        if raw.startswith("/orivellum-ui/api/"):
            return raw[len("/orivellum-ui"):]
        return raw

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
        path = _canonical_path(request.url.path)
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
    # Routes that stream the body to disk (multipart upload) are exempt from the
    # in-RAM body limit — their practical ceiling is disk space, not memory.
    _BODY_LIMIT_EXEMPT = frozenset({"/api/library/upload"})

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        if (request.headers.get("content-length")
                and _canonical_path(request.url.path) not in _BODY_LIMIT_EXEMPT):
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
        projects, backups, studio, files, system, dashboard, learning, write,
        mcos, review, claims, pklos, intake, generate, topics,
    )
    _route_modules = [
        auth, health, works, conversations, library, knowledge,
        projects, backups, studio, files, system, dashboard, learning, write,
        mcos, review, claims, pklos, intake, generate, topics,
    ]
    for module in _route_modules:
        app.include_router(module.router)
        # Also mount under /orivellum-ui so requests from the installed PWA —
        # which prefix every API call with BASE_URL (/orivellum-ui/) — resolve
        # to the right handlers.  include_in_schema=False avoids duplicate
        # operation IDs in the OpenAPI schema.
        app.include_router(module.router, prefix="/orivellum-ui", include_in_schema=False)

    # ── Governed-core exception handlers ─────────────────────────────────────
    from orivellum.database.db import VersionConflictError
    from orivellum.capabilities.state_machine import (
        InvalidTransitionError, BlockedTransitionError,
    )

    @app.exception_handler(VersionConflictError)
    async def version_conflict_handler(request: Request, exc: VersionConflictError):
        """Return 409 Conflict whenever an optimistic-concurrency check fails."""
        return JSONResponse(
            {
                "detail": str(exc),
                "error": "VERSION_CONFLICT",
                "object_id": exc.object_id,
                "expected_version": exc.expected,
                "actual_version": exc.actual,
                "retryable": True,
            },
            status_code=409,
        )

    @app.exception_handler(InvalidTransitionError)
    async def invalid_transition_handler(request: Request,
                                         exc: InvalidTransitionError):
        """Return 422 Unprocessable Entity for undeclared state-machine transitions.

        The response tells the client the current state, the disallowed target,
        and what targets are actually reachable, so it can surface a useful
        error to the user without a round-trip.
        """
        return JSONResponse(
            {
                "detail": str(exc),
                "error": "INVALID_TRANSITION",
                "from_state": exc.from_state,
                "to_state": exc.to_state,
                "allowed": sorted(exc.allowed),
                "retryable": False,
            },
            status_code=422,
        )

    @app.exception_handler(BlockedTransitionError)
    async def blocked_transition_handler(request: Request,
                                          exc: BlockedTransitionError):
        """Return 409 Conflict when open findings block a forward transition.

        Names every blocking finding so the client can link directly to the
        governance queue for resolution.
        """
        return JSONResponse(
            {
                "detail": str(exc),
                "error": "BLOCKED_TRANSITION",
                "from_state": exc.from_state,
                "to_state": exc.to_state,
                "blockers": [
                    {
                        "id": b["id"],
                        "kind": b.get("kind", "issue"),
                        "severity": b.get("severity", "high"),
                        "description": b.get("description", ""),
                    }
                    for b in exc.blockers
                ],
                "retryable": False,
            },
            status_code=409,
        )

    # 404 handler for /api/* paths
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    # ── Static UI serving (production PWA) ───────────────────────────────────
    # When the built UI exists (production / self-hosted mode), serve it from
    # the API so a single process handles everything — no Vite dev server needed.
    # SPAStaticFiles serves index.html for any path that doesn't match a real
    # file, which is required for client-side routing (wouter deep links).
    # Falls back gracefully when dist/public doesn't exist (Replit dev mode).
    _ui_dist = Path(__file__).resolve().parents[3] / "artifacts" / "orivellum-ui" / "dist" / "public"
    if _ui_dist.exists():
        from fastapi.staticfiles import StaticFiles as _StaticFiles
        from starlette.exceptions import HTTPException as _HTTPException

        class _SPAStaticFiles(_StaticFiles):
            """Static file handler that falls back to index.html for SPA routing."""
            async def get_response(self, path: str, scope):
                try:
                    return await super().get_response(path, scope)
                except _HTTPException as exc:
                    if exc.status_code == 404:
                        # Any path that isn't a real asset gets the SPA shell,
                        # then the client-side router (wouter) takes over.
                        return await super().get_response("index.html", scope)
                    raise

        app.mount("/orivellum-ui", _SPAStaticFiles(directory=str(_ui_dist), html=True), name="ui")
        logger.info("Serving built UI from %s", _ui_dist)

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
