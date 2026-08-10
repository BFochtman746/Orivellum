"""Shared pytest fixtures and constants for the Orivellum test suite.

Sets up a fixed test API key via SESSION_SECRET so that the auth middleware
accepts requests in all tests without requiring a real DB or a live server.

Usage in test files:
    from tests.conftest import AUTH_HEADERS
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
"""

from __future__ import annotations

import os

# Configure the test API key BEFORE any test module imports the FastAPI app.
# The auth middleware reads SESSION_SECRET at request time, so setting it here
# (conftest.py runs before any test collection) is sufficient.
TEST_API_KEY = "test-orivellum-api-key-1234567890abcdef"  # ≥32 chars required
os.environ["SESSION_SECRET"] = TEST_API_KEY

# Convenience header dict — pass to TestClient(..., headers=AUTH_HEADERS)
AUTH_HEADERS: dict[str, str] = {"X-Api-Key": TEST_API_KEY}
