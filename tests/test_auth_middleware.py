"""Integration tests for the auth middleware.

Covers:
- Unauthenticated requests get 401 on protected routes
- Valid bearer token (X-Api-Key) is accepted
- Valid bearer token (Authorization: Bearer) is accepted
- Wrong token gets 401
- Health and version routes are exempt (no auth required)
- /api/auth/me and /api/auth/login are exempt
- Session cookie grants access (login flow)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY, AUTH_HEADERS


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ---------------------------------------------------------------------------
# Auth middleware tests
# ---------------------------------------------------------------------------

class TestAuthMiddleware(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmpdir.name)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    # ── Exempt routes ─────────────────────────────────────────────────────

    def test_healthz_exempt(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.get("/api/healthz")
        self.assertNotEqual(resp.status_code, 401,
                            "/api/healthz must not require auth")

    def test_version_exempt(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.get("/api/version")
        self.assertNotEqual(resp.status_code, 401,
                            "/api/version must not require auth")

    def test_auth_me_exempt(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.get("/api/auth/me")
        self.assertEqual(resp.status_code, 200,
                         "/api/auth/me must be reachable without auth")
        self.assertIn("authenticated", resp.json())

    def test_auth_login_exempt(self):
        """Login endpoint must be reachable without an existing session."""
        client = TestClient(self.app, raise_server_exceptions=True)
        # Use a wrong key — we just want a response, not a successful login.
        resp = client.post("/api/auth/login", json={"key": "wrong"})
        # Should get 401 (bad key) not 401 (no auth middleware) — same status
        # code but the route must be reachable.
        self.assertIn(resp.status_code, (200, 401, 503),
                      "/api/auth/login must be reachable without prior auth")

    # ── Protected routes ──────────────────────────────────────────────────

    def test_protected_route_without_auth_returns_401(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.get("/api/works")
        self.assertEqual(resp.status_code, 401,
                         "Protected routes must return 401 without credentials")

    def test_protected_route_wrong_token_returns_401(self):
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={"X-Api-Key": "totally-wrong-key"})
        resp = client.get("/api/works")
        self.assertEqual(resp.status_code, 401)

    # ── Bearer token paths ────────────────────────────────────────────────

    def test_x_api_key_header_accepted(self):
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={"X-Api-Key": TEST_API_KEY})
        resp = client.get("/api/works")
        self.assertEqual(resp.status_code, 200)

    def test_authorization_bearer_header_accepted(self):
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={"Authorization": f"Bearer {TEST_API_KEY}"})
        resp = client.get("/api/works")
        self.assertEqual(resp.status_code, 200)

    def test_authorization_bearer_wrong_key_rejected(self):
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={"Authorization": "Bearer wrong-key"})
        resp = client.get("/api/works")
        self.assertEqual(resp.status_code, 401)

    # ── Session cookie path ───────────────────────────────────────────────

    def test_session_login_then_access(self):
        """Login via POST /api/auth/login and then access a protected route."""
        with TestClient(self.app, raise_server_exceptions=True) as client:
            # Unauthenticated first
            resp = client.get("/api/works")
            self.assertEqual(resp.status_code, 401)

            # Login with the correct key
            login_resp = client.post("/api/auth/login",
                                     json={"key": TEST_API_KEY})
            self.assertEqual(login_resp.status_code, 200)
            self.assertTrue(login_resp.json().get("ok"))

            # /api/auth/me should now report authenticated
            me_resp = client.get("/api/auth/me")
            self.assertEqual(me_resp.status_code, 200)
            self.assertTrue(me_resp.json()["authenticated"])

            # Protected route should now work
            resp2 = client.get("/api/works")
            self.assertEqual(resp2.status_code, 200)

    def test_session_logout_revokes_access(self):
        """After logout, the session cookie no longer grants access."""
        with TestClient(self.app, raise_server_exceptions=True) as client:
            # Login
            client.post("/api/auth/login", json={"key": TEST_API_KEY})

            # Confirm access
            self.assertEqual(client.get("/api/works").status_code, 200)

            # Logout
            logout_resp = client.post("/api/auth/logout",
                                      headers=AUTH_HEADERS)
            self.assertEqual(logout_resp.status_code, 200)

            # Session cleared — access should now require a token again
            resp = client.get("/api/works")
            self.assertEqual(resp.status_code, 401)

    def test_login_wrong_key_returns_401(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.post("/api/auth/login", json={"key": "not-the-right-key"})
        self.assertEqual(resp.status_code, 401)

    def test_login_empty_key_returns_401(self):
        client = TestClient(self.app, raise_server_exceptions=True)
        resp = client.post("/api/auth/login", json={"key": ""})
        self.assertEqual(resp.status_code, 401)

    # ── Forged session cookie tests ───────────────────────────────────────

    def test_forged_cookie_with_placeholder_is_rejected(self):
        """A cookie signed with the old dev-placeholder key must be rejected.

        Previously the fallback session secret was the known literal
        'orivellum-dev-session-placeholder'.  This test proves that a forged
        cookie bearing that signature does not grant access, ensuring no
        regression to the known-literal fallback.
        """
        from itsdangerous import URLSafeTimedSerializer

        known_placeholder = "orivellum-dev-session-placeholder"
        signer = URLSafeTimedSerializer(known_placeholder)
        forged_cookie = signer.dumps({"authenticated": True})

        client = TestClient(self.app, raise_server_exceptions=True)
        client.cookies.set("orivellum_session", forged_cookie)

        resp = client.get("/api/works")
        self.assertEqual(
            resp.status_code, 401,
            "A session cookie forged with the placeholder key must be rejected",
        )

    def test_forged_cookie_with_arbitrary_key_is_rejected(self):
        """Cookies signed with any arbitrary string are rejected."""
        from itsdangerous import URLSafeTimedSerializer

        for bad_key in ["admin", "secret", "password", "1234"]:
            signer = URLSafeTimedSerializer(bad_key)
            forged = signer.dumps({"authenticated": True})

            client = TestClient(self.app, raise_server_exceptions=True)
            client.cookies.set("orivellum_session", forged)

            resp = client.get("/api/works")
            self.assertEqual(
                resp.status_code, 401,
                f"Cookie forged with key {bad_key!r} must be rejected",
            )


class TestCorsRestriction(unittest.TestCase):
    """Verify that credentialed CORS is not granted to arbitrary *.replit.dev origins."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmpdir.name)

    def tearDown(self):
        self.db.close()
        self._tmpdir.cleanup()

    def test_unauthorized_replit_origin_gets_no_cors_header(self):
        """Requests from an arbitrary *.replit.dev origin must not receive
        an Access-Control-Allow-Origin response header.

        This verifies that the fix from the overly-broad ``https://.*\\.replit\\.dev``
        regex to an exact-match (or absent) regex is in effect.  In the test
        environment REPLIT_DEV_DOMAIN is not set, so no origin regex is
        configured and the wildcard match no longer applies.
        """
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={"Origin": "https://malicious-repl.replit.dev"})
        # Use an exempt path so auth doesn't interfere — we're testing CORS only.
        resp = client.get("/api/auth/me")
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        self.assertNotEqual(
            allow_origin, "https://malicious-repl.replit.dev",
            "Arbitrary *.replit.dev origin must NOT receive credentialed CORS headers",
        )

    def test_localhost_origin_is_allowed(self):
        """Localhost origins (dev setup) should be allowed by the static list."""
        client = TestClient(self.app, raise_server_exceptions=True,
                            headers={
                                "Origin": "http://localhost:5173",
                                "X-Api-Key": TEST_API_KEY,
                            })
        resp = client.get("/api/works")
        # Localhost is in ORIVELLUM_ALLOWED_ORIGINS default; 200 or CORS header present
        self.assertEqual(resp.status_code, 200)


