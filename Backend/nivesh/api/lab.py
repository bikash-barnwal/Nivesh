"""Strategy lab endpoints: strategies, backtest runs, leaderboard, equity curves and trade diagnostics."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

import polars as pl
import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nivesh.api.common import Envelope, envelope
from nivesh.backtest import store
from nivesh.backtest.spec import get_strategy, list_strategies, strategies_dir
from nivesh.config import settings
from nivesh.data.engine import get_engine

router = APIRouter(prefix="/api", tags=["strategy-lab"])

MAX_EQUITY_POINTS = 1500
TRADE_LIST_FIELDS = [
    "trade_id", "symbol", "sector", "entry_date", "exit_date", "days", "ret", "pnl", "exit_reason",
    "regime_at_entry", "rank",
]  # fmt: skip
WINDOW_BEFORE, WINDOW_AFTER = 40, 25


class BacktestRequest(BaseModel):
    strategy_id: str
    start: date | None = None
    end: date | None = None
    gates: bool = True


def _env(data: Any, source: str, calculation: str, as_of: date | None = None, **extra: Any) -> dict[str, Any]:
    engine = get_engine()
    return envelope(
        data, as_of=as_of or engine.data_through(), engine=engine, source=source, calculation=calculation, **extra
    )


def _strategy_or_404(strategy_id: str):
    try:
        return get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown strategy '{strategy_id}'") from exc


def _run_or_404(run_id: str) -> dict[str, Any]:
    try:
        return store.load_run(run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'") from exc


@router.get("/strategies", response_model=Envelope)
def strategies() -> dict[str, Any]:
    latest = {r["strategy_id"]: r for r in store.latest_per_strategy()}
    rows = []
    for s in list_strategies():
        rows.append(
            {
                "id": s.id,
                "name": s.name,
                "version": s.version,
                "label": s.label or s.id,
                "family": s.family,
                "status": s.status,
                "entry": s.entry.when,
                "rank_by": s.entry.rank_by,
                "max_positions": s.portfolio.max_positions,
                "hard_stop_pct": s.exit.hard_stop_pct,
                "latest_run": latest.get(s.id),
            }
        )
    return _env(rows, "context/strategies", "configured strategies with their latest run")


@router.get("/strategies/{strategy_id}", response_model=Envelope)
def strategy_detail(strategy_id: str) -> dict[str, Any]:
    s = _strategy_or_404(strategy_id)
    path = next(
        p
        for p in strategies_dir().glob("*.yaml")
        if yaml.safe_load(p.read_text(encoding="utf-8")).get("name") == s.name
        and str(yaml.safe_load(p.read_text(encoding="utf-8")).get("version")) == s.version
    )
    return _env(
        {"spec": s.model_dump(), "yaml": path.read_text(encoding="utf-8")}, "context/strategies", "strategy definition"
    )


@router.post("/backtests", response_model=Envelope)
def start_backtest(request: BacktestRequest) -> dict[str, Any]:
    strategy = _strategy_or_404(request.strategy_id)
    job_id = store.submit(strategy, request.start, request.end, request.gates)
    return _env({"job_id": job_id, "status": "running"}, "backtest", "queued")


@router.get("/backtests/jobs/{job_id}", response_model=Envelope)
def backtest_job(job_id: str) -> dict[str, Any]:
    job = store.job_status(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return _env(job, "backtest", "job status")


@router.get("/backtests", response_model=Envelope)
def backtests() -> dict[str, Any]:
    return _env(store.list_runs(), "data/backtests", "all saved runs, newest first")


@router.get("/leaderboard", response_model=Envelope)
def leaderboard() -> dict[str, Any]:
    rows = store.latest_per_strategy()
    rows.sort(key=lambda r: (r["calmar"] is None, -(r["calmar"] or 0)))
    return _env(rows, "data/backtests", "latest run per strategy, ranked by Calmar")


@router.get("/backtests/{run_id}", response_model=Envelope)
def backtest_run(run_id: str) -> dict[str, Any]:
    loaded = _run_or_404(run_id)
    return _env(loaded, "data/backtests", "saved run", as_of=date.fromisoformat(loaded["run"]["end"]))


@router.get("/backtests/{run_id}/equity", response_model=Envelope)
def backtest_equity(run_id: str) -> dict[str, Any]:
    loaded = _run_or_404(run_id)
    frame = store.load_equity(run_id)
    if frame.height > MAX_EQUITY_POINTS:
        step = -(-frame.height // MAX_EQUITY_POINTS)
        frame = frame.gather_every(step)  # keeps shape for charting; metrics come from the full series
    benchmark: list[dict[str, Any]] = []
    engine = get_engine()
    if "index_daily" in engine.views() and frame.height:
        lo, hi = frame["date"].min(), frame["date"].max()
        rows = engine.query(
            "SELECT date, close FROM index_daily WHERE index_name = ? AND date BETWEEN ? AND ? ORDER BY date",
            ["Nifty 500", lo, hi],
        ).rows
        if rows:
            base = loaded["run"]["strategy"]["portfolio"]["initial_capital"]
            benchmark = [{"date": d, "value": base * c / rows[0][1]} for d, c in rows]
    return _env(
        {"curve": frame.to_dicts(), "benchmark": benchmark},
        "data/backtests",
        "daily equity, drawdown and a Nifty 500 overlay rebased to the starting capital",
        as_of=date.fromisoformat(loaded["run"]["end"]),
    )


@router.get("/backtests/{run_id}/trades", response_model=Envelope)
def backtest_trades(
    run_id: str,
    year: Annotated[int | None, Query()] = None,
    exit_reason: Annotated[str | None, Query()] = None,
    sector: Annotated[str | None, Query()] = None,
    sort: Annotated[Literal["date", "pnl"], Query()] = "date",
    order: Annotated[Literal["asc", "desc"], Query()] = "desc",
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    loaded = _run_or_404(run_id)
    trades = store.load_trades(run_id)
    if year is not None:
        trades = trades.filter(pl.col("entry_date").dt.year() == year)
    if exit_reason:
        trades = trades.filter(pl.col("exit_reason") == exit_reason)
    if sector:
        trades = trades.filter(pl.col("sector") == sector)
    key = "entry_date" if sort == "date" else "pnl"
    trades = trades.sort([key, "trade_id"], descending=order == "desc")
    total = trades.height
    page = trades.select(TRADE_LIST_FIELDS).slice(offset, limit)
    return _env(
        page.to_dicts(),
        "data/backtests",
        "trades for the run",
        as_of=date.fromisoformat(loaded["run"]["end"]),
        total=total,
        offset=offset,
    )


def _explain(trade: dict[str, Any], strategy: dict[str, Any], signal_date: date | None) -> dict[str, str]:
    ex, reason = strategy["exit"], trade["exit_reason"]
    radar = (
        f"Selected by the rule '{strategy['entry']['when']}' on the close of {signal_date or 'the prior session'}, "
        f"ranked #{trade['rank']} by {', '.join(strategy['entry']['rank_by'])}. "
        f"Sector: {trade['sector']}. Market regime at entry: {trade['regime_at_entry'] or 'unknown'}. "
        f"Filled at the next open: Rs {trade['entry_price']:.2f}; initial hard stop Rs {trade['initial_stop']:.2f} "
        f"({ex['hard_stop_pct']}% below entry)."
    )
    how = {
        "stop": f"Stopped out intraday at the stop price (Rs {trade['final_stop']:.2f}).",
        "stop-gap": f"Gapped through the stop; filled at the open, Rs {trade['exit_price']:.2f}.",
        "trail": f"Fell below SMA{ex['trail_sma']} the session before; exited at the {trade['exit_date']} open.",
        "time": f"Time stop: held for the maximum {ex['time_stop_days']} sessions.",
        "end-of-test": "Still open when the test ended; closed at the last available price.",
    }.get(reason, reason)
    extra = " Breakeven stop was activated." if trade["be_activated"] else ""
    return {
        "radar": radar,
        "exit": f"{how}{extra} Final P&L {trade['ret'] * 100:+.1f}% over {trade['days']} sessions.",
    }


@router.get("/backtests/{run_id}/trades/{trade_id}", response_model=Envelope)
def backtest_trade(run_id: str, trade_id: int) -> dict[str, Any]:
    loaded = _run_or_404(run_id)
    trades = store.load_trades(run_id).filter(pl.col("trade_id") == trade_id)
    if trades.is_empty():
        raise HTTPException(status_code=404, detail=f"unknown trade {trade_id}")
    trade = trades.row(0, named=True)
    engine = get_engine()
    symbol = trade["symbol"]

    window = engine.query(
        "WITH w AS (SELECT d.date, d.open, d.high, d.low, d.close, d.volume, f.sma20, f.sma50, f.atr14_pct "
        "FROM stock_daily d LEFT JOIN stock_features f ON f.symbol = d.symbol AND f.date = d.date "
        "WHERE d.symbol = ? ORDER BY d.date), n AS (SELECT *, row_number() OVER (ORDER BY date) AS rn FROM w), "
        "e AS (SELECT min(rn) AS a FROM n WHERE date >= ?), x AS (SELECT max(rn) AS b FROM n WHERE date <= ?) "
        "SELECT n.* EXCLUDE (rn) FROM n, e, x WHERE n.rn BETWEEN e.a - ? AND x.b + ? ORDER BY n.date",
        [symbol, trade["entry_date"], trade["exit_date"], WINDOW_BEFORE, WINDOW_AFTER],
    ).records()

    regimes: list[dict[str, Any]] = []
    if "market_regime" in engine.views() and window:
        regimes = engine.query(
            "SELECT date, regime FROM market_regime WHERE date BETWEEN ? AND ? ORDER BY date",
            [window[0]["date"], window[-1]["date"]],
        ).records()

    signal_date = None
    if "stock_features" in engine.views():
        row = engine.query("SELECT max(date) FROM stock_features WHERE date < ?", [trade["entry_date"]]).rows
        signal_date = row[0][0] if row else None
    data = {
        "trade": trade,
        "window": window,
        "regimes": regimes,
        "explanation": _explain(trade, loaded["run"]["strategy"], signal_date),
    }
    return _env(data, "data/backtests,stock_daily", "trade diagnostics", as_of=trade["exit_date"])


__all__ = ["router", "settings"]
