# Nivesh Backend

Deterministic market data and intelligence for NSE equities.

## Setup

```bash
cd Backend
uv sync
cp .env.example .env        # LLM keys only needed from Group E
```

## Data (Group A)

```bash
uv run nivesh ingest --start 2024-01-01      # NSE bhavcopy + index + PR zip → data/raw (idempotent)
uv run nivesh build                          # → data/parquet (adjusted stock_daily, index_daily, corporate_actions)
uv run nivesh quality                        # → data/reports/quality_<date>.json
uv run nivesh pipeline                       # incremental: ingest + build + quality
```

Full 15-year backfill: `uv run nivesh ingest --start 2010-01-01` (~12k requests, several GB, run once).

## Play

```bash
uv run nivesh views
uv run nivesh freshness
uv run nivesh sql "SELECT symbol, date, close, volume FROM stock_daily WHERE symbol = 'RELIANCE' ORDER BY date DESC" --limit 10
uv run nivesh sql "SELECT * FROM adjust_events ORDER BY date DESC"
```

## API

```bash
uv run nivesh serve                  # http://127.0.0.1:8000/docs
uv run nivesh serve --scheduler      # also runs the EOD pipeline at 18:45 IST on weekdays
```

No auth in v0.x — keep it on localhost.

## Datasets

| View | Grain | Notes |
|---|---|---|
| `stock_daily_raw` | symbol × date | As published; EQ/BE/BZ series |
| `stock_daily` | symbol × date | Adjusted OHLCV; `close_raw`, `adj_factor`, `event_factor`, `ca_purpose` |
| `index_daily` | index × date | All NSE indices |
| `corporate_actions` | symbol × ex_date × purpose | `price_factor`, `factor_method` |
| `adjust_events` | symbol × date | Applied factors vs market-implied (audit) |

## Tests

```bash
uv run ruff check . && uv run pytest -q
```
