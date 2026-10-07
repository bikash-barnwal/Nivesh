"""Deterministic analytics snapshots built from adjusted stock_daily Parquet."""

from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from nivesh.config import settings


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


def _engine_config() -> tuple[int, int]:
    path = settings.context_dir / "engines.yaml"
    if not path.exists():
        return 756, 120
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return int(config.get("window_sessions", 756)), int(config.get("minimum_sessions", 120))


def _write_query(con: duckdb.DuckDBPyConnection, sql: str, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY ({sql}) TO '{_sql_path(output)}' (FORMAT parquet, COMPRESSION zstd)")
    return int(con.execute(f"SELECT count(*) FROM read_parquet('{_sql_path(output)}')").fetchone()[0])


def build_stock_features(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "stock_daily" / "**" / "*.parquet"
    if not list((root / "stock_daily").glob("**/*.parquet")):
        return 0
    sql = f"""
        WITH base AS (
          SELECT *,
            lag(close, 1) OVER w AS close_1,
            lag(close, 4) OVER w AS close_4,
            lag(close, 21) OVER w AS close_21,
            count(*) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS
            history_sessions,
            count(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n20,
            count(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS n50,
            count(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 149 PRECEDING AND CURRENT ROW) AS n150,
            count(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS n200,
            count(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS n252,
            count(volume) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS
            n_volume20,
            avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20_raw,
            avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50_raw,
            avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 149 PRECEDING AND CURRENT ROW) AS
            sma150_raw,
            avg(close) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS
            sma200_raw,
            avg(volume) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS
            avg_volume20_raw,
            avg(turnover_lacs) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS
            avg_turnover20_lacs,
            max(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS
            high_52w_raw,
            min(low) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w_raw
          FROM read_parquet('{_sql_path(source)}', hive_partitioning=true, union_by_name=true)
          WHERE series = 'EQ'
          WINDOW w AS (PARTITION BY symbol ORDER BY date)
        ), tr AS (
          SELECT *, greatest(high - low, abs(high - close_1), abs(low - close_1)) AS true_range_raw
          FROM base
        ), windows AS (
          SELECT *,
            count(true_range_raw) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW)
            AS n_atr14,
            avg(true_range_raw) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS
            atr14_raw,
            avg(greatest(close - close_1, 0)) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND
            CURRENT ROW) AS gain14,
            avg(greatest(close_1 - close, 0)) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 13 PRECEDING AND
            CURRENT ROW) AS loss14
          FROM tr
        ), values AS (
          SELECT *,
            CASE WHEN n20 = 20 THEN sma20_raw END AS sma20,
            CASE WHEN n50 = 50 THEN sma50_raw END AS sma50,
            CASE WHEN n150 = 150 THEN sma150_raw END AS sma150,
            CASE WHEN n200 = 200 THEN sma200_raw END AS sma200,
            CASE WHEN n252 = 252 THEN high_52w_raw END AS high_52w,
            CASE WHEN n252 = 252 THEN low_52w_raw END AS low_52w,
            CASE WHEN n_volume20 = 20 THEN avg_volume20_raw END AS avg_volume_20,
            CASE WHEN n_atr14 = 14 THEN atr14_raw END AS atr14,
            CASE WHEN n_atr14 = 14 THEN atr14_raw / nullif(close, 0) END AS atr14_pct,
            CASE WHEN n_atr14 = 14 THEN 100 * gain14 / nullif(gain14 + loss14, 0) END AS rsi14,
            CASE WHEN n20 = 20 THEN close / nullif(close_1, 0) - 1 END AS ret_1d,
            CASE WHEN n20 = 20 THEN close / nullif(close_4, 0) - 1 END AS ret_4d,
            CASE WHEN n20 = 20 THEN close / nullif(close_21, 0) - 1 END AS ret_21d,
            CASE WHEN n20 = 20 THEN close - close_1 END AS close_change
          FROM windows
        ), final AS (
          SELECT *, lag(sma150, 20) OVER (PARTITION BY symbol ORDER BY date) AS sma150_20ago
          FROM values
        )
        SELECT symbol, date, close, volume, turnover_lacs, history_sessions,
          sma20, sma50, sma150, sma200, ret_1d, ret_4d, ret_21d,
          (high - low) / nullif(close, 0) AS range_pct,
          true_range_raw AS true_range, atr14, atr14_pct, rsi14,
          close / nullif(sma20, 0) - 1 AS close_vs_sma20_pct,
          volume / nullif(avg_volume_20, 0) AS rvol20,
          avg_turnover20_lacs, high_52w, low_52w,
          close / nullif(high_52w, 0) - 1 AS dist_52w_high_pct,
          close / nullif(low_52w, 0) - 1 AS dist_52w_low_pct,
          CASE WHEN sma200 IS NULL THEN NULL ELSE close > sma20 AND sma20 > sma50 AND sma50 > sma200 END AS
          sma_stack_ok,
          CASE WHEN sma150 IS NULL OR sma150_20ago IS NULL OR sma200 IS NULL THEN NULL
               WHEN sma150 > sma150_20ago AND close >= sma150 AND close >= sma200 THEN 2
               WHEN sma150 <= sma150_20ago AND close >= sma150 AND close >= sma200 THEN 3
               WHEN sma150 > sma150_20ago AND close < sma150 THEN 1 ELSE 4 END AS stage,
          CASE WHEN sma150 IS NULL OR sma150_20ago IS NULL OR sma200 IS NULL THEN NULL
               ELSE sma150 - sma150_20ago END AS sma150_slope20
        FROM final ORDER BY symbol, date
    """
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "stock_features" / "stock_features.parquet")
    finally:
        con.close()


def build_market_breadth(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "stock_features" / "stock_features.parquet"
    if not source.exists():
        return 0
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
          count(*) FILTER (WHERE close >= high_52w) AS new_highs,
          count(*) FILTER (WHERE close <= low_52w) AS new_lows,
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
        sum(total_turnover) OVER (ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) /
          nullif(avg(total_turnover) OVER (ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW), 0) / 5
          AS turnover_ratio_5d_50d,
        above_sma200 / nullif(n_stocks, 0)::DOUBLE AS pct_above_sma200,
        adv / nullif(n_stocks, 0)::DOUBLE AS adv_share,
        dec / nullif(n_stocks, 0)::DOUBLE AS dec_share
      FROM a ORDER BY date
    """
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "market_breadth" / "market_breadth.parquet")
    finally:
        con.close()


def build_market_engines(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "market_breadth" / "market_breadth.parquet"
    if not source.exists():
        return 0
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""
      WITH b AS (SELECT * FROM read_parquet('{_sql_path(source)}')),
      inputs AS (
        SELECT date, 'buying_pressure' AS engine, (pct_up_4 + up_volume_ratio / nullif(1 + up_volume_ratio, 0)) / 2
        AS input FROM b
        UNION ALL SELECT date, 'selling_pressure', (pct_down_4 + breakdown_share) / 2 FROM b
        UNION ALL SELECT date, 'leadership', (new_highs - new_lows) / nullif(n_stocks, 0)::DOUBLE FROM b
        UNION ALL SELECT date, 'liquidity', turnover_ratio_5d_50d FROM b
        UNION ALL SELECT date, 'momentum_quality', pct_trend_aligned FROM b
        UNION ALL SELECT date, 'participation', pct_above_sma50 FROM b
        UNION ALL SELECT date, 'volatility', median_atr_pct FROM b
        UNION ALL SELECT date, 'risk_exhaustion', (pct_rsi_over_70 + pct_above_sma20) / 2 FROM b
        UNION ALL SELECT date, 'structural_breadth', pct_above_sma200 FROM b
      ), ranked AS (
        SELECT cur.date, cur.engine,
          CASE WHEN count(prev.input) < {minimum_sessions} THEN NULL
               ELSE 100 * count(*) FILTER (WHERE prev.input <= cur.input) /
          nullif(count(prev.input), 0) END AS value
        FROM inputs cur JOIN inputs prev ON prev.engine = cur.engine
          AND prev.date <= cur.date AND prev.date > cur.date - INTERVAL {window_sessions} DAY
        GROUP BY cur.date, cur.engine
      ), scored AS (
        SELECT *, value - lag(value, 10) OVER (PARTITION BY engine ORDER BY date) AS delta_10
        FROM ranked
      )
      SELECT date, engine, round(value, 2) AS value, round(delta_10, 2) AS delta_10,
        CASE WHEN value >= 75 THEN 'Very Strong' WHEN value >= 55 THEN 'Strong' WHEN value >= 40 THEN 'Neutral' WHEN
        value >= 20 THEN 'Weak' ELSE 'Very Weak' END AS label
      FROM scored ORDER BY date, engine
    """
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "market_engines" / "market_engines.parquet")
    finally:
        con.close()


def build_market_regime(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "market_breadth" / "market_breadth.parquet"
    if not source.exists():
        return 0
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""
      SELECT date, CASE WHEN pct_above_sma200 < .20 AND pct_above_sma50 < .35 THEN 'Bear'
        WHEN pct_above_sma200 < .35 AND pct_above_sma50 < .50 THEN 'Correction'
        WHEN pct_above_sma200 >= .70 AND pct_above_sma50 >= .60 AND adv_share >= .55 THEN 'Broad Bull'
        WHEN pct_above_sma200 >= .50 AND pct_above_sma50 >= .50 THEN 'Early Bull' ELSE 'Neutral' END AS regime,
        round(100 * greatest(pct_above_sma200, pct_above_sma50), 2) AS confidence,
        CASE WHEN pct_above_sma200 >= .50 AND pct_above_sma50 >= .50 THEN 'RISK ON' WHEN pct_above_sma200 < .35 THEN
        'DEFENSIVE' ELSE 'SELECTIVE' END AS posture,
        n_stocks, above_sma20, above_sma50, above_sma200, adv, dec
      FROM read_parquet('{_sql_path(source)}') ORDER BY date
    """
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "market_regime" / "market_regime.parquet")
    finally:
        con.close()


def build_sector_daily(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    features, sectors = root / "stock_features" / "stock_features.parquet", root / "sector_map" / "sector_map.parquet"
    if not features.exists() or not sectors.exists():
        return 0
    sql = f"""SELECT f.date, s.sector, count(*) AS n_stocks, avg(f.ret_21d) AS return_21d,
      avg(f.close_vs_sma20_pct) AS strength, count(*) FILTER (WHERE f.close > f.sma50) / count(*)::DOUBLE AS
      participation,
      count(*) FILTER (WHERE f.sma_stack_ok) / count(*)::DOUBLE AS leadership,
      rank() OVER (PARTITION BY f.date ORDER BY avg(f.ret_21d) DESC) AS rank
      FROM read_parquet('{_sql_path(features)}') f JOIN read_parquet('{_sql_path(sectors)}') s USING (symbol)
      WHERE s.sector IS NOT NULL GROUP BY f.date, s.sector ORDER BY f.date, rank"""
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "sector_daily" / "sector_daily.parquet")
    finally:
        con.close()


def build_market_events(*, parquet_dir: Path | None = None) -> int:
    root, source = (
        parquet_dir or settings.parquet_dir,
        (parquet_dir or settings.parquet_dir) / "market_engines" / "market_engines.parquet",
    )
    if not source.exists():
        return 0
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""WITH p AS (SELECT date, max(value) FILTER (WHERE engine='selling_pressure') selling,
      max(value) FILTER (WHERE engine='buying_pressure') buying, max(value) FILTER (WHERE engine='volatility')
      volatility,
      max(value) FILTER (WHERE engine='leadership') leadership, max(value) FILTER (WHERE engine='structural_breadth')
      structural
      FROM read_parquet('{_sql_path(source)}') GROUP BY date)
      SELECT date,event,severity,horizon,round(confidence,2) confidence,description FROM (
      SELECT date,'vocal_distribution' event,'CRITICAL' severity,'1-10d' horizon,(selling+100-structural)/2
      confidence,'Selling pressure is elevated while structural breadth is weak.' description FROM p WHERE
      selling>=75 AND structural<=35
      UNION ALL SELECT date,'breadth_thrust','INFO','1-10d',(buying+structural)/2,'Buying pressure and structural
      breadth are broadening.' FROM p WHERE buying>=75 AND structural>=65
      UNION ALL SELECT date,'volatility_explosion','WARNING','1-10d',volatility,'Market volatility is in the highest
      deterministic alert band.' FROM p WHERE volatility>=75
      UNION ALL SELECT date,'leadership_collapse','WARNING','1-3m',100-leadership,'Leadership breadth is weak
      relative to the tracked universe.' FROM p WHERE leadership<=25) events ORDER BY date,event"""
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "market_events" / "market_events.parquet")
    finally:
        con.close()


def build_market_analogues(*, parquet_dir: Path | None = None) -> int:
    root, source = (
        parquet_dir or settings.parquet_dir,
        (parquet_dir or settings.parquet_dir) / "market_engines" / "market_engines.parquet",
    )
    if not source.exists():
        return 0
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""WITH v AS (SELECT date,
      avg(value) FILTER (WHERE engine='buying_pressure') buying, avg(value) FILTER (WHERE engine='selling_pressure')
      selling,
      avg(value) FILTER (WHERE engine='leadership') leadership, avg(value) FILTER (WHERE engine='liquidity')
      liquidity,
      avg(value) FILTER (WHERE engine='momentum_quality') momentum, avg(value) FILTER (WHERE engine='participation')
      participation,
      avg(value) FILTER (WHERE engine='volatility') volatility, avg(value) FILTER (WHERE engine='risk_exhaustion')
      risk,
      avg(value) FILTER (WHERE engine='structural_breadth') structural FROM read_parquet('{_sql_path(source)}') GROUP
      BY date),
      indexed AS (SELECT *, row_number() OVER (ORDER BY date) AS session FROM v), pairs AS (
      SELECT a.date, b.date analogue_date,
        (abs(a.buying-b.buying)+abs(a.selling-b.selling)+abs(a.leadership-b.leadership)+abs(a.liquid
        ty-b.liquidity)+abs(a.momentum-b.momentum)+abs(a.participation-b.participation)+abs(a.volati
        ity-b.volatility)+abs(a.risk-b.risk)+abs(a.structural-b.structural))/9 distance
      FROM indexed a JOIN indexed b ON b.date < a.date AND date_diff('day',b.date,a.date)>=42 AND a.session>=120),
      ranked AS (
      SELECT *, row_number() OVER (PARTITION BY date ORDER BY distance, analogue_date) AS rn FROM pairs)
      SELECT date, analogue_date, round(distance,2) distance, round(greatest(0,100-distance),2) similarity_pct,
        NULL::DOUBLE fwd_1w, NULL::DOUBLE fwd_1m, NULL::DOUBLE fwd_3m FROM ranked WHERE rn<=5 ORDER BY date, rn"""
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "market_analogues" / "market_analogues.parquet")
    finally:
        con.close()


def build_scan_results(*, parquet_dir: Path | None = None) -> int:
    root = parquet_dir or settings.parquet_dir
    source = root / "stock_features" / "stock_features.parquet"
    if not source.exists():
        return 0
    window_sessions, minimum_sessions = _engine_config()
    sql = f"""WITH c AS (SELECT date,symbol,close,ret_21d,rvol20,dist_52w_high_pct,range_pct,atr14_pct,
      close_vs_sma20_pct,sma_stack_ok,
      CASE WHEN close_vs_sma20_pct BETWEEN 0 AND .05 AND rvol20>=1.2 THEN 70+least(30,rvol20*10) END early_entry,
      CASE WHEN dist_52w_high_pct>=-.03 AND rvol20>=1.5 THEN 70+least(30,rvol20*10) END breakout,
      CASE WHEN range_pct<=.05 AND atr14_pct<=.05 THEN 60+least(40,(.05-range_pct)*400) END tight_base,
      CASE WHEN dist_52w_high_pct>=-.10 AND ret_21d>0 AND sma_stack_ok THEN 65+least(35,ret_21d*100) END leaders,
      CASE WHEN rvol20>=1.5 AND ret_21d>0 THEN 60+least(40,rvol20*10) END accumulation FROM
      read_parquet('{_sql_path(source)}')),
      r AS (SELECT date,'early_entry' scan_id,symbol,early_entry score,'smart-money-turn' state FROM c WHERE
      early_entry IS NOT NULL UNION ALL
      SELECT date,'breakout_volume',symbol,breakout,'breakout' FROM c WHERE breakout IS NOT NULL UNION ALL
      SELECT date,'vcp_tight_base',symbol,tight_base,'tight-base' FROM c WHERE tight_base IS NOT NULL UNION ALL
      SELECT date,'leaders_near_highs',symbol,leaders,'leader' FROM c WHERE leaders IS NOT NULL UNION ALL
      SELECT date,'accumulation',symbol,accumulation,'accumulation' FROM c WHERE accumulation IS NOT NULL)
      SELECT date,scan_id,symbol,state,row_number() OVER (PARTITION BY date,scan_id ORDER BY score DESC,symbol)
      rank,round(greatest(0,least(100,score)),2) score FROM r ORDER BY date,scan_id,rank"""
    con = duckdb.connect()
    try:
        return _write_query(con, sql, root / "scan_results" / "scan_results.parquet")
    finally:
        con.close()


def build_analytics(*, parquet_dir: Path | None = None) -> dict[str, int]:
    root = parquet_dir or settings.parquet_dir
    return {
        "stock_features": build_stock_features(parquet_dir=root),
        "market_breadth": build_market_breadth(parquet_dir=root),
        "market_regime": build_market_regime(parquet_dir=root),
        "market_engines": build_market_engines(parquet_dir=root),
        "sector_daily": build_sector_daily(parquet_dir=root),
        "market_events": build_market_events(parquet_dir=root),
        "market_analogues": build_market_analogues(parquet_dir=root),
        "scan_results": build_scan_results(parquet_dir=root),
    }
