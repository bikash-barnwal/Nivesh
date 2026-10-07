"""Append-only local audit events, ready for a durable multi-user audit store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from nivesh.config import settings


def record(action: str, payload: dict[str, Any] | None = None) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    event = {"timestamp": datetime.now(UTC).isoformat(), "user_id": "local", "action": action, "payload": payload or {}}
    with (settings.logs_dir / "audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, default=str) + "\n")
