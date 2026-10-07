"""Parse raw NSE files into Parquet and build the adjusted `stock_daily` dataset.

Outputs (under data/parquet/):
  stock_daily_raw/year=YYYY/part.parquet      as published, EQ/BE/BZ series, one row per (symbol, date)
  index_daily/year=YYYY/part.parquet
  corporate_actions/corporate_actions.parquet from the PR zip `bc` files, with parsed price factors
  stock_daily/stock_daily.parquet             back-adjusted for splits, consolidations and bonuses
  adjust_events/adjust_events.parquet         every factor applied (audit trail)

NSE's PREV_CLOSE is NOT adjusted on ex-dates, so adjustment relies on the corporate actions file:
  - splits / consolidations / bonuses: factor parsed from the purpose text
  - demergers: market-implied factor = ex-date open (special pre-open price discovery) / previous close
Rights issues and dividends are stored but not adjusted for yet.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from nivesh.config import settings

log = logging.getLogger(__name__)

KEEP_SERIES = ("EQ", "BE", "BZ")
SERIES_PRIORITY = {"EQ": 0, "BE": 1, "BZ": 2}

_BONUS = re.compile(r"BONUS\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)")
_FACE_VALUE = re.compile(
    r"(SPLT|SPLIT|SUB-?DIVISION|CONSOLIDATION|CONSOLDN|CONSOL)\D*?"
    r"(?:FRM|FROM)\s*R[SE]\.?\s*(\d+(?:\.\d+)?)\D*?TO\s*R[SE]\.?\s*(\d+(?:\.\d+)?)"
)


def price_factor(purpose: str) -> float | None:
    """Multiplier for pre-ex-date prices implied by a corporate action purpose, or None.

    BONUS a:b  → a new shares per b held → prices × b / (a + b)
    FV split / consolidation from RS x to RS y → prices × y / x
    """
    text = purpose.upper()
    factor = 1.0
    found = False
    for m in _FACE_VALUE.finditer(text):
        old, new = float(m.group(2)), float(m.group(3))
        if old > 0 and new > 0 and old != new:
            factor *= new / old
            found = True
    for m in _BONUS.finditer(text):
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            factor *= b / (a + b)
            found = True
    return factor if found else None


BHAV_COLUMNS = {
    "symbol": ("symbol", pl.Utf8),
    "series": ("series", pl.Utf8),
    "prev_close": ("prev_close", pl.Float64),
    "open_price": ("open", pl.Float64),
    "high_price": ("high", pl.Float64),
    "low_price": ("low", pl.Float64),
    "last_price": ("last", pl.Float64),
    "close_price": ("close", pl.Float64),
    "avg_price": ("vwap", pl.Float64),
    "ttl_trd_qnty": ("volume", pl.Int64),
    "turnover_lacs": ("turnover_lacs", pl.Float64),
    "no_of_trades": ("trades", pl.Int64),
    "deliv_qty": ("deliv_qty", pl.Int64),
    "deliv_per": ("deliv_pct", pl.Float64),
}

INDEX_COLUMNS = {
    "index name": ("index_name", pl.Utf8),
    "open index value": ("open", pl.Float64),
    "high index value": ("high", pl.Float64),
    "low index value": ("low", pl.Float64),
    "closing index value": ("close", pl.Float64),
    "points change": ("points_change", pl.Float64),
    "change(%)": ("change_pct", pl.Float64),
    "volume": ("volume", pl.Float64),
    "turnover (rs. cr.)": ("turnover_cr", pl.Float64),
    "p/e": ("pe", pl.Float64),
    "p/b": ("pb", pl.Float64),
    "div yield": ("div_yield", pl.Float64),
}


def _read_str_csv(source: Path | io.BytesIO) -> pl.DataFrame:
    df = pl.read_csv(source, infer_schema=False, truncate_ragged_lines=True)
    df = df.rename({c: c.strip().lstrip("﻿").lower() for c in df.columns})
    return df.with_columns(pl.all().str.strip_chars())


def _cast(col: str, dtype: pl.DataType) -> pl.Expr:
    if dtype == pl.Utf8:
        return pl.col(col)
    # Numbers may be "-" or blank; go through Float64 so "123.00" also survives an Int64 cast.
    expr = pl.col(col).str.replace_all(",", "").cast(pl.Float64, strict=False)
    return expr.cast(dtype, strict=False) if dtype != pl.Float64 else expr


def parse_bhav(path: Path) -> pl.DataFrame:
    df = _read_str_csv(path)
    exprs = [_cast(src, dtype).alias(dst) for src, (dst, dtype) in BHAV_COLUMNS.items() if src in df.columns]
    exprs.append(pl.col("date1").str.strptime(pl.Date, "%d-%b-%Y", strict=False).alias("date"))
    out = df.select(exprs).filter(pl.col("series").is_in(KEEP_SERIES))
    for _, (dst, dtype) in BHAV_COLUMNS.items():
        if dst not in out.columns:
            out = out.with_columns(pl.lit(None, dtype).alias(dst))
    return out.select(["symbol", "date", *[d for d, _ in BHAV_COLUMNS.values() if d != "symbol"]])


def parse_index(path: Path) -> pl.DataFrame:
    df = _read_str_csv(path)
    exprs = [_cast(src, dtype).alias(dst) for src, (dst, dtype) in INDEX_COLUMNS.items() if src in df.columns]
    exprs.append(pl.col("index date").str.strptime(pl.Date, "%d-%m-%Y", strict=False).alias("date"))
    return df.select(exprs)


def _years(raw_dir: Path, dataset: str) -> list[int]:
    base = raw_dir / dataset
    return sorted(int(p.name) for p in base.glob("[0-9][0-9][0-9][0-9]") if p.is_dir())


def _file_date(path: Path) -> date:
    s = path.stem
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _date_matches(df: pl.DataFrame, path: Path) -> bool:
    """NSE sometimes serves the previous session's file under a holiday's name; reject those."""
    if df.is_empty():
        return False
    if (df["date"] == _file_date(path)).all():
        return True
    log.warning("skipping %s: contains dates %s", path.name, sorted(set(df["date"].drop_nulls()))[:3])
    return False


def build_stock_raw(
    years: list[int] | None = None, *, raw_dir: Path | None = None, parquet_dir: Path | None = None
) -> dict[int, int]:
    raw_dir = raw_dir or settings.raw_dir
    parquet_dir = parquet_dir or settings.parquet_dir
    written: dict[int, int] = {}
    for year in years or _years(raw_dir, "bhav"):
        frames = [
            df for f in sorted((raw_dir / "bhav" / str(year)).glob("*.csv")) if _date_matches(df := parse_bhav(f), f)
        ]
        if not frames:
            continue
        df = (
            pl.concat(frames, how="vertical_relaxed")
            .drop_nulls(["symbol", "date", "close"])
            .with_columns(pl.col("series").replace_strict(SERIES_PRIORITY, default=9).alias("_prio"))
            .sort(["symbol", "date", "_prio"])
            .unique(["symbol", "date"], keep="first", maintain_order=True)
            .drop("_prio")
        )
        out = parquet_dir / "stock_daily_raw" / f"year={year}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out, compression="zstd")
        written[year] = df.height
    return written


def build_index(
    years: list[int] | None = None, *, raw_dir: Path | None = None, parquet_dir: Path | None = None
) -> dict[int, int]:
    raw_dir = raw_dir or settings.raw_dir
    parquet_dir = parquet_dir or settings.parquet_dir
    written: dict[int, int] = {}
    for year in years or _years(raw_dir, "index"):
        frames = [
            df for f in sorted((raw_dir / "index" / str(year)).glob("*.csv")) if _date_matches(df := parse_index(f), f)
        ]
        if not frames:
            continue
        df = (
            pl.concat(frames, how="vertical_relaxed")
            .drop_nulls(["index_name", "date", "close"])
            .unique(["index_name", "date"], keep="last")
            .sort(["index_name", "date"])
        )
        out = parquet_dir / "index_daily" / f"year={year}" / "part.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out, compression="zstd")
        written[year] = df.height
    return written


def _parse_any_date(col: str) -> pl.Expr:
    c = pl.col(col)
    return pl.coalesce(
        c.str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        c.str.strptime(pl.Date, "%d-%b-%Y", strict=False),
        c.str.strptime(pl.Date, "%d-%b-%y", strict=False),
        c.str.strptime(pl.Date, "%d/%m/%Y", strict=False),
    )


def read_bc(zip_path: Path) -> pl.DataFrame | None:
    """Corporate actions (`bc*.csv`) from one PR zip."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = next(
                (n for n in zf.namelist() if Path(n).name.lower().startswith("bc") and n.lower().endswith(".csv")), None
            )
            if name is None:
                return None
            data = zf.read(name)
    except zipfile.BadZipFile:
        log.warning("corrupt zip %s", zip_path)
        return None
    df = _read_str_csv(io.BytesIO(data))
    if not {"symbol", "series", "ex_dt", "purpose"} <= set(df.columns):
        return None
    record = _parse_any_date("record_dt") if "record_dt" in df.columns else pl.lit(None, pl.Date)
    return df.select(
        pl.col("symbol"),
        pl.col("series"),
        _parse_any_date("ex_dt").alias("ex_date"),
        record.alias("record_date"),
        pl.col("purpose"),
    )


