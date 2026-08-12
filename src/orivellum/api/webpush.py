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


_MAX_ENDPOINT_LEN = 1024
_MAX_KEY_LEN = 512


def validate_subscription(endpoint: str, p256dh: str, auth: str) -> str | None:
    """Return an error string when the subscription is not acceptable.

    The server performs outbound requests to the stored endpoint, so an
    unvalidated endpoint is an SSRF primitive: require HTTPS, no embedded
    credentials, and a host that resolves only to global addresses (Web Push
    provider endpoints are always public HTTPS URLs).  Resolution happens at
    subscribe time; a later re-resolve to a private address (DNS rebinding)
    would only reach pywebpush's HTTP client, which never returns response
    bodies to the subscriber.
    """
    from urllib.parse import urlsplit

    if len(endpoint) > _MAX_ENDPOINT_LEN:
        return "endpoint URL too long"
    if len(p256dh) > _MAX_KEY_LEN or len(auth) > _MAX_KEY_LEN:
        return "subscription keys too long"
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return "malformed endpoint URL"
    if parts.scheme != "https":
        return "Web Push endpoints must use https"
    if parts.username or parts.password:
        return "endpoint must not embed credentials"
    host = parts.hostname
    if not host:
        return "endpoint has no host"
    return _resolve_check(host, parts.port or 443)


def _resolve_check(host: str, port: int) -> str | None:
    """Resolve *host* and refuse anything that is not a global address."""
    import ipaddress
    import socket

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return "endpoint host does not resolve"
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return "endpoint resolves to an invalid address"
        if (
            addr.is_loopback
            or addr.is_private
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return "endpoint resolves to a private or local address"
    return None


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
