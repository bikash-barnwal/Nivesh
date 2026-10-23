"""A scripted stand-in for the model layer: no network, fully deterministic."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from nivesh.copilot.client import ChatResult, Message

Item = dict[str, Any] | str | Exception | Callable[[list[Message]], dict[str, Any] | str]


class FakeLLM:
    """`script` maps a role to a list of replies consumed in order (the last one repeats once the list runs dry)."""

    def __init__(self, script: dict[str, list[Item]], delay_s: float = 0.0) -> None:
        self.script = {role: list(items) for role, items in script.items()}
        self.calls: list[tuple[str, list[Message]]] = []
        self.delay_s = delay_s

    def chat(
        self, role: str, messages: list[Message], *, json_mode: bool | None = None, private: bool = False
    ) -> ChatResult:
        self.calls.append((role, messages))
        if self.delay_s:
            time.sleep(self.delay_s)
        items = self.script.get(role)
        if not items:
            raise AssertionError(f"unexpected call for role '{role}'")
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(messages)
        data = item if isinstance(item, dict) else None
        text = json.dumps(item) if isinstance(item, dict) else str(item)
        return ChatResult(text=text, data=data, provider="fake", model=role, latency_s=0.0)

    @property
    def roles_called(self) -> list[str]:
        return [role for role, _ in self.calls]
