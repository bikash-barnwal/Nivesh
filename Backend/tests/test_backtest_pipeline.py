"""Backtests over the synthetic demo market: signals vs rules, look-ahead safety, gates, storage and jobs."""

from __future__ import annotations

import time

import polars as pl
import pytest

from nivesh import analytics
from nivesh.backtest import store
from nivesh.backtest.engine import BacktestError, load_panel, simulate
from nivesh.backtest.gates import evaluate_gates
from nivesh.backtest.spec import Strategy, get_strategy, list_strategies
from nivesh.config import settings
from nivesh.data.engine import Engine
from nivesh.demo import make_demo_dataset
from tests.synth import truncate_dataset


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("lab")
    parquet = base / "parquet"
    days = make_demo_dataset(parquet, sessions=700, symbols=90)
    previous = settings.data_dir
    settings.data_dir = base
    yield base, parquet, days
    settings.data_dir = previous


@pytest.fixture(scope="module")
def trend(world):
    _, parquet, _ = world
    strat = get_strategy("trend_stack_v1")
    panel = load_panel(strat, parquet)
    return strat, panel, simulate(strat, panel)


def test_all_shipped_strategies_validate_against_the_real_feature_columns(world):
    _, parquet, _ = world
    engine = Engine(parquet)
    columns = [c for c, _ in engine.schema("stock_features")]
    strategies = list_strategies()
    assert len(strategies) >= 3
    for strat in strategies:
        frags = strat.compile(columns)  # raises RuleError on an unknown column
        assert frags["universe"] and frags["entry"] and frags["order"]


def test_invalid_strategy_definitions_are_rejected():
    with pytest.raises(ValueError):
        Strategy.model_validate(
            {"name": "x", "version": "v1", "entry": {"when": "close > 0", "rank_by": ["ret_63d sideways"]}}
        )
    with pytest.raises(ValueError):
        Strategy.model_validate(
            {"name": "x", "version": "v1", "entry": {"when": "close > 0"}, "exit": {"hard_stop_pct": 0}}
        )
    with pytest.raises(ValueError):
        Strategy.model_validate({"name": "x", "version": "v1", "entry": {"when": "close > 0"}, "surprise": 1})


def test_signals_satisfy_the_strategy_rules_and_are_ranked(world, trend):
    _, parquet, _ = world
    strat, panel, _ = trend
    features = pl.read_parquet(parquet / "stock_features" / "stock_features.parquet")
    regimes = pl.read_parquet(parquet / "market_regime" / "market_regime.parquet").select("date", "regime")
    rows = [(panel.dates[i], s["symbol"], s["rank"]) for i, sigs in panel.signals.items() for s in sigs]
    assert rows, "the demo market should produce some trend-stack signals"
    sig = (
        pl.DataFrame(rows, schema=["date", "symbol", "rank"], orient="row")
        .join(features, on=["date", "symbol"])
        .join(regimes, on="date", how="left")
    )
    assert sig["sma_stack_ok"].all() and (sig["stage"] == 2).all()
    assert (sig["rvol20"] >= 1.0).all() and (sig["dist_52w_high_pct"] >= -0.15).all()
    assert (sig["close"] >= 30).all() and (sig["avg_turnover20_lacs"] >= 100).all()
    assert not sig["regime"].is_in(strat.regime_block).any()
    for day, group in sig.group_by("date"):
        ordered = group.sort("rank")
        assert ordered["rank"].to_list() == list(range(1, group.height + 1)), day
        assert ordered["ret_63d"].is_sorted(descending=True) or ordered["ret_63d"].n_unique() == 1


def test_backtest_results_are_consistent(trend):
    strat, _, result = trend
    t, eq = result.trades, result.equity
    assert t.height > 20
    assert (t["entry_date"] < t["exit_date"]).all() | (t["days"] == 0).any()
    assert (t["shares"] >= 1).all() and (t["entry_price"] > 0).all()
    assert eq[0] == pytest.approx(strat.portfolio.initial_capital)
    assert eq[-1] == pytest.approx(strat.portfolio.initial_capital + t["pnl"].sum())  # cash account reconciles
    assert result.invested[-1] == 0
    assert (result.invested <= result.equity + 1e-6).all()
    # never more positions than allowed at any time
    assert max(_open_positions(t, result.dates)) <= strat.portfolio.max_positions


def _open_positions(trades: pl.DataFrame, dates) -> list[int]:
    counts = []
    rows = trades.select("entry_date", "exit_date").rows()
    for d in dates:
        counts.append(sum(1 for entry, exit_ in rows if entry <= d < exit_ or (entry == exit_ == d)))
    return counts


