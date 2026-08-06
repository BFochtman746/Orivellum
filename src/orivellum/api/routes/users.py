"""User-device management endpoints.

POST /api/users/push-token  — Register or refresh an Expo push notification token
DELETE /api/users/push-token  — Remove a push token (e.g. on logout)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from orivellum.api._deps import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


class PushTokenRequest(BaseModel):
    token: str
    platform: str | None = None   # "ios" | "android" | "web"


def _current_key_hash() -> str | None:
    """SHA-256 hex of the active API key — used to scope push tokens."""
    import hashlib
    import os

    from orivellum.api.routes.auth import _resolve_expected_key
    key = _resolve_expected_key() or os.environ.get("SESSION_SECRET", "")
    if not key:
        return None
    return hashlib.sha256(key.encode()).hexdigest()


@router.post("/api/users/push-token")
def register_push_token(body: PushTokenRequest):
    """Register or refresh an Expo push notification token for this device.

    The token is scoped to the authenticated identity (via SHA-256 of the API
    key) so notifications are only delivered to devices that belong to the same
    identity that triggered the event.

    Idempotent — registering the same token twice updates ``updated_at`` only.
    Called on every authenticated app launch so stale tokens are refreshed.
    """
    if not body.token:
        from fastapi import HTTPException
        raise HTTPException(400, "token is required")

    db = get_db()
    key_hash = _current_key_hash()
    db.save_push_token(body.token, body.platform, key_hash=key_hash)
    logger.debug(
        "Push token registered: %s… (%s) key_hash=%s",
        body.token[:24], body.platform,
        key_hash[:8] if key_hash else None,
    )
    return {"ok": True}


@router.delete("/api/users/push-token")
def remove_push_token(body: PushTokenRequest):
    """Deregister a push token (e.g. when the user logs out of the mobile app)."""
    if not body.token:
        from fastapi import HTTPException
        raise HTTPException(400, "token is required")

    db = get_db()
    db.delete_push_token(body.token)
    return {"ok": True}
