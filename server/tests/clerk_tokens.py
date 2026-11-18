"""Test-only Clerk stand-in: a local RSA key signs session tokens, and the JWKS client is replaced to return it."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from nivesh.api import auth
from nivesh.config import settings

ISSUER = "https://example.clerk.accounts.dev"
SITE = "https://nivesh.example"
LISTED = "user_listed"  # approved through NIVESH_ALLOWED_USER_IDS


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Signer:
    """Turns sign-in on for a test and mints tokens like Clerk's."""

    def __init__(self, monkeypatch) -> None:
        self.key = new_key()
        jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=self.key.public_key()))
        monkeypatch.setattr(auth, "_jwks", lambda: jwks)
        monkeypatch.setattr(settings, "clerk_jwks_url", f"{ISSUER}/.well-known/jwks.json")
        monkeypatch.setattr(settings, "authorized_parties", [SITE])
        monkeypatch.setattr(settings, "allowed_user_ids", [LISTED])

    def token(self, sub: str = "user_a", *, role: str | None = "user", key=None, **claims) -> str:
        body = {"sub": sub, "iss": ISSUER, "azp": SITE, "exp": int(time.time()) + 300, **claims}
        if role is not None:
            body["role"] = role
        return jwt.encode(body, key or self.key, algorithm="RS256")

    def headers(self, sub: str = "user_a", **kw) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token(sub, **kw)}"}
