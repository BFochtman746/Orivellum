---
name: API auth design
description: Session-cookie auth for web; SecureStore bearer token for mobile. No secret ever in a client bundle.
---

# API auth design

## Core rule
All API routes require auth except `OPTIONS`, `/api/healthz`, `/api/version`, `/api/auth/login`, `/api/auth/me`.

## Web: session cookies
User enters key once in a login form → `POST /api/auth/login` → server sets a signed HttpOnly cookie (`orivellum_session`) → browser sends it automatically on every same-origin request. No secret ever in the JS bundle.

## Mobile: SecureStore bearer token
User enters key once in the mobile login screen → validated via `POST /api/auth/login` → key saved to `expo-secure-store` (device Keychain/Keystore) → loaded at startup via `loadToken()` → sent as `Authorization: Bearer` on every request. Key never in the app bundle or AsyncStorage.

## API clients: bearer token
`X-Api-Key: <key>` or `Authorization: Bearer <key>` header.

## Key resolution (middleware + `_init_session_secret`)
**Bearer credential** (middleware in `app.py`): `SESSION_SECRET` env var → `db.get_setting("api_key")` → generated at startup.  
**Session-signing secret** (`_init_session_secret()`): `SESSION_SECRET` env var → `data/.session_secret` file → `secrets.token_hex(32)`. NEVER a known literal.

## SessionMiddleware ordering — critical
Must be added LAST in `create_app()` so it wraps outermost. Request flow: session → body_limit → rate_limit → auth → CORS → routes. Moving it earlier breaks this. Requires `itsdangerous>=2.1` in pyproject.toml and `from pathlib import Path` in app.py.

## CORS
`allow_origin_regex` set to exact `https://<REPLIT_DEV_DOMAIN>` (from env var), NOT `https://.*\.replit\.dev`. This prevents arbitrary `*.replit.dev` origins from making credentialed requests with the user's session cookie. Also needs `import re` in app.py.

## Tests
- `tests/__init__.py` + `tests/conftest.py` (sets `SESSION_SECRET=test-key`, exports `AUTH_HEADERS`)
- All TestClient calls pass `headers=AUTH_HEADERS`
- `tests/test_auth_middleware.py`: 401 without auth, bearer acceptance, session login/logout flow, forged cookie rejection, `_init_session_secret` never-known-literal, CORS restriction
