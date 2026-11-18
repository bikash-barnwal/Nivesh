"""End-to-end tests for every analytics builder on a deterministic 560-session synthetic market."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest
import yaml

from nivesh import analytics
from nivesh.config import settings
from tests.synth import make_dataset, trading_days, truncate_dataset


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic")
    days = make_dataset(root)
    steps = analytics.run_analytics(parquet_dir=root)
    return root, days, steps


def _read(root: Path, name: str) -> pl.DataFrame:
    return pl.read_parquet(root / name / f"{name}.parquet")


def _yaml(name: str) -> dict:
    return yaml.safe_load((settings.context_dir / name).read_text(encoding="utf-8"))


def test_every_step_succeeds_with_rows_except_sector_without_map(market):
    _, _, steps = market
    by_name = {s.name: s for s in steps}
    assert all(s.error is None for s in steps), [s for s in steps if s.error]
    for name, step in by_name.items():
        if name != "sector_daily":  # no sector_map in the synthetic dataset
            assert step.rows and step.rows > 0, name


def test_engines_are_percentiles_distinct_and_respect_minimum_sessions(market):
    root, _, _ = market
    engines = _read(root, "market_engines")
    breadth = _read(root, "market_breadth")
    _, minimum = analytics._engine_config()

    values = engines["value"].drop_nulls()
    assert values.min() >= 0 and values.max() <= 100
    assert set(engines["engine"].unique()) == set(analytics.ENGINES)

    series = {}
    for engine in analytics.ENGINES:
        sub = engines.filter((pl.col("engine") == engine) & pl.col("value").is_not_null()).sort("date")
        assert sub["value"].n_unique() > 100, engine
        series[engine] = tuple(sub["value"].to_list())
    assert len(set(series.values())) == len(analytics.ENGINES), "two engines have identical series"

    first_value_date = engines.filter(pl.col("value").is_not_null())["date"].min()
    sessions_before_first_value = breadth.filter(pl.col("date") < first_value_date).height
    assert sessions_before_first_value == minimum - 1  # value appears once `minimum` sessions exist


def test_delta_10_is_set_after_ten_valid_sessions(market):
    root, _, _ = market
    sub = _read(root, "market_engines").filter(pl.col("engine") == "participation").sort("date")
    valid = sub.filter(pl.col("value").is_not_null())
    assert valid["delta_10"][:10].null_count() == 10
    assert valid["delta_10"][10:].null_count() == 0


def test_new_highs_and_lows_use_prior_252_sessions(tmp_path):
    days = trading_days(300)
    rows = []
    for i, day in enumerate(days):
        up, down = 100 + i, 1000 - i
        for sym, close in (("UP", up), ("DOWN", down)):
            rows.append((sym, day, "EQ", close, close + 1, close - 1, close, 1000, 600.0))
    out = tmp_path / "stock_daily"
    out.mkdir()
    pl.DataFrame(
        rows,
        schema=["symbol", "date", "series", "open", "high", "low", "close", "volume", "turnover_lacs"],
        orient="row",
    ).write_parquet(out / "stock_daily.parquet")
    analytics.build_stock_features(parquet_dir=tmp_path)
    df = _read(tmp_path, "stock_features")
    up = df.filter(pl.col("symbol") == "UP").sort("date")
    down = df.filter(pl.col("symbol") == "DOWN").sort("date")
    assert up["is_new_high_52w"][:252].null_count() == 252  # needs 252 prior sessions
    assert up["is_new_high_52w"][252:].all()
    assert not up["is_new_low_52w"][252:].any()
    assert down["is_new_low_52w"][252:].all()
    assert not down["is_new_high_52w"][252:].any()


def test_regimes_come_from_yaml_with_streak_and_confidence(market):
    root, _, _ = market
    regime = _read(root, "market_regime").sort("date")
    allowed = {r["name"] for r in _yaml("regimes.yaml")["rules"]} | {_yaml("regimes.yaml")["default"]["name"]}
    assert set(regime["regime"]) <= allowed
    assert regime["regime"].n_unique() >= 3
    assert regime["confidence"].min() > 0 and regime["confidence"].max() <= 100
    previous, run = None, 0
    for name, active in zip(regime["regime"], regime["sessions_active"], strict=True):
        run = run + 1 if name == previous else 1
        assert active == run
        previous = name


def test_events_satisfy_their_yaml_rules_and_streaks_are_consecutive(market):
    root, _, _ = market
    events = _read(root, "market_events").sort("date")
    engines = _read(root, "market_engines")
    pivot = engines.pivot(on="engine", index="date", values="value")
    assert events.height > 0
    rules = {e["name"]: e for e in _yaml("events.yaml")["events"]}
    con = duckdb.connect()
    con.register("eng_pivot", pivot)
    from nivesh.rules import compile_expr

    for name, rule in rules.items():
        sub = events.filter(pl.col("event") == name)
        if sub.is_empty():
            continue
        con.register("fired", sub.select("date"))
        cond = compile_expr(rule["when"], analytics.ENGINES)
        query = f"SELECT count(*) FROM eng_pivot JOIN fired USING (date) WHERE NOT coalesce(({cond}), false)"
        bad = con.execute(query).fetchone()[0]
        assert bad == 0, name
        assert sub["streak"].min() == 1
    assert events["confidence"].min() >= 0 and events["confidence"].max() <= 100


def test_events_carry_forward_returns_only_when_observed(market):
    root, days, _ = market
    events = _read(root, "market_events")
    with_fwd = events.filter(pl.col("fwd_3m").is_not_null())
    assert with_fwd.height > 0
    assert (with_fwd["fwd_3m_date"] > with_fwd["date"]).all()
    last = days[-1]
    assert events.filter(pl.col("fwd_3m_date").is_null() | (pl.col("fwd_3m_date") <= last)).height == events.height


def test_analogues_respect_gap_declustering_and_no_lookahead(market):
    root, _, _ = market
    an = _read(root, "market_analogues")
    assert an["rank"].max() == 5
    assert an["similarity_pct"].min() >= 0 and an["similarity_pct"].max() <= 100
    gap = timedelta(days=analytics.MIN_ANALOGUE_GAP_DAYS)
    assert ((an["date"] - an["analogue_date"]) >= gap).all()
    for _, group in an.group_by("date"):
        dates = sorted(group["analogue_date"].to_list())
        assert all((b - a) >= gap for a, b in zip(dates, dates[1:], strict=False))
    # fwd_3m must have been observable on the explained date: analogue + ~63 sessions <= date
    shown = an.filter(pl.col("fwd_3m").is_not_null())
    assert shown.height > 0
    assert ((shown["date"] - shown["analogue_date"]) >= timedelta(days=80)).all()


def test_scans_scores_and_universe(market):
    root, _, _ = market
    scans = _read(root, "scan_results")
    features = _read(root, "stock_features")
    assert scans.height > 0
    assert scans["score"].min() >= 0 and scans["score"].max() <= 100
    assert scans.filter(pl.col("rank") == 1).height > 0
    joined = scans.join(features, on=["symbol", "date"])
    assert (joined["close"] >= 30).all()
    assert (joined["avg_turnover20_lacs"] >= 100).all()
    assert (joined["history_sessions"] >= 200).all()


def test_features_and_engines_do_not_look_ahead(market, tmp_path):
    root, days, _ = market
    cutoff = days[450]
    truncate_dataset(root, tmp_path, cutoff)
    analytics.run_analytics(parquet_dir=tmp_path)

    for table, keys in (("stock_features", ["symbol", "date"]), ("market_engines", ["engine", "date"])):
        full = _read(root, table).filter(pl.col("date") <= cutoff).sort(keys)
        short = _read(tmp_path, table).sort(keys)
        assert full.height == short.height, table
        for col in full.columns:
            if full[col].dtype in (pl.Float64, pl.Float32):
                a, b = full[col].to_numpy(), short[col].to_numpy()
                assert (
                    pl.Series(a).fill_nan(None).equals(pl.Series(b).fill_nan(None))
                    or abs((pl.Series(a).fill_null(0) - pl.Series(b).fill_null(0)).abs().max()) < 1e-9
                ), f"{table}.{col}"
            else:
                assert full[col].equals(short[col]), f"{table}.{col}"


def test_failed_step_skips_dependents_and_build_analytics_raises(tmp_path, monkeypatch):
    def boom(**_):
        raise RuntimeError("boom")

    steps = (
        ("a", boom, ()),
        ("b", lambda **_: 5, ("a",)),
        ("c", lambda **_: 7, ()),
    )
    monkeypatch.setattr(analytics, "STEPS", steps)
    results = {s.name: s for s in analytics.run_analytics(parquet_dir=tmp_path)}
    assert "boom" in results["a"].error
    assert results["b"].error.startswith("skipped")
    assert results["c"].rows == 7
    with pytest.raises(analytics.AnalyticsError):
        analytics.build_analytics(parquet_dir=tmp_path)


def test_missing_upstream_removes_stale_snapshot(tmp_path):
    stale = tmp_path / "market_engines" / "market_engines.parquet"
    stale.parent.mkdir(parents=True)
    pl.DataFrame({"x": [1]}).write_parquet(stale)
    assert analytics.build_market_engines(parquet_dir=tmp_path) == 0
    assert not stale.exists()


def test_failed_write_leaves_previous_snapshot_intact(tmp_path):
    out = tmp_path / "t" / "t.parquet"
    con = duckdb.connect()
    analytics._publish(con, "SELECT 1 AS a", out)
    with pytest.raises(duckdb.Error):
        analytics._publish(con, "SELECT nope FROM missing_table", out)
    assert duckdb.sql(f"SELECT a FROM '{out.as_posix()}'").fetchall() == [(1,)]
    assert not list(out.parent.glob("*.tmp"))
    assert isinstance(date.today(), date)
