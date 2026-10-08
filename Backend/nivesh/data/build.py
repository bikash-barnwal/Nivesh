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
    r"(?:SPL\w*|FVS|SUB-?DIVISION|CONSOL\w*)\D*?"
    r"(\d+(?:\.\d+)?)(?:/-)?(?:\s*PER\s*SH\w*)?\s*TO\s*(?:R[SE]\.?\s*)?(\d+(?:\.\d+)?)"
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
        old, new = float(m.group(1)), float(m.group(2))
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
    try:
        df = pl.read_csv(source, infer_schema=False, truncate_ragged_lines=True, encoding="utf8-lossy")
    except pl.exceptions.NoDataError:  # e.g. a day's corporate-actions file with no actions at all
        return pl.DataFrame()
    df = df.rename({c: c.strip().lstrip("﻿").lower() for c in df.columns})
    # older NSE files abbreviate the series column as "SR"
    if "sr" in df.columns and "series" not in df.columns:
        df = df.rename({"sr": "series"})
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


def parse_mto(path: Path) -> pl.DataFrame:
    """Legacy delivery file (MTO_DDMMYYYY.DAT): rows look like `20,<sr>,<symbol>,<series>,<traded>,<deliv>,<pct>`."""
    rows = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 7 and parts[0] == "20":
            rows.append(parts[2:7])
    if not rows:
        schema = {"symbol": pl.Utf8, "series": pl.Utf8, "deliv_qty": pl.Int64, "deliv_pct": pl.Float64}
        return pl.DataFrame(schema=schema)
    df = pl.DataFrame(rows, schema=["symbol", "series", "traded", "deliv_qty", "deliv_pct"], orient="row")
    return df.select(
        "symbol",
        "series",
        pl.col("deliv_qty").cast(pl.Float64, strict=False).cast(pl.Int64, strict=False),
        pl.col("deliv_pct").cast(pl.Float64, strict=False),
    )


def parse_bhav_old(zip_path: Path, mto_path: Path | None = None) -> pl.DataFrame:
    """Legacy bhavcopy zip (cmDDMONYYYYbhav.csv) mapped onto the standard bhav schema."""
    with zipfile.ZipFile(zip_path) as zf:
        name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        df = _read_str_csv(io.BytesIO(zf.read(name)))
    df = df.filter(pl.col("series").is_in(KEEP_SERIES))
    qty = pl.col("tottrdqty").str.replace_all(",", "").cast(pl.Float64, strict=False)
    val = pl.col("tottrdval").str.replace_all(",", "").cast(pl.Float64, strict=False)
    out = df.select(
        pl.col("symbol"),
        pl.col("timestamp").str.to_titlecase().str.strptime(pl.Date, "%d-%b-%Y", strict=False).alias("date"),
        pl.col("series"),
        _cast("prevclose", pl.Float64).alias("prev_close"),
        _cast("open", pl.Float64).alias("open"),
        _cast("high", pl.Float64).alias("high"),
        _cast("low", pl.Float64).alias("low"),
        _cast("last", pl.Float64).alias("last"),
        _cast("close", pl.Float64).alias("close"),
        pl.when(qty > 0).then(val / qty).otherwise(None).alias("vwap"),
        qty.cast(pl.Int64, strict=False).alias("volume"),
        (val / 1e5).alias("turnover_lacs"),
        (_cast("totaltrades", pl.Int64) if "totaltrades" in df.columns else pl.lit(None, pl.Int64)).alias("trades"),
    )
    if mto_path is not None and mto_path.exists():
        out = out.join(parse_mto(mto_path), on=["symbol", "series"], how="left")
    else:
        out = out.with_columns(pl.lit(None, pl.Int64).alias("deliv_qty"), pl.lit(None, pl.Float64).alias("deliv_pct"))
    return out


_INDEX_OVERRIDES = {
    "s&p cnx nifty": "Nifty 50",
    "cnx nifty": "Nifty 50",
    "cnx nifty junior": "Nifty Next 50",
    "cnx midcap": "Nifty Midcap 100",
    "cnx smallcap": "Nifty Smallcap 100",
    "bank nifty": "Nifty Bank",
}


