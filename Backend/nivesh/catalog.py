"""Validated semantic catalog loader for Group B definitions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from nivesh.config import settings

REQUIRED_KEYS = {"metric", "version", "definition", "source_view"}


def load_catalog(context_dir: Path | None = None) -> dict[str, Any]:
    root = context_dir or settings.context_dir
    metrics: dict[str, Any] = {}
    metric_dir = root / "metrics"
    if metric_dir.exists():
        for path in sorted(metric_dir.glob("*.yaml")):
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            missing = REQUIRED_KEYS - set(value)
            if missing:
                raise ValueError(f"{path}: missing required keys: {sorted(missing)}")
            name = value["metric"]
            if name in metrics:
                raise ValueError(f"duplicate metric: {name}")
            metrics[name] = value
    return {"metrics": metrics, "metric_count": len(metrics)}


def catalog_summary(context_dir: Path | None = None) -> dict[str, Any]:
    catalog = load_catalog(context_dir)
    return {"metric_count": catalog["metric_count"], "metrics": sorted(catalog["metrics"])}
