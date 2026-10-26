import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nivesh.api import auth
from nivesh.config import settings


def _request(authorization: str | None = None) -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request({"type": "http", "headers": headers})


def test_auth_off_without_jwks_url(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    assert auth.require_user(_request()) is None


def test_missing_token_is_401(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "https://example.clerk.accounts.dev/.well-known/jwks.json")
    with pytest.raises(HTTPException) as err:
        auth.require_user(_request())
    assert err.value.status_code == 401


def test_garbage_token_is_401(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "https://example.clerk.accounts.dev/.well-known/jwks.json")
    with pytest.raises(HTTPException) as err:
        auth.require_user(_request("Bearer not-a-jwt"))
    assert err.value.status_code == 401


ISSUER = "https://example.clerk.accounts.dev"


@pytest.fixture
def signer(monkeypatch):
    import time
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(settings, "clerk_jwks_url", f"{ISSUER}/.well-known/jwks.json")
    monkeypatch.setattr(settings, "allowed_user_ids", ["user_ok"])
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth, "_jwks", lambda: jwks)

    def sign(sub="user_ok", iss=ISSUER, exp_in=300, signing_key=key):
        return jwt.encode({"sub": sub, "iss": iss, "exp": int(time.time()) + exp_in}, signing_key, algorithm="RS256")

    sign.key_type = rsa
    return sign


def test_allowed_user_passes(signer):
    assert auth.require_user(_request(f"Bearer {signer()}")) == "user_ok"


def test_unlisted_user_is_403_with_id(signer):
    with pytest.raises(HTTPException) as err:
        auth.require_user(_request(f"Bearer {signer(sub='user_other')}"))
    assert err.value.status_code == 403
    assert "user_other" in err.value.detail


def test_expired_wrong_issuer_and_forged_are_401(signer):
    forged = signer.key_type.generate_private_key(public_exponent=65537, key_size=2048)
    for token in (signer(exp_in=-120), signer(iss="https://evil.example"), signer(signing_key=forged)):
        with pytest.raises(HTTPException) as err:
            auth.require_user(_request(f"Bearer {token}"))
        assert err.value.status_code == 401


def test_health_open_and_api_protected(signer):
    from fastapi.testclient import TestClient

    from nivesh.api.main import app

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/data/freshness").status_code == 401
    other = {"authorization": f"Bearer {signer(sub='user_other')}"}
    assert client.get("/api/data/freshness", headers=other).status_code == 403
