"""Clerk session-token check. Off when NIVESH_CLERK_JWKS_URL is empty (local dev: one local user).

One kind of user: every approved account is equal. An account is approved when its Clerk id is in
NIVESH_ALLOWED_USER_IDS, or its Clerk public metadata has role "user" (exposed by the `role` session-token claim).
Anyone else who signs in gets 403 "access pending".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request

from nivesh.config import settings

log = logging.getLogger(__name__)

# Tolerated clock drift vs Clerk; a slow clock otherwise rejects fresh tokens ("not yet valid (iat)").
CLOCK_SKEW_S = 60
APPROVED_ROLE = "user"
LOCAL_ID = "local"


@dataclass(frozen=True)
class CurrentUser:
    id: str

    @property
    def is_local(self) -> bool:
        """Sign-in off: the single user of a local install (may use the server's model keys, no limits)."""
        return self.id == LOCAL_ID


LOCAL_USER = CurrentUser(id=LOCAL_ID)


def auth_enabled() -> bool:
    return bool(settings.clerk_jwks_url)


def validate_auth_settings() -> None:
    """Fail at startup instead of serving a half-configured sign-in."""
    if not auth_enabled():
        return
    if not settings.clerk_jwks_url.startswith("https://"):
        raise RuntimeError("NIVESH_CLERK_JWKS_URL must be an https URL")
    if not settings.authorized_parties:
        raise RuntimeError("NIVESH_AUTHORIZED_PARTIES must list the site origin(s) when sign-in is on")


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.clerk_jwks_url, cache_keys=True)


def _issuer() -> str:
    parts = urlsplit(settings.clerk_jwks_url)
    return f"{parts.scheme}://{parts.netloc}"


def current_user(request: Request) -> CurrentUser:
    """FastAPI dependency: the signed-in, approved user, or 401/403."""
    if not auth_enabled():
        return LOCAL_USER
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, "Sign in required")
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=_issuer(),
            leeway=CLOCK_SKEW_S,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        log.warning("rejected token: %s", exc)
        raise HTTPException(401, "Invalid or expired session") from exc
    if claims.get("azp") not in settings.authorized_parties:
        log.warning("rejected token for origin %r", claims.get("azp"))
        raise HTTPException(401, "Session was issued for another site")
    user_id = str(claims["sub"])
    approved = user_id in settings.allowed_user_ids or claims.get("role") == APPROVED_ROLE
    if user_id == LOCAL_ID or not approved:
        log.warning("user %s is not approved yet", user_id)
        raise HTTPException(403, f"Access pending: ask the owner to approve user {user_id}.")
    return CurrentUser(id=user_id)


# Route parameter type: `user: User` gives the signed-in user (one token check per request).
User = Annotated[CurrentUser, Depends(current_user)]
