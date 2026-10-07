from datetime import date

import duckdb
import polars as pl
import pytest

from nivesh.data import build
from tests.conftest import bhav_row, write_bhav, write_pr


def _stock_daily(parquet, symbol):
    return duckdb.sql(
        f"SELECT date, close, close_raw, volume, adj_factor, event_factor FROM "
        f"'{(parquet / 'stock_daily' / 'stock_daily.parquet').as_posix()}' "
        f"WHERE symbol = '{symbol}' ORDER BY date"
    ).fetchall()


def _build(raw, parquet):
    build.build_stock_raw(raw_dir=raw, parquet_dir=parquet)
    build.build_corporate_actions(raw_dir=raw, parquet_dir=parquet)
    return build.build_adjusted(parquet_dir=parquet)


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("BONUS 1:1", 0.5),
        ("BONUS 2:1", 1 / 3),
        ("BONUS 1:2", 2 / 3),
        ("FVSPLT FRM RS 10 TO RS 5", 0.5),
        ("FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- PER SHARE TO RS 2/- PER SHARE", 0.2),
        ("FV SPLIT FRM RS 2 TO RE 1", 0.5),
        ("FACE VALUE SPLIT FROM RS.10/- TO RE.1/-", 0.1),
        ("CONSOLIDATION FRM RS 1 TO RS 10", 10.0),
        ("BONUS 1:1 / FVSPLT FRM RS 10 TO RS 5", 0.25),
        ("DIVIDEND - RS 2 PER SHARE", None),
        ("RIGHTS 1:5 @ PREMIUM RS 90", None),
        ("ANNUAL GENERAL MEETING", None),
    ],
)
def test_price_factor(purpose, expected):
    result = build.price_factor(purpose)
    assert result == (pytest.approx(expected) if expected is not None else None)


def test_parse_bhav_strips_columns_and_filters_series(split_dataset):
    raw, _, days = split_dataset
    df = build.parse_bhav(raw / "bhav" / "2026" / f"{days[0]:%Y%m%d}.csv")

    assert set(df["symbol"]) == {"ABC", "XYZ"}  # SME series dropped
    row = df.filter(pl.col("symbol") == "ABC").row(0, named=True)
    assert row["date"] == date(2026, 1, 5)
    assert row["close"] == 100.0
    assert row["volume"] == 1000
    assert row["deliv_qty"] == 600
    assert row["deliv_pct"] == 60.0


def test_parse_bhav_handles_dash_as_null(split_dataset):
    raw, _, days = split_dataset
    df = build.parse_bhav(raw / "bhav" / "2026" / f"{days[2]:%Y%m%d}.csv")
    row = df.filter(pl.col("symbol") == "ABC").row(0, named=True)
    assert row["deliv_qty"] is None
    assert row["deliv_pct"] is None


def test_parse_index(split_dataset):
    raw, _, days = split_dataset
    df = build.parse_index(raw / "index" / "2026" / f"{days[1]:%Y%m%d}.csv")
    row = df.row(0, named=True)
    assert row["index_name"] == "Nifty 500"
    assert row["date"] == days[1]
    assert row["close"] == 101.0
    assert row["volume"] is None


def test_corporate_actions_parsed_and_filtered(split_dataset):
    raw, parquet, _ = split_dataset
    assert build.build_corporate_actions(raw_dir=raw, parquet_dir=parquet) == 3  # SME row dropped
    df = pl.read_parquet(parquet / "corporate_actions" / "corporate_actions.parquet")
    abc = df.filter(pl.col("symbol") == "ABC")
    assert abc["ex_date"].to_list() == [date(2026, 1, 7)] * 2
    assert abc["price_factor"].to_list() == [0.5, 0.5]
    assert df.filter(pl.col("symbol") == "XYZ")["price_factor"].to_list() == [None]


def test_adjustment_applies_split_once(split_dataset):
    raw, parquet, _ = split_dataset
    assert _build(raw, parquet) == {"rows": 8, "adjust_events": 1}

    rows = _stock_daily(parquet, "ABC")
    assert rows[0][1] == pytest.approx(50.0)  # 100 * 0.5
    assert rows[1][1] == pytest.approx(51.0)  # 102 * 0.5
    assert rows[2][1] == pytest.approx(52.0)  # ex-date and later untouched
    assert rows[1][2] == 102  # raw close kept
    assert rows[0][3] == 2000  # volume doubled
    assert rows[2][5] == pytest.approx(0.5)  # event recorded on ex-date
    assert rows[3][4] == 1.0


def test_dividend_does_not_adjust(split_dataset):
    raw, parquet, _ = split_dataset
    _build(raw, parquet)
    assert {r[4] for r in _stock_daily(parquet, "XYZ")} == {1.0}


def test_no_corporate_actions_means_no_adjustment(split_dataset):
    raw, parquet, _ = split_dataset
    build.build_stock_raw(raw_dir=raw, parquet_dir=parquet)
    assert build.build_adjusted(parquet_dir=parquet) == {"rows": 8, "adjust_events": 0}


def test_demerger_uses_market_implied_factor(data_dirs):
    raw, parquet = data_dirs
    d1, d2, d3 = date(2026, 9, 3), date(2026, 9, 4), date(2026, 9, 7)
    write_bhav(raw, d1, [bhav_row("HEG", "EQ", d1, 700, 700, 712, 698, 707.45, 100)])
    write_bhav(raw, d2, [bhav_row("HEG", "EQ", d2, 707.45, 709, 730, 705, 728.25, 100)])
    write_bhav(raw, d3, [bhav_row("HEG", "EQ", d3, 728.25, 260, 275, 255, 272.2, 300)])
    write_pr(raw, d1, ["EQ,HEG,HEG Ltd,2026-09-07,,,2026-09-07,,,DEMERGER"])
    _build(raw, parquet)

    rows = _stock_daily(parquet, "HEG")
    factor = 260 / 728.25
    assert rows[2][5] == pytest.approx(factor)
    assert rows[1][1] == pytest.approx(728.25 * factor)
    assert abs(rows[2][1] / rows[1][1] - 1) < 0.06  # no artificial -63% gap left


def test_file_with_wrong_dates_is_skipped(data_dirs):
    raw, parquet = data_dirs
    write_bhav(raw, date(2026, 10, 1), [bhav_row("ABC", "EQ", date(2026, 10, 1), 10, 10, 10, 10, 10, 5)])
    # NSE served 1 Oct data under the 2 Oct (holiday) file name
    write_bhav(raw, date(2026, 10, 2), [bhav_row("ABC", "EQ", date(2026, 10, 1), 10, 10, 10, 10, 10, 5)])
    assert build.build_stock_raw(raw_dir=raw, parquet_dir=parquet) == {2026: 1}
