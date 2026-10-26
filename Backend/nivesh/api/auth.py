"""Clerk session-token check. Off when NIVESH_CLERK_JWKS_URL is empty (local dev)."""

from __future__ import annotations

import logging
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
from fastapi import HTTPException, Request

from nivesh.config import settings

log = logging.getLogger(__name__)

# Tolerated clock drift vs Clerk; a slow clock otherwise rejects fresh tokens ("not yet valid (iat)").
CLOCK_SKEW_S = 60


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(settings.clerk_jwks_url, cache_keys=True)


def _issuer() -> str:
    parts = urlsplit(settings.clerk_jwks_url)
    return f"{parts.scheme}://{parts.netloc}"


def require_user(request: Request) -> str | None:
    """FastAPI dependency: returns the Clerk user id, or raises 401/403."""
    if not settings.clerk_jwks_url:
        return None
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
    user_id = str(claims["sub"])
    if user_id not in settings.allowed_user_ids:
        log.warning("user %s not in NIVESH_ALLOWED_USER_IDS", user_id)
        raise HTTPException(403, f"User {user_id} is not allowed. Add it to NIVESH_ALLOWED_USER_IDS.")
    return user_id
