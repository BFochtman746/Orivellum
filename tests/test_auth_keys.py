"""Tests for login-credential resolution and constant-time comparison.

auth_keys is the only credential path (repo hygiene rule) and sits under the
security floor.  Pins:

  * key_matches — constant-time semantics aside, correct truth table
    including non-string and empty inputs (reject, don't 500)
  * resolve_login_key — resolution order ORIVELLUM_LOGIN_KEY env >
    login_key DB setting > SESSION_SECRET env (deprecated) > api_key DB
  * missing/broken DB falls through instead of raising
"""

from __future__ import annotations

import pytest

from orivellum.api import auth_keys

# ── key_matches ───────────────────────────────────────────────────────────────


def test_key_matches_truth_table():
    assert auth_keys.key_matches("secret", "secret")
    assert not auth_keys.key_matches("secret", "other")
    assert not auth_keys.key_matches("", "secret")
    assert not auth_keys.key_matches("secret", "")
    assert not auth_keys.key_matches("", "")


def test_key_matches_rejects_non_strings_instead_of_raising():
    assert not auth_keys.key_matches(123, "secret")  # type: ignore[arg-type]
    assert not auth_keys.key_matches("secret", None)  # type: ignore[arg-type]
    assert not auth_keys.key_matches(["secret"], "secret")  # type: ignore[arg-type]


# ── resolve_login_key resolution order ────────────────────────────────────────


@pytest.fixture()
def _clean_env(monkeypatch):
    monkeypatch.delenv("ORIVELLUM_LOGIN_KEY", raising=False)
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    yield monkeypatch


def _stub_db(monkeypatch, settings: dict):
    monkeypatch.setattr(auth_keys, "_db_setting", lambda name: settings.get(name, ""))


def test_env_login_key_wins_over_everything(_clean_env):
    _clean_env.setenv("ORIVELLUM_LOGIN_KEY", "env-key")
    _clean_env.setenv("SESSION_SECRET", "session-secret")
    _stub_db(_clean_env, {"login_key": "db-key", "api_key": "api-key"})
    assert auth_keys.resolve_login_key() == "env-key"


def test_db_login_key_beats_session_secret(_clean_env):
    _clean_env.setenv("SESSION_SECRET", "session-secret")
    _stub_db(_clean_env, {"login_key": "db-key", "api_key": "api-key"})
    assert auth_keys.resolve_login_key() == "db-key"


def test_session_secret_is_the_deprecated_fallback(_clean_env):
    _clean_env.setenv("SESSION_SECRET", "session-secret")
    _stub_db(_clean_env, {"api_key": "api-key"})
    assert auth_keys.resolve_login_key() == "session-secret"


def test_api_key_is_the_last_resort(_clean_env):
    _stub_db(_clean_env, {"api_key": "api-key"})
    assert auth_keys.resolve_login_key() == "api-key"


def test_nothing_configured_returns_empty(_clean_env):
    _stub_db(_clean_env, {})
    assert auth_keys.resolve_login_key() == ""


def test_broken_db_falls_through_not_raises(_clean_env):
    """A DB error must not break login when an env fallback exists —
    exercises the real _db_setting catch, not a stub."""
    from orivellum.api import _deps

    def _boom():
        raise RuntimeError("db not ready")

    _clean_env.setenv("SESSION_SECRET", "session-secret")
    _clean_env.setattr(_deps, "get_db", _boom)
    assert auth_keys.resolve_login_key() == "session-secret"
