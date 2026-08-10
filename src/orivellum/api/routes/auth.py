"""Auth endpoints for the Orivellum API.

Design: single-user session-cookie auth.

  POST /api/auth/login   — Validate the API key → set authenticated session
  GET  /api/auth/me      — Return {"authenticated": bool} (always exempt from auth)
  POST /api/auth/logout  — Clear the session

The session cookie is HttpOnly and signed with SESSION_SECRET via Starlette's
SessionMiddleware.  The key itself is never transmitted to the client —
the user enters it once in the login form and the session cookie handles
all subsequent authentication transparently.

API / mobile clients that cannot use cookies may still supply a bearer token
directly (the auth middleware in app.py checks both paths).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orivellum.api.auth_keys import key_matches, resolve_login_key

router = APIRouter()


@router.post("/api/auth/login")
async def login(request: Request):
    """Validate the API key and create an authenticated session.

    Body: ``{"key": "<api-key>"}``
    Response 200: ``{"ok": true}`` + sets a signed session cookie.
    Response 401: ``{"detail": "Invalid key"}``
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"detail": "Invalid request body"}, status_code=400)

    provided_key: str = body.get("key", "")
    expected_key = resolve_login_key()

    if not expected_key:
        return JSONResponse(
            {"detail": "Service unavailable — API key not configured yet"}, status_code=503
        )

    # Constant-time comparison — timing leaks nothing about partial matches.
    if not key_matches(provided_key, expected_key):
        return JSONResponse({"detail": "Invalid key"}, status_code=401)

    request.session["authenticated"] = True
    return {"ok": True}


@router.get("/api/auth/me")
async def me(request: Request):
    """Return the current auth status.  Always exempt from the auth middleware.

    Response: ``{"authenticated": bool}``
    """
    return {"authenticated": bool(request.session.get("authenticated"))}


@router.post("/api/auth/logout")
async def logout(request: Request):
    """Clear the authenticated session."""
    request.session.clear()
    return {"ok": True}
