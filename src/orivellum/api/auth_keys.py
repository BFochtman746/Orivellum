"""Login-credential resolution and constant-time comparison.

Security decisions (Aug 2026 hardening):

1. **Constant-time comparison** — every credential check goes through
   :func:`key_matches`, which uses :func:`secrets.compare_digest` so response
   timing leaks nothing about how many leading characters matched.

2. **Dedicated login key** — ``SESSION_SECRET`` is the cookie-*signing* secret
   and should never double as the login credential (anyone who learns the
   login key could otherwise forge session cookies too).  Resolution order:

   1. ``ORIVELLUM_LOGIN_KEY`` env var         (recommended)
   2. ``login_key`` DB setting                (set via the System page / CLI)
   3. ``SESSION_SECRET`` env var              (DEPRECATED fallback — keeps
      existing installs working; logs a warning once per process)
   4. ``api_key`` DB setting                  (auto-generated at startup)

   Existing installs that log in with the ``SESSION_SECRET`` value keep
   working unchanged; setting ``ORIVELLUM_LOGIN_KEY`` (or the ``login_key``
   DB setting) migrates them off the shared secret with zero downtime.
"""
from __future__ import annotations

import logging
import os
import secrets

logger = logging.getLogger(__name__)

_deprecation_warned = False


def key_matches(provided: str, expected: str) -> bool:
    """Constant-time equality check; False when either side is empty."""
    if not provided or not expected:
        return False
    return secrets.compare_digest(provided.encode("utf-8"),
                                  expected.encode("utf-8"))


def resolve_login_key() -> str:
    """Return the credential a client must present to authenticate, or ''.

    Side effects: logs a one-time deprecation warning when the resolved key
    is the ``SESSION_SECRET`` fallback.  Raises nothing — a missing DB simply
    falls through to the next source (callers treat '' as "not configured").
    """
    global _deprecation_warned

    key = os.environ.get("ORIVELLUM_LOGIN_KEY", "")
    if key:
        return key

    db_key = _db_setting("login_key")
    if db_key:
        return db_key

    key = os.environ.get("SESSION_SECRET", "")
    if key:
        if not _deprecation_warned:
            _deprecation_warned = True
            logger.warning(
                "SESSION_SECRET is being used as the login credential. This is "
                "deprecated: SESSION_SECRET signs session cookies and should not "
                "double as a login key. Set ORIVELLUM_LOGIN_KEY (env) or the "
                "'login_key' DB setting to migrate — existing logins keep working "
                "until then."
            )
        return key

    return _db_setting("api_key")


def _db_setting(name: str) -> str:
    """Read a settings row, returning '' when the DB is not ready."""
    try:
        from orivellum.api import _deps
        db = _deps.get_db()
        return db.get_setting(name, "") or ""
    except Exception:
        return ""
