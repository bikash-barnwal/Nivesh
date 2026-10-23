"""Copilot endpoints: a streaming NDJSON `ask`, model status, and local history."""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from nivesh.api.common import Envelope, envelope
from nivesh.appdb import add_history, list_history
from nivesh.copilot.client import LLM, OpenAICompatibleLLM
from nivesh.copilot.config import LLMConfigError, load_config
from nivesh.copilot.knowledge import KnowledgeIndex
from nivesh.copilot.pipeline import Copilot
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api/copilot", tags=["copilot"])

HEARTBEAT_S = 10.0  # keeps the connection alive while a slow model works; the UI ignores these events


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    """The configured model layer. Tests replace this with a scripted fake."""
    return OpenAICompatibleLLM()


@lru_cache(maxsize=1)
def get_knowledge() -> KnowledgeIndex:
    return KnowledgeIndex()


def _encode(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, default=str) + "\n").encode("utf-8")


def _stream(question: str) -> Iterator[bytes]:
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    captured: dict[str, Any] = {"answer": "", "evidence": None, "models": {}}

    def work() -> None:
        try:
            copilot = Copilot(get_engine(), get_llm(), get_knowledge())
            for event in copilot.ask(question):
                if event["type"] == "answer":
                    captured["answer"] = event["markdown"]
                elif event["type"] == "evidence":
                    captured["evidence"] = event["evidence"]
                elif event["type"] == "done":
                    captured["models"] = event["models"]
                events.put(event)
        except LLMConfigError as exc:
            events.put({"type": "error", "message": str(exc)})
        except Exception as exc:  # never leave the client hanging on an open stream
            events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            events.put(None)

    threading.Thread(target=work, daemon=True, name="copilot").start()
    while True:
        try:
            event = events.get(timeout=HEARTBEAT_S)
        except queue.Empty:
            yield _encode({"type": "heartbeat"})
            continue
        if event is None:
            break
        yield _encode(event)
    if captured["answer"]:
        add_history(
            question,
            captured["answer"],
            json.dumps(captured["evidence"], default=str),
            json.dumps(captured["models"]),
        )


@router.post("/ask")
def ask(request: AskRequest) -> StreamingResponse:
    return StreamingResponse(
        _stream(request.question.strip()),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/models", response_model=Envelope)
def models() -> dict[str, Any]:
    engine = get_engine()
    try:
        config = load_config()
    except LLMConfigError as exc:
        return envelope(
            {"error": str(exc), "roles": []},
            as_of=None,
            engine=engine,
            source="config/llm.yaml",
            calculation="config error",
        )
    import os

    roles = []
    for role in config.roles:
        for position, target in enumerate(config.chain(role)):
            provider = config.providers[target.provider]
            roles.append(
                {
                    "role": role,
                    "position": "primary" if position == 0 else f"fallback {position}",
                    "provider": target.provider,
                    "model": target.model,
                    "local": provider.local,
                    "key_present": provider.key_env is None or bool(os.environ.get(provider.key_env, "").strip()),
                    "key_env": provider.key_env,
                }
            )
    return envelope(
        {"roles": roles, "policy": config.policy},
        as_of=None,
        engine=engine,
        source="config/llm.yaml",
        calculation="configured models; keys are never returned",
    )


@router.get("/history", response_model=Envelope)
def history(limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    rows = list_history(limit)
    for row in rows:
        row["evidence"] = json.loads(row["evidence"]) if row["evidence"] else None
        row["models"] = json.loads(row["models"]) if row["models"] else {}
    return envelope(rows, as_of=None, engine=get_engine(), source="appdb.copilot_history", calculation="local history")
