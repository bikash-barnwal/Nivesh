"""Daily-bar portfolio backtester.

Timing rules (no look-ahead):
  * a signal is evaluated on the close of session t and filled at the OPEN of session t+1 (plus slippage);
  * a hard stop is checked against each bar's low; a gap through the stop fills at the open;
  * a trailing exit (close below SMA) is a close-of-day signal that fills at the next open;
  * a locked bar (high == low) cannot be traded.
Costs are charged per side; slippage worsens each fill price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nivesh.backtest import metrics as m
from nivesh.backtest.spec import Strategy
from nivesh.config import settings
from nivesh.data.engine import Engine

QUERY_TIMEOUT_S = 900.0
BAR_FIELDS = ("open", "high", "low", "close", "sma20", "sma50")
UNCLASSIFIED = "Unclassified"


class BacktestError(RuntimeError):
    pass


@dataclass
class Panel:
    """Everything the simulator needs that does not depend on exit/portfolio/cost parameters."""

    dates: list[date]
    signals: dict[int, list[dict[str, Any]]]  # session index -> candidates, best rank first
    bars: dict[str, dict[str, np.ndarray]]  # symbol -> field -> array over sessions (NaN = no bar)
    entry_rule: str = ""
    rank_by: list[str] = field(default_factory=list)


@dataclass
class Result:
    strategy: Strategy
    dates: list[date]
    equity: np.ndarray
    cash: np.ndarray
    invested: np.ndarray
    trades: pl.DataFrame
    metrics: dict[str, Any]
    data_through: date | None = None
    open_positions: list[dict[str, Any]] = field(default_factory=list)  # only when close_open=False
    next_orders: dict[str, Any] = field(default_factory=dict)


def load_panel(
    strategy: Strategy, parquet_dir: Path | None = None, start: date | None = None, end: date | None = None
) -> Panel:
    engine = Engine(parquet_dir or settings.parquet_dir)
    views = set(engine.views())
    if not {"stock_features", "stock_daily"} <= views:
        raise BacktestError("stock_features and stock_daily must be built before backtesting")
    lo, hi = start or date(1900, 1, 1), end or date(2999, 1, 1)

    columns = [c for c, _ in engine.schema("stock_features")]
    frags = strategy.compile(columns)
    sector_sql = "m.sector" if "sector_map" in views else "NULL::VARCHAR AS sector"
    regime_sql = "r.regime" if "market_regime" in views else "NULL::VARCHAR AS regime"
    joins = ("LEFT JOIN sector_map m ON m.symbol = f.symbol " if "sector_map" in views else "") + (
        "LEFT JOIN market_regime r ON r.date = f.date" if "market_regime" in views else ""
    )
    blocked = ", ".join("'" + b.replace("'", "''") + "'" for b in strategy.regime_block)
    regime_clause = f"AND coalesce(regime, '') NOT IN ({blocked})" if blocked and "market_regime" in views else ""

    dates = [
        r[0]
        for r in engine.query(
            "SELECT DISTINCT date FROM stock_features WHERE date BETWEEN ? AND ? ORDER BY date", [lo, hi]
        ).rows
    ]
    if not dates:
        raise BacktestError("no sessions in the requested range")
    index = {d: i for i, d in enumerate(dates)}

    sig_rows = engine.query(
        f"""
        WITH f0 AS (SELECT * FROM stock_features WHERE date BETWEEN ? AND ?),
        u AS (SELECT f.*, {sector_sql}, {regime_sql} FROM f0 f {joins})
        SELECT symbol, date, sector, regime, row_number() OVER (PARTITION BY date ORDER BY {frags["order"]}) AS rank
        FROM u WHERE ({frags["universe"]}) AND ({frags["entry"]}) {regime_clause}
        ORDER BY date, rank
        """,
        [lo, hi],
        timeout_s=QUERY_TIMEOUT_S,
    )
    signals: dict[int, list[dict[str, Any]]] = {}
    for symbol, d, sector, regime, rank in sig_rows.rows:
        signals.setdefault(index[d], []).append({"symbol": symbol, "sector": sector, "regime": regime, "rank": rank})

    symbols = sorted({s["symbol"] for rows in signals.values() for s in rows})
    bars: dict[str, dict[str, np.ndarray]] = {}
    if symbols:
        rows = engine.query(
            "SELECT d.symbol, d.date, d.open, d.high, d.low, d.close, f.sma20, f.sma50 FROM stock_daily d "
            "JOIN stock_features f ON f.symbol = d.symbol AND f.date = d.date "
            "WHERE d.symbol IN (SELECT unnest(?)) AND d.date BETWEEN ? AND ?",
            [symbols, lo, hi],
            timeout_s=QUERY_TIMEOUT_S,
        ).rows
        for symbol, d, *values in rows:
            arrays = bars.setdefault(symbol, {f: np.full(len(dates), np.nan) for f in BAR_FIELDS})
            i = index.get(d)
            if i is None:
                continue
            for name, value in zip(BAR_FIELDS, values, strict=True):
                if value is not None:
                    arrays[name][i] = value
    engine.close()
    return Panel(dates, signals, bars, strategy.entry.when, list(strategy.entry.rank_by))


@dataclass
class _Pos:
    symbol: str
    sector: str
    entry_idx: int
    entry_date: date
    entry_fill: float
    shares: int
    cost: float
    stop: float
    initial_stop: float
    regime: str | None
    rank: int
    last_close: float
    peak: float
    mae: float = 0.0
    mae_day: int = 0
    mfe: float = 0.0
    mfe_day: int = 0
    days_held: int = 0
    be_active: bool = False
    pending_exit: bool = False


def simulate(strategy: Strategy, panel: Panel, *, close_open: bool = True) -> Result:
    p, ex, costs = strategy.portfolio, strategy.exit, strategy.costs
    n = len(panel.dates)
    cash = p.initial_capital
    positions: dict[str, _Pos] = {}
    trades: list[dict[str, Any]] = []
    equity, cash_series, invested = np.zeros(n), np.zeros(n), np.zeros(n)
    stop_dist = ex.hard_stop_pct / 100
    fee, slip = costs.fee_rate, costs.slippage

    def close_position(pos: _Pos, price: float, i: int, reason: str) -> None:
        nonlocal cash
        fill = price * (1 - slip)
        proceeds = pos.shares * fill * (1 - fee)
        cash += proceeds
        pnl = proceeds - pos.cost
        trades.append(
            {
                "symbol": pos.symbol,
                "sector": pos.sector,
                "entry_date": pos.entry_date,
                "entry_price": pos.entry_fill,
                "exit_date": panel.dates[i],
                "exit_price": fill,
                "shares": pos.shares,
                "pnl": pnl,
                "ret": pnl / pos.cost,
                "days": i - pos.entry_idx,
                "exit_reason": reason,
                "regime_at_entry": pos.regime,
                "rank": pos.rank,
                "mae": pos.mae,
                "mae_day": pos.mae_day,
                "mfe": pos.mfe,
                "mfe_day": pos.mfe_day,
                "peak_price": pos.peak,
                "giveback": 1 - fill / pos.peak if pos.peak > 0 else 0.0,
                "initial_stop": pos.initial_stop,
                "final_stop": pos.stop,
                "be_activated": pos.be_active,
                "selection_reason": f"rule '{panel.entry_rule}' true on the prior close; ranked #{pos.rank} by "
                + ", ".join(panel.rank_by),
            }
        )
        del positions[pos.symbol]

    def process_bar(pos: _Pos, i: int, entry_day: bool) -> None:
        b = panel.bars[pos.symbol]
        o, h, lo, c = b["open"][i], b["high"][i], b["low"][i], b["close"][i]
        if math.isnan(c):
            return  # no trading in this stock today; hold
        pos.days_held = i - pos.entry_idx
        pos.peak = max(pos.peak, h)
        up, down = h / pos.entry_fill - 1, lo / pos.entry_fill - 1
        if up > pos.mfe:
            pos.mfe, pos.mfe_day = up, pos.days_held
        if down < pos.mae:
            pos.mae, pos.mae_day = down, pos.days_held
        pos.last_close = c
        if pos.pending_exit and not entry_day:
            close_position(pos, o, i, "trail")
            return
        if not entry_day and o <= pos.stop:
            close_position(pos, o, i, "stop-gap")
            return
        if lo <= pos.stop:
            close_position(pos, pos.stop, i, "stop")
            return
        if (
            ex.breakeven_trigger_pct
            and not pos.be_active
            and h >= pos.entry_fill * (1 + ex.breakeven_trigger_pct / 100)
        ):
            pos.stop, pos.be_active = max(pos.stop, pos.entry_fill), True
        if ex.trail_sma:
            sma = b[f"sma{ex.trail_sma}"][i]
            if not math.isnan(sma) and c < sma:
                pos.pending_exit = True
        if ex.time_stop_days and pos.days_held >= ex.time_stop_days:
            close_position(pos, c, i, "time")

    for i in range(n):
        # 1) entries: yesterday's ranked signals, filled at today's open
        if i > 0 and len(positions) < p.max_positions:
            equity_now = cash + sum(x.shares * x.last_close for x in positions.values())
            added = 0
            for sig in panel.signals.get(i - 1, []):
                if len(positions) >= p.max_positions or added >= strategy.entry.max_new_per_day:
                    break
                sym = sig["symbol"]
                if sym in positions or sym not in panel.bars:
                    continue
                bar = panel.bars[sym]
                o, h, lo = bar["open"][i], bar["high"][i], bar["low"][i]
                if math.isnan(o) or o <= 0 or h == lo:  # no bar, or locked at a circuit
                    continue
                fill = o * (1 + slip)
                shares = math.floor(
                    min(
                        equity_now * p.risk_per_trade_pct / 100 / (fill * stop_dist),
                        equity_now * p.max_position_pct / 100 / fill,
                        cash / (fill * (1 + fee)),
                    )
                )
                if shares < 1:
                    continue
                sector = sig["sector"] or UNCLASSIFIED
                if sector != UNCLASSIFIED:
                    held = sum(x.shares * x.last_close for x in positions.values() if x.sector == sector)
                    if held + shares * fill > p.max_sector_pct / 100 * equity_now:
                        continue
                cost = shares * fill * (1 + fee)
                cash -= cost
                stop = fill * (1 - stop_dist)
                positions[sym] = _Pos(
                    sym, sector, i, panel.dates[i], fill, shares, cost, stop, stop, sig["regime"], sig["rank"], o, h
                )
                added += 1
                process_bar(positions[sym], i, entry_day=True)
        # 2) exits and bookkeeping for positions entered earlier
        for sym in list(positions):
            pos = positions[sym]
            if pos.entry_idx != i:
                process_bar(pos, i, entry_day=False)
        invested[i] = sum(x.shares * x.last_close for x in positions.values())
        cash_series[i] = cash
        equity[i] = cash + invested[i]

    open_positions: list[dict[str, Any]] = []
    next_orders: dict[str, Any] = {}
    if close_open:
        for pos in list(positions.values()):  # backtests close whatever is open at the end of the data
            last = max(i for i in range(n) if not math.isnan(panel.bars[pos.symbol]["close"][i]))
            close_position(pos, panel.bars[pos.symbol]["close"][last], last, "end-of-test")
        if positions == {} and trades:
            equity[-1] = cash
            invested[-1] = 0.0
            cash_series[-1] = cash
    else:  # forward tests keep positions open and report what would happen next session
        open_positions = [_position_row(pos) for pos in positions.values()]
        next_orders = _next_orders(strategy, panel, positions, cash, float(equity[-1]))

    frame = pl.DataFrame(trades) if trades else _empty_trades()
    frame = frame.sort(["entry_date", "symbol"]).with_row_index("trade_id", offset=1) if trades else frame
    return Result(
        strategy, panel.dates, equity, cash_series, invested, frame, m.summarize(panel.dates, equity, invested, frame),
        open_positions=open_positions, next_orders=next_orders,
    )  # fmt: skip


def _position_row(pos: _Pos) -> dict[str, Any]:
    value = pos.shares * pos.last_close
    return {
        "symbol": pos.symbol,
        "sector": pos.sector,
        "regime_at_entry": pos.regime,
        "entry_date": pos.entry_date,
        "entry_price": pos.entry_fill,
        "shares": pos.shares,
        "last_close": pos.last_close,
        "market_value": value,
        "unrealized_pnl": value - pos.cost,
        "unrealized_ret": value / pos.cost - 1,
        "stop": pos.stop,
        "initial_stop": pos.initial_stop,
        "breakeven_active": pos.be_active,
        "days_held": pos.days_held,
        "pending_exit": pos.pending_exit,
    }


def _next_orders(
    strategy: Strategy, panel: Panel, positions: dict[str, _Pos], cash: float, equity: float
) -> dict[str, Any]:
    """What the strategy would do at the next open: indicative entries (sized at the last close), exits and stops."""
    p, ex, costs = strategy.portfolio, strategy.exit, strategy.costs
    last = len(panel.dates) - 1
    stop_dist = ex.hard_stop_pct / 100
    slots = p.max_positions - len(positions)
    entries: list[dict[str, Any]] = []
    budget = cash
    for sig in panel.signals.get(last, []):
        if len(entries) >= min(slots, strategy.entry.max_new_per_day):
            break
        sym = sig["symbol"]
        bar = panel.bars.get(sym)
        if sym in positions or bar is None or math.isnan(bar["close"][last]):
            continue
        price = float(bar["close"][last])
        shares = math.floor(
            min(
                equity * p.risk_per_trade_pct / 100 / (price * stop_dist),
                equity * p.max_position_pct / 100 / price,
                budget / (price * (1 + costs.fee_rate)),
            )
        )
        if shares < 1:
            continue
        sector = sig["sector"] or UNCLASSIFIED
        if sector != UNCLASSIFIED:
            held = sum(x.shares * x.last_close for x in positions.values() if x.sector == sector)
            held += sum(e["indicative_value"] for e in entries if e["sector"] == sector)
            if held + shares * price > p.max_sector_pct / 100 * equity:
                continue
        budget -= shares * price * (1 + costs.fee_rate)
        entries.append(
            {
                "symbol": sym, "sector": sector, "rank": sig["rank"], "regime": sig["regime"],
                "indicative_price": price, "indicative_shares": shares, "indicative_value": shares * price,
                "initial_stop": price * (1 + costs.slippage) * (1 - stop_dist),
            }
        )  # fmt: skip
    exits = [
        {"symbol": x.symbol, "reason": "trail", "detail": f"closed below SMA{ex.trail_sma}; exits at the next open"}
        for x in positions.values()
        if x.pending_exit
    ]
    return {
        "as_of": panel.dates[last],
        "slots_free": slots,
        "entries": entries,
        "exits": exits,
        "stops": [{"symbol": x.symbol, "stop": x.stop, "breakeven": x.be_active} for x in positions.values()],
        "note": "Entries are indicative: they fill at the next open, so size and price will differ slightly.",
    }


def _empty_trades() -> pl.DataFrame:
    schema = {
        "trade_id": pl.UInt32, "symbol": pl.Utf8, "sector": pl.Utf8, "entry_date": pl.Date, "entry_price": pl.Float64,
        "exit_date": pl.Date, "exit_price": pl.Float64, "shares": pl.Int64, "pnl": pl.Float64, "ret": pl.Float64,
        "days": pl.Int64, "exit_reason": pl.Utf8, "regime_at_entry": pl.Utf8, "rank": pl.Int64, "mae": pl.Float64,
        "mae_day": pl.Int64, "mfe": pl.Float64, "mfe_day": pl.Int64, "peak_price": pl.Float64, "giveback": pl.Float64,
        "initial_stop": pl.Float64, "final_stop": pl.Float64, "be_activated": pl.Boolean, "selection_reason": pl.Utf8,
    }  # fmt: skip
    return pl.DataFrame(schema=schema)


def run_backtest(
    strategy: Strategy,
    parquet_dir: Path | None = None,
    start: date | None = None,
    end: date | None = None,
    panel: Panel | None = None,
) -> Result:
    panel = panel or load_panel(strategy, parquet_dir, start, end)
    result = simulate(strategy, panel)
    result.data_through = panel.dates[-1]
    return result
