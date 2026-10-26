"""Own model keys (encrypted, write-only, private), per-user limits, and the SQL sandbox."""

from __future__ import annotations

import base64
import os
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from nivesh import analytics, ratelimit, userkeys
from nivesh.config import settings
from nivesh.copilot import byok
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from nivesh.llm import validate_readonly_sql
from tests.clerk_tokens import LISTED, Signer
from tests.synth import make_dataset

GOOD_KEY = "AIzaSy" + "k" * 33  # shaped like a Gemini key, not a real one
BAD_KEY = "AIzaSy" + "b" * 33
BUSY_KEY = "AIzaSy" + "z" * 33  # a valid key whose models are all overloaded right now


class FakeCompletions:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def create(self, **_params):
        if self.secret == BUSY_KEY:
            raise RuntimeError("Error code: 503 - This model is currently experiencing high demand. UNAVAILABLE")
        if self.secret != GOOD_KEY:
            # Providers sometimes echo part of the key back; it must never reach the user.
            raise RuntimeError(f"401 API key not valid: {self.secret}")
        message = SimpleNamespace(content='{"ok": true}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def fake_client(secret, _provider, _timeout):
    return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(secret)))


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("byok")
    parquet = root / "parquet"
    make_dataset(parquet)
    analytics.build_analytics(parquet_dir=parquet)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = root
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as c:
        c.root = root
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


