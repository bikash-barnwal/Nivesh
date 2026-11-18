"""Look-ahead protection: refuse questions that need data beyond the latest loaded session."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_FUTURE = re.compile(
    r"\b(tomorrow|next\s+(?:session|trading\s+day|week|month|quarter|year)|"
    r"(?:will|going to)\b[^.?]*\b(?:close|trade|open|hit|reach|rise|fall)\b)",
    re.IGNORECASE,
)
_NOW = re.compile(r"\b(today|now|current(?:ly)?|latest|right now|this morning)\b", re.IGNORECASE)


@dataclass
class TemporalCheck:
    refuse: bool = False
    reason: str = ""
    notes: list[str] = field(default_factory=list)


def check(question: str, data_through: date | None) -> TemporalCheck:
    if data_through is None:
        return TemporalCheck(refuse=True, reason="No market data is loaded yet, so there is nothing to answer from.")
    for y, m, d in _ISO.findall(question):
        try:
            asked = date(int(y), int(m), int(d))
        except ValueError:
            continue
        if asked > data_through:
            return TemporalCheck(
                refuse=True,
                reason=f"The data ends on {data_through}, so I cannot answer about {asked} (after the last session).",
            )
    match = _FUTURE.search(question)
    if match:
        return TemporalCheck(
            refuse=True,
            reason=f"That asks about the future ('{match.group(0).strip()}'). Data ends {data_through}; no forecasts.",
        )
    notes = []
    if _NOW.search(question):
        notes.append(f"'today/latest' is interpreted as the last loaded session, {data_through}.")
    return TemporalCheck(notes=notes)
