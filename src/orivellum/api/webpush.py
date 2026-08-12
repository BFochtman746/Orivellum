"""Web Push delivery (VAPID) — minimal payloads only.

Payloads carry the event kind + deep link, never content: the service worker
maps the kind to a generic title.  This keeps push bodies free of user data
(they transit Apple/Google push relays) and works with iOS Home-Screen PWAs.

VAPID keys are generated once and stored as *secret settings* via
``set_setting_unaudited`` — the private key must never appear in the audit
trail.  Delivery runs on the shared executor (see notifications._fan_out_push);
410/404 responses prune dead subscriptions.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_PRIV_KEY = "webpush_vapid_private_pem"
_PUB_KEY = "webpush_vapid_public_b64u"
_SUB_CLAIM = "mailto:orivellum@localhost"


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def ensure_vapid_keys(db: OrivellumDB) -> str:
    """Return the public key (base64url, uncompressed point), creating keys once."""
    pub = db.get_setting(_PUB_KEY, "")
    if pub:
        return pub
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    point = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    pub = _b64u(point)
    db.set_setting_unaudited(_PRIV_KEY, pem)
    db.set_setting_unaudited(_PUB_KEY, pub)
    logger.info("Generated VAPID keypair for Web Push")
    return pub


def _private_pem(db: OrivellumDB) -> str | None:
    pem = db.get_setting(_PRIV_KEY, "")
    return pem or None


def send_to_all(db: OrivellumDB, payload: dict) -> dict:
    """Send a minimal payload to every subscription; prune dead ones.

    Returns {"sent": n, "failed": n, "pruned": n} — callers may log it.
    """
    pem = _private_pem(db)
    subs = db.list_push_subscriptions()
    if not pem or not subs:
        return {"sent": 0, "failed": 0, "pruned": 0}
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - dependency is declared
        logger.warning("pywebpush not installed — Web Push disabled")
        return {"sent": 0, "failed": len(subs), "pruned": 0}

    body = json.dumps(payload)
    sent = failed = pruned = 0
    for sub in subs:
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=body,
                vapid_private_key=pem,
                vapid_claims={"sub": _SUB_CLAIM},
                ttl=3600,
            )
            db.mark_push_result(sub["endpoint"], ok=True)
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                db.delete_push_subscription(sub["endpoint"])
                pruned += 1
            else:
                db.mark_push_result(sub["endpoint"], ok=False, error=str(exc))
                failed += 1
        except Exception as exc:  # noqa: BLE001 - one bad sub must not stop fan-out
            db.mark_push_result(sub["endpoint"], ok=False, error=str(exc))
            failed += 1
    if failed or pruned:
        logger.info("webpush fan-out: sent=%d failed=%d pruned=%d", sent, failed, pruned)
    return {"sent": sent, "failed": failed, "pruned": pruned}
