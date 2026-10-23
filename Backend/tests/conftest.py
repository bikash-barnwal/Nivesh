import zipfile
from datetime import date
from pathlib import Path

import pytest

from nivesh.config import settings

# Tests must see the committed config/llm.yaml, never a machine's llm.local.yaml.
settings.llm_local_override = False

BHAV_HEADER = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, "
    "AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER"
)

INDEX_HEADER = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,"
    "Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield"
)


def bhav_row(symbol, series, d: date, prev, o, h, low, c, vol, deliv="-", deliv_pct="-"):
    return (
        f"{symbol}, {series}, {d:%d-%b-%Y}, {prev}, {o}, {h}, {low}, {c}, {c}, {c}, "
        f"{vol}, {vol * c / 1e5:.2f}, 100, {deliv}, {deliv_pct}"
    )


def write_bhav(raw_dir: Path, d: date, rows: list[str]) -> Path:
    path = raw_dir / "bhav" / f"{d:%Y}" / f"{d:%Y%m%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([BHAV_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


def write_index(raw_dir: Path, d: date, rows: list[str]) -> Path:
    path = raw_dir / "index" / f"{d:%Y}" / f"{d:%Y%m%d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([INDEX_HEADER, *rows]) + "\n", encoding="utf-8")
    return path


BC_HEADER = "SERIES,SYMBOL,SECURITY,RECORD_DT,BC_STRT_DT,BC_END_DT,EX_DT,ND_STRT_DT,ND_END_DT,PURPOSE"


def write_pr(raw_dir: Path, d: date, bc_rows: list[str]) -> Path:
    path = raw_dir / "pr" / f"{d:%Y}" / f"{d:%Y%m%d}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"bc{d:%d%m%Y}.csv", "\n".join([BC_HEADER, *bc_rows]) + "\n")
        zf.writestr("readme.txt", "test")
    return path


@pytest.fixture
def data_dirs(tmp_path: Path) -> tuple[Path, Path]:
    raw, parquet = tmp_path / "raw", tmp_path / "parquet"
    raw.mkdir()
    parquet.mkdir()
    return raw, parquet


@pytest.fixture
def split_dataset(data_dirs):
    """ABC splits FV 10 → 5 with ex-date 2026-01-07. NSE prev_close is NOT adjusted; the bc file drives it.

    The same action is listed twice with different text (as NSE does) and must be applied once.
    XYZ has a dividend, which must not adjust prices.
    """
    raw, parquet = data_dirs
    days = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]
    abc = [
        bhav_row("ABC", "EQ", days[0], 98, 99, 101, 97, 100, 1000, 600, 60.0),
        bhav_row("ABC", "EQ", days[1], 100, 100, 104, 99, 102, 1200, 700, 58.33),
        bhav_row("ABC", "EQ", days[2], 102, 51, 53, 50, 52, 2600),
        bhav_row("ABC", "EQ", days[3], 52, 52, 54, 51, 53, 2400, 1500, 62.5),
    ]
    xyz = [bhav_row("XYZ", "EQ", d, 10.5, 10, 11, 9.5, 10.5, 500) for d in days]
    junk = [bhav_row("ABCSME", "SM", d, 5, 5, 5, 5, 5, 10) for d in days]
    for i, d in enumerate(days):
        write_bhav(raw, d, [abc[i], xyz[i], junk[i]])
        write_index(raw, d, [f"Nifty 500,{d:%d-%m-%Y},100,101,99,{100 + i},1,1,-,-,20,3,1.2"])
    write_pr(
        raw,
        days[0],
        [
            "EQ,ABC,ABC Ltd,2026-01-07,,,2026-01-07,,,FVSPLT FRM RS 10 TO RS 5",
            "EQ,XYZ,XYZ Ltd,2026-01-06,,,2026-01-06,,,DIVIDEND - RS 2 PER SHARE",
            "SM,ABCSME,ABC SME,2026-01-06,,,2026-01-06,,,BONUS 1:1",
        ],
    )
    write_pr(
        raw,
        days[1],
        [
            "EQ,ABC,ABC Ltd,2026-01-07,,,2026-01-07,,,FACE VALUE SPLIT (SUB-DIVISION) - FROM RS 10/- TO RS 5/-",
        ],
    )
    return raw, parquet, days
