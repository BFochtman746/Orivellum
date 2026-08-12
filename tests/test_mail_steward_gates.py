"""Security floor tests for the Mail Steward action facade.

Covers the paths where a defect is an exposure:
  * token freshness — expired token without a refresh token means NOT
    connected; a valid refresh path stores the new token encrypted
  * send_draft refuses BEFORE any Graph call when send is disabled
  * nonces are single-use and bound to (record, action)
  * create_draft refuses unassessed or unknown messages

No network — GraphClient and the OAuth refresh call are monkeypatched.
"""

from __future__ import annotations

import time

import pytest

from orivellum.capabilities.mail import steward, token_vault
from orivellum.capabilities.mail.models import ACTION_CREATE_DRAFT, ACTION_SEND, MailStewardError


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


class _FakeGraphClient:
    def __init__(self, access_token):
        self.access_token = access_token


@pytest.fixture()
def _fake_graph(monkeypatch):
    from orivellum.capabilities.mail import graph_client

    monkeypatch.setattr(graph_client, "GraphClient", _FakeGraphClient)
    yield


# ── Token freshness ───────────────────────────────────────────────────────────


def test_no_token_means_no_client(tmp_path):
    db = _make_db(tmp_path)
    assert steward._get_fresh_client(db) is None


def test_live_token_used_directly(tmp_path, _fake_graph):
    db = _make_db(tmp_path)
    token_vault.store_token(
        db, {"access_token": "live-at", "expires_at": time.time() + 3600}
    )
    client = steward._get_fresh_client(db)
    assert client is not None
    assert client.access_token == "live-at"


def test_expired_token_without_refresh_is_not_connected(tmp_path, _fake_graph):
    db = _make_db(tmp_path)
    token_vault.store_token(db, {"access_token": "old", "expires_at": time.time() - 10})
    assert steward._get_fresh_client(db) is None


def test_expired_token_refreshes_and_stores_encrypted(tmp_path, _fake_graph, monkeypatch):
    from orivellum.capabilities.mail import oauth

    db = _make_db(tmp_path)
    token_vault.store_token(
        db, {"access_token": "old", "refresh_token": "rt-1", "expires_at": time.time() - 10}
    )
    monkeypatch.setattr(
        oauth,
        "refresh_access_token",
        lambda rt: {"access_token": "fresh-at", "refresh_token": "rt-2", "expires_in": 3600},
    )
    client = steward._get_fresh_client(db)
    assert client is not None
    assert client.access_token == "fresh-at"
    stored = token_vault.load_token(db)
    assert stored["access_token"] == "fresh-at"
    assert stored["expires_at"] > time.time()
    assert "fresh-at" not in db.get_setting("mail_steward.token", "")


def test_failed_refresh_degrades_to_not_connected(tmp_path, _fake_graph, monkeypatch):
    from orivellum.capabilities.mail import oauth

    def _boom(rt):
        raise MailStewardError("refresh denied")

    db = _make_db(tmp_path)
    token_vault.store_token(
        db, {"access_token": "old", "refresh_token": "rt-1", "expires_at": time.time() - 10}
    )
    monkeypatch.setattr(oauth, "refresh_access_token", _boom)
    assert steward._get_fresh_client(db) is None


# ── Send gate ordering ────────────────────────────────────────────────────────


def test_send_draft_refuses_when_send_disabled_before_anything_else(tmp_path):
    """Policy is consulted first — no store lookup, nonce burn, or Graph call."""
    db = _make_db(tmp_path)
    with pytest.raises(MailStewardError, match="[Ss]end is disabled"):
        steward.send_draft(db, "nonexistent-action", "nonce")


def test_send_draft_with_send_enabled_still_requires_real_action(tmp_path):
    db = _make_db(tmp_path)
    db.set_setting_unaudited("mail_steward.send_enabled", "true")
    with pytest.raises(MailStewardError, match="not found"):
        steward.send_draft(db, "nonexistent-action", "nonce")


# ── Nonces ────────────────────────────────────────────────────────────────────


