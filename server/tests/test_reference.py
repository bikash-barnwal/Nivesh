import zipfile
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from nivesh.data import build, reference
from tests.conftest import bhav_row, write_bhav

D = date(2026, 10, 7)


def _write(raw: Path, name: str, text: str, d: date = D) -> None:
    path = raw / name / f"{d:%Y}" / f"{d:%Y%m%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read(parquet: Path, name: str) -> pl.DataFrame:
    return pl.read_parquet(parquet / name / f"{name}.parquet")


def test_security_master(data_dirs):
    raw, parquet = data_dirs
    _write(
        raw,
        "ref_equity_list",
        "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
        "20MICRONS,20 Microns Limited,EQ,06-OCT-2008,5,1,INE144J01027,5\n",
    )
    assert reference.build_security_master(raw, parquet) == 1
    row = _read(parquet, "security_master").row(0, named=True)
    assert row["listing_date"] == date(2008, 10, 6)
    assert row["isin"] == "INE144J01027" and row["face_value"] == 5.0


def test_symbol_chain_resolution():
    changes = pl.DataFrame(
        {
            "old_symbol": ["A", "B", "X"],
            "new_symbol": ["B", "C", "Y"],
            "change_date": [date(2020, 1, 1), date(2022, 1, 1), date(2021, 1, 1)],
        }
    )
    out = reference.resolve_symbol_chains(changes)
    assert out["final_symbol"].to_list() == ["C", "C", "Y"]


def test_symbol_chain_ignores_earlier_reuse():
    # B was renamed to Z *before* A became B; A's history must stay as B.
    changes = pl.DataFrame(
        {
            "old_symbol": ["B", "A"],
            "new_symbol": ["Z", "B"],
            "change_date": [date(2019, 1, 1), date(2020, 1, 1)],
        }
    )
    assert reference.resolve_symbol_chains(changes)["final_symbol"].to_list() == ["Z", "B"]


def test_rename_joins_history_under_current_symbol(data_dirs):
    raw, parquet = data_dirs
    d1, d2 = date(2026, 10, 5), date(2026, 10, 6)
    write_bhav(raw, d1, [bhav_row("OLDCO", "EQ", d1, 100, 100, 101, 99, 100, 10)])
    write_bhav(raw, d2, [bhav_row("NEWCO", "EQ", d2, 100, 100, 102, 99, 101, 10)])
    _write(raw, "ref_symbol_change", " Some Company Ltd,OLDCO,NEWCO,06-OCT-2026\n")
    reference.build_symbol_changes(raw, parquet)
    build.build_stock_raw(raw_dir=raw, parquet_dir=parquet)
    build.build_adjusted(parquet_dir=parquet)

    rows = duckdb.sql(
        f"SELECT symbol, symbol_raw, date FROM '{(parquet / 'stock_daily' / 'stock_daily.parquet').as_posix()}' "
        "ORDER BY date"
    ).fetchall()
    assert rows == [("NEWCO", "OLDCO", d1), ("NEWCO", "NEWCO", d2)]


def test_sector_map_and_membership(data_dirs, tmp_path):
    raw, parquet = data_dirs
    header = "Company Name,Industry,Symbol,Series,ISIN Code\n"
    _write(
        raw,
        "ref_idx_totalmarket",
        header + "Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021\n"
        "Odd Co.,Brand New Industry,ODD,EQ,INE000000001\n",
    )
    _write(raw, "ref_idx_nifty50", header + "Infosys Ltd.,Information Technology,INFY,EQ,INE009A01021\n")

    assert reference.build_sector_map(raw, parquet) == 2
    sm = {r["symbol"]: r for r in _read(parquet, "sector_map").iter_rows(named=True)}
    assert sm["INFY"]["sector"] == "Technology" and sm["INFY"]["source"] == "ref_idx_totalmarket"
    assert sm["ODD"]["sector"] is None

    assert reference.build_index_membership(raw, parquet) == 3
    names = set(_read(parquet, "index_membership").filter(pl.col("symbol") == "INFY")["index_name"])
    assert names == {"Nifty Total Market", "Nifty 50"}


