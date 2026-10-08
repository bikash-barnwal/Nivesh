# Nivesh backend

Python 3.12 · FastAPI · DuckDB over Parquet · Polars. See the [root README](../README.md) for the overview and
quick start.

```bash
uv sync
uv run ruff check . && uv run pytest -q     # no network needed
```

> While a long-running `nivesh ingest`/`serve` holds `nivesh.exe` (Windows), `uv run` cannot rebuild the package after
> `pyproject.toml` changes. Use `.venv/Scripts/python.exe -m pytest` (or stop the process) in that case.

## Data lifecycle

```bash
uv run nivesh ingest --start 2024-01-01        # raw files → data/raw (immutable, resumable)
uv run nivesh build                            # → data/parquet (adjusted stock_daily, index, reference, analytics inputs)
uv run nivesh quality                          # → data/reports/quality_<date>.json
uv run nivesh pipeline                         # incremental: ingest + build + analytics + quality
uv run nivesh serve --scheduler                # API + daily 18:45 IST job
```

Ingest options: `--datasets all|daily|ref|<comma list>`, `--workers`, `--rate` (requests/second across workers).
NSE answers HTTP 403 to clients that go too fast; the downloader backs off for 30 s+ on 403/429, never caches a
throttled request as "missing", and can simply be re-run.

| Dataset | Source | Covers |
|---|---|---|
| `bhav` | `sec_bhavdata_full` CSV (with delivery) | 2019-08 → today |
| `bhav_old` + `mto` | legacy `cm…bhav.csv.zip` + MTO delivery file | 2010 → 2019-09 |
| `index` | `ind_close_all` | 2013 → today |
| `pr` | daily PR zip (corporate actions, circuit hits, market cap) | 2010 → today |
| `bands` | `sec_list` price bands | 2022 → today |
| `ref_*` | equity list, symbol changes, index constituents, F&O lots | snapshots (history accrues from first capture) |

Parquet datasets (each becomes a DuckDB view): `stock_daily` (adjusted), `stock_daily_raw`, `index_daily`,
`corporate_actions`, `adjust_events`, `security_master`, `symbol_changes`, `sector_map`, `index_membership`,
`fno_underlyings`, `price_bands`, `circuit_hits`, `market_cap`, then the analytics outputs `stock_features`,
`market_breadth`, `market_engines`, `market_regime`, `market_events`, `market_analogues`, `sector_daily`,
`sector_events`, `scan_results`. Descriptions for each are in `context/views.yaml`.

Price adjustment: bonus `a:b` → `b/(a+b)`; face-value split/consolidation `x→y` → `y/x`; demerger → ex-date open /
previous close. NSE's `PREV_CLOSE` is **not** adjusted on ex-dates, so the corporate-actions file drives adjustment.

## Rules live in YAML (`context/`)

| File | Defines |
|---|---|
| `engines.yaml` | 9 engines: inputs, percentile window (756 sessions), warm-up (120), label bands |
| `regimes.yaml` | 10 regimes as ordered rules, posture, confidence window |
| `events.yaml` | market events (trigger, severity, horizon, playbook text) |
| `scans.yaml` | scan universe and per-scan rule and score |
| `sectors.yaml` | industry→sector map, ranking weights, trend/verdict rules, group events |
| `stock_panel.yaml` | Stage-1 safety thresholds, lifecycle rules, gauge formulas |
| `strategies/*.yaml` | strategy definitions for the lab |
| `metrics/*.yaml`, `views.yaml` | metric and view documentation (also fed to the copilot) |

Expressions in these files are compiled by `nivesh/rules.py`: tokenised and checked against an allow-list of columns
and functions, so a rule can never inject SQL.

## API (OpenAPI at `/docs`)

All intelligence endpoints return `{as_of, data_through, version, data, provenance}` and accept `as_of=YYYY-MM-DD` to
show the state on that date (a non-session date snaps back to the previous session).

```
GET  /api/market/{overview,engines,regime-history,events,events/log,analogues,breadth}
GET  /api/sectors · /api/sectors/events
GET  /api/stocks/search · /api/stocks/{symbol}[/panel|/ohlcv|/features]
GET  /api/scans · /api/scans/{id} · /api/scan-catalog
GET  /api/strategies[/{id}] · POST /api/backtests · GET /api/backtests[/{run}[/equity|/trades[/{n}]]] · /api/leaderboard
POST /api/copilot/ask (NDJSON stream) · GET /api/copilot/{models,history}
POST /api/copilot/sql   guarded read-only SQL console
GET/POST/DELETE /api/watchlist · /api/alerts
```

## Strategy lab

`context/strategies/*.yaml` → `nivesh/backtest/` (spec, engine, metrics, gates, store). Runs are stored in `data/backtests/<run_id>/`.

## Copilot

`nivesh/copilot/`: `client.py` (OpenAI-compatible, fallbacks), `pipeline.py` (orchestrator), `temporal.py`,
`grounding.py`, `knowledge.py`, `prompts.py`.

```bash
cp .env.example .env                 # keys for the providers you use
uv run nivesh llm-ping               # one tiny call per role
uv run nivesh ask "How many stocks are above their 200-day average?"
```

Edit `config/llm.yaml` (or point `NIVESH_LLM_CONFIG` at a local copy) to choose models. Event stream:
`status`, `route`, `sql`, `evidence`, `answer`, `done`, `error`, plus `heartbeat` while a slow model works.

## Safety notes

- v0.x has no authentication: keep it on `127.0.0.1`.
- Copilot SQL: one read-only statement over allow-listed views, parsed with sqlglot, row-capped, with a timeout.
- Run ids are validated (no path traversal); SQLite connections are closed; app state is a local SQLite file
  (`user_id='local'` on every row so auth can be added later without a schema rewrite).
