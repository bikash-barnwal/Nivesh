"""Forward-test mode: positions stay open and the engine reports the orders it would place next session."""

from __future__ import annotations

import numpy as np
import pytest

from nivesh.backtest.engine import simulate
from tests.test_backtest_engine import DATES, FEE, SLIP, N, bars, panel, strategy


def sig(symbol, rank, sector="Tech"):
    return {"symbol": symbol, "sector": sector, "regime": "Bull", "rank": rank}


def test_positions_stay_open_and_are_marked_to_market():
    s = strategy()
    r = simulate(s, panel({"AAA": bars()}), close_open=False)
    assert r.trades.height == 0 and len(r.open_positions) == 1
    pos = r.open_positions[0]
    fill = 100.0 * (1 + SLIP)
    assert pos["symbol"] == "AAA" and pos["entry_date"] == DATES[1] and pos["entry_price"] == pytest.approx(fill)
    assert pos["last_close"] == 100.5 and pos["market_value"] == pytest.approx(pos["shares"] * 100.5)
    cost = pos["shares"] * fill * (1 + FEE)
    assert pos["unrealized_pnl"] == pytest.approx(pos["market_value"] - cost)
    assert r.equity[-1] == pytest.approx(r.cash[-1] + pos["market_value"])
    assert r.invested[-1] == pytest.approx(pos["market_value"])


def test_the_default_mode_still_closes_everything_and_reports_no_open_positions():
    r = simulate(strategy(), panel({"AAA": bars()}))
    assert r.open_positions == [] and r.next_orders == {} and r.trades["exit_reason"].to_list() == ["end-of-test"]


def test_closed_trades_match_the_standard_backtest_exactly():
    s = strategy(exit={"time_stop_days": 3})
    data = {"AAA": bars(), "BBB": bars(d4={"low": 90.0})}
    signals = {0: [sig("AAA", 1), sig("BBB", 2)], 5: [sig("AAA", 1)]}
    standard = simulate(s, panel(data, signals))
    forward = simulate(s, panel(data, signals), close_open=False)
    key = ["symbol", "entry_date", "exit_date", "exit_reason", "pnl"]
    closed_standard = standard.trades.filter(standard.trades["exit_reason"] != "end-of-test").select(key).sort(key)
    assert forward.trades.select(key).sort(key).equals(closed_standard)


def test_a_pending_trailing_exit_is_listed_for_the_next_open():
    s = strategy(exit={"trail_sma": 20})
    # the last bar closes below its SMA20: a close-of-day signal that fills at the next open
    data = {"AAA": bars(**{f"d{N - 1}": {"close": 98.0}})}
    r = simulate(s, panel(data), close_open=False)
    assert [e["symbol"] for e in r.next_orders["exits"]] == ["AAA"]
    assert r.next_orders["exits"][0]["reason"] == "trail" and "SMA20" in r.next_orders["exits"][0]["detail"]
    assert r.open_positions[0]["pending_exit"] is True


def test_stops_are_reported_with_the_breakeven_flag():
    s = strategy(exit={"breakeven_trigger_pct": 8})
    # lows stay above the raised (breakeven) stop, so the position survives to the last bar
    held = bars(o=100.5, h=101.0, low=100.3, c=100.8, d1={"open": 100.0}, d2={"high": 112.0})
    r = simulate(s, panel({"AAA": held}), close_open=False)
    stop = r.next_orders["stops"][0]
    assert stop["breakeven"] is True and stop["stop"] == pytest.approx(100.0 * (1 + SLIP))


def test_next_orders_list_ranked_candidates_from_the_last_session():
    s = strategy(entry={"max_new_per_day": 2}, portfolio={"max_positions": 5, "max_sector_pct": 100})
    data = {k: bars() for k in "ABC"}
    signals = {N - 1: [sig("A", 1, "S1"), sig("B", 2, "S2"), sig("C", 3, "S3")]}
    r = simulate(s, panel(data, signals), close_open=False)
    entries = r.next_orders["entries"]
    assert [e["symbol"] for e in entries] == ["A", "B"], "ranked, capped by max_new_per_day"
    first = entries[0]
    assert first["indicative_price"] == 100.5 and first["indicative_shares"] >= 1
    assert first["initial_stop"] == pytest.approx(100.5 * (1 + SLIP) * 0.97)
    assert r.next_orders["slots_free"] == 5 and r.next_orders["as_of"] == DATES[-1]


def test_next_orders_skip_held_symbols_and_respect_free_slots_and_cash():
    s = strategy(portfolio={"max_positions": 2, "max_sector_pct": 100})
    data = {k: bars() for k in "ABC"}
    signals = {0: [sig("A", 1)], N - 1: [sig("A", 1), sig("B", 2), sig("C", 3)]}
    r = simulate(s, panel(data, signals), close_open=False)
    assert [p["symbol"] for p in r.open_positions] == ["A"]
    assert [e["symbol"] for e in r.next_orders["entries"]] == ["B"], "A is already held; only one slot remains"
    assert r.next_orders["slots_free"] == 1
    spent = sum(e["indicative_value"] for e in r.next_orders["entries"])
    assert spent <= r.cash[-1]


def test_sector_cap_applies_to_indicative_entries():
    s = strategy(portfolio={"max_positions": 5, "max_sector_pct": 20})
    data = {k: bars() for k in "AB"}
    signals = {N - 1: [sig("A", 1, "Tech"), sig("B", 2, "Tech")]}
    r = simulate(s, panel(data, signals), close_open=False)
    assert [e["symbol"] for e in r.next_orders["entries"]] == ["A"]


def test_no_signals_means_no_orders():
    r = simulate(strategy(), panel({}, signals={}), close_open=False)
    assert r.next_orders["entries"] == [] and r.next_orders["exits"] == [] and np.allclose(r.equity, 10_000_000)
