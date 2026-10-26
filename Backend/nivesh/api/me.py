"""The signed-in user: who am I, my settings (AI model), and my own Copilot model keys (write-only, masked)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from nivesh import appdb, ratelimit, userkeys
from nivesh.api.auth import User
from nivesh.config import settings
from nivesh.copilot import byok
from nivesh.copilot.client import LLMError
from nivesh.copilot.config import LLMConfigError, load_config

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"])

_TEST_PROMPT = [{"role": "user", "content": 'Reply with the JSON {"ok": true}.'}]


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=300)


class SettingsIn(BaseModel):
    # None = the configured default model for each Copilot step.
    copilot_model: str | None = Field(default=None, max_length=80)


def _model_options() -> dict[str, list[str]]:
    """Offered providers and their models; empty when the model config is broken (the page says so)."""
    try:
        config = load_config()
    except LLMConfigError:
        log.exception("LLM config cannot be loaded")
        return {}
    return {provider: config.user_models(provider) for provider in config.byok}


def _keys_or_503() -> None:
    if not userkeys.enabled():
        raise HTTPException(status_code=503, detail="Own model keys are not enabled on this server.")


@router.get("")
def me(user: User) -> dict[str, Any]:
    enabled = userkeys.enabled()
    models = _model_options()
    return {
        "id": user.id,
        "copilot": {
            "keys_enabled": enabled,
            "providers": list(models),
            "keys": userkeys.list_keys(user.id) if enabled else [],
            "uses_server_keys": user.is_local,
            "models": models,
        },
        "settings": appdb.get_settings(user_id=user.id),
    }


@router.put("/settings")
def save_settings(body: SettingsIn, user: User) -> dict[str, Any]:
    allowed = {m for models in _model_options().values() for m in models}
    if body.copilot_model is not None and body.copilot_model not in allowed:
        raise HTTPException(status_code=422, detail=f"model must be one of {sorted(allowed)}")
    values = {**appdb.get_settings(user_id=user.id), "copilot_model": body.copilot_model}
    return appdb.save_settings(values, user_id=user.id)


@router.get("/llm-keys")
def list_keys(user: User) -> list[dict[str, Any]]:
    return userkeys.list_keys(user.id) if userkeys.enabled() else []


@router.put("/llm-keys/{provider}")
def save_key(provider: str, body: KeyIn, user: User) -> dict[str, Any]:
    _keys_or_503()
    if provider not in byok.providers():
        raise HTTPException(status_code=404, detail=f"'{provider}' is not offered for your own key")
    try:
        secret = userkeys.check_shape(body.key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ratelimit.allow(user.id, "key_test", settings.user_key_tests_per_hour, 3600):
        raise HTTPException(status_code=429, detail="Too many key tests; try again in an hour.")
    try:
        byok.build(user.id, provider, secret).chat("router", _TEST_PROMPT)
    except (LLMError, LLMConfigError) as exc:
        reason = userkeys.redact(str(exc), secret)[:300]
        raise HTTPException(status_code=400, detail=f"The key did not work: {reason}") from None
    return userkeys.save_key(user.id, provider, secret, "ok")


@router.delete("/llm-keys/{provider}")
def delete_key(provider: str, user: User) -> dict[str, Any]:
    _keys_or_503()
    if not userkeys.delete_key(user.id, provider):
        raise HTTPException(status_code=404, detail="no saved key for that provider")
    return {"status": "deleted", "provider": provider}