def build_corporate_actions(*, raw_dir: Path | None = None, parquet_dir: Path | None = None) -> int:
    raw_dir = raw_dir or settings.raw_dir
    parquet_dir = parquet_dir or settings.parquet_dir
    frames = [df for z in sorted((raw_dir / "pr").glob("*/*.zip")) if (df := read_bc(z)) is not None]
    if not frames:
        return 0
    df = (
        pl.concat(frames, how="vertical_relaxed")
        .filter(pl.col("series").is_in(KEEP_SERIES))
        .drop_nulls(["symbol", "ex_date", "purpose"])
        .unique(["symbol", "ex_date", "purpose"])
        .with_columns(pl.col("purpose").map_elements(price_factor, return_dtype=pl.Float64).alias("price_factor"))
        .with_columns(
            pl.when(pl.col("price_factor").is_not_null())
            .then(pl.lit("purpose"))
            .when(pl.col("purpose").str.to_uppercase().str.contains("DEMERGER"))
            .then(pl.lit("market_implied"))
            .otherwise(pl.lit(None, pl.Utf8))
            .alias("factor_method")
        )
        .select(["symbol", "ex_date", "record_date", "purpose", "price_factor", "factor_method"])
        .sort(["ex_date", "symbol"])
    )
    out = parquet_dir / "corporate_actions" / "corporate_actions.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out, compression="zstd")
    return df.height


