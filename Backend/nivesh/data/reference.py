"""Reference datasets: security master, symbol changes, sector map, index membership,
price bands, circuit hits, market cap and F&O underlyings.

Snapshot sources (EQUITY_L, index lists, F&O lots) are only published as "current"; each day's copy is
kept in data/raw/<name>/YYYY/YYYYMMDD.csv, so point-in-time history accumulates from the first capture.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import yaml

from nivesh.config import settings
from nivesh.data.build import KEEP_SERIES, _cast, _file_date, _read_str_csv

log = logging.getLogger(__name__)

INDEX_SNAPSHOTS = {
    "ref_idx_nifty50": "Nifty 50",
    "ref_idx_nifty500": "Nifty 500",
    "ref_idx_midsmallcap400": "Nifty MidSmallcap 400",
    "ref_idx_totalmarket": "Nifty Total Market",
    "ref_idx_microcap250": "Nifty Microcap 250",
}


def _files(raw_dir: Path, name: str, ext: str = ".csv") -> list[Path]:
    return sorted((raw_dir / name).glob(f"*/*{ext}"))


def _write(df: pl.DataFrame, parquet_dir: Path, name: str) -> int:
    out = parquet_dir / name / f"{name}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out, compression="zstd")
    return df.height


def _date_col(col: str, fmt: str) -> pl.Expr:
    return pl.col(col).str.to_titlecase().str.strptime(pl.Date, fmt, strict=False)


# --------------------------------------------------------------------------- security master
def build_security_master(raw_dir: Path, parquet_dir: Path) -> int:
    files = _files(raw_dir, "ref_equity_list")
    if not files:
        return 0
    df = _read_str_csv(files[-1])
    out = df.select(
        pl.col("symbol"),
        pl.col("name of company").alias("company"),
        pl.col("series"),
        _date_col("date of listing", "%d-%b-%Y").alias("listing_date"),
        _cast("paid up value", pl.Float64).alias("paid_up_value"),
        _cast("market lot", pl.Int64).alias("market_lot"),
        pl.col("isin number").alias("isin"),
        _cast("face value", pl.Float64).alias("face_value"),
        pl.lit(_file_date(files[-1])).alias("snapshot_date"),
    )
    return _write(out, parquet_dir, "security_master")


# --------------------------------------------------------------------------- symbol changes
def resolve_symbol_chains(changes: pl.DataFrame) -> pl.DataFrame:
    """Add `final_symbol`: follow old → new → newer, only through changes that happen later."""
    by_old: dict[str, list[tuple[date, str]]] = {}
    for old, new, d in changes.select("old_symbol", "new_symbol", "change_date").iter_rows():
        by_old.setdefault(old, []).append((d, new))
    for v in by_old.values():
        v.sort()

    finals = []
    for old, new, d in changes.select("old_symbol", "new_symbol", "change_date").iter_rows():
        cur, cur_date, seen = new, d, {old}
        while cur not in seen:
            seen.add(cur)
            nxt = next(((nd, ns) for nd, ns in by_old.get(cur, []) if nd > cur_date), None)
            if nxt is None:
                break
            cur_date, cur = nxt
        finals.append(cur)
    return changes.with_columns(pl.Series("final_symbol", finals, dtype=pl.Utf8))


def build_symbol_changes(raw_dir: Path, parquet_dir: Path) -> int:
    files = _files(raw_dir, "ref_symbol_change")
    if not files:
        return 0
    df = pl.read_csv(
        files[-1],
        has_header=False,
        infer_schema=False,
        truncate_ragged_lines=True,
        new_columns=["company", "old_symbol", "new_symbol", "change_date"],
    )
    df = (
        df.with_columns(pl.all().str.strip_chars())
        .with_columns(_date_col("change_date", "%d-%b-%Y").alias("change_date"))
        .drop_nulls(["old_symbol", "new_symbol", "change_date"])
        .filter(pl.col("old_symbol") != pl.col("new_symbol"))
        .unique(["old_symbol", "new_symbol", "change_date"])
        .sort("change_date")
    )
    return _write(resolve_symbol_chains(df), parquet_dir, "symbol_changes")


# --------------------------------------------------------------------------- index lists & sectors
def _read_index_list(path: Path, source: str) -> pl.DataFrame:
    df = _read_str_csv(path)
    return df.select(
        pl.lit(INDEX_SNAPSHOTS[source]).alias("index_name"),
        pl.lit(source).alias("source"),
        pl.col("symbol"),
        pl.col("company name").alias("company"),
        pl.col("industry").alias("nse_industry"),
        pl.col("isin code").alias("isin"),
        pl.lit(_file_date(path)).alias("snapshot_date"),
    )


def build_index_membership(raw_dir: Path, parquet_dir: Path) -> int:
    frames = [_read_index_list(f, src) for src in INDEX_SNAPSHOTS for f in _files(raw_dir, src)]
    if not frames:
        return 0
    df = pl.concat(frames).unique(["index_name", "symbol", "snapshot_date"]).sort(["snapshot_date", "index_name"])
    return _write(df, parquet_dir, "index_membership")


def load_sector_config(context_dir: Path | None = None) -> dict:
    path = (context_dir or settings.context_dir) / "sectors.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def build_sector_map(raw_dir: Path, parquet_dir: Path, context_dir: Path | None = None) -> int:
    cfg = load_sector_config(context_dir)
    industry_to_sector = {ind: sector for sector, inds in cfg["sectors"].items() for ind in inds}
    priority = {src: i for i, src in enumerate(cfg["source_priority"])}

    latest = [_read_index_list(files[-1], src) for src in INDEX_SNAPSHOTS if (files := _files(raw_dir, src))]
    if not latest:
        return 0
    df = (
        pl.concat(latest)
        .with_columns(pl.col("source").replace_strict(priority, default=99).alias("_prio"))
        .sort(["symbol", "_prio"])
        .unique("symbol", keep="first", maintain_order=True)
        .with_columns(
            pl.col("nse_industry").replace_strict(industry_to_sector, default=None).alias("sector"),
            pl.lit(cfg["version"]).alias("mapping_version"),
        )
        .select(["symbol", "company", "isin", "nse_industry", "sector", "source", "snapshot_date", "mapping_version"])
        .sort("symbol")
    )
    unmapped = df.filter(pl.col("sector").is_null())["nse_industry"].unique().to_list()
    if unmapped:
        log.warning("industries without a sector mapping: %s", unmapped)
    return _write(df, parquet_dir, "sector_map")


# --------------------------------------------------------------------------- F&O
def build_fno_underlyings(raw_dir: Path, parquet_dir: Path) -> int:
    files = _files(raw_dir, "ref_fno_lots")
    if not files:
        return 0
    df = _read_str_csv(files[-1])
    lot_cols = [c for c in df.columns if c not in {"underlying", "symbol"}]
    out = (
        df.select(
            pl.col("symbol"),
            pl.col("underlying"),
            _cast(lot_cols[0], pl.Int64).alias("lot_size") if lot_cols else pl.lit(None, pl.Int64).alias("lot_size"),
            pl.lit(_file_date(files[-1])).alias("snapshot_date"),
        )
        .filter(pl.col("symbol").is_not_null() & (pl.col("symbol") != "symbol"))
        .with_columns(pl.col("underlying").str.to_uppercase().str.contains("NIFTY").alias("is_index"))
    )
    return _write(out, parquet_dir, "fno_underlyings")


# --------------------------------------------------------------------------- price bands (daily)
def build_price_bands(raw_dir: Path, parquet_dir: Path) -> int:
    frames = []
    for f in _files(raw_dir, "bands"):
        df = _read_str_csv(f)
        frames.append(
            df.select(
                pl.col("symbol"),
                pl.col("series"),
                pl.lit(_file_date(f)).alias("date"),
                _cast("band", pl.Float64).alias("band_pct"),  # "No Band" → null
                pl.col("band").alias("band_raw"),
            ).filter(pl.col("series").is_in(KEEP_SERIES))
        )
    if not frames:
        return 0
    df = pl.concat(frames).unique(["symbol", "date"], keep="first").sort(["date", "symbol"])
    return _write(df, parquet_dir, "price_bands")


# --------------------------------------------------------------------------- PR zip: bh + mcap
def _zip_member(zip_path: Path, prefix: str) -> bytes | None:
    try:
        with zipfile.ZipFile(zip_path) as zf:
            name = next(
                (n for n in zf.namelist() if Path(n).name.lower().startswith(prefix) and n.lower().endswith(".csv")),
                None,
            )
            return zf.read(name) if name else None
    except zipfile.BadZipFile:
        log.warning("corrupt zip %s", zip_path)
        return None


def build_circuit_hits(raw_dir: Path, parquet_dir: Path) -> int:
    frames = []
    for z in _files(raw_dir, "pr", ".zip"):
        data = _zip_member(z, "bh")
        if not data:
            continue
        df = _read_str_csv(io.BytesIO(data))
        side_col = next((c for c in df.columns if "high" in c and "low" in c), None)
        if side_col is None or df.is_empty():
            continue
        frames.append(
            df.select(
                pl.col("symbol"),
                pl.col("series"),
                pl.lit(_file_date(z)).alias("date"),
                pl.col(side_col).alias("side"),  # H = upper circuit, L = lower circuit
            ).filter(pl.col("series").is_in(KEEP_SERIES))
        )
    if not frames:
        return 0
    df = pl.concat(frames).unique(["symbol", "date", "side"]).sort(["date", "symbol"])
    return _write(df, parquet_dir, "circuit_hits")


def build_market_cap(raw_dir: Path, parquet_dir: Path) -> int:
    frames = []
    for z in _files(raw_dir, "pr", ".zip"):
        data = _zip_member(z, "mcap")
        if not data:
            continue
        df = _read_str_csv(io.BytesIO(data))
        cols = {c: c for c in df.columns}
        mcap = next((c for c in cols if c.startswith("market cap")), None)
        issue = next((c for c in cols if c.startswith("issue size")), None)
        fv = next((c for c in cols if c.startswith("face value")), None)
        if mcap is None:
            continue
        frames.append(
            df.select(
                pl.col("symbol"),
                pl.col("series"),
                pl.lit(_file_date(z)).alias("date"),
                pl.col("category") if "category" in cols else pl.lit(None, pl.Utf8).alias("category"),
                (_cast(fv, pl.Float64) if fv else pl.lit(None, pl.Float64)).alias("face_value"),
                (_cast(issue, pl.Int64) if issue else pl.lit(None, pl.Int64)).alias("shares_issued"),
                (_cast(mcap, pl.Float64) / 1e7).alias("mcap_cr"),
            ).filter(pl.col("series").is_in(KEEP_SERIES))
        )
    if not frames:
        return 0
    df = pl.concat(frames, how="vertical_relaxed").unique(["symbol", "date"], keep="first").sort(["date", "symbol"])
    return _write(df, parquet_dir, "market_cap")


# --------------------------------------------------------------------------- all
def build_reference(
    *, raw_dir: Path | None = None, parquet_dir: Path | None = None, context_dir: Path | None = None
) -> dict[str, int]:
    raw_dir = raw_dir or settings.raw_dir
    parquet_dir = parquet_dir or settings.parquet_dir
    return {
        "security_master": build_security_master(raw_dir, parquet_dir),
        "symbol_changes": build_symbol_changes(raw_dir, parquet_dir),
        "index_membership": build_index_membership(raw_dir, parquet_dir),
        "sector_map": build_sector_map(raw_dir, parquet_dir, context_dir),
        "fno_underlyings": build_fno_underlyings(raw_dir, parquet_dir),
        "price_bands": build_price_bands(raw_dir, parquet_dir),
        "circuit_hits": build_circuit_hits(raw_dir, parquet_dir),
        "market_cap": build_market_cap(raw_dir, parquet_dir),
    }
