from datetime import date, timedelta
from pathlib import Path

import polars as pl

from nivesh.analytics import build_stock_features


def _dataset(root: Path, sessions: int = 300) -> None:
    rows = []
    start = date(2020, 1, 1)
    for i in range(sessions):
        close = 100 + i * 0.1
        rows.append(
            {
                "symbol": "AAA",
                "date": start + timedelta(days=i),
                "series": "EQ",
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1000,
                "turnover_lacs": 600,
            }
        )
    out = root / "stock_daily"
    out.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(out / "stock_daily.parquet")


def test_windows_are_full_before_values_exist(tmp_path):
    _dataset(tmp_path)
    assert build_stock_features(parquet_dir=tmp_path) == 300
    df = pl.read_parquet(tmp_path / "stock_features" / "stock_features.parquet")
    assert df["sma20"][:19].null_count() == 19
    assert df["sma50"][:49].null_count() == 49
    assert df["sma200"][:199].null_count() == 199
    assert df["sma200"][199] is not None
    assert df["high_52w"][:251].null_count() == 251
    assert df["high_52w"][251] is not None
    assert df["rvol20"][:20].null_count() == 20
    assert df["rvol20"][20] is not None
    assert df["atr14"][:13].null_count() == 13
    assert df["atr14"][13] is not None


def test_stage_and_stack_are_null_before_sma200(tmp_path):
    _dataset(tmp_path)
    build_stock_features(parquet_dir=tmp_path)
    df = pl.read_parquet(tmp_path / "stock_features" / "stock_features.parquet")
    assert df["sma_stack_ok"][:199].null_count() == 199
    assert df["stage"][:199].null_count() == 199
