"""Event study: what did stocks do after each event type, relative to the whole market on the same days?

An event is first usable at the close of its `available_from` session (see `extract.available_from`); the study enters
at the next session's open, exactly as the backtester fills signals, and measures the close `k` sessions after entry.
The baseline is the equal-weight average of the same measure over all liquid stocks on the same entry date, so market
moves cancel. An event type may be referenced by a strategy only after its study shows a stable positive edge.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from nivesh.config import settings

HORIZONS = (1, 5, 20)
LIQUID_TURNOVER_LACS = 50

STUDY_SQL = """
WITH px AS (
    SELECT symbol, date, close, turnover_lacs,
           lead(open, 1) OVER w AS entry_open,
           {leads}
    FROM read_parquet('{prices}', hive_partitioning = false)
    WHERE series = 'EQ'
    WINDOW w AS (PARTITION BY symbol ORDER BY date)
),
fwd AS (
    SELECT symbol, date, turnover_lacs, {fwds} FROM px WHERE entry_open > 0
),
base AS (
    SELECT date, {bases} FROM fwd WHERE turnover_lacs >= {liquid} GROUP BY date
),
ev AS (
    SELECT * FROM read_parquet('{events}') WHERE confidence >= {min_conf}
),
hit AS (
    SELECT ev.event_type, ev.direction, ev.symbol, ev.available_from, f.date AS entry_signal_date,
           {excess}
    FROM ev ASOF JOIN fwd f ON ev.symbol = f.symbol AND ev.available_from <= f.date
    JOIN base b ON b.date = f.date
)
SELECT event_type, direction, count(*) AS n, {stats}
FROM hit GROUP BY event_type, direction ORDER BY event_type
"""


def run_study(
    *, parquet_dir: Path | None = None, min_confidence: float = 0.6, write: bool = True
) -> list[dict[str, object]]:
    parquet_dir = parquet_dir or settings.parquet_dir
    events = parquet_dir / "stock_events" / "stock_events.parquet"
    prices = parquet_dir / "stock_daily" / "stock_daily.parquet"
    if not events.exists() or not prices.exists():
        return []
    leads = ", ".join(f"lead(close, {k}) OVER w AS close_{k}" for k in HORIZONS)
    fwds = ", ".join(f"close_{k} / entry_open - 1 AS r{k}" for k in HORIZONS)
    bases = ", ".join(f"avg(r{k}) AS b{k}" for k in HORIZONS)
    excess = ", ".join(f"f.r{k} - b.b{k} AS x{k}" for k in HORIZONS)
    stats = ", ".join(
        f"avg(x{k}) AS mean_excess_{k}d, "
        f"avg((x{k} > 0)::INT) AS hit_rate_{k}d, "
        f"avg(x{k}) / nullif(stddev_samp(x{k}) / sqrt(count(x{k})), 0) AS t_stat_{k}d"
        for k in HORIZONS
    )
    sql = STUDY_SQL.format(
        leads=leads,
        fwds=fwds,
        bases=bases,
        excess=excess,
        stats=stats,
        liquid=LIQUID_TURNOVER_LACS,
        min_conf=min_confidence,
        prices=prices.as_posix().replace("'", "''"),
        events=events.as_posix().replace("'", "''"),
    )
    con = duckdb.connect()
    try:
        cursor = con.execute(sql)
        cols = [d[0] for d in cursor.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]
        if write:
            out = parquet_dir / "stock_event_study" / "stock_event_study.parquet"
            out.parent.mkdir(parents=True, exist_ok=True)
            con.execute(f"COPY ({sql}) TO '{out.as_posix()}' (FORMAT parquet)")
    finally:
        con.close()
    return rows
