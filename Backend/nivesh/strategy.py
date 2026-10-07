"""Small deterministic strategy DSL and backtest foundation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml


@dataclass(frozen=True)
class Strategy:
    name: str
    version: int
    entry: str
    exit: str
    position_size: float = 1.0


def load_strategy(path: str) -> Strategy:
    raw = yaml.safe_load(open(path, encoding="utf-8")) or {}
    required = {"name", "version", "entry", "exit"}
    missing = required - set(raw)
    if missing:
        raise ValueError(f"missing strategy keys: {sorted(missing)}")
    if not 0 < float(raw.get("position_size", 1)) <= 1:
        raise ValueError("position_size must be in (0, 1]")
    return Strategy(raw["name"], int(raw["version"]), raw["entry"], raw["exit"], float(raw.get("position_size", 1)))


def backtest(rows: list[dict[str, Any]], strategy: Strategy) -> dict[str, Any]:
    """Run a conservative boolean-feature backtest; expressions are supplied by the caller as booleans."""
    ordered = sorted(rows, key=lambda r: (r.get("symbol", ""), r.get("date", "")))
    trades = []
    open_trade = None
    for row in ordered:
        if open_trade is None and bool(row.get(strategy.entry)):
            open_trade = {"symbol": row["symbol"], "entry_date": row["date"], "entry": row["close"]}
        elif open_trade is not None and bool(row.get(strategy.exit)):
            exit_price = row["close"]
            entry = open_trade["entry"]
            trades.append(
                {**open_trade, "exit_date": row["date"], "exit": exit_price, "return": exit_price / entry - 1}
            )
            open_trade = None
    returns = [t["return"] for t in trades]
    return {
        "strategy": strategy.name,
        "version": strategy.version,
        "trades": trades,
        "trade_count": len(trades),
        "total_return": sum(returns),
        "win_rate": sum(r > 0 for r in returns) / len(returns) if returns else None,
    }