def _posix(p: Path) -> str:
    return p.as_posix().replace("'", "''")


NO_ACTIONS = (
    "SELECT NULL::VARCHAR AS symbol, NULL::DATE AS ex_date, NULL::DOUBLE AS price_factor, "
    "NULL::VARCHAR AS purpose, NULL::VARCHAR AS factor_method WHERE false"
)

# A market-implied factor outside this range is not a believable demerger; skip it.
MARKET_FACTOR_RANGE = (0.02, 0.98)

NO_SYMBOL_CHANGES = (
    "SELECT NULL::VARCHAR AS old_symbol, NULL::VARCHAR AS final_symbol, NULL::DATE AS change_date WHERE false"
)

ADJUST_SQL = """
WITH m AS ({sc}),
r0 AS (
    SELECT * EXCLUDE (year) FROM read_parquet('{src}', hive_partitioning = true)
),
r1 AS (
    -- rows traded under an old symbol before a rename belong to the company's current symbol
    SELECT r0.* REPLACE (coalesce(
               (SELECT arg_min(m.final_symbol, m.change_date) FROM m
                WHERE m.old_symbol = r0.symbol AND r0.date < m.change_date),
               r0.symbol) AS symbol),
           r0.symbol AS symbol_raw
    FROM r0
),
r AS (
    SELECT *, lag(close) OVER (PARTITION BY symbol ORDER BY date) AS prev_close_raw
    FROM r1
    QUALIFY row_number() OVER (PARTITION BY symbol, date ORDER BY (symbol_raw = symbol) DESC) = 1
),
ca AS (
    -- the same action is often listed with slightly different text; collapse equal factors per ex-date
    SELECT coalesce(
               (SELECT arg_min(m.final_symbol, m.change_date) FROM m
                WHERE m.old_symbol = src.symbol AND src.ex_date < m.change_date),
               src.symbol) AS symbol,
           ex_date, price_factor, factor_method, min(purpose) AS purpose
    FROM ({ca}) AS src WHERE factor_method IS NOT NULL
    GROUP BY ALL
),
ev AS (
    -- apply on the symbol's first session on or after the ex-date
    SELECT ca.*,
           (SELECT min(r2.date) FROM r AS r2 WHERE r2.symbol = ca.symbol AND r2.date >= ca.ex_date) AS date
    FROM ca
),
evf AS (
    SELECT ev.symbol, ev.date, ev.purpose, ev.factor_method,
           CASE WHEN ev.factor_method = 'purpose' THEN ev.price_factor
                WHEN r.prev_close_raw > 0
                     AND r.open / r.prev_close_raw BETWEEN {mf_lo} AND {mf_hi} THEN r.open / r.prev_close_raw
           END AS factor
    FROM ev JOIN r USING (symbol, date)
),
evd AS (
    SELECT symbol, date, exp(sum(ln(factor))) AS event_factor,
           string_agg(purpose || ' [' || factor_method || ']', ' | ') AS ca_purpose
    FROM evf WHERE factor IS NOT NULL
    GROUP BY symbol, date
),
j AS (
    SELECT r.*, coalesce(evd.event_factor, 1.0) AS event_factor, evd.ca_purpose
    FROM r LEFT JOIN evd USING (symbol, date)
),
cf AS (
    SELECT *,
        coalesce(exp(sum(ln(event_factor)) OVER (
            PARTITION BY symbol ORDER BY date
            ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING)), 1.0) AS adj_factor
    FROM j
)
SELECT
    symbol, date, series, symbol_raw,
    open * adj_factor  AS open,
    high * adj_factor  AS high,
    low * adj_factor   AS low,
    close * adj_factor AS close,
    vwap * adj_factor  AS vwap,
    close              AS close_raw,
    open               AS open_raw,
    prev_close_raw,
    CAST(round(volume / adj_factor) AS BIGINT) AS volume,
    turnover_lacs, trades,
    CAST(round(deliv_qty / adj_factor) AS BIGINT) AS deliv_qty,
    deliv_pct,
    event_factor, ca_purpose,
    adj_factor
FROM cf
"""