def test_price_bands(data_dirs):
    raw, parquet = data_dirs
    _write(
        raw,
        "bands",
        'Symbol,Series,Security Name,Band,Remarks\nACS,BE,ACS LTD,2,"-"\n'
        'BIG,EQ,BIG LTD,No Band,"-"\nSME1,SM,SME LTD,5,"-"\n',
    )
    assert reference.build_price_bands(raw, parquet) == 2
    bands = {r["symbol"]: r["band_pct"] for r in _read(parquet, "price_bands").iter_rows(named=True)}
    assert bands == {"ACS": 2.0, "BIG": None}


def test_circuit_hits_and_mcap_from_pr_zip(data_dirs):
    raw, parquet = data_dirs
    z = raw / "pr" / "2026" / "20261006.zip"
    z.parent.mkdir(parents=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bh06102026.csv", "SYMBOL,SERIES,SECURITY,HIGH/LOW\nABAN,BE,ABAN OFFSHORE,H\nABH,SM,ABH,H\n")
        zf.writestr(
            "mcap06102026.csv",
            "Trade Date,Symbol,Series,Security Name,Category,Last Trade Date,Face Value(Rs.),Issue Size,"
            "Close Price/Paid up value(Rs.),Market Cap(Rs.)              \n"
            "06 OCT 2026,20MICRONS,EQ,20 MICRONS LTD   ,Listed    ,06 OCT 2026,   5.00,  35286502,"
            "  207.85,     7335005170.75 \n",
        )
    assert reference.build_circuit_hits(raw, parquet) == 1
    hit = _read(parquet, "circuit_hits").row(0, named=True)
    assert (hit["symbol"], hit["date"], hit["side"]) == ("ABAN", date(2026, 10, 6), "H")

    assert reference.build_market_cap(raw, parquet) == 1
    mc = _read(parquet, "market_cap").row(0, named=True)
    assert mc["shares_issued"] == 35286502
    assert abs(mc["mcap_cr"] - 733.5005) < 1e-3


def test_empty_daily_files_inside_a_pr_zip_are_tolerated(data_dirs):
    """Days with no corporate actions / circuit hits publish zero-byte files; they must not abort a rebuild."""
    import zipfile

    raw, parquet = data_dirs
    z = raw / "pr" / "2026" / "20261006.zip"
    z.parent.mkdir(parents=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bc06102026.csv", "")
        zf.writestr("bh06102026.csv", "")
        zf.writestr("mcap06102026.csv", "")
    from nivesh.data import build

    assert build.read_bc(z) is None
    assert build.build_corporate_actions(raw_dir=raw, parquet_dir=parquet) == 0
    assert reference.build_circuit_hits(raw, parquet) == 0
    assert reference.build_market_cap(raw, parquet) == 0


def test_old_circuit_hit_files_with_an_sr_column_parse(data_dirs):
    import zipfile

    raw, parquet = data_dirs
    z = raw / "pr" / "2012" / "20120103.zip"
    z.parent.mkdir(parents=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(
            "bh03012012.csv", "SYMBOL,SR,SECURITY,HIGH/LOW,INDEX FLAG\nABAN,EQ,ABAN OFFSHORE,H,N\nXYZ,BE,XYZ LTD,L,N\n"
        )
    assert reference.build_circuit_hits(raw, parquet) == 2
    frame = pl.read_parquet(parquet / "circuit_hits" / "circuit_hits.parquet")
    assert dict(zip(frame["symbol"], frame["side"], strict=True)) == {"ABAN": "H", "XYZ": "L"}


def test_non_utf8_bytes_in_a_legacy_file_do_not_break_parsing(data_dirs):
    import zipfile

    raw, parquet = data_dirs
    z = raw / "pr" / "2011" / "20110103.zip"
    z.parent.mkdir(parents=True)
    body = b"SYMBOL,SR,SECURITY,HIGH/LOW,INDEX FLAG\nABAN,EQ,CAF\xc9 COMPANY,H,N\n"  # 0xC9 is not valid UTF-8
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bh03012011.csv", body)
    assert reference.build_circuit_hits(raw, parquet) == 1
