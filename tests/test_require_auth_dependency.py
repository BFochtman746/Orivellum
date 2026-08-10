"""FA-10 — router-level require_auth dependency (defense in depth).

These tests prove the second authorization layer works even if the global
auth middleware were bypassed by a mounting/path-normalization regression.
They call ``require_auth`` directly (so no middleware runs at all) and also
exercise a dependency-protected route with an isolated app that has the auth
middleware deliberately removed.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from tests.conftest import AUTH_HEADERS, TEST_API_KEY


def _init_deps(tmp: str):
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "orivellum.db"))
    _deps.init(db=db, cfg=cfg)
    return db, cfg


class RequireAuthDependencyTest(unittest.TestCase):
    """require_auth is enforced independently of the global middleware."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _init_deps(self._tmp.name)

        from orivellum.api._deps import require_auth

        # A minimal app with NO auth middleware — only the router-level
        # dependency guards the route. This simulates the middleware being
        # bypassed/regressed while the second layer still holds.
        app = FastAPI()
        # SessionMiddleware must be present so require_auth can read the
        # session cookie source (mirrors the real app stack).
        app.add_middleware(SessionMiddleware, secret_key="test-secret")

        @app.get("/guarded", dependencies=[Depends(require_auth)])
        def guarded():
            return {"ok": True}

        self.client = TestClient(app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_credentials_gets_401(self):
        r = self.client.get("/guarded")
        self.assertEqual(r.status_code, 401)

    def test_wrong_api_key_gets_401(self):
        r = self.client.get("/guarded", headers={"X-Api-Key": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_valid_api_key_passes(self):
        r = self.client.get("/guarded", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)

    def test_valid_bearer_passes(self):
        r = self.client.get(
            "/guarded", headers={"Authorization": f"Bearer {TEST_API_KEY}"}
        )
        self.assertEqual(r.status_code, 200)


class RequireAuthUnitTest(unittest.TestCase):
    """Call require_auth directly with a stub request (no middleware at all)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _init_deps(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _stub_request(self, headers: dict[str, str]):
        class _Req:
            def __init__(self, hdrs):
                self.headers = hdrs

            @property
            def session(self):
                # No session middleware ran — mimic Starlette's assertion.
                raise AssertionError("SessionMiddleware must be installed")

        return _Req(headers)

    def test_missing_credentials_raises_401(self):
        from orivellum.api._deps import require_auth

        with self.assertRaises(HTTPException) as ctx:
            require_auth(self._stub_request({}))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_valid_key_no_raise(self):
        from orivellum.api._deps import require_auth

        # Should not raise.
        require_auth(self._stub_request({"x-api-key": TEST_API_KEY}))


if __name__ == "__main__":
    unittest.main()