def test_no_look_ahead_signals_and_closed_trades_match_on_truncated_data(world, trend, tmp_path):
    _, parquet, days = world
    strat, panel, result = trend
    cutoff = days[500]
    short_root = tmp_path / "parquet"
    truncate_dataset(parquet, short_root, cutoff)
    (short_root / "security_master").mkdir(exist_ok=True)
    analytics.run_analytics(parquet_dir=short_root)

    short_panel = load_panel(strat, short_root)
    full_idx = {d: i for i, d in enumerate(panel.dates)}
    for i, sigs in short_panel.signals.items():
        full = panel.signals[full_idx[short_panel.dates[i]]]
        assert [(s["symbol"], s["rank"]) for s in sigs] == [(s["symbol"], s["rank"]) for s in full]

    short_result = simulate(strat, short_panel)
    key = ["symbol", "entry_date", "exit_date"]
    closed = short_result.trades.filter(pl.col("exit_reason") != "end-of-test")
    assert closed.height > 0
    full_trades = result.trades.select(key + ["entry_price", "exit_price", "shares", "exit_reason"])
    matched = closed.select(key + ["entry_price", "exit_price", "shares", "exit_reason"]).join(
        full_trades, on=key, how="left", suffix="_full"
    )
    assert matched["entry_price_full"].null_count() == 0, "a trade seen on truncated data must exist in the full run"
    assert (matched["exit_price"] - matched["exit_price_full"]).abs().max() < 1e-9
    assert (matched["shares"] == matched["shares_full"]).all()


def test_gates_report_every_check(world, trend):
    _, _, _ = world
    strat, panel, result = trend
    report = evaluate_gates(strat, panel, result)
    names = [g["name"] for g in report["gates"]]
    assert names == ["minimum_trades", "out_of_sample", "parameter_sensitivity", "cost_stress", "regime_spread"]
    sens = next(g for g in report["gates"] if g["name"] == "parameter_sensitivity")["value"]["variants"]
    assert len(sens) == 8 and {v["param"] for v in sens} == {
        "exit.hard_stop_pct", "exit.time_stop_days", "portfolio.max_positions", "portfolio.risk_per_trade_pct",
    }  # fmt: skip
    oos = next(g for g in report["gates"] if g["name"] == "out_of_sample")["value"]
    assert oos["in_sample"]["years"] > oos["out_of_sample"]["years"] > 0
    assert isinstance(report["passed_all"], bool) and isinstance(report["review_required"], bool)


def test_a_strategy_with_too_few_trades_cannot_pass(world):
    _, parquet, _ = world
    strat = get_strategy("breakout_volume_v1").with_overrides({"portfolio.max_positions": 1})
    panel = load_panel(strat, parquet)
    result = simulate(strat, panel)
    report = evaluate_gates(strat, panel, result)
    if result.metrics["trades"] < 30:
        assert report["passed_all"] is False
        assert next(g for g in report["gates"] if g["name"] == "minimum_trades")["passed"] is False


def test_run_is_saved_listed_and_reloadable(world):
    strat = get_strategy("leaders_pullback_v1")
    run_id = store.run_and_save(strat)
    assert run_id.startswith("leaders_pullback_v1-")
    loaded = store.load_run(run_id)
    assert loaded["run"]["strategy"]["name"] == "leaders_pullback" and loaded["gates"] is not None
    equity = store.load_equity(run_id)
    assert equity["drawdown"].max() <= 0 and equity.height == loaded["run"]["sessions"]
    assert store.load_trades(run_id).height == loaded["metrics"]["trades"]
    assert any(r["run_id"] == run_id for r in store.list_runs())
    assert any(r["run_id"] == run_id for r in store.latest_per_strategy())


def test_run_ids_cannot_escape_the_runs_directory(world):
    for bad in ("../x", "..", "a/b", "", "x" * 5 + "/../.."):
        with pytest.raises(KeyError):
            store.load_run(bad)


def _wait(job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.job_status(job_id)
        if job and job["status"] != "running":
            return job
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_background_job_completes_and_failures_are_reported(world):
    done = _wait(store.submit(get_strategy("trend_stack_v1"), None, None, with_gates=False))
    assert done["status"] == "done" and done["run_id"]
    bad = get_strategy("trend_stack_v1").model_copy(
        update={"entry": get_strategy("trend_stack_v1").entry.model_copy(update={"when": "not_a_column > 1"})}
    )
    failed = _wait(store.submit(bad, None, None, with_gates=False))
    assert failed["status"] == "failed" and "not_a_column" in failed["error"]


def test_backtest_without_built_analytics_fails_clearly(tmp_path):
    with pytest.raises(BacktestError):
        load_panel(get_strategy("trend_stack_v1"), tmp_path)