def canonical_index_name(name: str) -> str:
    """NSE renamed its indices (CNX Nifty → Nifty 50, CNX 500 → Nifty 500, ...); keep one continuous series."""
    key = " ".join(name.split()).strip()
    lower = key.lower()
    if lower in _INDEX_OVERRIDES:
        return _INDEX_OVERRIDES[lower]
    for prefix in ("cnx ", "nifty "):
        if lower.startswith(prefix):
            return "Nifty " + key[len(prefix) :]
    return key


def _index_date(path: Path) -> pl.Expr:
    """Resolve the session date of an index file row.

    NSE changed the date format of these files over the years (some are day-first, some month-first), so a string like
    04-06-2023 is ambiguous. The filename is the reliable key: use it whenever either reading agrees with it. When
    neither does (an exchange-holiday duplicate or a special session), accept the date only if exactly one reading is a
    valid calendar date, otherwise leave it null so the file is rejected rather than guessed.
    """
    fd = _file_date(path)
    raw = pl.col("index date").str.replace_all("/", "-")  # 2015 files use 29/05/2015
    day_first = raw.str.strptime(pl.Date, "%d-%m-%Y", strict=False)
    month_first = raw.str.strptime(pl.Date, "%m-%d-%Y", strict=False)
    return (
        pl.when(day_first == fd).then(pl.lit(fd))
        .when(month_first == fd).then(pl.lit(fd))
        .when(day_first.is_not_null() & month_first.is_null()).then(day_first)
        .when(month_first.is_not_null() & day_first.is_null()).then(month_first)
        .otherwise(None)
        .alias("date")
    )  # fmt: skip


def parse_index(path: Path) -> pl.DataFrame:
    df = _read_str_csv(path)
    exprs = [_cast(src, dtype).alias(dst) for src, (dst, dtype) in INDEX_COLUMNS.items() if src in df.columns]
    exprs.append(_index_date(path))
    out = df.select(exprs)
    return out.with_columns(
        pl.col("index_name").alias("index_name_raw"),
        pl.col("index_name").map_elements(canonical_index_name, return_dtype=pl.Utf8).alias("index_name"),
    )


def _years(raw_dir: Path, dataset: str) -> list[int]:
    base = raw_dir / dataset
    return sorted(int(p.name) for p in base.glob("[0-9][0-9][0-9][0-9]") if p.is_dir())


def _file_date(path: Path) -> date:
    s = path.stem
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _date_matches(df: pl.DataFrame, path: Path) -> bool:
    """Accept a file whose rows carry its own date, or a lone weekend session filed under a later weekday.

    NSE serves the previous session's data under an exchange holiday's name (reject: it duplicates a session we already
    have). But special Saturday/Sunday sessions (budget day, Muhurat trading, a rescheduled session) are published under
    the next weekday's name; those are real sessions and are kept under their true date.
    """
    if df.is_empty():
        return False
    dates = set(df["date"].drop_nulls())
    if dates == {_file_date(path)}:
        return True
    if len(dates) == 1 and next(iter(dates)).weekday() >= 5:
        log.info("keeping weekend session %s found in %s", next(iter(dates)), path.name)
        return True
    log.warning("skipping %s: contains dates %s", path.name, sorted(dates)[:3])
    return False


SKIPPED_FILES: list[str] = []  # files that could not be parsed in the last build, reported by `build_all`


def _bhav_frames(raw_dir: Path, year: int) -> list[pl.DataFrame]:
    """Parsed daily frames for one year, tagged `_src` (0 = current format, 1 = legacy) for de-duplication.

    A file that cannot be parsed is skipped and recorded in `SKIPPED_FILES`: one odd file must not abort a 15-year
    build, but it must never be skipped silently either.
    """
    frames = []

    def keep(df: pl.DataFrame, path: Path, src: int) -> None:
        if _date_matches(df, path):
            frames.append(df.with_columns(pl.lit(src).alias("_src")))

    for f in sorted((raw_dir / "bhav" / str(year)).glob("*.csv")):
        try:
            keep(parse_bhav(f), f, 0)
        except Exception as exc:
            SKIPPED_FILES.append(f"bhav/{year}/{f.name}: {type(exc).__name__}: {str(exc)[:120]}")
            log.warning("skipping unreadable bhavcopy %s: %s", f.name, exc)
    for f in sorted((raw_dir / "bhav_old" / str(year)).glob("*.zip")):
        mto = raw_dir / "mto" / str(year) / f"{f.stem}.dat"
        try:
            keep(parse_bhav_old(f, mto), f, 1)
        except Exception as exc:
            SKIPPED_FILES.append(f"bhav_old/{year}/{f.name}: {type(exc).__name__}: {str(exc)[:120]}")
            log.warning("skipping unreadable legacy bhavcopy %s: %s", f.name, exc)
    return frames


