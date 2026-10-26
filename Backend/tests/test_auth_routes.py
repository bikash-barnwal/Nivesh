"""Every API route is behind sign-in, and sign-in never changes what a route returns (routes found automatically)."""

from __future__ import annotations

import re
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from nivesh import analytics
from nivesh.api import auth
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from tests.synth import make_dataset

OPEN = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
ISSUER = "https://example.clerk.accounts.dev"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("authroutes")
    parquet = root / "parquet"
    make_dataset(parquet)
    analytics.build_analytics(parquet_dir=parquet)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = root
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as c:
        # The OpenAPI schema lists every route, including those of included routers.
        c.app_routes = [
            SimpleNamespace(path=path, methods={m.upper() for m in ops})
            for path, ops in app.openapi()["paths"].items()
        ]
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


@pytest.fixture
def token(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = SimpleNamespace(get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=key.public_key()))
    monkeypatch.setattr(auth, "_jwks", lambda: jwks)
    monkeypatch.setattr(settings, "clerk_jwks_url", f"{ISSUER}/.well-known/jwks.json")
    monkeypatch.setattr(settings, "allowed_user_ids", ["user_ok"])
    return jwt.encode({"sub": "user_ok", "iss": ISSUER, "exp": int(time.time()) + 300}, key, algorithm="RS256")


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _status(client, route, method, headers=None):
    return client.request(method, _fill(route.path), headers=headers).status_code


def test_every_api_route_rejects_missing_token(client, token):
    unprotected = []
    for route in client.app_routes:
        if route.path in OPEN:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if _status(client, route, method) != 401:
                unprotected.append(f"{method} {route.path}")
    assert not unprotected, unprotected


def test_every_api_route_rejects_unlisted_user(client, token, monkeypatch):
    monkeypatch.setattr(settings, "allowed_user_ids", [])
    wrong = []
    for route in client.app_routes:
        if route.path in OPEN:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if _status(client, route, method, {"authorization": f"Bearer {token}"}) != 403:
                wrong.append(f"{method} {route.path}")
    assert not wrong, wrong


def test_sign_in_does_not_change_any_get_response(client, token, monkeypatch):
    """Same status with sign-in on (valid token) as with sign-in off, for every GET route without path params."""
    routes = [r for r in client.app_routes if "GET" in r.methods and "{" not in r.path and r.path not in OPEN]
    assert len(routes) > 20
    with monkeypatch.context() as m:
        m.setattr(settings, "clerk_jwks_url", "")
        off = {r.path: _status(client, r, "GET") for r in routes}
    on = {r.path: _status(client, r, "GET", {"authorization": f"Bearer {token}"}) for r in routes}
    assert on == off
    assert not [p for p, s in on.items() if s in (401, 403) or s >= 500], on


def test_signed_in_user_can_write_and_delete(client, token):
    """POST/DELETE work end to end with a valid token (watchlist and alert rules)."""
    auth_header = {"authorization": f"Bearer {token}"}

    assert client.post("/api/watchlist/ABC", headers=auth_header).status_code == 200
    symbols = [r["symbol"] for r in client.get("/api/watchlist", headers=auth_header).json()["data"]]
    assert "ABC" in symbols
    assert client.delete("/api/watchlist/ABC", headers=auth_header).status_code == 200
    symbols = [r["symbol"] for r in client.get("/api/watchlist", headers=auth_header).json()["data"]]
    assert "ABC" not in symbols

    created = client.post("/api/alerts/defaults", headers=auth_header)
    assert created.status_code == 200
    rule_id = created.json()["data"][0]["id"]
    assert client.delete(f"/api/alerts/{rule_id}", headers=auth_header).status_code == 200
    assert client.delete(f"/api/alerts/{rule_id}", headers=auth_header).status_code == 404
