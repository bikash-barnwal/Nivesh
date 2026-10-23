"""Performance metrics from an equity curve and a trade list."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import polars as pl

TRADING_DAYS = 252


def _clean(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def curve_metrics(dates: list, equity: np.ndarray) -> dict[str, float | None]:
    """CAGR, drawdown, Calmar, Sharpe and Sortino of an equity series (daily, risk-free rate 0)."""
    if len(equity) < 2 or equity[0] <= 0:
        return {k: None for k in ("cagr", "max_drawdown", "calmar", "sharpe", "sortino", "total_return", "years")}
    returns = np.diff(equity) / equity[:-1]
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9)
    total = equity[-1] / equity[0] - 1
    cagr = (equity[-1] / equity[0]) ** (1 / years) - 1 if equity[-1] > 0 else -1.0
    peak = np.maximum.accumulate(equity)
    max_dd = float(np.min(equity / peak - 1))
    std = returns.std(ddof=1) if len(returns) > 1 else 0.0
    downside = returns[returns < 0]
    dstd = math.sqrt(float(np.mean(downside**2))) if len(downside) else 0.0
    return {
        "cagr": _clean(cagr),
        "max_drawdown": _clean(max_dd),
        "calmar": _clean(cagr / abs(max_dd)) if max_dd < 0 else None,
        "sharpe": _clean(returns.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else None,
        "sortino": _clean(returns.mean() / dstd * math.sqrt(TRADING_DAYS)) if dstd > 0 else None,
        "total_return": _clean(total),
        "years": _clean(years),
    }


def trade_metrics(trades: pl.DataFrame) -> dict[str, Any]:
    if trades.is_empty():
        return {"trades": 0, "win_rate": None, "avg_win": None, "avg_loss": None, "profit_factor": None, "pnl": 0.0}
    wins = trades.filter(pl.col("pnl") > 0)
    losses = trades.filter(pl.col("pnl") <= 0)
    gross_win, gross_loss = float(wins["pnl"].sum()), -float(losses["pnl"].sum())
    return {
        "trades": trades.height,
        "win_rate": wins.height / trades.height,
        "avg_win": _clean(wins["ret"].mean()) if wins.height else None,
        "avg_loss": _clean(losses["ret"].mean()) if losses.height else None,
        "profit_factor": _clean(gross_win / gross_loss) if gross_loss > 0 else None,
        "pnl": float(trades["pnl"].sum()),
        "avg_days": _clean(trades["days"].mean()),
    }


def yearly_returns(dates: list, equity: np.ndarray) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    start_idx = 0
    for i in range(1, len(dates) + 1):
        if i == len(dates) or dates[i].year != dates[start_idx].year:
            end = i - 1
            base = equity[start_idx - 1] if start_idx > 0 else equity[0]
            out.append({"year": dates[start_idx].year, "return": _clean(equity[end] / base - 1)})
            start_idx = i
    return out


def regime_breakdown(trades: pl.DataFrame) -> list[dict[str, Any]]:
    if trades.is_empty():
        return []
    grouped = (
        trades.with_columns(pl.col("regime_at_entry").fill_null("Unknown"))
        .group_by("regime_at_entry")
        .agg(
            pl.len().alias("trades"),
            (pl.col("pnl") > 0).mean().alias("win_rate"),
            pl.col("ret").mean().alias("avg_ret"),
            pl.col("pnl").sum().alias("pnl"),
        )
        .sort("pnl", descending=True)
    )
    return grouped.rename({"regime_at_entry": "regime"}).to_dicts()


def summarize(dates: list, equity: np.ndarray, invested: np.ndarray, trades: pl.DataFrame) -> dict[str, Any]:
    return {
        **curve_metrics(dates, equity),
        **trade_metrics(trades),
        "exposure": _clean(float(np.mean(invested / equity))) if len(equity) else None,
        "final_equity": _clean(float(equity[-1])) if len(equity) else None,
        "by_year": yearly_returns(dates, equity) if len(equity) else [],
        "by_regime": regime_breakdown(trades),
    }