def _seed_mail_record(store) -> str:
    """Insert a minimal mail record and return its id (nonces are FK-bound)."""
    assert store.upsert_mail_record(
        {
            "id": "graph-msg-1",
            "subject": "Test",
            "receivedDateTime": "2026-08-12T00:00:00Z",
            "from": {"emailAddress": {"address": "a@example.com", "name": "A"}},
        },
        folder_id="inbox",
    )
    row = store._read().execute("SELECT id FROM mail_records LIMIT 1").fetchone()
    return row["id"]


def test_nonce_is_single_use_and_bound(tmp_path):
    from orivellum.database.mail_store import MailStore

    db = _make_db(tmp_path)
    store = MailStore(db)
    rec_id = _seed_mail_record(store)
    nonce = store.issue_nonce(rec_id, ACTION_CREATE_DRAFT)

    # Wrong record and wrong action must not consume it.
    assert not store.consume_nonce(nonce, "rec-OTHER", ACTION_CREATE_DRAFT)
    assert not store.consume_nonce(nonce, rec_id, ACTION_SEND)
    # Correct use consumes it exactly once.
    assert store.consume_nonce(nonce, rec_id, ACTION_CREATE_DRAFT)
    assert not store.consume_nonce(nonce, rec_id, ACTION_CREATE_DRAFT)


# ── Draft prerequisites ───────────────────────────────────────────────────────


def test_create_draft_refuses_unknown_record(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(MailStewardError, match="not found"):
        steward.create_draft(db, "missing-record", "some-nonce")


# ── Persistence of steward state writes ──────────────────────────────────────


def test_sync_mail_disconnect_is_durable_across_reopen(tmp_path):
    """A failed token path must mark disconnected VISIBLY and DURABLY —
    otherwise the system keeps reporting connected and retries forever."""
    from orivellum.database.db import OrivellumDB

    path = str(tmp_path / "test.db")
    db = OrivellumDB(path)
    db.set_setting_unaudited("mail_steward.connected", "true")

    out = steward.sync_mail(db, cfg=None)  # no token stored → client is None
    assert out == {"error": "token_unavailable"}
    assert db.get_setting("mail_steward.connected", "") == "false"
    db.close()

    db2 = OrivellumDB(path)
    assert db2.get_setting("mail_steward.connected", "") == "false", (
        "disconnect flag must survive a restart (committed, not left in an "
        "open transaction)"
    )
    db2.close()


class _FolderClient:
    def __init__(self, existing=None):
        self._existing = existing or []
        self.created = []

    def list_mail_folders(self):
        return self._existing

    def create_mail_folder(self, name):
        self.created.append(name)
        return {"id": "fid-created", "displayName": name}


def test_review_folder_id_persists_across_reopen(tmp_path):
    from orivellum.database.db import OrivellumDB

    path = str(tmp_path / "test.db")
    db = OrivellumDB(path)
    client = _FolderClient(existing=[{"id": "fid-existing", "displayName": "A-01 Review"}])
    assert steward._ensure_review_folder(client, db) == "fid-existing"
    db.close()

    db2 = OrivellumDB(path)
    assert db2.get_setting("mail_steward.review_folder_id", "") == "fid-existing", (
        "resolved folder id must be committed or every sync re-lists folders"
    )
    # Cached path: no Graph calls needed at all on a warm DB.
    assert steward._ensure_review_folder(None, db2) == "fid-existing"
    db2.close()


def test_review_folder_created_when_missing_and_persisted(tmp_path):
    from orivellum.database.db import OrivellumDB

    path = str(tmp_path / "test.db")
    db = OrivellumDB(path)
    client = _FolderClient(existing=[{"id": "x", "displayName": "Inbox"}])
    assert steward._ensure_review_folder(client, db) == "fid-created"
    assert client.created == ["A-01 Review"]
    db.close()

    db2 = OrivellumDB(path)
    assert db2.get_setting("mail_steward.review_folder_id", "") == "fid-created"
    db2.close()
