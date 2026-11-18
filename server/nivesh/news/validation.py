"""Human-label validation for extracted news event types."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path
from typing import Any

from nivesh.news.schema import EVENT_TYPES

NONE = "none"


def score_labels(path: Path, *, minimum_labels: int = 1) -> dict[str, Any]:
    rows = list(csv.DictReader(path.open(encoding="utf-8", newline="")))
    scored = [row for row in rows if row.get("human_event_type", "").strip()]
    by_type: dict[str, dict[str, int | float | bool | None]] = {}
    for event_type in (*EVENT_TYPES, NONE):
        predicted = [row for row in scored if (row.get("model_event_type") or NONE) == event_type]
        correct = sum(1 for row in predicted if row["human_event_type"].strip() == event_type)
        precision = correct / len(predicted) if predicted else None
        by_type[event_type] = {
            "labelled": sum(1 for row in scored if row["human_event_type"].strip() == event_type),
            "predicted": len(predicted),
            "correct": correct,
            "precision": precision,
            "passed": precision is not None and len(predicted) >= minimum_labels and precision >= 0.90,
        }
    return {
        "sample_size": len(rows),
        "labelled": len(scored),
        "unlabelled": len(rows) - len(scored),
        "event_types": by_type,
        "all_clear": bool(scored) and all(v["passed"] for v in by_type.values() if v["predicted"]),
        "label_counts": dict(Counter(row["human_event_type"].strip() for row in scored)),
    }
