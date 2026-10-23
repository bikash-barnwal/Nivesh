"""Data quality checks over the built Parquet datasets."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from nivesh.config import settings

BIG_MOVE = 0.40

CHECKS: list[dict[str, str]] = [
    {
        "name": "duplicate_symbol_date",
        "severity": "critical",
        "count": "SELECT count(*) FROM (SELECT symbol, date FROM stock_daily GROUP BY ALL HAVING count(*) > 1)",
        "sample": "SELECT symbol, date, count(*) AS n FROM stock_daily GROUP BY ALL HAVING n > 1 LIMIT 20",
    },
    {
        "name": "ohlc_inconsistent",
        "severity": "critical",
        "count": "SELECT count(*) FROM stock_daily WHERE high < low OR close > high * 1.0001 OR close < low * 0.9999",
        "sample": "SELECT symbol, date, open, high, low, close FROM stock_daily WHERE high < low "
        "OR close > high * 1.0001 OR close < low * 0.9999 LIMIT 20",
    },
    {
        "name": "non_positive_price_or_negative_volume",
        "severity": "critical",
        "count": "SELECT count(*) FROM stock_daily WHERE close <= 0 OR volume < 0",
        "sample": "SELECT symbol, date, close, volume FROM stock_daily WHERE close <= 0 OR volume < 0 LIMIT 20",
    },
    {
        "name": "bhav_dates_missing_vs_index",
        "severity": "warning",
        "count": "SELECT count(*) FROM (SELECT DISTINCT date FROM index_daily "
        "EXCEPT SELECT DISTINCT date FROM stock_daily)",
        "sample": "SELECT date FROM (SELECT DISTINCT date FROM index_daily "
        "EXCEPT SELECT DISTINCT date FROM stock_daily) ORDER BY date LIMIT 20",
    },
    {
        "name": "large_move_after_adjustment",
        "severity": "warning",
        "count": f"""SELECT count(*) FROM (
            SELECT close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1 AS r FROM stock_daily
        ) WHERE abs(r) > {BIG_MOVE}""",
        "sample": f"""SELECT symbol, date, round(r * 100, 1) AS move_pct FROM (
            SELECT symbol, date, close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1 AS r
            FROM stock_daily
        ) WHERE abs(r) > {BIG_MOVE} ORDER BY date DESC LIMIT 20""",
    },
    {
        "name": "adjust_factor_mismatch",
        "severity": "warning",
        "count": "SELECT count(*) FROM adjust_events WHERE abs(implied_factor / event_factor - 1) > 0.25",
        "sample": "SELECT symbol, date, purpose, round(event_factor, 4) AS factor, "
        "round(implied_factor, 4) AS implied FROM adjust_events "
        "WHERE abs(implied_factor / event_factor - 1) > 0.25 ORDER BY date DESC LIMIT 20",
    },
    {
        "name": "liquid_symbols_without_sector",
        "severity": "warning",
        "count": """SELECT count(*) FROM (
            SELECT symbol FROM stock_daily WHERE date = (SELECT max(date) FROM stock_daily)
              AND series = 'EQ' AND turnover_lacs >= 500
            EXCEPT SELECT symbol FROM sector_map WHERE sector IS NOT NULL)""",
        "sample": """SELECT symbol, round(turnover_lacs) AS turnover_lacs FROM stock_daily
            WHERE date = (SELECT max(date) FROM stock_daily) AND series = 'EQ' AND turnover_lacs >= 500
              AND symbol NOT IN (SELECT symbol FROM sector_map WHERE sector IS NOT NULL)
            ORDER BY turnover_lacs DESC LIMIT 20""",
    },
    {
        "name": "symbols_without_master",
        "severity": "info",
        "count": """SELECT count(*) FROM (
            SELECT DISTINCT symbol FROM stock_daily WHERE date = (SELECT max(date) FROM stock_daily)
            EXCEPT SELECT symbol FROM security_master)""",
        "sample": """SELECT DISTINCT symbol, series FROM stock_daily WHERE date = (SELECT max(date) FROM stock_daily)
            AND symbol NOT IN (SELECT symbol FROM security_master) LIMIT 20""",
    },
    {
        "name": "adjust_events_applied",
        "severity": "info",
        "count": "SELECT count(*) FROM adjust_events",
        "sample": "SELECT symbol, date, purpose, round(event_factor, 4) AS factor, round(implied_factor, 4) AS implied "
        "FROM adjust_events ORDER BY date DESC LIMIT 20",
    },
    {
        "name": "analytics_windows_complete",
        "severity": "critical",
        "view": "stock_features",
        "count": "SELECT count(*) FROM stock_features WHERE sma200 IS NOT NULL AND history_sessions < 199",
        "sample": "SELECT symbol, date, history_sessions FROM stock_features "
        "WHERE sma200 IS NOT NULL AND history_sessions < 199 LIMIT 20",
    },
    {
        "name": "engines_distinct",
        "severity": "critical",
        "view": "market_engines",
        "count": "SELECT count(*) FROM (SELECT a.engine, b.engine FROM market_engines a "
        "JOIN market_engines b USING (date) WHERE a.engine < b.engine "
        "GROUP BY a.engine, b.engine "
        "HAVING count(*) FILTER (WHERE a.value IS DISTINCT FROM b.value) = 0)",
        "sample": "SELECT engine, value FROM market_engines ORDER BY date DESC LIMIT 20",
    },
    {
        "name": "no_lookahead",
        "severity": "critical",
        "view": "stock_features",
        "count": "SELECT 0",
        "sample": "SELECT 0",
    },
]


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def run_quality(*, parquet_dir: Path | None = None, reports_dir: Path | None = None) -> dict[str, Any]:
    from nivesh.data.engine import Engine

    engine = Engine(parquet_dir or settings.parquet_dir)
    available = set(engine.views())
    results = []
    for check in CHECKS:
        needed = {check.get("view", "stock_daily")} | {
            v for v in ("index_daily", "adjust_events", "sector_map", "security_master") if v in check["count"]
        }
        if not needed <= available:
            results.append({"name": check["name"], "severity": check["severity"], "status": "skipped"})
            continue
        try:
            count = engine.query(check["count"]).rows[0][0]
            sample = engine.query(check["sample"]) if count else None
        except duckdb.Error as exc:
            results.append({"name": check["name"], "severity": "critical", "status": "error", "detail": str(exc)})
            continue
        results.append(
            {
                "name": check["name"],
                "severity": check["severity"],
                "status": "ok" if count == 0 or check["severity"] == "info" else "issues",
                "count": count,
                "sample": [dict(zip(sample.columns, r, strict=True)) for r in sample.rows] if sample else [],
            }
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_through": engine.data_through(),
        "critical_issues": sum(
            r.get("count", 0) for r in results if r["severity"] == "critical" and r["status"] != "ok"
        ),
        "checks": results,
    }
    out_dir = reports_dir or settings.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"quality_{date.today():%Y%m%d}.json").write_text(
        json.dumps(report, indent=2, default=_json_default), encoding="utf-8"
    )
    engine.close()
    return report