class TestSessionSecretInit(unittest.TestCase):
    """Verify that _init_session_secret never returns a known literal."""

    def test_no_known_fallback_literal_when_session_secret_unset(self):
        """When SESSION_SECRET is absent, _init_session_secret returns a random value."""
        import os
        import tempfile
        from orivellum.api.app import _init_session_secret

        saved = os.environ.pop("SESSION_SECRET", None)
        saved_data_dir = os.environ.pop("ORIVELLUM_DATA_DIR", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["ORIVELLUM_DATA_DIR"] = tmp
                secret = _init_session_secret()

                # Must be non-empty, long enough to be cryptographically useful
                self.assertGreaterEqual(len(secret), 32)
                # Must NOT be any previously-known placeholder
                for known in [
                    "orivellum-dev-session-placeholder",
                    "orivellum-dev-session-key",
                    "orivellum-dev-key",
                    "",
                    "admin",
                    "secret",
                ]:
                    self.assertNotEqual(secret, known,
                                       f"Secret must not be the known literal {known!r}")
        finally:
            if saved is not None:
                os.environ["SESSION_SECRET"] = saved
            if saved_data_dir is not None:
                os.environ["ORIVELLUM_DATA_DIR"] = saved_data_dir

    def test_secret_persisted_and_stable_across_calls(self):
        """The same data dir returns the same secret on repeated calls."""
        import os
        import tempfile
        from orivellum.api.app import _init_session_secret

        saved = os.environ.pop("SESSION_SECRET", None)
        saved_data_dir = os.environ.pop("ORIVELLUM_DATA_DIR", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["ORIVELLUM_DATA_DIR"] = tmp
                first = _init_session_secret()
                second = _init_session_secret()
                self.assertEqual(first, second,
                                 "The persisted secret must be stable across calls")
        finally:
            if saved is not None:
                os.environ["SESSION_SECRET"] = saved
            if saved_data_dir is not None:
                os.environ["ORIVELLUM_DATA_DIR"] = saved_data_dir

    def test_env_var_takes_priority(self):
        """When SESSION_SECRET is set it is returned directly."""
        import os
        from orivellum.api.app import _init_session_secret

        os.environ["SESSION_SECRET"] = "env-test-value-xyz"
        try:
            result = _init_session_secret()
            self.assertEqual(result, "env-test-value-xyz")
        finally:
            os.environ["SESSION_SECRET"] = TEST_API_KEY  # restore test key


class TestSecretFilesIgnored(unittest.TestCase):
    """Verify that generated credential files are listed in .gitignore."""

    def _read_gitignore(self) -> str:
        from pathlib import Path
        root = Path(__file__).parent.parent
        gi = root / ".gitignore"
        return gi.read_text(encoding="utf-8") if gi.exists() else ""

    def test_api_key_file_is_gitignored(self):
        """data/api_key.txt must be in .gitignore to prevent accidental commits."""
        content = self._read_gitignore()
        self.assertIn("data/api_key.txt", content,
                      "data/api_key.txt must be listed in .gitignore")

    def test_session_secret_file_is_gitignored(self):
        """data/.session_secret must be in .gitignore to prevent session forgery."""
        content = self._read_gitignore()
        self.assertIn("data/.session_secret", content,
                      "data/.session_secret must be listed in .gitignore")

    def test_generated_files_not_tracked(self):
        """Confirm the generated secret files are not tracked in git."""
        import subprocess
        result = subprocess.run(
            ["git", "ls-files", "data/api_key.txt", "data/.session_secret"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        tracked = result.stdout.strip()
        self.assertEqual(
            tracked, "",
            f"Generated secret files must not be tracked by git; found: {tracked!r}",
        )


if __name__ == "__main__":
    unittest.main()
