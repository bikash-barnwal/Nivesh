"""Every API route is behind sign-in, users only ever see their own data, and nobody uses the server's keys.

Routes are found from the OpenAPI schema, so a new route cannot be forgotten.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nivesh import analytics
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from tests.clerk_tokens import LISTED, Signer
from tests.synth import make_dataset

OPEN = {"/health", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


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
            SimpleNamespace(path=path, methods={m.upper() for m in ops}) for path, ops in app.openapi()["paths"].items()
        ]
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


@pytest.fixture
def signer(monkeypatch):
    return Signer(monkeypatch)


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _status(client, route, method, headers=None):
    return client.request(method, _fill(route.path), headers=headers).status_code


def _protected(client):
    for route in client.app_routes:
        if route.path in OPEN:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            yield route, method


def test_every_api_route_rejects_missing_token(client, signer):
    unprotected = [f"{m} {r.path}" for r, m in _protected(client) if _status(client, r, m) != 401]
    assert not unprotected, unprotected


def test_every_api_route_rejects_a_user_not_yet_approved(client, signer):
    pending = signer.headers("user_new", role=None)
    wrong = [f"{m} {r.path}" for r, m in _protected(client) if _status(client, r, m, pending) != 403]
    assert not wrong, wrong


def test_sign_in_does_not_change_any_get_response(client, signer, monkeypatch):
    """Same status for a signed-in user as with sign-in off, for every GET route without path params."""
    routes = [r for r in client.app_routes if "GET" in r.methods and "{" not in r.path and r.path not in OPEN]
    assert len(routes) > 20
    with monkeypatch.context() as m:
        m.setattr(settings, "clerk_jwks_url", "")
        off = {r.path: _status(client, r, "GET") for r in routes}
    on = {r.path: _status(client, r, "GET", signer.headers(LISTED)) for r in routes}
    assert on == off
    assert not [p for p, s in on.items() if s in (401, 403) or s >= 500], on


def test_normal_user_can_read_every_shared_page(client, signer):
    routes = [r for r in client.app_routes if "GET" in r.methods and "{" not in r.path and r.path not in OPEN]
    user = signer.headers("user_a")
    denied = {r.path: s for r in routes if (s := _status(client, r, "GET", user)) in (401, 403) or s >= 500}
    assert not denied, denied


def test_watchlist_alerts_paper_and_history_are_private(client, signer):
    a, b = signer.headers("user_a"), signer.headers("user_b")

    assert client.post("/api/watchlist/TCS", headers=a).status_code == 200
    assert [r["symbol"] for r in client.get("/api/watchlist", headers=a).json()["data"]] == ["TCS"]
    assert client.get("/api/watchlist", headers=b).json()["data"] == []
    assert client.delete("/api/watchlist/TCS", headers=b).status_code == 200  # a no-op for b
    assert [r["symbol"] for r in client.get("/api/watchlist", headers=a).json()["data"]] == ["TCS"]

    created = client.post("/api/alerts/defaults", headers=a).json()["data"]
    rule_id = created[0]["id"]
    assert client.get("/api/alerts", headers=b).json()["data"] == []
    assert client.delete(f"/api/alerts/{rule_id}", headers=b).status_code == 404
    assert client.delete(f"/api/alerts/{rule_id}", headers=a).status_code == 200
    assert client.get("/api/alerts/inbox", headers=b).json()["data"] == []

    account = client.post("/api/paper", json={"strategy_id": "trend_stack_v1"}, headers=a)
    assert account.status_code == 200
    account_id = account.json()["data"]["id"]
    assert client.get("/api/paper", headers=b).json()["data"] == []
    assert client.get(f"/api/paper/{account_id}", headers=b).status_code == 404
    assert client.delete(f"/api/paper/{account_id}", headers=b).status_code == 404
    assert client.delete(f"/api/paper/{account_id}", headers=a).status_code == 200

    assert client.get("/api/copilot/history", headers=b).json()["data"] == []


def test_watchlist_rejects_junk_symbols(client, signer):
    a = signer.headers("user_a")
    for junk in ("x" * 40, "a b", "<script>"):
        assert client.post(f"/api/watchlist/{junk}", headers=a).status_code in (404, 422)


def test_no_signed_in_user_gets_the_server_model_keys(client, signer, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-key-that-must-not-be-used")
    for headers in (signer.headers("user_a"), signer.headers(LISTED)):
        assert client.post("/api/copilot/ask", json={"question": "hi"}, headers=headers).status_code == 403
        roles = client.get("/api/copilot/models", headers=headers).json()["data"]["roles"]
        assert roles and all(r["key_env"] is None and r["key_present"] is False and r["own_key"] for r in roles)


def test_local_user_with_sign_in_off_keeps_the_server_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    roles = client.get("/api/copilot/models").json()["data"]["roles"]
    assert any(r["key_env"] for r in roles) and not any(r["own_key"] for r in roles)
