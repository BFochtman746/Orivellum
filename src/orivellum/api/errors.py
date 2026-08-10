"""Shared error helpers for API routes.

Keeps the "generic message to user, full detail to logs" discipline in one
place.  Route handlers that catch an unexpected exception on a 500-class path
should use :func:`internal_error` so the traceback (plus a short reference id)
lands in the logs while the client only ever sees a generic message with that
reference id — never raw exception text (SQLite errors, filesystem paths,
provider URLs, …).

Deliberate 4xx validation messages are *not* the target of this helper and
should keep returning their explicit, user-facing detail.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException


def internal_error(
    logger: logging.Logger,
    exc: BaseException,
    context: str = "",
) -> HTTPException:
    """Log an unexpected exception and return a client-safe 500 HTTPException.

    Generates a short reference id, logs the full traceback under that id via
    ``logger.exception`` (so the id in the client response can be correlated
    with the log line), and returns an :class:`HTTPException` whose detail
    contains only the generic message + reference id — never ``str(exc)``.

    Usage::

        try:
            ...
        except Exception as exc:
            raise internal_error(logger, exc, "audiobook generation") from exc
    """
    ref = uuid.uuid4().hex[:8]
    logger.exception("[%s] %s", ref, context or "internal error")
    return HTTPException(status_code=500, detail=f"Internal error (ref {ref})")
