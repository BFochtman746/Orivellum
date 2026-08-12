"""Durable browser-notification feed (schema v152).

Workers call :func:`emit` when something the user cares about finishes
("document ready", "audiobook ready").  Two delivery channels:

1. **Durable ledger** — events are written to the ``notif_ledger`` table, so
   the PWA polling cursor (``GET /api/system/notifications?after=<id>``)
   survives server restarts instead of resetting with an in-memory ring.
2. **Web Push (opt-in)** — when subscriptions exist, a *minimal* payload
   (event kind + deep link, never content) is fanned out in the background so
   an iPhone Home-Screen PWA hears about it even while suspended.

``configure(db)`` is called once at app startup; until then (unit tests,
early boot) emit falls back to an in-memory ring with the same shape.
``BOOT_ID`` is persisted as a setting so clients no longer see a cursor reset
on every restart — it only changes if the ledger itself is lost.

``emit`` must never raise: it is called from pipeline/TTS workers where a
notification failure must not affect the actual work.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_BOOT_ID_KEY = "notif_boot_id"

# Fallback (pre-configure / tests): in-memory ring, same event shape.
_MAX_EVENTS = 200
_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=_MAX_EVENTS)
_next_id = 0

_db: OrivellumDB | None = None

# Process-local default; replaced by the persisted value in configure().
BOOT_ID = uuid.uuid4().hex


def configure(db: OrivellumDB) -> None:
    """Wire the durable ledger + push fan-out.  Called once at app startup."""
    global _db, BOOT_ID
    _db = db
    try:
        persisted = db.get_setting(_BOOT_ID_KEY, "")
        if not persisted:
            persisted = uuid.uuid4().hex
            db.set_setting_unaudited(_BOOT_ID_KEY, persisted)
        BOOT_ID = persisted
    except Exception:  # pragma: no cover - defensive
        logger.exception("notification configure failed (falling back to ring)")
        _db = None


def emit(kind: str, title: str, body: str = "", url: str = "") -> None:
    """Append a notification event and fan out push (background).  Never raises."""
    global _next_id
    try:
        if _db is not None:
            nid = _db.add_notification(kind, str(title), str(body), str(url))
            if nid is not None:
                _fan_out_push(nid, kind, str(url))
            return
        with _lock:
            _next_id += 1
            _events.append(
                {
                    "id": _next_id,
                    "kind": kind,
                    "title": str(title)[:120],
                    "body": str(body)[:300],
                    "url": str(url)[:500],
                    "created_at": time.time(),
                }
            )
    except Exception:  # pragma: no cover - defensive
        logger.exception("notification emit failed (non-fatal)")


def _fan_out_push(nid: int, kind: str, url: str) -> None:
    """Schedule Web Push delivery on the shared executor (never inline)."""
    try:
        if _db is None or not _db.list_push_subscriptions():
            return
        from orivellum.api import webpush
        from orivellum.api.executor import submit_bg

        submit_bg(
            webpush.send_to_all,
            _db,
            {"id": nid, "kind": kind, "url": url},
            kind="push",
            label="webpush_fanout",
        )
    except Exception:  # pragma: no cover - defensive
        logger.exception("push fan-out scheduling failed (non-fatal)")


def list_after(after_id: int) -> tuple[list[dict], int]:
    """Return (events newer than *after_id*, latest id).

    The latest id is returned even when no events match so clients can
    fast-forward their cursor (e.g. after first load).
    """
    if _db is not None:
        try:
            return _db.list_notifications(after_id)
        except Exception:  # pragma: no cover - defensive
            logger.exception("ledger read failed; falling back to ring")
    with _lock:
        events = [dict(e) for e in _events if e["id"] > after_id]
        return events, _next_id


def _reset_for_tests() -> None:
    """Test helper: detach the ledger, empty the ring, restart ids."""
    global _next_id, _db
    _db = None
    with _lock:
        _events.clear()
        _next_id = 0
