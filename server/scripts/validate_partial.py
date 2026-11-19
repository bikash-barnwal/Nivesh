import polars as pl
from pathlib import Path

root = Path("data/parquet/stock_intraday_1m")
sym_dirs = sorted(root.glob("symbol=*"))
print(f"Symbol partitions so far: {len(sym_dirs)}")

total_rows = 0
for d in sym_dirs:
    for f in d.glob("*.parquet"):
        total_rows += pl.read_parquet(f).shape[0]
print(f"Total candle rows written: {total_rows:,}")

# Spot-check a few early-alphabet symbols that should be done
for sym in ["20MICRONS", "360ONE", "3MINDIA", "5PAISA", "ACE"]:
    d = root / f"symbol={sym}"
    if d.exists():
        files = sorted(d.glob("*.parquet"))
        df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp")
        dates = df["timestamp"].dt.date().unique().sort().to_list()
        rows_per_day = {str(dt): len(df.filter(pl.col("timestamp").dt.date() == dt)) for dt in dates}
        bad = [dt for dt, cnt in rows_per_day.items() if cnt != 375]
        print(f"{sym}: {len(files)} files, {len(df)} rows, {len(dates)} dates, non-375-row days={bad[:3]}")
    else:
        print(f"{sym}: not yet fetched")
