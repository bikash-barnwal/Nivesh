"""Users' own model keys, encrypted at rest.

AES-256-GCM under NIVESH_KEYS_MASTER_KEY (lives only in the server's env file). The user id and provider are bound
into each ciphertext as associated data, so a row copied to another user or provider no longer decrypts. Keys are
write-only: nothing here returns a key except `decrypt_key`, which only the Copilot calls, per request.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from nivesh import appdb
from nivesh.config import settings

KEY_VERSION = 1
_KEY_SHAPE = re.compile(r"^[\x21-\x7e]{20,200}$")  # printable ASCII, no spaces

_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_llm_keys (
    user_id TEXT NOT NULL, provider TEXT NOT NULL, ciphertext TEXT NOT NULL, last4 TEXT NOT NULL,
    key_version INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_tested_at TEXT, last_status TEXT,
    PRIMARY KEY (user_id, provider)
);
"""


class KeysUnavailable(RuntimeError):
    """The server has no (valid) master key, so users' keys cannot be stored or used."""


def _master() -> bytes:
    raw = settings.keys_master_key.strip()
    if not raw:
        raise KeysUnavailable("own model keys are not enabled on this server")
    try:
        key = base64.urlsafe_b64decode(raw)
    except (binascii.Error, ValueError) as exc:
        raise KeysUnavailable("NIVESH_KEYS_MASTER_KEY is not valid base64") from exc
    if len(key) != 32:
        raise KeysUnavailable("NIVESH_KEYS_MASTER_KEY must be 32 bytes")
    return key


def validate_master_key() -> None:
    """Startup check: an empty key just turns the feature off, a malformed one stops the server."""
    if settings.keys_master_key.strip():
        _master()


def enabled() -> bool:
    try:
        _master()
    except KeysUnavailable:
        return False
    return True


def _aad(user_id: str, provider: str) -> bytes:
    return f"nivesh:{KEY_VERSION}:{user_id}:{provider}".encode()


def encrypt(secret: str, user_id: str, provider: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_master()).encrypt(nonce, secret.encode(), _aad(user_id, provider))
    return base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(ciphertext: str, user_id: str, provider: str) -> str:
    blob = base64.urlsafe_b64decode(ciphertext)
    try:
        return AESGCM(_master()).decrypt(blob[:12], blob[12:], _aad(user_id, provider)).decode()
    except InvalidTag as exc:  # wrong master key, tampered row, or row moved to another user
        raise KeysUnavailable("your saved key cannot be read; please add it again") from exc


def check_shape(secret: str) -> str:
    secret = secret.strip()
    if not _KEY_SHAPE.match(secret):
        raise ValueError("that does not look like an API key")
    return secret


def _connect(path: Path | None) -> sqlite3.Connection:
    con = appdb.connect(path)
    con.executescript(_SCHEMA)
    return con


def save_key(user_id: str, provider: str, secret: str, status: str, path: Path | None = None) -> dict[str, Any]:
    with closing(_connect(path)) as con:
        with con:
            con.execute(
                "INSERT INTO user_llm_keys(user_id, provider, ciphertext, last4, key_version, last_tested_at, "
                "last_status) VALUES (?,?,?,?,?,CURRENT_TIMESTAMP,?) "
                "ON CONFLICT(user_id, provider) DO UPDATE SET ciphertext=excluded.ciphertext, last4=excluded.last4, "
                "key_version=excluded.key_version, created_at=CURRENT_TIMESTAMP, "
                "last_tested_at=excluded.last_tested_at, last_status=excluded.last_status",
                (user_id, provider, encrypt(secret, user_id, provider), secret[-4:], KEY_VERSION, status),
            )
    return next(k for k in list_keys(user_id, path) if k["provider"] == provider)


def list_keys(user_id: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Masked view only: provider, last 4 characters, dates, last test result."""
    with closing(_connect(path)) as con:
        rows = con.execute(
            "SELECT provider, last4, created_at, last_tested_at, last_status FROM user_llm_keys "
            "WHERE user_id=? ORDER BY provider",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def decrypt_key(user_id: str, provider: str, path: Path | None = None) -> str | None:
    with closing(_connect(path)) as con:
        row = con.execute(
            "SELECT ciphertext FROM user_llm_keys WHERE user_id=? AND provider=?", (user_id, provider)
        ).fetchone()
    return decrypt(row["ciphertext"], user_id, provider) if row else None


def delete_key(user_id: str, provider: str, path: Path | None = None) -> bool:
    with closing(_connect(path)) as con:
        with con:
            cur = con.execute("DELETE FROM user_llm_keys WHERE user_id=? AND provider=?", (user_id, provider))
    return cur.rowcount > 0


def redact(text: str, secret: str | None) -> str:
    """Remove a key and recognisable pieces of it from text that may reach a user or a log."""
    if not secret:
        return text
    for piece in (secret, secret[:12], secret[-8:]):
        if len(piece) >= 6:
            text = text.replace(piece, "[key]")
    return text
