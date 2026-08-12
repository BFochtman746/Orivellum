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

# SSRF defense: the server makes outbound HTTPS requests to stored endpoints,
# so only hosts operated by real browser push services are accepted.  Browsers
# only ever hand out subscription endpoints from these providers, and an
# attacker cannot control their DNS — which removes the rebinding surface
# entirely (no DNS check of our own to race against).  This is deliberately an
# allowlist, NOT a resolve-and-check: a preliminary or delivery-time DNS
# lookup is always a TOCTOU race against the resolver used by the HTTP client.
_ALLOWED_PUSH_HOSTS = frozenset(
    {
        "fcm.googleapis.com",  # Chrome / Chromium
        "updates.push.services.mozilla.com",  # Firefox
        "web.push.apple.com",  # Safari / iOS PWA
    }
)
_ALLOWED_PUSH_HOST_SUFFIXES = (
    ".push.apple.com",  # Apple regional endpoints
    ".push.services.mozilla.com",  # Mozilla autopush shards
    ".notify.windows.com",  # Edge (WNS)
    ".fcm.googleapis.com",  # FCM regional endpoints
)


def _host_allowed(host: str) -> bool:
    host = host.lower().rstrip(".")
    return host in _ALLOWED_PUSH_HOSTS or host.endswith(_ALLOWED_PUSH_HOST_SUFFIXES)


def validate_subscription(endpoint: str, p256dh: str, auth: str) -> str | None:
    """Return an error string when the subscription is not acceptable.

    Requirements: HTTPS, default port, no embedded credentials, size caps,
    and a hostname on the trusted Web Push provider allowlist (see above).
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
    if parts.port not in (None, 443):
        return "endpoint must use the default https port"
    host = parts.hostname
    if not host:
        return "endpoint has no host"
    if not _host_allowed(host):
        return "endpoint host is not a recognized Web Push provider"
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
        # Re-check the allowlist at DELIVERY time too: prunes rows saved
        # before the allowlist existed (or edited out-of-band) so no request
        # ever leaves for a non-provider host.  Pure string check — no DNS.
        err = validate_subscription(sub["endpoint"], sub["p256dh"], sub["auth"])
        if err:
            logger.warning(
                "Pruning push subscription failing delivery-time validation (%s)", err
            )
            db.delete_push_subscription(sub["endpoint"])
            pruned += 1
            continue
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
