"""Encrypted token vault for the A-01 Mail Steward.

Tokens (access + refresh) are encrypted at rest using Fernet symmetric
encryption.  The encryption key is derived from SESSION_SECRET so that
tokens are unreadable without the session secret.

Never log, audit-detail, or back up decrypted tokens.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from orivellum.capabilities.mail.models import MailStewardError

_SETTINGS_KEY = "mail_steward.token"


def _derive_fernet_key() -> bytes:
    """Derive a 32-byte Fernet key from SESSION_SECRET."""
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise MailStewardError("SESSION_SECRET is not set; cannot protect mail tokens")
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_token(token_data: dict[str, Any]) -> str:
    """Serialize and encrypt token_data dict; return base64-encoded ciphertext."""
    f = Fernet(_derive_fernet_key())
    plaintext = json.dumps(token_data).encode()
    return f.encrypt(plaintext).decode()


def decrypt_token(ciphertext: str) -> dict[str, Any]:
    """Decrypt and deserialize a stored token blob."""
    f = Fernet(_derive_fernet_key())
    try:
        plaintext = f.decrypt(ciphertext.encode())
    except InvalidToken as exc:
        raise MailStewardError("Mail token decryption failed — token may be corrupt") from exc
    return json.loads(plaintext)


def encrypt_str(value: str) -> str:
    """Encrypt a single string (used for Graph IDs stored in DB)."""
    if not value:
        return ""
    f = Fernet(_derive_fernet_key())
    return f.encrypt(value.encode()).decode()


def decrypt_str(ciphertext: str) -> str:
    """Decrypt a single encrypted string."""
    if not ciphertext:
        return ""
    f = Fernet(_derive_fernet_key())
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""


def store_token(db: Any, token_data: dict[str, Any]) -> None:
    """Encrypt and persist token_data to the settings table."""
    encrypted = encrypt_token(token_data)
    # Unaudited so the ciphertext never appears in the audit log, but
    # committed — a bare _set_setting is not durable (no commit).
    db.set_setting_unaudited(_SETTINGS_KEY, encrypted)


def load_token(db: Any) -> dict[str, Any] | None:
    """Load and decrypt the stored token, or return None if not present."""
    raw = db.get_setting(_SETTINGS_KEY, "")
    if not raw:
        return None
    try:
        return decrypt_token(raw)
    except MailStewardError:
        return None


def delete_token(db: Any) -> None:
    """Wipe the stored token on disconnect."""
    db.set_setting_unaudited(_SETTINGS_KEY, "")
