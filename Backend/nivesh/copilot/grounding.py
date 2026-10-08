"""Numeric grounding: every number in a model's wording must come from the evidence (or the question).

Models are only allowed to *format* evidence. This catches the failure we care about most: a plausible number that was
never computed. Numbers glued to letters ("52-week", "SMA20", "RVol20") are labels, not claims, and are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

_LOOSE = re.compile(r"\d[\d,]*(?:\.\d+)?")  # any digits, even inside labels such as "20-session"
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.\-])-?\d[\d,]*(?:\.\d+)?(?![A-Za-z]|-[A-Za-z]|\d)")


@dataclass
class Grounding:
    ok: bool
    ungrounded: list[str] = field(default_factory=list)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def numbers_in(text: str) -> list[tuple[str, float]]:
    out = []
    for raw in _NUMBER.findall(text):
        cleaned = raw.replace(",", "")
        try:
            out.append((raw, float(cleaned)))
        except ValueError:
            continue
    return out


def _walk(value: Any, sink: set[float]) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        sink.add(float(value))
    elif isinstance(value, (date, datetime)):
        sink.update({float(value.year), float(value.month), float(value.day)})
    elif isinstance(value, str):
        for raw in _LOOSE.findall(value):
            try:
                sink.add(float(raw.replace(",", "")))
            except ValueError:
                continue
        for y, m, d in re.findall(r"(\d{4})-(\d{2})-(\d{2})", value):
            sink.update({float(y), float(m), float(d), float(int(m)), float(int(d))})
    elif isinstance(value, dict):
        for v in value.values():
            _walk(v, sink)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, sink)


def evidence_numbers(evidence: dict[str, Any], extra_text: str = "") -> set[float]:
    sink: set[float] = set()
    _walk(evidence.get("rows"), sink)
    _walk(evidence.get("passages"), sink)
    for key in ("data_through", "row_count", "sql", "question"):
        _walk(evidence.get(key), sink)
    _walk(extra_text, sink)
    row_count = evidence.get("row_count") or len(evidence.get("rows") or [])
    sink.update(float(i) for i in range(1, min(int(row_count), 50) + 1))  # list numbering / "top 5" style counts
    return sink


def _matches(token: str, value: float, candidates: set[float]) -> bool:
    """Sign-insensitive: "fell 1.08%" is a faithful rendering of -0.0108. Direction words cannot be verified here."""
    tolerance = 0.5 * 10 ** (-_decimals(token)) + 1e-9
    target = abs(value)
    for c in candidates:
        for scaled in (abs(c), abs(c) * 100):  # a fraction shown as a percentage
            if abs(target - scaled) <= tolerance + scaled * 1e-9:
                return True
    return False


def check(answer: str, evidence: dict[str, Any], question: str = "") -> Grounding:
    candidates = evidence_numbers(evidence, question)
    bad = [raw for raw, value in numbers_in(answer) if not _matches(raw, value, candidates)]
    return Grounding(ok=not bad, ungrounded=bad)
