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
            raise LLMConfigError(f"provider '{name}' needs an API key: set {self.key_env} (see server/.env.example)")
        return key


class Target(_Model):
    provider: str
    model: str
    temperature: float = 0.0
    json_output: bool = Field(False, alias="json")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ByokTarget(_Model):
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
    byok: dict[str, dict[str, ByokTarget]] = Field(default_factory=dict)
    byok_models: dict[str, list[str]] = Field(default_factory=dict)
    byok_fallbacks: dict[str, list[str]] = Field(default_factory=dict)

    def chain(self, role: str) -> list[Target]:
        if role not in self.roles:
            raise LLMConfigError(f"no model configured for role '{role}'")
        return [self.roles[role], *self.fallbacks.get(role, [])]

    def validate_references(self) -> None:
        for role in self.roles:
            for target in self.chain(role):
                if target.provider not in self.providers:
                    raise LLMConfigError(f"role '{role}' uses unknown provider '{target.provider}'")
        for name, roles in self.byok.items():
            if name not in self.providers or self.providers[name].local:
                raise LLMConfigError(f"byok provider '{name}' must be a hosted provider listed under providers")
            unknown = set(roles) - set(self.roles)
            if unknown:
                raise LLMConfigError(f"byok provider '{name}' sets unknown roles {sorted(unknown)}")
        for key, listed in (("byok_models", self.byok_models), ("byok_fallbacks", self.byok_fallbacks)):
            for name in listed:
                if name not in self.byok:
                    raise LLMConfigError(f"{key} lists '{name}', which is not under byok")

    def user_models(self, provider: str) -> list[str]:
        """Models a user may pick for `provider`: the configured list, else the models its roles already use."""
        return self.byok_models.get(provider) or sorted({t.model for t in self.byok.get(provider, {}).values()})

    def for_user_key(self, provider: str, model: str | None = None) -> LLMConfig:
        """Only the byok roles, all on `provider` (fallbacks too): a user key never reaches the server's keys.
        `model` (the user's pick, if allowed) replaces every role's model."""
        if provider not in self.byok:
            raise LLMConfigError(f"'{provider}' is not offered for your own key")
        if model is not None and model not in self.user_models(provider):
            model = None  # a pick that is no longer offered falls back to the defaults
        roles = {
            role: Target(provider=provider, model=model or t.model, temperature=t.temperature, json=t.json_output)
            for role, t in self.byok[provider].items()
        }
        # Same provider, same user key: other models to try when the chosen one is busy or refuses.
        fallbacks = {
            role: [
                Target(provider=provider, model=m, temperature=t.temperature, json=t.json_output)
                for m in self.byok_fallbacks.get(provider, [])
                if m != t.model
            ]
            for role, t in roles.items()
        }
        return LLMConfig(
            providers={provider: self.providers[provider]},
            roles=roles,
            fallbacks=fallbacks,
            limits=self.limits,
            policy=self.policy,
        )


def load_config(path: Path | None = None) -> LLMConfig:
    source = path or settings.llm_config
    local = source.with_name("llm.local.yaml")
    if path is None and settings.llm_local_override and source.name == "llm.yaml" and local.exists():
        source = local  # machine-specific override, gitignored
    if not source.exists():
        raise LLMConfigError(f"LLM config not found: {source}")
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if source != (path or settings.llm_config) and settings.llm_config.exists():
        # A machine override picks the server's models; own-key options it does not set come from llm.yaml.
        base = yaml.safe_load(settings.llm_config.read_text(encoding="utf-8")) or {}
        for key in ("byok", "byok_models"):
            if key not in raw and key in base:
                raw[key] = base[key]
        providers = raw.setdefault("providers", {})
        for name in raw.get("byok", {}):
            if name not in providers and name in base.get("providers", {}):
                providers[name] = base["providers"][name]
    try:
        config = LLMConfig.model_validate(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{source.name}: {exc}") from exc
    config.validate_references()
    return config
