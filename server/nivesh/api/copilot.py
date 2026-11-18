"""Copilot endpoints: a streaming NDJSON `ask`, model status, and the user's history.

Model keys: with sign-in on, every user runs the Copilot on their own key (see /api/me/llm-keys), with a daily
question limit and one question at a time. Only the local user (sign-in off) may use the server's keys.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from nivesh import ratelimit, userkeys
from nivesh.api.auth import CurrentUser, User
from nivesh.api.common import Envelope, envelope
from nivesh.appdb import add_history, list_history
from nivesh.config import settings
from nivesh.copilot import byok
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


# One question at a time per user. An entry older than ACTIVE_TTL_S is stale (a dropped connection whose stream
# was never closed) and no longer blocks.
ACTIVE_TTL_S = 600
_active: dict[str, float] = {}
_active_lock = threading.Lock()


def llm_for(user: CurrentUser) -> LLM:
    """The user's own key if saved; else the server's keys for the local user only; else a clear 403."""
    try:
        own = byok.for_user(user.id) if userkeys.enabled() else None
    except userkeys.KeysUnavailable as exc:
        if not user.is_local:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        own = None
    if own is not None:
        return own
    if user.is_local:
        return get_llm()
    if not userkeys.enabled():
        raise HTTPException(status_code=403, detail="The Copilot is not available for your account on this server.")
    raise HTTPException(status_code=403, detail="Add your own model key (Copilot keys) to use the Copilot.")


@lru_cache(maxsize=1)
def get_knowledge() -> KnowledgeIndex:
    return KnowledgeIndex()


def _encode(event: dict[str, Any]) -> bytes:
    return (json.dumps(event, default=str) + "\n").encode("utf-8")


def _stream(question: str, user_id: str, llm: LLM) -> Iterator[bytes]:
    try:
        yield from _answer(question, user_id, llm)
    finally:
        with _active_lock:
            _active.pop(user_id, None)


def _answer(question: str, user_id: str, llm: LLM) -> Iterator[bytes]:
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()
    captured: dict[str, Any] = {"answer": "", "evidence": None, "models": {}}

    def work() -> None:
        try:
            copilot = Copilot(get_engine(), llm, get_knowledge())
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
            user_id=user_id,
        )


@router.post("/ask")
def ask(request: AskRequest, user: User) -> StreamingResponse:
    llm = llm_for(user)
    if not user.is_local and not ratelimit.allow(user.id, "copilot", settings.user_copilot_per_day, 86400):
        raise HTTPException(status_code=429, detail=f"Daily Copilot limit reached ({settings.user_copilot_per_day}).")
    with _active_lock:
        if time.monotonic() - _active.get(user.id, float("-inf")) < ACTIVE_TTL_S:
            raise HTTPException(status_code=429, detail="Wait for your current question to finish.")
        _active[user.id] = time.monotonic()
    return StreamingResponse(
        _stream(request.question.strip(), user.id, llm),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/models", response_model=Envelope)
def models(user: User) -> dict[str, Any]:
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

    own = byok.user_provider(user.id, config) if userkeys.enabled() else None
    if own or not user.is_local:
        # What the user's own key runs; the server's providers and key names are not shown.
        offered = own or next(iter(config.byok), None)
        picked = byok.chosen_model(user.id)
        config = config.for_user_key(offered, picked) if offered else config.model_copy(update={"roles": {}})
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
                    "key_present": bool(own)
                    or (
                        user.is_local
                        and (provider.key_env is None or bool(os.environ.get(provider.key_env, "").strip()))
                    ),
                    "key_env": None if own or not user.is_local else provider.key_env,
                    "own_key": bool(own) or not user.is_local,
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
def history(user: User, limit: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, Any]:
    rows = list_history(limit, user_id=user.id)
    for row in rows:
        row["evidence"] = json.loads(row["evidence"]) if row["evidence"] else None
        row["models"] = json.loads(row["models"]) if row["models"] else {}
    return envelope(rows, as_of=None, engine=get_engine(), source="appdb.copilot_history", calculation="your history")
