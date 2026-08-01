"""Shared dependency injection for route handlers.

Call init() once during application startup to wire the database and config
into all route modules. Routes import get_db() / get_config() — never globals.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
