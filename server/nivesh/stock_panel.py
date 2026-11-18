"""Stock-panel rules shared by the API and background jobs (lifecycle state, panel configuration)."""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml

from nivesh.analytics import rule_case
from nivesh.config import settings
from nivesh.data.engine import Engine

LIFECYCLE_HISTORY_SESSIONS = 260


def load_cfg() -> dict[str, Any]:
    return yaml.safe_load((settings.context_dir / "stock_panel.yaml").read_text(encoding="utf-8")) or {}


def feature_columns(engine: Engine) -> list[str]:
    return [c for c, _ in engine.schema("stock_features")]


def lifecycle(engine: Engine, symbol: str, state: date) -> dict[str, Any]:
    cfg = load_cfg()
    columns = feature_columns(engine)
    case = rule_case(cfg["lifecycle_rules"], "state", columns, cfg.get("lifecycle_default", "BASE"))
    rows = engine.query(
        f"SELECT date, CASE WHEN \"stage\" IS NULL THEN 'UNKNOWN' ELSE {case} END AS state FROM stock_features "
        "WHERE symbol = ? AND date <= ? ORDER BY date DESC LIMIT ?",
        [symbol, state, LIFECYCLE_HISTORY_SESSIONS],
    ).rows
    if not rows:
        return {"state": None, "dwell_sessions": 0, "since": None, "previous_state": None}
    current = rows[0][1]
    dwell = 0
    for _, s in rows:
        if s != current:
            break
        dwell += 1
    previous = next((s for _, s in rows[dwell:] if s != "UNKNOWN"), None) if dwell < len(rows) else None
    since = rows[dwell - 1][0]
    return {
        "state": current,
        "dwell_sessions": dwell,
        "dwell_is_lower_bound": dwell == len(rows),
        "since": since,
        "previous_state": previous,
    }
