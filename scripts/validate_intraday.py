"""Post-backfill validation script. Run from repo root via:
   uv run --directory server python ../scripts/validate_intraday.py
"""
import polars as pl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "data" / "parquet" / "stock_intraday_1m"

print(f"Checking: {ROOT}")
if not ROOT.exists():
    print("Directory does not exist yet — backfill may still be running.")
    raise SystemExit(0)

sym_dirs = sorted(ROOT.glob("symbol=*"))
print(f"\n=== PARQUET COVERAGE ===")
print(f"Symbol partitions: {len(sym_dirs)}")

total_rows = 0
total_files = 0
for d in sym_dirs:
    for f in d.glob("*.parquet"):
        total_rows += pl.read_parquet(f).shape[0]
        total_files += 1
print(f"Parquet files:     {total_files}")
print(f"Total candle rows: {total_rows:,}")
print(f"Avg rows/file:     {total_rows // total_files if total_files else 0}")

print(f"\n=== SPOT CHECK (first 6 alphabetical symbols) ===")
for d in sym_dirs[:6]:
    sym = d.name.replace("symbol=", "")
    files = sorted(d.glob("*.parquet"))
    df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp")
    ts_col = "timestamp"
    dates = df[ts_col].dt.date().unique().sort().to_list()
    anomalous = []
    for dt in dates:
        cnt = len(df.filter(pl.col(ts_col).dt.date() == dt))
        if cnt not in (371, 375):  # 375 normal; 371 known for some sessions
            anomalous.append(f"{dt}={cnt}")
    status = "OK" if not anomalous else f"ANOMALOUS: {anomalous[:3]}"
    print(f"  {sym:20s} files={len(files):3d}  rows={len(df):6,}  dates={len(dates)}  {status}")

print(f"\n=== RELIANCE DEEP CHECK ===")
rel_dir = ROOT / "symbol=RELIANCE"
if rel_dir.exists():
    files = sorted(rel_dir.glob("*.parquet"))
    df = pl.concat([pl.read_parquet(f) for f in files]).sort("timestamp")
    dates = df["timestamp"].dt.date().unique().sort().to_list()
    print(f"Files: {[f.name for f in files]}")
    print(f"Trading dates: {dates}")
    for dt in dates:
        day_df = df.filter(pl.col("timestamp").dt.date() == dt)
        first = day_df["timestamp"].min()
        last  = day_df["timestamp"].max()
        print(f"  {dt}: {len(day_df)} candles  {first} → {last}")
else:
    print("RELIANCE not yet fetched (backfill may still be running)")
