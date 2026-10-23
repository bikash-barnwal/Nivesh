"""LLM provider and role configuration (config/llm.yaml)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nivesh.config import settings


class LLMConfigError(RuntimeError):
    """Configuration problem the user can fix (missing key, unknown provider, ...)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provider(_Model):
    base_url: str
    key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    local: bool = False

    def api_key(self, name: str) -> str:
        if self.key_env is None:
            return "not-needed"  # local servers such as Ollama ignore the key
        key = os.environ.get(self.key_env, "").strip()
        if not key:
            raise LLMConfigError(f"provider '{name}' needs an API key: set {self.key_env} (see Backend/.env.example)")
        return key


class Target(_Model):
    provider: str
    model: str
    temperature: float = 0.0
    json_output: bool = Field(False, alias="json")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Limits(_Model):
    timeout_s: float = 90.0
    max_calls_per_day: int = 500
    max_sql_attempts: int = 3
    max_result_rows: int = 200


class LLMConfig(_Model):
    providers: dict[str, Provider]
    roles: dict[str, Target]
    fallbacks: dict[str, list[Target]] = Field(default_factory=dict)
    limits: Limits = Field(default_factory=Limits)
    policy: dict[str, bool] = Field(default_factory=dict)

    def chain(self, role: str) -> list[Target]:
        if role not in self.roles:
            raise LLMConfigError(f"no model configured for role '{role}'")
        return [self.roles[role], *self.fallbacks.get(role, [])]

    def validate_references(self) -> None:
        for role in self.roles:
            for target in self.chain(role):
                if target.provider not in self.providers:
                    raise LLMConfigError(f"role '{role}' uses unknown provider '{target.provider}'")


def load_config(path: Path | None = None) -> LLMConfig:
    source = path or settings.llm_config
    local = source.with_name("llm.local.yaml")
    if path is None and settings.llm_local_override and source.name == "llm.yaml" and local.exists():
        source = local  # machine-specific override, gitignored
    if not source.exists():
        raise LLMConfigError(f"LLM config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    try:
        config = LLMConfig.model_validate(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{source.name}: {exc}") from exc
    config.validate_references()
    return config
