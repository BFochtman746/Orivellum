"""Security floor tests for the mail token vault.

The vault holds OAuth credentials — the single highest-consequence store in
the platform.  These tests pin the guarantees the module claims:

  * round-trip fidelity for token dicts and single strings
  * NO plaintext at rest — the persisted setting must never contain the
    token material, and the write must bypass the audited settings path
  * key rotation / corruption never yields silently wrong data: dict
    decryption raises, load_token degrades to None (re-auth), never garbage
  * a missing SESSION_SECRET refuses to operate rather than encrypting
    under a guessable key
"""

from __future__ import annotations

import pytest

from orivellum.capabilities.mail import token_vault
from orivellum.capabilities.mail.models import MailStewardError


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


TOKEN = {
    "access_token": "SECRET-ACCESS-abc123",
    "refresh_token": "SECRET-REFRESH-def456",
    "expires_at": 1900000000.0,
}


# ── Round trips ───────────────────────────────────────────────────────────────


def test_token_round_trip():
    blob = token_vault.encrypt_token(TOKEN)
    assert token_vault.decrypt_token(blob) == TOKEN


def test_str_round_trip_and_empty_passthrough():
    assert token_vault.decrypt_str(token_vault.encrypt_str("graph-id-42")) == "graph-id-42"
    assert token_vault.encrypt_str("") == ""
    assert token_vault.decrypt_str("") == ""


def test_encryption_is_not_deterministic_marker():
    # Fernet includes a random IV — two encryptions of the same plaintext must
    # differ, otherwise ciphertext equality would leak token equality.
    assert token_vault.encrypt_str("same") != token_vault.encrypt_str("same")


# ── No plaintext at rest ──────────────────────────────────────────────────────


def test_store_token_persists_no_plaintext(tmp_path):
    db = _make_db(tmp_path)
    token_vault.store_token(db, TOKEN)
    raw = db.get_setting("mail_steward.token", "")
    assert raw, "token must be persisted"
    assert "SECRET-ACCESS-abc123" not in raw
    assert "SECRET-REFRESH-def456" not in raw
    assert "access_token" not in raw  # even structure must not leak
    assert token_vault.load_token(db) == TOKEN


def test_ciphertext_never_reaches_audit_log(tmp_path):
    db = _make_db(tmp_path)
    token_vault.store_token(db, TOKEN)
    with db._lock:
        rows = db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%audit%'"
        ).fetchall()
        for (table,) in [tuple(r) for r in rows]:
            dump = db._conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            text = " ".join(str(tuple(r)) for r in dump)
            assert "SECRET-ACCESS-abc123" not in text
            assert "mail_steward.token" not in text, (
                f"token setting write leaked into audit table {table}"
            )


def test_delete_token_wipes(tmp_path):
    db = _make_db(tmp_path)
    token_vault.store_token(db, TOKEN)
    token_vault.delete_token(db)
    assert token_vault.load_token(db) is None
    assert db.get_setting("mail_steward.token", "") == ""


# ── Corruption & key rotation ─────────────────────────────────────────────────


def test_corrupt_blob_raises_for_dict_and_empty_for_str():
    with pytest.raises(MailStewardError):
        token_vault.decrypt_token("not-a-fernet-blob")
    assert token_vault.decrypt_str("not-a-fernet-blob") == ""


def test_key_rotation_degrades_to_reauth_not_garbage(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    token_vault.store_token(db, TOKEN)

    monkeypatch.setenv("SESSION_SECRET", "a-completely-different-secret")
    # Direct decryption must refuse loudly…
    with pytest.raises(MailStewardError):
        token_vault.decrypt_token(db.get_setting("mail_steward.token", ""))
    # …and the loader must degrade to "not connected" (None), never junk.
    assert token_vault.load_token(db) is None


def test_missing_session_secret_refuses_to_encrypt(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "")
    with pytest.raises(MailStewardError):
        token_vault.encrypt_token(TOKEN)


# ── set_setting_unaudited transaction discipline ─────────────────────────────


def test_unaudited_write_is_durable_across_reopen(tmp_path):
    from orivellum.database.db import OrivellumDB

    path = str(tmp_path / "t.db")
    db = OrivellumDB(path)
    db.set_setting_unaudited("plumbing_key", "v1")
    assert db.get_setting("plumbing_key", "") == "v1"
    db.close()
    db2 = OrivellumDB(path)
    assert db2.get_setting("plumbing_key", "") == "v1", (
        "unaudited setting writes must be committed, not left in an open txn"
    )
    db2.close()


def test_unaudited_write_inside_atomic_rolls_back_with_the_block(tmp_path):
    from orivellum.database.db import OrivellumDB

    db = OrivellumDB(str(tmp_path / "t.db"))
    with pytest.raises(RuntimeError):
        with db.atomic():
            db.set_setting_unaudited("plumbing_key", "should-roll-back")
            raise RuntimeError("boom")
    assert db.get_setting("plumbing_key", "") == "", (
        "inside atomic(), set_setting_unaudited must defer to the outer "
        "transaction so a later exception rolls it back"
    )
    db.close()


def test_unaudited_write_inside_atomic_commits_with_the_block(tmp_path):
    from orivellum.database.db import OrivellumDB

    db = OrivellumDB(str(tmp_path / "t.db"))
    with db.atomic():
        db.set_setting_unaudited("plumbing_key", "v2")
    assert db.get_setting("plumbing_key", "") == "v2"
    db.close()
