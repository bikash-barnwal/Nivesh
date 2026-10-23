"""Deterministic analytics snapshots built from adjusted stock_daily Parquet.

Every snapshot is written to a temporary file and renamed into place only when the query succeeded, so a failing
step never leaves a half-written or schema-mismatched table behind. Thresholds for regimes, events and scans live in
`context/*.yaml` and are compiled through `nivesh.rules`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import yaml

from nivesh.config import settings
from nivesh.rules import compile_expr

ENGINES = (
    "buying_pressure",
    "selling_pressure",
    "leadership",
    "liquidity",
    "momentum_quality",
    "participation",
    "volatility",
    "risk_exhaustion",
    "structural_breadth",
)
FORWARD_HORIZONS = (("1w", 5), ("1m", 21), ("3m", 63))
MIN_ANALOGUE_GAP_DAYS = 42
ANALOGUES_PER_DATE = 5


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _quote(text: str) -> str:
    return text.replace("'", "''")


def _yaml(name: str) -> dict[str, Any]:
    path = settings.context_dir / name
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _engine_config() -> tuple[int, int]:
    path = settings.context_dir / "engines.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    config = config or {}
    return int(config.get("window_sessions", 756)), int(config.get("minimum_sessions", 120))


def _label_case(column: str) -> str:
    bands = (_yaml("engines.yaml").get("label_bands")) or {
        "very_weak": [0, 20],
        "weak": [20, 40],
        "neutral": [40, 55],
        "strong": [55, 75],
        "very_strong": [75, 100],
    }
    ordered = sorted(bands.items(), key=lambda kv: -float(kv[1][0]))
    parts = []
    for name, (lo, _hi) in ordered:
        label = _quote(name.replace("_", " ").title())
        parts.append(f"WHEN {column} >= {float(lo)} THEN '{label}'")
    return f"CASE {' '.join(parts)} END"


def _publish(con: duckdb.DuckDBPyConnection, sql: str, output: Path) -> int:
    """Run `sql`, write it to a temp Parquet file next to `output`, then atomically replace `output`."""
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".tmp")
    try:
        con.execute(f"COPY ({sql}) TO '{_sql_path(tmp)}' (FORMAT parquet, COMPRESSION zstd)")
        rows = int(con.execute(f"SELECT count(*) FROM read_parquet('{_sql_path(tmp)}')").fetchone()[0])
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return rows


def _run(sql: str, output: Path) -> int:
    con = duckdb.connect()
    try:
        return _publish(con, sql, output)
    finally:
        con.close()


def _clear(output: Path) -> int:
    """Upstream data is missing: remove a stale snapshot instead of serving outdated numbers."""
    output.unlink(missing_ok=True)
    return 0


def _index_base_sql(root: Path, index_name: str) -> str:
    folder = root / "index_daily"
    if not folder.exists() or not any(folder.rglob("*.parquet")):
        return "SELECT NULL::DATE AS date, NULL::DOUBLE AS close WHERE false"
    glob = _sql_path(folder / "**" / "*.parquet")
    return (
        f"SELECT date, close FROM read_parquet('{glob}', hive_partitioning = true, union_by_name = true) "
        f"WHERE index_name = '{_quote(index_name)}'"
    )


def _benchmark_sql(root: Path, index_name: str) -> str:
    """Benchmark closes with forward returns (and the date each forward return is observed)."""
    leads = ", ".join(
        f"lead(close, {n}) OVER w / nullif(close, 0) - 1 AS fwd_{label}, lead(date, {n}) OVER w AS fwd_{label}_date"
        for label, n in FORWARD_HORIZONS
    )
    return f"SELECT date, close, {leads} FROM ({_index_base_sql(root, index_name)}) WINDOW w AS (ORDER BY date)"


def _engine_pivot_sql(source: Path) -> str:
    cols = ", ".join(f"max(value) FILTER (WHERE engine = '{e}') AS \"{e}\"" for e in ENGINES)
    return f"SELECT date, {cols} FROM read_parquet('{_sql_path(source)}') GROUP BY date"


def build_stock_features(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    out = root / "stock_features" / "stock_features.parquet"
    if not list((root / "stock_daily").glob("**/*.parquet")):
        return _clear(out)
    source = root / "stock_daily" / "**" / "*.parquet"

    def win(preceding: int, following: str = "CURRENT ROW") -> str:
        return f"OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN {preceding} PRECEDING AND {following})"

    sql = f"""
        WITH base AS (
          SELECT *,
            lag(close, 1) OVER w AS close_1,
            lag(close, 4) OVER w AS close_4,
            lag(close, 21) OVER w AS close_21,
            lag(close, 63) OVER w AS close_63,
            count(*) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
              AS history_sessions,
            count(close) {win(19)} AS n20,
            count(close) {win(49)} AS n50,
            count(close) {win(149)} AS n150,
            count(close) {win(199)} AS n200,
            count(close) {win(251)} AS n252,
            count(volume) {win(20, "1 PRECEDING")} AS n_volume20,
            avg(close) {win(19)} AS sma20_raw,
            avg(close) {win(49)} AS sma50_raw,
            avg(close) {win(149)} AS sma150_raw,
            avg(close) {win(199)} AS sma200_raw,
            avg(volume) {win(20, "1 PRECEDING")} AS avg_volume20_raw,
            avg(turnover_lacs) {win(19)} AS avg_turnover20_lacs,
            max(high) {win(251)} AS high_52w_raw,
            min(low) {win(251)} AS low_52w_raw,
            count(high) {win(252, "1 PRECEDING")} AS n_prior252,
            max(high) {win(252, "1 PRECEDING")} AS prior_high_raw,
            min(low) {win(252, "1 PRECEDING")} AS prior_low_raw
          FROM read_parquet('{_sql_path(source)}', hive_partitioning=true, union_by_name=true)
          WHERE series = 'EQ'
          WINDOW w AS (PARTITION BY symbol ORDER BY date)
        ), tr AS (
          SELECT *, greatest(high - low, abs(high - close_1), abs(low - close_1)) AS true_range_raw
          FROM base
        ), windows AS (
          SELECT *,
            count(true_range_raw) {win(13)} AS n_atr14,
            avg(true_range_raw) {win(13)} AS atr14_raw,
            avg(greatest(close - close_1, 0)) {win(13)} AS gain14,
            avg(greatest(close_1 - close, 0)) {win(13)} AS loss14
          FROM tr
        ), vals AS (
          SELECT *,
            CASE WHEN n20 = 20 THEN sma20_raw END AS sma20,
            CASE WHEN n50 = 50 THEN sma50_raw END AS sma50,
            CASE WHEN n150 = 150 THEN sma150_raw END AS sma150,
            CASE WHEN n200 = 200 THEN sma200_raw END AS sma200,
            CASE WHEN n252 = 252 THEN high_52w_raw END AS high_52w,
            CASE WHEN n252 = 252 THEN low_52w_raw END AS low_52w,
            CASE WHEN n_prior252 = 252 THEN prior_high_raw END AS prior_high_52w,
            CASE WHEN n_prior252 = 252 THEN prior_low_raw END AS prior_low_52w,
            CASE WHEN n_volume20 = 20 THEN avg_volume20_raw END AS avg_volume_20,
            CASE WHEN n_atr14 = 14 THEN atr14_raw END AS atr14,
            CASE WHEN n_atr14 = 14 THEN atr14_raw / nullif(close, 0) END AS atr14_pct,
            CASE WHEN n_atr14 = 14 THEN 100 * gain14 / nullif(gain14 + loss14, 0) END AS rsi14,
            CASE WHEN n20 = 20 THEN close / nullif(close_1, 0) - 1 END AS ret_1d,
            CASE WHEN n20 = 20 THEN close / nullif(close_4, 0) - 1 END AS ret_4d,
            CASE WHEN n20 = 20 THEN close / nullif(close_21, 0) - 1 END AS ret_21d,
            CASE WHEN n20 = 20 THEN close / nullif(close_63, 0) - 1 END AS ret_63d
          FROM windows
        ), final AS (
          SELECT *, lag(sma150, 20) OVER (PARTITION BY symbol ORDER BY date) AS sma150_20ago
          FROM vals
        )
        SELECT symbol, date, close, volume, turnover_lacs, history_sessions,
          sma20, sma50, sma150, sma200, ret_1d, ret_4d, ret_21d, ret_63d,
          (high - low) / nullif(close, 0) AS range_pct,
          true_range_raw AS true_range, atr14, atr14_pct, rsi14,
          close / nullif(sma20, 0) - 1 AS close_vs_sma20_pct,
          volume / nullif(avg_volume_20, 0) AS rvol20,
          avg_turnover20_lacs, high_52w, low_52w,
          close / nullif(high_52w, 0) - 1 AS dist_52w_high_pct,
          close / nullif(low_52w, 0) - 1 AS dist_52w_low_pct,
          high >= prior_high_52w AS is_new_high_52w,
          low <= prior_low_52w AS is_new_low_52w,
          CASE WHEN sma200 IS NULL THEN NULL ELSE close > sma20 AND sma20 > sma50 AND sma50 > sma200 END
            AS sma_stack_ok,
          CASE WHEN sma150 IS NULL OR sma150_20ago IS NULL OR sma200 IS NULL THEN NULL
               WHEN sma150 > sma150_20ago AND close >= sma150 AND close >= sma200 THEN 2
               WHEN sma150 <= sma150_20ago AND close >= sma150 AND close >= sma200 THEN 3
               WHEN sma150 > sma150_20ago AND close < sma150 THEN 1 ELSE 4 END AS stage,
          CASE WHEN sma150 IS NULL OR sma150_20ago IS NULL OR sma200 IS NULL THEN NULL
               ELSE sma150 - sma150_20ago END AS sma150_slope20
        FROM final ORDER BY symbol, date
    """
    return _run(sql, out)


def build_market_breadth(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "stock_features" / "stock_features.parquet"
    out = root / "market_breadth" / "market_breadth.parquet"
    if not source.exists():
        return _clear(out)
    sql = f"""
      WITH u AS (
        SELECT * FROM read_parquet('{_sql_path(source)}')
        WHERE history_sessions >= 200 AND close >= 20 AND avg_turnover20_lacs >= 500
      ), a AS (
        SELECT date, count(*) AS n_stocks,
          count(*) FILTER (WHERE close > sma20) AS above_sma20,
          count(*) FILTER (WHERE close > sma50) AS above_sma50,
          count(*) FILTER (WHERE close > sma200) AS above_sma200,
          count(*) FILTER (WHERE ret_1d > 0) AS adv, count(*) FILTER (WHERE ret_1d < 0) AS dec,
          count(*) FILTER (WHERE ret_4d > 0) / count(*)::DOUBLE AS pct_up_4,
          count(*) FILTER (WHERE ret_4d < 0) / count(*)::DOUBLE AS pct_down_4,
          count(*) FILTER (WHERE is_new_high_52w) AS new_highs,
          count(*) FILTER (WHERE is_new_low_52w) AS new_lows,
          sum(volume) FILTER (WHERE ret_1d > 0) / nullif(sum(volume) FILTER (WHERE ret_1d < 0), 0) AS up_volume_ratio,
          count(*) FILTER (WHERE close < sma20 AND ret_4d < 0) / count(*)::DOUBLE AS breakdown_share,
          count(*) FILTER (WHERE sma_stack_ok) / count(*)::DOUBLE AS pct_trend_aligned,
          count(*) FILTER (WHERE rsi14 > 70) / count(*)::DOUBLE AS pct_rsi_over_70,
          median(atr14_pct) AS median_atr_pct, sum(turnover_lacs) AS total_turnover,
          sum(volume) AS total_volume
        FROM u GROUP BY date
      )
      SELECT *, above_sma20 / nullif(n_stocks, 0)::DOUBLE AS pct_above_sma20,
        above_sma50 / nullif(n_stocks, 0)::DOUBLE AS pct_above_sma50,
        CASE WHEN count(*) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) = 50 THEN
          (sum(total_turnover) OVER (ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) / 5) /
          nullif(avg(total_turnover) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 0)
        END AS turnover_ratio_5d_50d,
        above_sma200 / nullif(n_stocks, 0)::DOUBLE AS pct_above_sma200,
        adv / nullif(n_stocks, 0)::DOUBLE AS adv_share,
        dec / nullif(n_stocks, 0)::DOUBLE AS dec_share
      FROM a ORDER BY date
    """
    return _run(sql, out)


def build_market_engines(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "market_breadth" / "market_breadth.parquet"
    out = root / "market_engines" / "market_engines.parquet"
    if not source.exists():
        return _clear(out)
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""
      WITH b AS (SELECT * FROM read_parquet('{_sql_path(source)}')),
      inputs AS (
        SELECT date, 'buying_pressure' AS engine,
          (pct_up_4 + up_volume_ratio / nullif(1 + up_volume_ratio, 0)) / 2 AS input FROM b
        UNION ALL SELECT date, 'selling_pressure', (pct_down_4 + breakdown_share) / 2 FROM b
        UNION ALL SELECT date, 'leadership', (new_highs - new_lows) / nullif(n_stocks, 0)::DOUBLE FROM b
        UNION ALL SELECT date, 'liquidity', turnover_ratio_5d_50d FROM b
        UNION ALL SELECT date, 'momentum_quality', pct_trend_aligned FROM b
        UNION ALL SELECT date, 'participation', pct_above_sma50 FROM b
        UNION ALL SELECT date, 'volatility', median_atr_pct FROM b
        UNION ALL SELECT date, 'risk_exhaustion', (pct_rsi_over_70 + pct_above_sma20) / 2 FROM b
        UNION ALL SELECT date, 'structural_breadth', pct_above_sma200 FROM b
      ), numbered AS (
        SELECT *, row_number() OVER (PARTITION BY engine ORDER BY date) AS rn FROM inputs
      ), ranked AS (
        SELECT cur.date, cur.engine,
          CASE WHEN cur.input IS NULL OR count(prev.input) < {minimum_sessions} THEN NULL
               ELSE 100.0 * count(*) FILTER (WHERE prev.input <= cur.input) / count(prev.input) END AS value
        FROM numbered cur
        JOIN numbered prev ON prev.engine = cur.engine AND prev.rn BETWEEN cur.rn - {window_sessions - 1} AND cur.rn
        GROUP BY cur.date, cur.engine, cur.input
      ), scored AS (
        SELECT *, value - lag(value, 10) OVER (PARTITION BY engine ORDER BY date) AS delta_10
        FROM ranked
      )
      SELECT date, engine, round(value, 2) AS value, round(delta_10, 2) AS delta_10,
        {_label_case("value")} AS label
      FROM scored ORDER BY date, engine
    """
    return _run(sql, out)