def build_adjusted(*, parquet_dir: Path | None = None) -> dict[str, int]:
    parquet_dir = parquet_dir or settings.parquet_dir
    src = parquet_dir / "stock_daily_raw" / "**" / "*.parquet"
    ca_file = parquet_dir / "corporate_actions" / "corporate_actions.parquet"
    ca = (
        f"SELECT symbol, ex_date, price_factor, purpose, factor_method FROM read_parquet('{_posix(ca_file)}')"
        if ca_file.exists()
        else NO_ACTIONS
    )
    sc_file = parquet_dir / "symbol_changes" / "symbol_changes.parquet"
    sc = (
        f"SELECT old_symbol, final_symbol, change_date FROM read_parquet('{_posix(sc_file)}')"
        if sc_file.exists()
        else NO_SYMBOL_CHANGES
    )
    out_dir = parquet_dir / "stock_daily"
    ev_dir = parquet_dir / "adjust_events"
    out_dir.mkdir(parents=True, exist_ok=True)
    ev_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        lo, hi = MARKET_FACTOR_RANGE
        con.execute("CREATE TEMP TABLE adj AS " + ADJUST_SQL.format(src=_posix(src), ca=ca, sc=sc, mf_lo=lo, mf_hi=hi))
        con.execute(
            "COPY (SELECT * EXCLUDE (open_raw, prev_close_raw) FROM adj ORDER BY symbol, date) "
            f"TO '{_posix(out_dir / 'stock_daily.parquet')}' (FORMAT parquet, COMPRESSION zstd)"
        )
        # Audit trail: implied_factor is what the market did at the open; it should be close to event_factor.
        con.execute(
            "COPY (SELECT symbol, date, ca_purpose AS purpose, event_factor, prev_close_raw, open_raw, "
            "open_raw / prev_close_raw AS implied_factor FROM adj WHERE event_factor <> 1.0 "
            "ORDER BY date, symbol) "
            f"TO '{_posix(ev_dir / 'adjust_events.parquet')}' (FORMAT parquet)"
        )
        rows = con.execute("SELECT count(*) FROM adj").fetchone()[0]
        events = con.execute("SELECT count(*) FROM adj WHERE event_factor <> 1.0").fetchone()[0]
    finally:
        con.close()
    return {"rows": rows, "adjust_events": events}


def build_all(years: list[int] | None = None) -> dict[str, object]:
    from nivesh.data.reference import build_reference

    stock = build_stock_raw(years)
    index = build_index(years)
    actions = build_corporate_actions()
    reference = build_reference()  # symbol_changes must exist before adjustment
    adjusted = build_adjusted()
    return {
        "stock_daily_raw": stock,
        "index_daily": index,
        "corporate_actions": actions,
        "reference": reference,
        "stock_daily": adjusted,
    }
