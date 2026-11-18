import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nivesh.api import auth
from nivesh.config import settings
from tests.clerk_tokens import LISTED, Signer, new_key


def _request(authorization: str | None = None) -> Request:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    return Request({"type": "http", "headers": headers})


@pytest.fixture
def signer(monkeypatch):
    return Signer(monkeypatch)


def _status(signer_or_header) -> int:
    with pytest.raises(HTTPException) as err:
        auth.current_user(_request(signer_or_header))
    return err.value.status_code


def test_auth_off_is_one_local_user(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    assert auth.current_user(_request()) == auth.LOCAL_USER
    assert auth.LOCAL_USER.is_local


def test_missing_or_garbage_token_is_401(signer):
    assert _status(None) == 401
    assert _status("Bearer not-a-jwt") == 401


def test_approved_by_clerk_role_or_by_the_allowed_list_and_all_equal(signer):
    by_role = auth.current_user(_request(f"Bearer {signer.token('user_a', role='user')}"))
    by_list = auth.current_user(_request(f"Bearer {signer.token(LISTED, role=None)}"))
    assert by_role == auth.CurrentUser("user_a") and by_list == auth.CurrentUser(LISTED)
    assert not by_role.is_local and not by_list.is_local


def test_a_token_cannot_claim_to_be_the_local_user(signer):
    assert _status(f"Bearer {signer.token('local', role='user')}") == 403


def test_signed_up_but_not_approved_is_403_with_the_id(signer):
    for role in (None, "", "admin", "superuser"):
        with pytest.raises(HTTPException) as err:
            auth.current_user(_request(f"Bearer {signer.token('user_new', role=role)}"))
        assert err.value.status_code == 403 and "user_new" in err.value.detail


def test_expired_wrong_issuer_forged_and_other_site_are_401(signer):
    for token in (
        signer.token(exp=1),
        signer.token(iss="https://evil.example"),
        signer.token(key=new_key()),
        signer.token(azp="https://evil.example"),
        signer.token(azp=None),
    ):
        assert _status(f"Bearer {token}") == 401


def test_startup_refuses_half_configured_sign_in(monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    auth.validate_auth_settings()  # off: nothing to check
    monkeypatch.setattr(settings, "clerk_jwks_url", "http://example.clerk.accounts.dev/.well-known/jwks.json")
    monkeypatch.setattr(settings, "authorized_parties", ["https://nivesh.example"])
    with pytest.raises(RuntimeError, match="https"):
        auth.validate_auth_settings()
    monkeypatch.setattr(settings, "clerk_jwks_url", "https://example.clerk.accounts.dev/.well-known/jwks.json")
    monkeypatch.setattr(settings, "authorized_parties", [])
    with pytest.raises(RuntimeError, match="AUTHORIZED_PARTIES"):
        auth.validate_auth_settings()