def build_market_regime(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "market_breadth" / "market_breadth.parquet"
    out = root / "market_regime" / "market_regime.parquet"
    if not source.exists():
        return _clear(out)
    cfg = _yaml("regimes.yaml")
    rules = cfg["rules"]
    default = cfg.get("default", {"name": "Neutral", "posture": "SELECTIVE"})
    window = max(1, int(cfg.get("confidence_window_sessions", 5)))
    columns = [
        "n_stocks", "pct_above_sma20", "pct_above_sma50", "pct_above_sma200", "adv_share", "dec_share",
        "idx_close", "idx_sma50", "idx_sma200", "idx_drawdown", "idx_below_sma50", "idx_below_sma200",
    ]  # fmt: skip
    whens = " ".join(f"WHEN {compile_expr(r['when'], columns)} THEN {i}" for i, r in enumerate(rules))
    names = ", ".join(
        f"({i}, '{_quote(r['name'])}', '{_quote(r['posture'])}')" for i, r in enumerate([*rules, default])
    ).replace(f"({len(rules)},", "(-1,")
    same_prior = " + ".join(f"coalesce((regime = lag(regime, {k}) OVER w)::INT, 0)" for k in range(1, window)) or "0"
    bench = _index_base_sql(root, cfg.get("benchmark_index", "Nifty 500"))
    sql = f"""
      WITH idx AS (
        SELECT date, close AS idx_close,
          count(*) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS n50,
          avg(close) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS a50,
          count(*) OVER (ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200,
          avg(close) OVER (ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS a200,
          count(*) OVER (ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS n252,
          max(close) OVER (ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS hi252
        FROM ({bench})
      ), idxf AS (
        SELECT date, idx_close,
          CASE WHEN n50 = 50 THEN a50 END AS idx_sma50,
          CASE WHEN n200 = 200 THEN a200 END AS idx_sma200,
          CASE WHEN n252 = 252 THEN idx_close / nullif(hi252, 0) - 1 END AS idx_drawdown
        FROM idx
      ), b AS (
        SELECT m.*, i.idx_close, i.idx_sma50, i.idx_sma200, i.idx_drawdown,
          coalesce(i.idx_close < i.idx_sma50, false) AS idx_below_sma50,
          coalesce(i.idx_close < i.idx_sma200, false) AS idx_below_sma200
        FROM read_parquet('{_sql_path(source)}') m LEFT JOIN idxf i USING (date)
      ), names AS (
        SELECT * FROM (VALUES {names}) AS t(regime_idx, regime, posture)
      ), r AS (
        SELECT b.*, n.regime, n.posture FROM (
          SELECT *, CASE {whens} ELSE -1 END AS regime_idx FROM b
        ) b JOIN names n USING (regime_idx)
      ), s AS (
        SELECT *, {same_prior} AS same_prior,
          row_number() OVER (ORDER BY date) - row_number() OVER (PARTITION BY regime ORDER BY date) AS grp
        FROM r WINDOW w AS (ORDER BY date)
      )
      SELECT date, regime, posture,
        round(100.0 * (1 + same_prior) / {window}, 1) AS confidence,
        row_number() OVER (PARTITION BY regime, grp ORDER BY date) AS sessions_active,
        n_stocks, above_sma20, above_sma50, above_sma200, adv, dec,
        pct_above_sma20, pct_above_sma50, pct_above_sma200, adv_share, idx_close, idx_drawdown
      FROM s ORDER BY date
    """
    return _run(sql, out)


def rule_case(rules: list[dict[str, Any]], key: str, columns: list[str], default: str) -> str:
    whens = " ".join(f"WHEN {compile_expr(r['when'], columns)} THEN '{_quote(r[key])}'" for r in rules)
    return f"CASE {whens} ELSE '{_quote(default)}' END"


SECTOR_RANK_COLUMNS = ["rank", "n_sectors", "d1q", "d1m", "d1w"]
SECTOR_EVENT_COLUMNS = [
    *SECTOR_RANK_COLUMNS, "best_rank_63", "excess_1m", "excess_3m", "participation", "participation_chg_10",
    "flow", "rs", "strength", "exhaustion",
]  # fmt: skip


def build_sector_daily(*, parquet_dir: Path | None = None) -> int:
    """Per sector per session: RS, participation and flow scores, rank history, trend, verdict and RS line."""
    root = parquet_dir or settings.parquet_dir
    features, sectors = root / "stock_features" / "stock_features.parquet", root / "sector_map" / "sector_map.parquet"
    out = root / "sector_daily" / "sector_daily.parquet"
    if not features.exists() or not sectors.exists():
        return _clear(out)
    cfg = _yaml("sectors.yaml")
    uni = cfg.get("liquid_universe", {})
    w = cfg.get("strength_weights", {"rs": 0.4, "participation": 0.3, "flow": 0.3})
    trend_case = rule_case(cfg["trend_rules"], "trend", SECTOR_RANK_COLUMNS, cfg.get("trend_default", "STABLE"))
    verdict_case = rule_case(
        cfg["verdict_rules"], "verdict", SECTOR_RANK_COLUMNS, cfg.get("verdict_default", "NEUTRAL")
    )
    bench = _index_base_sql(root, cfg.get("benchmark_index", "Nifty 500"))
    sql = f"""
      WITH f AS (
        SELECT f.date, f.symbol, coalesce(s.sector, 'Unclassified') AS sector, f.ret_1d, f.ret_21d, f.ret_63d,
          f.close, f.sma50, f.sma_stack_ok, f.rsi14, f.turnover_lacs
        FROM read_parquet('{_sql_path(features)}') f LEFT JOIN read_parquet('{_sql_path(sectors)}') s USING (symbol)
        WHERE f.history_sessions >= {int(uni.get("min_history_sessions", 63))}
          AND f.avg_turnover20_lacs >= {float(uni.get("min_turnover_lacs", 100))}
          AND f.close >= {float(uni.get("min_close", 20))}
      ), mkt AS (
        SELECT date, median(ret_1d) AS m_1d, median(ret_21d) AS m_21, median(ret_63d) AS m_63 FROM f GROUP BY date
      ), idx AS (
        SELECT date, close / lag(close) OVER w - 1 AS b_1d, close / lag(close, 21) OVER w - 1 AS b_21,
          close / lag(close, 63) OVER w - 1 AS b_63
        FROM ({bench}) WINDOW w AS (ORDER BY date)
      ), agg AS (
        SELECT date, sector, count(*) AS n_stocks,
          median(ret_1d) AS ret_1d, median(ret_21d) AS ret_21d, median(ret_63d) AS ret_63d,
          avg((close > sma50)::INT) FILTER (WHERE sma50 IS NOT NULL) AS part_raw,
          avg(sma_stack_ok::INT) FILTER (WHERE sma_stack_ok IS NOT NULL) AS lead_raw,
          avg((rsi14 > 70)::INT) FILTER (WHERE rsi14 IS NOT NULL) AS exh_raw,
          sum(turnover_lacs) AS turnover_lacs,
          sum(turnover_lacs * sign(ret_1d)) / nullif(sum(turnover_lacs) FILTER (WHERE ret_1d IS NOT NULL), 0)
            AS flow_intensity,
          sum(turnover_lacs * sign(ret_1d)) / 100.0 AS net_flow_cr
        FROM f GROUP BY date, sector
      ), x AS (
        SELECT a.*, coalesce(i.b_1d, m.m_1d) AS b1, coalesce(i.b_21, m.m_21) AS b21, coalesce(i.b_63, m.m_63) AS b63,
          a.turnover_lacs / sum(a.turnover_lacs) OVER (PARTITION BY a.date) AS turnover_share
        FROM agg a JOIN mkt m USING (date) LEFT JOIN idx i USING (date)
      ), y AS (
        SELECT *, ret_21d - b21 AS excess_1m, ret_63d - b63 AS excess_3m,
          exp(sum(ln(1 + coalesce(ret_1d, 0)) - ln(1 + coalesce(b1, 0))) OVER (PARTITION BY sector ORDER BY date))
            AS rs_line
        FROM x
      ), c AS (
        SELECT *, count(*) OVER (PARTITION BY date) AS n_sectors,
          100 * percent_rank() OVER (PARTITION BY date ORDER BY excess_3m) AS rs,
          100 * part_raw AS participation, 100 * lead_raw AS leadership, 100 * exh_raw AS exhaustion,
          100 * percent_rank() OVER (PARTITION BY date ORDER BY flow_intensity) AS flow
        FROM y WHERE sector <> 'Unclassified'
      ), z AS (
        SELECT *, {float(w["rs"])} * rs + {float(w["participation"])} * participation + {float(w["flow"])} * flow
          AS strength
        FROM c
      ), r AS (
        SELECT *, rank() OVER (PARTITION BY date ORDER BY strength DESC, sector) AS rank FROM z
      ), l AS (
        SELECT *, lag(rank, 63) OVER s AS rank_1q, lag(rank, 42) OVER s AS rank_2m, lag(rank, 21) OVER s AS rank_1m,
          lag(rank, 5) OVER s AS rank_1w, participation - lag(participation, 10) OVER s AS participation_chg_10
        FROM r WINDOW s AS (PARTITION BY sector ORDER BY date)
      ), t AS (
        SELECT *, rank_1q - rank AS d1q, rank_1m - rank AS d1m, rank_1w - rank AS d1w FROM l
      ), v AS (
        SELECT *, {trend_case} AS rs_trend, {verdict_case} AS verdict FROM t
      ), g AS (
        SELECT *, row_number() OVER (PARTITION BY sector ORDER BY date)
          - row_number() OVER (PARTITION BY sector, verdict ORDER BY date) AS vgrp
        FROM v
      ), ranked AS (
        SELECT date, sector, n_stocks, n_sectors, rank, rank_1w, rank_1m, rank_2m, rank_1q, d1w, d1m, d1q,
          rs_trend, verdict,
          row_number() OVER (PARTITION BY sector, verdict, vgrp ORDER BY date) AS days_in_verdict,
          round(strength, 1) AS strength, round(rs, 1) AS rs, round(participation, 1) AS participation,
          round(flow, 1) AS flow, round(leadership, 1) AS leadership, round(exhaustion, 1) AS exhaustion,
          participation_chg_10, ret_21d, ret_63d, excess_1m, excess_3m, turnover_share, net_flow_cr, rs_line
        FROM g
      ), unclassified AS (
        SELECT date, 'Unclassified' AS sector, n_stocks, NULL::BIGINT AS n_sectors, NULL::BIGINT AS rank,
          NULL::BIGINT AS rank_1w, NULL::BIGINT AS rank_1m, NULL::BIGINT AS rank_2m, NULL::BIGINT AS rank_1q,
          NULL::BIGINT AS d1w, NULL::BIGINT AS d1m, NULL::BIGINT AS d1q, NULL::VARCHAR AS rs_trend,
          NULL::VARCHAR AS verdict, NULL::BIGINT AS days_in_verdict, NULL::DOUBLE AS strength, NULL::DOUBLE AS rs,
          100 * part_raw AS participation, NULL::DOUBLE AS flow, 100 * lead_raw AS leadership,
          100 * exh_raw AS exhaustion, NULL::DOUBLE AS participation_chg_10, ret_21d, ret_63d, excess_1m, excess_3m,
          turnover_share, net_flow_cr, rs_line
        FROM y WHERE sector = 'Unclassified'
      )
      SELECT * FROM ranked UNION ALL BY NAME SELECT * FROM unclassified ORDER BY date, rank NULLS LAST, sector
    """
    return _run(sql, out)


def build_sector_events(*, parquet_dir: Path | None = None) -> int:
    """Group events (rotation, distribution, accumulation) with forward excess returns of the sector RS line."""
    root = parquet_dir or settings.parquet_dir
    source = root / "sector_daily" / "sector_daily.parquet"
    out = root / "sector_events" / "sector_events.parquet"
    if not source.exists():
        return _clear(out)
    cfg = _yaml("sectors.yaml")
    leads = ", ".join(
        f"lead(rs_line, {n}) OVER s / nullif(rs_line, 0) - 1 AS fwd_{label}, lead(date, {n}) OVER s AS fwd_{label}_date"
        for label, n in FORWARD_HORIZONS
    )
    branches = []
    for ev in cfg["events"]:
        cond = compile_expr(ev["when"], SECTOR_EVENT_COLUMNS)
        branches.append(
            f"SELECT date, sector, '{_quote(ev['name'])}' AS event, '{_quote(ev['severity'])}' AS severity, "
            f"'{_quote(ev['description'])}' AS description, coalesce(({cond}), false) AS active FROM s"
        )
    sql = f"""
      WITH s AS (
        SELECT *, min(rank) OVER (PARTITION BY sector ORDER BY date ROWS BETWEEN 63 PRECEDING AND 1 PRECEDING)
            AS best_rank_63, {leads}
        FROM read_parquet('{_sql_path(source)}') WHERE rank IS NOT NULL WINDOW s AS (PARTITION BY sector ORDER BY date)
      ), conds AS ({" UNION ALL ".join(branches)}),
      flagged AS (
        SELECT *, active AND NOT coalesce(lag(active) OVER (PARTITION BY sector, event ORDER BY date), false) AS fires
        FROM conds
      )
      SELECT f.date, f.sector, f.event, f.severity, f.description,
        s.rank, s.rs_trend, s.verdict, s.fwd_1w, s.fwd_1w_date, s.fwd_1m, s.fwd_1m_date, s.fwd_3m, s.fwd_3m_date
      FROM flagged f JOIN s USING (date, sector) WHERE f.fires ORDER BY f.date, f.sector, f.event
    """
    return _run(sql, out)


def build_market_events(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "market_engines" / "market_engines.parquet"
    out = root / "market_events" / "market_events.parquet"
    if not source.exists():
        return _clear(out)
    cfg = _yaml("events.yaml")
    benchmark = _yaml("regimes.yaml").get("benchmark_index", "Nifty 500")
    branches = []
    for ev in cfg["events"]:
        branches.append(
            f"SELECT date, '{_quote(ev['name'])}' AS event_name, '{_quote(ev['severity'])}' AS severity, "
            f"'{_quote(ev['horizon'])}' AS horizon, "
            f"round(greatest(0, least(100, ({compile_expr(ev['confidence'], ENGINES)})))::DOUBLE, 2) AS confidence, "
            f"'{_quote(ev['description'])}' AS description "
            f"FROM p WHERE ({compile_expr(ev['when'], ENGINES)})"
        )
    fired = " UNION ALL ".join(branches)
    sql = f"""
      WITH p AS ({_engine_pivot_sql(source)}),
      bench AS ({_benchmark_sql(root, benchmark)}),
      sess AS (SELECT date, row_number() OVER (ORDER BY date) AS s FROM (SELECT DISTINCT date FROM p)),
      fired AS ({fired}),
      j AS (SELECT f.*, sess.s FROM fired f JOIN sess USING (date)),
      g AS (SELECT *, s - row_number() OVER (PARTITION BY event_name ORDER BY s) AS grp FROM j)
      SELECT g.date, g.event_name AS event, g.severity, g.horizon, g.confidence, g.description,
        row_number() OVER (PARTITION BY g.event_name, g.grp ORDER BY g.date) AS streak,
        b.fwd_1w, b.fwd_1w_date, b.fwd_1m, b.fwd_1m_date, b.fwd_3m, b.fwd_3m_date
      FROM g LEFT JOIN bench b ON b.date = g.date ORDER BY g.date, g.event_name
    """
    return _run(sql, out)


def build_market_analogues(*, parquet_dir: Path | None = None) -> int:
    """For each session, the most similar earlier sessions (engine-profile distance) with forward index returns.

    Matches are at least `MIN_ANALOGUE_GAP_DAYS` before the date and at least that far apart from each other. A
    forward return is only shown when it had already been observed on the date being explained.
    """
    root = parquet_dir or settings.parquet_dir
    source = root / "market_engines" / "market_engines.parquet"
    out = root / "market_analogues" / "market_analogues.parquet"
    if not source.exists():
        return _clear(out)
    benchmark = _yaml("regimes.yaml").get("benchmark_index", "Nifty 500")

    con = duckdb.connect()
    try:
        cols = ", ".join(f'"{e}"' for e in ENGINES)
        where = " AND ".join(f'"{e}" IS NOT NULL' for e in ENGINES)
        frame = con.execute(f"SELECT date, {cols} FROM ({_engine_pivot_sql(source)}) WHERE {where} ORDER BY date").pl()
        pairs = _analogue_pairs(frame)
        schema = {"date": pl.Date, "analogue_date": pl.Date, "rank": pl.Int32, "distance": pl.Float64}
        pair_df = pl.DataFrame(pairs, schema=schema, orient="row")
        con.register("pairs", pair_df)
        masked = ", ".join(
            f"CASE WHEN b.fwd_{label}_date <= p.date THEN b.fwd_{label} END AS fwd_{label}"
            for label, _ in FORWARD_HORIZONS
        )
        sql = f"""
          WITH bench AS ({_benchmark_sql(root, benchmark)})
          SELECT p.date, p.analogue_date, p.rank, round(p.distance, 2) AS distance,
            round(greatest(0, 100 - p.distance), 2) AS similarity_pct, {masked}
          FROM pairs p LEFT JOIN bench b ON b.date = p.analogue_date ORDER BY p.date, p.rank
        """
        return _publish(con, sql, out)
    finally:
        con.close()


def _analogue_pairs(frame: pl.DataFrame) -> list[tuple[Any, Any, int, float]]:
    if frame.is_empty():
        return []
    dates = frame["date"].to_numpy().astype("datetime64[D]")
    matrix = frame.select(list(ENGINES)).to_numpy().astype(float)
    gap = np.timedelta64(MIN_ANALOGUE_GAP_DAYS, "D")
    result: list[tuple[Any, Any, int, float]] = []
    for i in range(len(dates)):
        hi = int(np.searchsorted(dates, dates[i] - gap, side="right"))
        if hi == 0:
            continue
        distance = np.abs(matrix[:hi] - matrix[i]).mean(axis=1)
        chosen: list[int] = []
        for j in np.argsort(distance, kind="stable"):
            if all(abs(dates[j] - dates[k]) >= gap for k in chosen):
                chosen.append(int(j))
                if len(chosen) == ANALOGUES_PER_DATE:
                    break
        result.extend(
            (dates[i].astype(object), dates[j].astype(object), rank, float(distance[j]))
            for rank, j in enumerate(chosen, start=1)
        )
    return result


def build_scan_results(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "stock_features" / "stock_features.parquet"
    out = root / "scan_results" / "scan_results.parquet"
    if not source.exists():
        return _clear(out)
    cfg = _yaml("scans.yaml")
    con = duckdb.connect()
    try:
        columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{_sql_path(source)}')").fetchall()]
        universe = compile_expr(cfg["universe"], columns)
        branches = [
            f"SELECT date, '{_quote(s['id'])}' AS scan_id, symbol, '{_quote(s['state'])}' AS state, "
            f"greatest(0, least(100, ({compile_expr(s['score'], columns)})))::DOUBLE AS score "
            f"FROM f WHERE ({compile_expr(s['when'], columns)})"
            for s in cfg["scans"]
        ]
        sql = f"""
          WITH f AS (SELECT * FROM read_parquet('{_sql_path(source)}') WHERE {universe}),
          r AS ({" UNION ALL ".join(branches)})
          SELECT date, scan_id, symbol, state,
            row_number() OVER (PARTITION BY date, scan_id ORDER BY score DESC, symbol) AS rank,
            round(score, 2) AS score
          FROM r ORDER BY date, scan_id, rank
        """
        return _publish(con, sql, out)
    finally:
        con.close()


@dataclass
class StepResult:
    name: str
    rows: int | None = None
    error: str | None = None
    seconds: float = 0.0


class AnalyticsError(RuntimeError):
    def __init__(self, steps: list[StepResult]) -> None:
        self.steps = steps
        failed = [f"{s.name}: {s.error}" for s in steps if s.error]
        super().__init__("analytics steps failed: " + "; ".join(failed))


STEPS: tuple[tuple[str, Callable[..., int], tuple[str, ...]], ...] = (
    ("stock_features", build_stock_features, ()),
    ("market_breadth", build_market_breadth, ("stock_features",)),
    ("market_engines", build_market_engines, ("market_breadth",)),
    ("market_regime", build_market_regime, ("market_breadth",)),
    ("sector_daily", build_sector_daily, ("stock_features",)),
    ("sector_events", build_sector_events, ("sector_daily",)),
    ("market_events", build_market_events, ("market_engines",)),
    ("market_analogues", build_market_analogues, ("market_engines",)),
    ("scan_results", build_scan_results, ("stock_features",)),
)


def run_analytics(*, parquet_dir: Path | None = None) -> list[StepResult]:
    """Run every step; a step whose upstream failed is skipped. Never raises."""
    root = parquet_dir or settings.parquet_dir
    results: dict[str, StepResult] = {}
    for name, fn, deps in STEPS:
        failed_dep = next((d for d in deps if results[d].error), None)
        if failed_dep:
            results[name] = StepResult(name, error=f"skipped: upstream {failed_dep} failed")
            continue
        start = time.perf_counter()
        try:
            results[name] = StepResult(name, rows=fn(parquet_dir=root))
        except Exception as exc:  # recorded per step so one failure is visible, not hidden
            results[name] = StepResult(name, error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        results[name].seconds = round(time.perf_counter() - start, 2)
    return list(results.values())


def build_analytics(*, parquet_dir: Path | None = None) -> dict[str, int]:
    """Run all steps and return rows per step; raises `AnalyticsError` if any step failed."""
    steps = run_analytics(parquet_dir=parquet_dir)
    if any(s.error for s in steps):
        raise AnalyticsError(steps)
    return {s.name: int(s.rows or 0) for s in steps}