def build_stock_raw(
    years: list[int] | None = None, *, raw_dir: Path | None = None, parquet_dir: Path | None = None
) -> dict[int, int]:
    raw_dir = raw_dir or settings.raw_dir
    parquet_dir = parquet_dir or settings.parquet_dir
    written: dict[int, int] = {}
    all_years = sorted(set(_years(raw_dir, "bhav")) | set(_years(raw_dir, "bhav_old")))
    for year in years or all_years:
        frames = _bhav_frames(raw_dir, year)
        if not frames:
            continue
        df = (
            pl.concat(frames, how="vertical_relaxed")
            .drop_nulls(["symbol", "date", "close"])
            .with_columns(pl.col("series").replace_strict(SERIES_PRIORITY, default=9).alias("_prio"))
            .sort(["symbol", "date", "_prio", "_src"])
            .unique(["symbol", "date"], keep="first", maintain_order=True)
            .drop("_prio", "_src")
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
           CASE WHEN ev.factor_method <> 'market_implied' THEN ev.price_factor
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


def _align_actions(con: duckdb.DuckDBPyConnection, src: Path, ca_file: Path, sc: str, parquet_dir: Path) -> str:
    """Verify listed actions against prices (see `align`); returns SQL for the events to apply."""
    from nivesh.data.align import align_actions

    if not ca_file.exists():
        return NO_ACTIONS
    renamed = (
        "coalesce((SELECT arg_min(m.final_symbol, m.change_date) FROM m "
        "WHERE m.old_symbol = {t}.symbol AND {t}.{d} < m.change_date), {t}.symbol)"
    )
    con.execute(f"CREATE TEMP TABLE m AS {sc}")
    actions = con.execute(
        f"SELECT {renamed.format(t='a', d='ex_date')} AS symbol, ex_date, price_factor, purpose, factor_method "
        f"FROM read_parquet('{_posix(ca_file)}') a WHERE factor_method IS NOT NULL"
    ).pl()
    bars = con.execute(
        f"SELECT {renamed.format(t='b', d='date')} AS symbol, date, open, close "
        f"FROM read_parquet('{_posix(src)}', hive_partitioning = true) b "
        "WHERE open > 0 AND close > 0 "
        f"AND b.symbol IN (SELECT DISTINCT symbol FROM read_parquet('{_posix(ca_file)}') "
        "WHERE price_factor IS NOT NULL "
        "UNION SELECT old_symbol FROM m) "
        "QUALIFY row_number() OVER (PARTITION BY symbol, date ORDER BY (b.symbol = symbol) DESC) = 1"
    ).pl()
    events, audit = align_actions(actions, bars)
    audit_dir = parquet_dir / "adjust_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit.write_parquet(audit_dir / "adjust_audit.parquet")
    events.write_parquet(audit_dir / "aligned_actions.parquet")
    return (
        "SELECT symbol, ex_date, price_factor, purpose, factor_method "
        f"FROM read_parquet('{_posix(audit_dir / 'aligned_actions.parquet')}')"
    )


def build_adjusted(*, parquet_dir: Path | None = None) -> dict[str, int]:
    parquet_dir = parquet_dir or settings.parquet_dir
    src = parquet_dir / "stock_daily_raw" / "**" / "*.parquet"
    ca_file = parquet_dir / "corporate_actions" / "corporate_actions.parquet"
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
        ca = _align_actions(con, src, ca_file, sc, parquet_dir)
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

    SKIPPED_FILES.clear()
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
        "skipped_files": list(SKIPPED_FILES),
    }
