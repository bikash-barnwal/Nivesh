"""Deterministic synthetic market data for tests and for exploring the app without the real NSE history.

Nothing here is real market data: prices are random walks driven by a common market factor, turnover varies, and a
Nifty-500-like benchmark index is included. `make_demo_dataset` builds a complete Parquet tree (including the
reference tables the stock panel uses) and runs the analytics on top of it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

REAL_SECTORS = [
    "Technology",
    "Healthcare",
    "Energy",
    "Financial Services",
    "Industrials",
    "Utilities",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Basic Materials",
    "Real Estate",
    "Communication Services",
]


def trading_days(sessions: int, start: date = date(2020, 1, 1)) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < sessions:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def make_dataset(
    root: Path,
    sessions: int = 560,
    symbols: int = 40,
    seed: int = 7,
    sector_names: list[str] | None = None,
) -> list[date]:
    """Write stock_daily, index_daily and sector_map Parquet under `root`; returns the session dates."""
    rng = np.random.default_rng(seed)
    days = trading_days(sessions)
    market = np.cumsum(rng.normal(0.0004, 0.01, sessions))
    rows = []
    for s in range(symbols):
        noise = rng.normal(0, 0.015, sessions) + 0.6 * np.diff(market, prepend=0)
        price = 100 * np.exp(np.cumsum(noise))
        for i, day in enumerate(days):
            c = float(price[i])
            rows.append(
                (
                    f"S{s:03d}",
                    day,
                    "EQ",
                    c * 0.99,
                    c * 1.01,
                    c * 0.985,
                    c,
                    int(rng.integers(200_000, 2_000_000)),
                    float(rng.uniform(600, 1500)),
                )
            )
    stock = pl.DataFrame(
        rows,
        schema=["symbol", "date", "series", "open", "high", "low", "close", "volume", "turnover_lacs"],
        orient="row",
    )
    out = root / "stock_daily"
    out.mkdir(parents=True, exist_ok=True)
    stock.write_parquet(out / "stock_daily.parquet")

    # Symbols 0..(symbols-5) map to sectors round-robin; the last few stay unmapped ("Unclassified").
    names = sector_names or ["Technology", "Healthcare", "Energy", "Financial Services", "Industrials", "Utilities"]
    mapped = [(f"S{s:03d}", names[s % len(names)]) for s in range(max(0, symbols - 4))]
    sector_dir = root / "sector_map"
    sector_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(mapped, schema=["symbol", "sector"], orient="row").write_parquet(sector_dir / "sector_map.parquet")

    index = pl.DataFrame(
        [("Nifty 500", day, float(1000 * np.exp(market[i]))) for i, day in enumerate(days)],
        schema=["index_name", "date", "close"],
        orient="row",
    )
    for year in sorted({d.year for d in days}):
        part = root / "index_daily" / f"year={year}"
        part.mkdir(parents=True, exist_ok=True)
        index.filter(pl.col("date").dt.year() == year).write_parquet(part / "part.parquet")
    return days


def _write(root: Path, name: str, frame: pl.DataFrame) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(folder / f"{name}.parquet")


def make_demo_dataset(root: Path, sessions: int = 700, symbols: int = 90, seed: int = 11) -> list[date]:
    """Full demo tree under `root` (a Parquet directory) including reference tables, with analytics built."""
    from nivesh import analytics

    days = make_dataset(root, sessions=sessions, symbols=symbols, seed=seed, sector_names=REAL_SECTORS)
    sym = lambda n: f"S{n:03d}"  # noqa: E731
    _write(
        root,
        "security_master",
        pl.DataFrame(
            {
                "symbol": [sym(i) for i in range(symbols)],
                "company": [f"Demo Company {i:03d} Ltd" for i in range(symbols)],
                "isin": [f"INE{i:06d}01" for i in range(symbols)],
                "listing_date": [days[0]] * symbols,
            }
        ),
    )
    _write(
        root,
        "circuit_hits",
        pl.DataFrame({"symbol": [sym(2)] * 3, "series": ["EQ"] * 3, "date": days[-3:], "side": ["H"] * 3}),
    )
    _write(
        root,
        "price_bands",
        pl.DataFrame(
            {
                "symbol": [sym(i) for i in range(symbols)],
                "series": ["EQ"] * symbols,
                "date": [days[-1]] * symbols,
                "band_pct": [5.0 if i == 3 else 20.0 for i in range(symbols)],
                "band_raw": ["5" if i == 3 else "20" for i in range(symbols)],
            }
        ),
    )
    _write(
        root,
        "corporate_actions",
        pl.DataFrame(
            {
                "symbol": [sym(1)],
                "ex_date": [days[sessions // 2]],
                "record_date": [days[sessions // 2]],
                "purpose": ["BONUS 1:1"],
                "price_factor": [0.5],
                "factor_method": ["purpose"],
            }
        ),
    )
    analytics.build_analytics(parquet_dir=root)
    return days
