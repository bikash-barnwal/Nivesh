"""Copilot on a user's own key: a per-request model client that only knows that user's key and provider."""

from __future__ import annotations

import re
from typing import Any

from nivesh import appdb, userkeys
from nivesh.config import settings
from nivesh.copilot.client import OpenAICompatibleLLM
from nivesh.copilot.config import LLMConfig, Provider, load_config


def _openai_client(secret: str, provider: Provider, timeout_s: float) -> Any:
    from openai import OpenAI

    return OpenAI(
        api_key=secret,
        base_url=provider.base_url,  # from config/llm.yaml only; users never choose where their key is sent
        default_headers=provider.headers or None,
        timeout=timeout_s,
        max_retries=0,
    )


# Tests replace this to avoid network calls.
client_factory = _openai_client


def providers(config: LLMConfig | None = None) -> list[str]:
    """Providers offered for own keys, default first."""
    return list((config or load_config()).byok)


def user_provider(user_id: str, config: LLMConfig | None = None) -> str | None:
    """The first offered provider the user has a key for."""
    saved = {k["provider"] for k in userkeys.list_keys(user_id)}
    return next((p for p in providers(config) if p in saved), None)


def chosen_model(user_id: str) -> str | None:
    """The model the user picked on the Other page, if any."""
    return appdb.get_settings(user_id=user_id).get("copilot_model")


def build(
    user_id: str, provider: str, secret: str, config: LLMConfig | None = None, model: str | None = None
) -> OpenAICompatibleLLM:
    user_config = (config or load_config()).for_user_key(provider, model)
    target = user_config.providers[provider]
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", user_id)
    return OpenAICompatibleLLM(
        user_config,
        client_factory=lambda _name: client_factory(secret, target, user_config.limits.timeout_s),
        log_path=settings.logs_dir / "llm_calls_users" / f"{safe_id}.jsonl",
        secret=secret,
    )


def for_user(user_id: str, config: LLMConfig | None = None) -> OpenAICompatibleLLM | None:
    """The user's own-key model client, or None when they have not added a key."""
    provider = user_provider(user_id, config)
    if provider is None:
        return None
    secret = userkeys.decrypt_key(user_id, provider)
    return build(user_id, provider, secret, config, chosen_model(user_id)) if secret else None
