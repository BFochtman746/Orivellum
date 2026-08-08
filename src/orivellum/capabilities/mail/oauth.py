"""Microsoft device-code OAuth flow for the A-01 Mail Steward.

Uses the OAuth 2.0 device authorization grant so the server never handles
the user's Microsoft password.  The user signs in on their phone or browser
while A-01 polls for the token.

Ref: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from orivellum.capabilities.mail.models import MailStewardError

logger = logging.getLogger("orivellum.mail.oauth")

_DEVICE_CODE_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
_TOKEN_URL       = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_TIMEOUT         = 15  # seconds for individual HTTP calls


def _client_id() -> str:
    cid = os.environ.get("MAIL_CLIENT_ID", "")
    if not cid:
        raise MailStewardError("MAIL_CLIENT_ID environment variable is not set")
    return cid


def _tenant() -> str:
    return os.environ.get("MAIL_TENANT", "consumers")


def _scopes(include_send: bool = False) -> str:
    base = "openid profile offline_access User.Read Mail.ReadWrite"
    if include_send:
        base += " Mail.Send"
    return base


def request_device_code(include_send: bool = False) -> dict[str, Any]:
    """Request a device code from Microsoft.

    Returns the full Microsoft response:
      device_code, user_code, verification_uri, expires_in, interval, message

    The device_code must NOT be returned to the browser — keep it server-side.
    """
    url = _DEVICE_CODE_URL.format(tenant=_tenant())
    scopes = _scopes(include_send)
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, data={"client_id": _client_id(), "scope": scopes})
    if resp.status_code != 200:
        raise MailStewardError(f"Device code request failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    if "error" in data:
        raise MailStewardError(f"Device code error: {data.get('error_description', data['error'])}")
    return data


def poll_for_token(device_code: str, interval: int = 5, max_wait: int = 300) -> dict[str, Any]:
    """Poll Microsoft token endpoint until the user completes sign-in.

    Returns the token response dict on success.
    Raises MailStewardError on denial, expiry, or timeout.
    """
    url = _TOKEN_URL.format(tenant=_tenant())
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        time.sleep(interval)
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(url, data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": _client_id(),
                "device_code": device_code,
            })
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            logger.info("mail.oauth: token acquired successfully")
            return data
        error = data.get("error", "")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval = min(interval + 5, 30)
            continue
        if error in ("authorization_declined", "expired_token", "access_denied"):
            raise MailStewardError(f"Authorization denied: {data.get('error_description', error)}")
        # Unexpected error
        raise MailStewardError(f"Token poll error: {data.get('error_description', error)}")
    raise MailStewardError("Device-code authorization timed out")


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a fresh access token.

    Returns the new token response dict.
    """
    url = _TOKEN_URL.format(tenant=_tenant())
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, data={
            "grant_type": "refresh_token",
            "client_id": _client_id(),
            "refresh_token": refresh_token,
            "scope": _scopes(),
        })
    if resp.status_code != 200:
        raise MailStewardError(f"Token refresh failed: {resp.status_code} {resp.text[:200]}")
    data = resp.json()
    if "error" in data:
        raise MailStewardError(f"Token refresh error: {data.get('error_description', data['error'])}")
    return data