@pytest.fixture
def signer(monkeypatch):
    monkeypatch.setattr(settings, "keys_master_key", base64.urlsafe_b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(byok, "client_factory", fake_client)
    ratelimit.reset()
    return Signer(monkeypatch)


def test_key_is_tested_encrypted_masked_and_private(client, signer):
    a, b = signer.headers("user_a"), signer.headers("user_b")
    me = client.get("/api/me", headers=a).json()
    assert me["id"] == "user_a" and me["copilot"]["providers"][0] == "gemini" and me["copilot"]["keys"] == []
    assert me["copilot"]["uses_server_keys"] is False

    bad = client.put("/api/me/llm-keys/gemini", json={"key": BAD_KEY}, headers=a)
    assert bad.status_code == 400 and BAD_KEY not in bad.text and BAD_KEY[-8:] not in bad.text
    assert client.get("/api/me/llm-keys", headers=a).json() == [], "a failing key is not saved"

    saved = client.put("/api/me/llm-keys/gemini", json={"key": GOOD_KEY}, headers=a)
    assert saved.status_code == 200 and saved.json()["last4"] == GOOD_KEY[-4:]
    for response in (saved, client.get("/api/me/llm-keys", headers=a), client.get("/api/me", headers=a)):
        assert GOOD_KEY not in response.text
    raw = sqlite3.connect(client.root / "app.sqlite3").execute("SELECT * FROM user_llm_keys").fetchall()
    assert raw and GOOD_KEY not in repr(raw), "stored encrypted"

    assert client.get("/api/me/llm-keys", headers=b).json() == [], "b cannot see a's key"
    assert client.delete("/api/me/llm-keys/gemini", headers=b).status_code == 404
    assert byok.for_user("user_b") is None
    assert byok.for_user("user_a") is not None
    assert client.delete("/api/me/llm-keys/gemini", headers=a).status_code == 200


def test_only_offered_providers_and_key_shaped_values(client, signer):
    a = signer.headers("user_a")
    assert client.put("/api/me/llm-keys/openai", json={"key": GOOD_KEY}, headers=a).status_code == 404
    assert client.put("/api/me/llm-keys/ollama", json={"key": GOOD_KEY}, headers=a).status_code == 404
    for junk in ("short", "has spaces in it but is long enough", "x" * 250):
        assert client.put("/api/me/llm-keys/gemini", json={"key": junk}, headers=a).status_code == 422


def test_ciphertext_is_bound_to_its_user_and_master_key(client, signer, monkeypatch):
    sealed = userkeys.encrypt(GOOD_KEY, "user_a", "gemini")
    assert userkeys.decrypt(sealed, "user_a", "gemini") == GOOD_KEY
    with pytest.raises(userkeys.KeysUnavailable):
        userkeys.decrypt(sealed, "user_b", "gemini")
    monkeypatch.setattr(settings, "keys_master_key", base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(userkeys.KeysUnavailable):
        userkeys.decrypt(sealed, "user_a", "gemini")


def test_a_user_key_never_falls_back_to_server_keys(client, signer):
    from nivesh.copilot.config import load_config

    user_config = load_config().for_user_key("gemini")
    assert set(user_config.providers) == {"gemini"}
    assert user_config.fallbacks["router"], "busy models fall back to other models of the same provider"
    assert all(t.provider == "gemini" for role in user_config.roles for t in user_config.chain(role))


def test_copilot_without_a_key_explains_and_without_master_key_is_off(client, signer, monkeypatch):
    a = signer.headers("user_a")
    no_key = client.post("/api/copilot/ask", json={"question": "hi"}, headers=a)
    assert no_key.status_code == 403 and "key" in no_key.json()["detail"].lower()
    monkeypatch.setattr(settings, "keys_master_key", "")
    assert client.get("/api/me", headers=a).json()["copilot"]["keys_enabled"] is False
    assert client.put("/api/me/llm-keys/gemini", json={"key": GOOD_KEY}, headers=a).status_code == 503


def test_key_tests_are_rate_limited(client, signer, monkeypatch):
    monkeypatch.setattr(settings, "user_key_tests_per_hour", 2)
    a = signer.headers("user_a")
    codes = [client.put("/api/me/llm-keys/gemini", json={"key": BAD_KEY}, headers=a).status_code for _ in range(3)]
    assert codes == [400, 400, 429]


def test_sql_is_rate_limited_for_every_signed_in_user(client, signer, monkeypatch):
    monkeypatch.setattr(settings, "user_sql_per_minute", 2)
    body = {"sql": "SELECT count(*) AS n FROM stock_daily"}
    for headers in (signer.headers("user_a"), signer.headers(LISTED)):
        codes = [client.post("/api/copilot/sql", json=body, headers=headers).status_code for _ in range(3)]
        assert codes == [200, 200, 429]


def test_malformed_master_key_stops_startup(monkeypatch):
    monkeypatch.setattr(settings, "keys_master_key", "not-base64-and-wrong-length")
    with pytest.raises(userkeys.KeysUnavailable):
        userkeys.validate_master_key()


def test_redact_removes_key_and_its_pieces():
    text = f"bad key {GOOD_KEY}; starts {GOOD_KEY[:12]}; ends {GOOD_KEY[-8:]}"
    cleaned = userkeys.redact(text, GOOD_KEY)
    assert GOOD_KEY[:12] not in cleaned and GOOD_KEY[-8:] not in cleaned


SANDBOX_PROBES = [
    "SELECT current_setting('memory_limit') AS v FROM stock_daily",
    "SELECT getenv('HOME') AS v FROM stock_daily",
    "SELECT (SELECT content FROM read_text('pyproject.toml')) AS v FROM stock_daily",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT x FROM stock_daily WHERE x IN (SELECT 1 FROM 'pyproject.toml')",
    "SELECT (SELECT count(*) FROM glob('*')) FROM stock_daily",
    "SELECT * FROM pragma_database_list()",
    "SELECT count(*) FROM range(1000000000000) t(a), stock_daily",
    "SELECT 1; SELECT 2",
    "CALL pragma_version()",
]


@pytest.mark.parametrize("sql", SANDBOX_PROBES)
def test_sql_guard_blocks_escapes(sql):
    with pytest.raises(ValueError):
        validate_readonly_sql(sql, {"stock_daily"})


def test_engine_cannot_read_outside_its_data_or_change_settings(client):
    engine = engine_mod._engine
    assert engine.query("SELECT count(*) FROM stock_daily").rows[0][0] > 0
    for sql in ("SELECT * FROM read_text('pyproject.toml')", "SET memory_limit = '1GB'", "SET threads = 1"):
        with pytest.raises(Exception):  # noqa: B017 - any DuckDB refusal is fine
            engine.query(sql)


def test_each_user_picks_their_own_model_from_the_allowed_list(client, signer):
    a, b = signer.headers("user_a"), signer.headers("user_b")
    options = client.get("/api/me", headers=a).json()["copilot"]["models"]["gemini"]
    assert "gemini-3.1-pro-preview" in options
    assert client.put("/api/me/settings", json={"copilot_model": "gpt-4o"}, headers=a).status_code == 422
    pro = {"copilot_model": "gemini-3.1-pro-preview"}
    assert client.put("/api/me/settings", json=pro, headers=a).status_code == 200
    assert client.get("/api/me", headers=a).json()["settings"]["copilot_model"] == "gemini-3.1-pro-preview"
    assert client.get("/api/me", headers=b).json()["settings"] == {}, "settings are per user"
    assert client.put("/api/me/settings", json={"copilot_model": None}, headers=a).json()["copilot_model"] is None


def test_the_picked_model_runs_every_copilot_step(client, signer):
    from nivesh.copilot.config import load_config

    config = load_config()
    picked = config.for_user_key("gemini", "gemini-3.1-pro-preview")
    assert {picked.chain(role)[0].model for role in picked.roles} == {"gemini-3.1-pro-preview"}, "first try: the pick"
    assert picked.roles["synth"].temperature == config.byok["gemini"]["synth"].temperature
    unknown = config.for_user_key("gemini", "not-offered-anymore")
    assert unknown.roles["router"].model == config.byok["gemini"]["router"].model, "unknown pick falls back"
    client.put("/api/me/settings", json={"copilot_model": "gemini-3.1-pro-preview"}, headers=signer.headers("user_m"))
    assert byok.chosen_model("user_m") == "gemini-3.1-pro-preview"


def test_a_machine_override_without_own_key_options_keeps_them_from_llm_yaml(tmp_path, monkeypatch):
    import shutil

    from nivesh.copilot.config import load_config

    base = tmp_path / "llm.yaml"
    shutil.copy(settings.llm_config, base)
    local = tmp_path / "llm.local.yaml"
    local.write_text(
        "providers:\n  gemini: {base_url: 'https://example.invalid/v1', key_env: GEMINI_API_KEY}\n"
        "roles:\n  router: {provider: gemini, model: x}\n  sql: {provider: gemini, model: x}\n"
        "  repair: {provider: gemini, model: x}\n  synth: {provider: gemini, model: x}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "llm_config", base)
    monkeypatch.setattr(settings, "llm_local_override", True)
    config = load_config()
    assert config.roles["router"].model == "x", "the override still picks the server models"
    assert "gemini" in config.byok and "gemini-3.1-pro-preview" in config.user_models("gemini")


def test_a_busy_model_does_not_reject_a_good_key(client, signer):
    a = signer.headers("user_busy")
    saved = client.put("/api/me/llm-keys/gemini", json={"key": BUSY_KEY}, headers=a)
    assert saved.status_code == 200 and "busy" in saved.json()["last_status"]
    assert BUSY_KEY not in saved.text
