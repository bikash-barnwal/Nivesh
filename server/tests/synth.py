"""Synthetic market data for analytics tests; the generator itself lives in `nivesh.demo`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from nivesh.demo import make_dataset, trading_days

__all__ = ["make_dataset", "trading_days", "truncate_dataset"]


def truncate_dataset(src: Path, dst: Path, last: date) -> None:
    """Copy the dataset keeping only sessions up to and including `last`."""
    stock = pl.read_parquet(src / "stock_daily" / "stock_daily.parquet").filter(pl.col("date") <= last)
    (dst / "stock_daily").mkdir(parents=True, exist_ok=True)
    stock.write_parquet(dst / "stock_daily" / "stock_daily.parquet")
    if (src / "sector_map").exists():
        (dst / "sector_map").mkdir(parents=True, exist_ok=True)
        source_file = src / "sector_map" / "sector_map.parquet"
        (dst / "sector_map" / "sector_map.parquet").write_bytes(source_file.read_bytes())
    for part in (src / "index_daily").glob("year=*/part.parquet"):
        frame = pl.read_parquet(part).filter(pl.col("date") <= last)
        if frame.height:
            target = dst / "index_daily" / part.parent.name
            target.mkdir(parents=True, exist_ok=True)
            frame.write_parquet(target / "part.parquet")
