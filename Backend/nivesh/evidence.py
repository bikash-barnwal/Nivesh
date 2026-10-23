"""Evidence objects used by deterministic APIs and future Copilot answers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Evidence:
    source: str
    as_of: date | None
    query_id: str
    calculation: str
    confidence: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
