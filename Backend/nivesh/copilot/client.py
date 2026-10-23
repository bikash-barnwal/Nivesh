"""Provider-agnostic chat client with ordered fallbacks, JSON repair and a call log (no prompt text is logged)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from nivesh.config import settings
from nivesh.copilot.config import LLMConfig, LLMConfigError, Target, load_config

Message = dict[str, str]


class LLMError(RuntimeError):
    """Every model in the chain failed."""


class PrivacyError(LLMConfigError):
    """Private data was about to be sent to a hosted model."""


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    latency_s: float
    data: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: list[str] = field(default_factory=list)  # failures before the one that worked

    @property
    def model_id(self) -> str:
        return f"{self.provider}/{self.model}"


class LLM(Protocol):
    """What the copilot needs from a model layer. Tests substitute a scripted implementation."""

    def chat(
        self, role: str, messages: list[Message], *, json_mode: bool | None = None, private: bool = False
    ) -> ChatResult: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply (handles code fences and chatty prefixes)."""
    cleaned = _FENCE.sub("", text.strip())
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object in the model reply")
    depth, in_string, escape = 0, False, False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if in_string:
            escape = (ch == "\\") and not escape
            if ch == '"' and not escape:
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(cleaned[start : i + 1])
                if not isinstance(value, dict):
                    raise ValueError("expected a JSON object")
                return value
    raise ValueError("unterminated JSON object in the model reply")


class OpenAICompatibleLLM:
    """Calls the configured chain for a role: primary model, then fallbacks, using the `openai` SDK."""

    def __init__(self, config: LLMConfig | None = None, *, client_factory: Any = None, sleep: Any = time.sleep) -> None:
        self.config = config or load_config()
        self._factory = client_factory or self._default_factory
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._sleep = sleep
        self._calls_today = self._count_calls_today()

    # -- plumbing -------------------------------------------------------------------------------------------------
    def _default_factory(self, name: str):
        from openai import OpenAI

        provider = self.config.providers[name]
        return OpenAI(
            api_key=provider.api_key(name),
            base_url=provider.base_url,
            default_headers=provider.headers or None,
            timeout=self.config.limits.timeout_s,
            max_retries=0,
        )

    def _client(self, name: str):
        with self._lock:
            if name not in self._clients:
                self._clients[name] = self._factory(name)
            return self._clients[name]

    @staticmethod
    def _log_path():
        return settings.logs_dir / "llm_calls.jsonl"

    def _count_calls_today(self) -> int:
        path = self._log_path()
        if not path.exists():
            return 0
        today = datetime.now(UTC).date().isoformat()
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if f'"ts": "{today}' in line)

    def _log(self, record: dict[str, Any]) -> None:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        with self._log_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **record}) + "\n")

    # -- API ------------------------------------------------------------------------------------------------------
    def chat(
        self, role: str, messages: list[Message], *, json_mode: bool | None = None, private: bool = False
    ) -> ChatResult:
        chain = self.config.chain(role)
        wants_json = chain[0].json_output if json_mode is None else json_mode
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()[:16]
        failures: list[str] = []
        for target in chain:
            provider = self.config.providers[target.provider]
            if private and not provider.local:
                failures.append(f"{target.provider}: refused (private data never goes to hosted models)")
                continue
            if self._calls_today >= self.config.limits.max_calls_per_day:
                raise LLMError(f"daily call limit reached ({self.config.limits.max_calls_per_day})")
            started = time.perf_counter()
            try:
                result = self._call(target, messages, wants_json)
            except LLMConfigError:
                raise
            except Exception as exc:  # any provider error moves on to the next model in the chain
                failures.append(f"{target.provider}/{target.model}: {type(exc).__name__}: {str(exc)[:200]}")
                elapsed = round(time.perf_counter() - started, 3)
                self._log(
                    {"role": role, "provider": target.provider, "model": target.model, "ok": False,
                     "latency_s": elapsed, "error": failures[-1], "prompt": digest}
                )  # fmt: skip
                continue
            self._calls_today += 1
            result.attempts = failures
            result.latency_s = round(time.perf_counter() - started, 3)
            self._log(
                {
                    "role": role,
                    "provider": target.provider,
                    "model": target.model,
                    "ok": True,
                    "latency_s": result.latency_s,
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "prompt": digest,
                }
            )
            return result
        raise LLMError(f"all models failed for role '{role}': " + " | ".join(failures))

    def _call(self, target: Target, messages: list[Message], wants_json: bool) -> ChatResult:
        client = self._client(target.provider)
        params: dict[str, Any] = {"model": target.model, "messages": messages, "temperature": target.temperature}
        if wants_json:
            params["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**params)
        except Exception as exc:
            # Some providers reject response_format; retry once without it and rely on JSON extraction.
            if wants_json and "response_format" in str(exc).lower():
                params.pop("response_format")
                response = client.chat.completions.create(**params)
            else:
                raise
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty reply")
        data = parse_json(text) if wants_json else None
        usage = getattr(response, "usage", None)
        return ChatResult(
            text=text,
            data=data,
            provider=target.provider,
            model=target.model,
            latency_s=0.0,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
        )

    def ping(self) -> list[dict[str, Any]]:
        """One tiny call per configured role, for `nivesh llm ping`."""
        report = []
        for role in self.config.roles:
            try:
                asked_json = self.config.roles[role].json_output
                prompt = 'Reply with the JSON {"ok": true}.' if asked_json else "Reply with the single word: ok"
                res = self.chat(role, [{"role": "user", "content": prompt}])
                report.append(
                    {
                        "role": role,
                        "ok": True,
                        "model": res.model_id,
                        "latency_s": res.latency_s,
                        "fell_back": bool(res.attempts),
                    }
                )
            except Exception as exc:
                report.append({"role": role, "ok": False, "error": str(exc)[:300]})
        return report
