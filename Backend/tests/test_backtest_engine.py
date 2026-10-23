"""Backtest engine on hand-built price paths where the correct answer is known exactly."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nivesh.backtest.engine import Panel, simulate
from nivesh.backtest.metrics import curve_metrics
from nivesh.backtest.spec import Strategy
from tests.synth import trading_days

N = 10
DATES = trading_days(N)
FEE, SLIP = (3 + 10 + 0.5) / 1e4, 10 / 1e4


def strategy(**sections) -> Strategy:
    base = {
        "name": "t",
        "version": "v1",
        "entry": {"when": "close > 0", "max_new_per_day": 3},
        "exit": {"hard_stop_pct": 3, "breakeven_trigger_pct": None, "trail_sma": None, "time_stop_days": None},
    }
    for key, value in sections.items():
        base[key] = {**base.get(key, {}), **value}
    return Strategy.model_validate(base)


def bars(o=100.0, h=101.0, low=99.5, c=100.5, sma20=100.0, sma50=100.0, **by_day):
    """Flat bars; `by_day` maps a session index to a dict overriding any field for that day."""
    arrays = {
        k: np.full(N, v, dtype=float)
        for k, v in dict(open=o, high=h, low=low, close=c, sma20=sma20, sma50=sma50).items()
    }
    for day, fields in by_day.items():
        for name, value in fields.items():
            arrays[name][int(day.removeprefix("d"))] = value
    return arrays


def panel(symbol_bars: dict, signals: dict | None = None) -> Panel:
    sigs = (
        signals
        if signals is not None
        else {0: [{"symbol": s, "sector": "Tech", "regime": "Bull", "rank": i + 1} for i, s in enumerate(symbol_bars)]}
    )
    return Panel(DATES, sigs, symbol_bars, "close > 0", ["ret_63d desc"])


def expected_shares(strat: Strategy, fill: float, equity: float = 10_000_000.0) -> int:
    p = strat.portfolio
    return math.floor(
        min(
            equity * p.risk_per_trade_pct / 100 / (fill * strat.exit.hard_stop_pct / 100),
            equity * p.max_position_pct / 100 / fill,
            equity / (fill * (1 + FEE)),
        )
    )


def only_trade(result):
    assert result.trades.height == 1, result.trades
    return result.trades.row(0, named=True)


def test_signal_fills_at_next_open_with_costs_and_time_exit():
    s = strategy(exit={"time_stop_days": 3})
    r = simulate(s, panel({"AAA": bars()}))
    t = only_trade(r)
    assert t["entry_date"] == DATES[1], "a day-0 signal must fill on day 1, never day 0"
    fill_in, fill_out = 100.0 * (1 + SLIP), 100.5 * (1 - SLIP)
    shares = expected_shares(s, fill_in)
    assert t["shares"] == shares and t["entry_price"] == pytest.approx(fill_in)
    assert t["exit_reason"] == "time" and t["days"] == 3 and t["exit_date"] == DATES[4]
    expected_pnl = shares * fill_out * (1 - FEE) - shares * fill_in * (1 + FEE)
    assert t["pnl"] == pytest.approx(expected_pnl)
    assert r.equity[-1] == pytest.approx(10_000_000 + expected_pnl)  # cash account reconciles to the trade


def test_intraday_stop_fills_at_stop_price():
    s = strategy()
    r = simulate(s, panel({"AAA": bars(d2={"low": 90.0})}))
    t = only_trade(r)
    stop = 100.0 * (1 + SLIP) * 0.97
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(stop * (1 - SLIP))


def test_gap_through_stop_fills_at_the_open_not_the_stop():
    r = simulate(strategy(), panel({"AAA": bars(d2={"open": 90.0, "high": 91.0, "low": 89.0, "close": 90.0})}))
    t = only_trade(r)
    assert t["exit_reason"] == "stop-gap" and t["exit_price"] == pytest.approx(90.0 * (1 - SLIP))


def test_stop_can_trigger_on_the_entry_day():
    r = simulate(strategy(), panel({"AAA": bars(d1={"low": 90.0})}))
    t = only_trade(r)
    assert t["exit_reason"] == "stop" and t["days"] == 0


def test_breakeven_moves_stop_to_entry_then_exits_there():
    s = strategy(exit={"breakeven_trigger_pct": 8})
    r = simulate(s, panel({"AAA": bars(d2={"high": 112.0}, d3={"open": 102.0, "high": 103.0, "low": 99.0})}))
    t = only_trade(r)
    entry = 100.0 * (1 + SLIP)
    assert t["be_activated"] and t["exit_reason"] == "stop"
    assert t["final_stop"] == pytest.approx(entry)
    assert t["exit_price"] == pytest.approx(entry * (1 - SLIP))
    assert t["mfe"] == pytest.approx(112.0 / entry - 1)


def test_trailing_exit_is_a_close_signal_filled_at_next_open():
    s = strategy(exit={"trail_sma": 20})
    r = simulate(s, panel({"AAA": bars(d2={"close": 99.0}, d3={"open": 98.5, "low": 98.0})}))
    t = only_trade(r)
    assert t["exit_reason"] == "trail" and t["exit_date"] == DATES[3]
    assert t["exit_price"] == pytest.approx(98.5 * (1 - SLIP))


def test_locked_bar_and_missing_bar_cannot_be_traded():
    locked = simulate(strategy(), panel({"AAA": bars(d1={"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0})}))
    assert locked.trades.height == 0
    missing = simulate(
        strategy(), panel({"AAA": bars(d1={"open": np.nan, "high": np.nan, "low": np.nan, "close": np.nan})})
    )
    assert missing.trades.height == 0
    assert np.allclose(missing.equity, 10_000_000)


def test_max_positions_and_daily_entry_limits():
    names = {k: bars() for k in "ABCD"}
    s = strategy(entry={"max_new_per_day": 2}, portfolio={"max_positions": 3})
    r = simulate(
        s,
        panel(
            names,
            signals={
                0: [{"symbol": k, "sector": f"S{k}", "regime": None, "rank": i + 1} for i, k in enumerate("ABCD")]
            },
        ),
    )
    assert sorted(r.trades["symbol"].to_list()) == ["A", "B"], "rank order, capped at max_new_per_day"
    s2 = strategy(entry={"max_new_per_day": 5}, portfolio={"max_positions": 3})
    r2 = simulate(
        s2,
        panel(
            names,
            signals={
                0: [{"symbol": k, "sector": f"S{k}", "regime": None, "rank": i + 1} for i, k in enumerate("ABCD")]
            },
        ),
    )
    assert sorted(r2.trades["symbol"].to_list()) == ["A", "B", "C"], "capped at max_positions"


def test_sector_cap_blocks_the_second_position_in_the_same_sector():
    sig = {
        0: [
            {"symbol": "A", "sector": "Tech", "regime": None, "rank": 1},
            {"symbol": "B", "sector": "Tech", "regime": None, "rank": 2},
        ]
    }
    capped = simulate(strategy(portfolio={"max_sector_pct": 20}), panel({"A": bars(), "B": bars()}, signals=sig))
    assert capped.trades["symbol"].to_list() == ["A"]
    open_ = simulate(strategy(portfolio={"max_sector_pct": 100}), panel({"A": bars(), "B": bars()}, signals=sig))
    assert sorted(open_.trades["symbol"].to_list()) == ["A", "B"]


def test_open_position_is_closed_at_the_end_of_the_data():
    r = simulate(strategy(), panel({"AAA": bars()}))
    t = only_trade(r)
    assert t["exit_reason"] == "end-of-test" and t["exit_date"] == DATES[-1]
    assert r.invested[-1] == 0 and r.equity[-1] == pytest.approx(r.cash[-1])


def test_excursions_and_giveback():
    r = simulate(
        strategy(exit={"time_stop_days": 4}), panel({"AAA": bars(d2={"high": 110.0, "low": 98.0}, d3={"close": 103.0})})
    )
    t = only_trade(r)
    entry = 100.0 * (1 + SLIP)
    assert t["mfe"] == pytest.approx(110.0 / entry - 1) and t["mfe_day"] == 1
    assert t["mae"] == pytest.approx(98.0 / entry - 1) and t["mae_day"] == 1
    assert t["peak_price"] == 110.0
    assert t["giveback"] == pytest.approx(1 - t["exit_price"] / 110.0)


def test_position_sizing_respects_cash():
    s = strategy(
        portfolio={"initial_capital": 50_000, "max_position_pct": 100, "risk_per_trade_pct": 10, "max_sector_pct": 100}
    )
    r = simulate(s, panel({"AAA": bars()}))
    t = only_trade(r)
    assert t["shares"] * 100.0 * (1 + SLIP) * (1 + FEE) <= 50_000 + 1e-6


def test_costs_scale_losses_up_and_wins_down():
    base = simulate(strategy(exit={"time_stop_days": 3}), panel({"AAA": bars()}))
    pricey = simulate(
        strategy(exit={"time_stop_days": 3}, costs={"slippage_bps": 50, "brokerage_bps": 20}), panel({"AAA": bars()})
    )
    assert pricey.trades["pnl"][0] < base.trades["pnl"][0]


def test_no_signals_means_flat_equity_and_empty_trades():
    r = simulate(strategy(), panel({}, signals={}))
    assert r.trades.height == 0 and np.allclose(r.equity, 10_000_000)
    assert r.metrics["trades"] == 0


def test_with_overrides_validates_and_rejects_unknown_paths():
    s = strategy()
    assert s.with_overrides({"exit.hard_stop_pct": 4}).exit.hard_stop_pct == 4
    with pytest.raises(KeyError):
        s.with_overrides({"exit.nope": 1})
    with pytest.raises(ValueError):
        s.with_overrides({"exit.hard_stop_pct": -1})


def test_curve_metrics_known_values():
    dates = trading_days(253)
    equity = np.linspace(100, 200, 253)
    m = curve_metrics(dates, equity)
    assert m["cagr"] == pytest.approx(2 ** (1 / m["years"]) - 1)
    assert m["max_drawdown"] == 0.0 and m["calmar"] is None
    dd = np.array([100, 120, 60, 90.0])
    assert curve_metrics(trading_days(4), dd)["max_drawdown"] == pytest.approx(-0.5)
