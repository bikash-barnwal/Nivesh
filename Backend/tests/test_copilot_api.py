"""Copilot HTTP API: streaming protocol, heartbeats, history and model status."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from nivesh.api import copilot as copilot_api
from nivesh.config import settings
from nivesh.copilot.knowledge import KnowledgeIndex
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from nivesh.demo import make_demo_dataset
from tests.fake_llm import FakeLLM


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    base = tmp_path_factory.mktemp("copilotapi")
    parquet = base / "parquet"
    make_demo_dataset(parquet, sessions=300, symbols=30)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = base
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as client:
        yield client, engine_mod._engine
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def use(monkeypatch, llm: FakeLLM) -> None:
    monkeypatch.setattr(copilot_api, "get_llm", lambda: llm)
    monkeypatch.setattr(copilot_api, "get_knowledge", lambda: KnowledgeIndex(roots=[]))


def ask(client: TestClient, question: str):
    response = client.post("/api/copilot/ask", json={"question": question})
    return response, [json.loads(line) for line in response.text.splitlines() if line.strip()]


def sql_for(engine) -> str:
    return f"SELECT symbol, close FROM stock_daily WHERE date = DATE '{engine.data_through()}' ORDER BY symbol LIMIT 2"


def test_stream_is_ndjson_with_the_documented_event_sequence(stack, monkeypatch):
    client, engine = stack
    use(monkeypatch, FakeLLM({
        "router": [{"intent": "analytical", "reason": "numbers"}],
        "sql": [{"sql": sql_for(engine), "explanation": "two closes"}],
        "synth": ["[FACT] ok"],
    }))  # fmt: skip
    response, events = ask(client, "two closing prices")
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/x-ndjson")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert {"route", "sql", "evidence", "answer"} <= set(kinds)
    evidence = next(e for e in events if e["type"] == "evidence")["evidence"]
    assert evidence["row_count"] == 2 and evidence["sql"].lower().startswith("select")
    assert next(e for e in events if e["type"] == "done")["models"]["sql"] == "fake/sql"


def test_heartbeats_are_sent_while_a_slow_model_is_working(stack, monkeypatch):
    client, engine = stack
    monkeypatch.setattr(copilot_api, "HEARTBEAT_S", 0.05)
    use(monkeypatch, FakeLLM({
        "router": [{"intent": "analytical", "reason": "x"}],
        "sql": [{"sql": sql_for(engine), "explanation": "x"}],
        "synth": ["[FACT] ok"],
    }, delay_s=0.2))  # fmt: skip
    _, events = ask(client, "two closing prices")
    assert any(e["type"] == "heartbeat" for e in events)
    assert events[-1]["type"] == "done", "heartbeats must not end or corrupt the stream"


def test_refusals_stream_without_calling_a_model(stack, monkeypatch):
    client, _ = stack
    llm = FakeLLM({})
    use(monkeypatch, llm)
    _, events = ask(client, "What is the close on 2099-12-31?")
    assert llm.calls == [] and any(e["type"] == "answer" and "after the last session" in e["markdown"] for e in events)


def test_model_errors_become_an_error_event_not_a_broken_stream(stack, monkeypatch):
    client, _ = stack
    from nivesh.copilot.client import LLMError

    use(monkeypatch, FakeLLM({"router": [LLMError("all models failed for role 'router'")]}))
    response, events = ask(client, "anything about prices")
    assert (
        response.status_code == 200 and events[-1]["type"] == "error" and "all models failed" in events[-1]["message"]
    )


def test_missing_keys_are_reported_clearly(stack, monkeypatch):
    client, _ = stack
    from nivesh.copilot.config import LLMConfigError

    class NoKeys(FakeLLM):
        def chat(self, role, messages, **kw):
            raise LLMConfigError("provider 'gemini' needs an API key: set GEMINI_API_KEY (see Backend/.env.example)")

    use(monkeypatch, NoKeys({}))
    _, events = ask(client, "prices")
    assert events[-1]["type"] == "error" and "GEMINI_API_KEY" in events[-1]["message"]


def test_history_records_answers_with_evidence_and_models(stack, monkeypatch):
    client, engine = stack
    use(monkeypatch, FakeLLM({
        "router": [{"intent": "analytical", "reason": "x"}],
        "sql": [{"sql": sql_for(engine), "explanation": "x"}],
        "synth": ["[FACT] ok"],
    }))  # fmt: skip
    ask(client, "remember this question")
    rows = client.get("/api/copilot/history?limit=5").json()["data"]
    mine = next(r for r in rows if r["question"] == "remember this question")
    assert mine["answer"] and mine["evidence"]["kind"] == "analytical" and mine["models"]["router"] == "fake/router"


def test_refused_or_failed_requests_without_an_answer_are_not_stored(stack, monkeypatch):
    client, _ = stack
    from nivesh.copilot.client import LLMError

    use(monkeypatch, FakeLLM({"router": [LLMError("down")]}))
    ask(client, "this one fails")
    assert all(r["question"] != "this one fails" for r in client.get("/api/copilot/history").json()["data"])


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 1001}, {"question": 5}])
def test_invalid_questions_are_rejected(stack, body):
    client, _ = stack
    assert client.post("/api/copilot/ask", json=body).status_code == 422


def test_models_endpoint_lists_roles_and_never_returns_key_values(stack, monkeypatch):
    client, _ = stack
    monkeypatch.setenv("GEMINI_API_KEY", "sk-top-secret-value")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    response = client.get("/api/copilot/models")
    body = response.json()
    assert "sk-top-secret-value" not in response.text
    roles = body["data"]["roles"]
    assert {"router", "sql", "synth"} <= {r["role"] for r in roles}
    gemini = next(r for r in roles if r["provider"] == "gemini")
    openrouter = next(r for r in roles if r["provider"] == "openrouter")
    assert (
        gemini["key_present"] is True
        and openrouter["key_present"] is False
        and openrouter["key_env"] == "OPENROUTER_API_KEY"
    )
    assert any(r["position"].startswith("fallback") for r in roles)
