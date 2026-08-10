"""In-process browser-notification feed.

Replaces the retired mobile push channel (schema v111) with a local-first
mechanism: workers call :func:`emit` when something the user cares about
finishes ("document ready", "audiobook ready"), and the PWA polls
``GET /api/system/notifications?after=<id>`` to pick up new events and show
them as browser Notifications.

Design notes:
- Purely in-memory ring buffer — notifications are ephemeral alerts, not
  records.  No DB writes on the hot pipeline path, no schema migration.
- ``BOOT_ID`` lets clients detect a server restart (ids reset to 0) and
  resynchronise their cursor without replaying stale alerts.
- ``emit`` must never raise: it is called from pipeline/TTS workers where a
  notification failure must not affect the actual work.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque

logger = logging.getLogger(__name__)

# Identifies this server process; changes on every restart.
BOOT_ID = uuid.uuid4().hex

# Keep the newest N events — enough for a client that polls every ~15 s to
# never miss one, small enough to be irrelevant memory-wise.
_MAX_EVENTS = 200

_lock = threading.Lock()
_events: deque[dict] = deque(maxlen=_MAX_EVENTS)
_next_id = 0


def emit(kind: str, title: str, body: str = "", url: str = "") -> None:
    """Append a notification event.  Never raises."""
    global _next_id
    try:
        with _lock:
            _next_id += 1
            _events.append({
                "id": _next_id,
                "kind": kind,
                "title": str(title)[:120],
                "body": str(body)[:300],
                "url": str(url)[:500],
                "created_at": time.time(),
            })
    except Exception:  # pragma: no cover - defensive; deque append can't fail
        logger.exception("notification emit failed (non-fatal)")


def list_after(after_id: int) -> tuple[list[dict], int]:
    """Return (events newer than *after_id*, latest id).

    The latest id is returned even when no events match so clients can
    fast-forward their cursor (e.g. after a restart or first load).
    """
    with _lock:
        events = [dict(e) for e in _events if e["id"] > after_id]
        return events, _next_id


def _reset_for_tests() -> None:
    """Test helper: empty the ring and restart ids."""
    global _next_id
    with _lock:
        _events.clear()
        _next_id = 0
