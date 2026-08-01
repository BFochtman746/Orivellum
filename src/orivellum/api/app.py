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
import sys
from contextlib import asynccontextmanager

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

    # Step 5: Wire deps
    _deps.init(db=db, cfg=cfg)

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

    # CORS — restricted; configure ORIVELLUM_ALLOWED_ORIGINS for LAN access
    allowed_origins = os.environ.get(
        "ORIVELLUM_ALLOWED_ORIGINS",
        "http://localhost:5173,http://localhost:3000,http://localhost:80",
    ).split(",")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        # Also allow all *.replit.dev subdomains (Expo web preview, dev proxy)
        allow_origin_regex=r"https://.*\.replit\.dev",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request size limit middleware
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
        health, works, conversations, library, knowledge,
        projects, backups, studio, files, system, dashboard,
    )
    for module in [health, works, conversations, library, knowledge,
                   projects, backups, studio, files, system, dashboard]:
        app.include_router(module.router)

    # 404 handler for /api/* paths
    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app


app = create_app()
