"""Expo push notification sender.

Uses the Expo Push Notification service (https://exp.host/--/api/v2/push/send).
No server-side credentials are required — Expo tokens issued by the mobile SDK
are self-contained.  All public calls are best-effort and never raise.

Typical usage (fire-and-forget from a background thread)::

    import threading
    from orivellum.capabilities.push import notify_push_best_effort

    threading.Thread(
        target=notify_push_best_effort,
        args=(db, "📄 Document ready", "14 items extracted", {"screen": f"library/{doc_id}"}),
        daemon=True,
    ).start()
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_push(
    token: str,
    title: str,
    body: str,
    data: dict | None = None,
    *,
    sound: str = "default",
) -> bool:
    """Send a single Expo push notification.

    Args:
        token:  Expo push token (starts with ``ExponentPushToken[``).
        title:  Notification title line.
        body:   Notification body text.
        data:   Optional payload dict (e.g. ``{"screen": "library/abc123"}``).
        sound:  Notification sound name. ``"default"`` uses the system default.

    Returns:
        ``True`` if Expo accepted the message, ``False`` otherwise.
    """
    try:
        import httpx

        resp = httpx.post(
            _EXPO_PUSH_URL,
            json={
                "to": token,
                "title": title,
                "body": body,
                "data": data or {},
                "sound": sound,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=6.0,
        )
        if resp.status_code != 200:
            logger.warning("Expo push API HTTP %d: %s", resp.status_code, resp.text[:200])
            return False

        # Expo 2.0 response: {"data": [{"status": "ok"|"error", ...}]}
        payload = resp.json()
        for item in payload.get("data", []):
            if item.get("status") == "error":
                logger.warning(
                    "Expo rejected push to %s…: %s",
                    token[:24],
                    item.get("message", "unknown error"),
                )
                return False

        return True

    except Exception as exc:
        logger.debug("send_push non-fatal: %s", exc)
        return False


def _current_key_hash(db: "OrivellumDB") -> str | None:
    """Return the SHA-256 hex digest of the configured API key.

    This value is used to scope push tokens to the identity that registered
    them.  When the key is empty (bootstrap / unconfigured state) returns None
    so all stored tokens are targeted — safe because there are no cross-user
    concerns when no key is set.
    """
    import hashlib
    import os

    key = os.environ.get("SESSION_SECRET", "") or db.get_setting("api_key", "")
    if not key:
        return None
    return hashlib.sha256(key.encode()).hexdigest()


def notify_push_best_effort(
    db: "OrivellumDB",
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    """Send a push notification to device tokens authorized for the current identity.

    Scoping: fetches only tokens whose ``key_hash`` matches the SHA-256 of the
    currently configured API key — ensuring that in any deployment with multiple
    identities a user never receives events triggered by another user's resources.

    Completely non-fatal — errors are logged at DEBUG level and swallowed.
    Intended to be called from daemon threads so it never blocks a pipeline.
    """
    try:
        key_hash = _current_key_hash(db)
        tokens = db.get_all_push_tokens(key_hash=key_hash)
        if not tokens:
            logger.debug("notify_push_best_effort: no tokens for key_hash=%s, skipping",
                         key_hash[:8] if key_hash else None)
            return
        for token in tokens:
            send_push(token, title, body, data)
    except Exception as exc:
        logger.debug("notify_push_best_effort failed: %s", exc)
