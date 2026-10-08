# Nivesh

Deterministic market intelligence for Indian equities (NSE end-of-day data), with a strategy lab and an
evidence-first AI copilot.

> **Correctness over cleverness.** Every number comes from SQL or Python over versioned rules. LLMs never calculate
> and never decide a trade: they route a question, write one read-only query, and phrase a result. Every number in a
> model's wording is checked against the evidence, and if it does not match, the raw result is shown instead.

*Educational analytics, not investment advice.* v0.x runs locally for one user: **no authentication**, API bound to
`127.0.0.1`. Do not expose it publicly.

## What is in it

| Area | What you get |
|---|---|
| **Data** | NSE bhavcopy (2010→today, legacy and current formats, delivery data), index history, corporate actions with split/bonus/demerger price adjustment, reference data, quality checks, daily pipeline |
| **Intelligence** | 9 market engines (percentile ranks), 10-regime classifier, market events with historical outcomes, similar past markets, sector rotation (RS, verdicts, trajectories), stock panel (safety filter, lifecycle, gauges), scans |
| **Strategy lab** | YAML strategies, daily-bar portfolio backtester (next-open fills, stops, gaps, costs, slippage), anti-overfitting gates, trade journal with MAE/MFE |
| **Signals** | Live strategy candidates, paper accounts (forward tests with open positions and next-open orders), alert rules with a de-duplicated inbox, optional Telegram/email delivery, a templated daily brief |
| **Copilot** | Ask in plain English; the answer links to the SQL, result table, raw evidence and freshness. Works with Gemini, Kimi, OpenRouter, NVIDIA Nemotron or local Ollama |
| **UI** | React + TypeScript + TanStack Router/Query/Table/Virtual, time-travel via `?as_of=` on every page |


## Quick start

Requirements: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+, Git.

### Clone and run locally

Terminal 1 (API, synthetic demo data):

```bash
git clone -b dev https://github.com/bikash-barnwal/Nivesh.git
cd Nivesh/Backend
uv sync
uv run nivesh demo-data
NIVESH_DATA_DIR=../data/demo uv run nivesh serve          # http://127.0.0.1:15000
```

On Windows PowerShell, replace the last line with:
`$env:NIVESH_DATA_DIR="../data/demo"; uv run nivesh serve`

Terminal 2 (UI):

```bash
cd Nivesh/Frontend
npm install
npm run dev                                               # open http://127.0.0.1:15001
```

### 1. Explore it right now with synthetic data (no downloads)

```bash
cd Backend
uv sync
uv run nivesh demo-data                       # synthetic market, NOT real prices → data/demo
NIVESH_DATA_DIR=../data/demo uv run nivesh serve

# in another terminal
cd Frontend
npm install
npm run dev                                   # http://localhost:15001
```

### 2. Use real NSE data

```bash
cd Backend
uv run nivesh ingest --start 2024-01-01       # polite by default: 2.5 requests/s, resumable, never re-downloads
uv run nivesh build                           # parse → adjusted Parquet
uv run nivesh quality                         # data quality report
uv run nivesh serve                           # http://127.0.0.1:15000/docs
```

Full history: `uv run nivesh ingest --start 2010-01-01 --rate 1.5` (about 15k requests, ~4 GB; NSE rate-limits aggressive
clients with HTTP 403, so keep the rate low and just re-run if it stops). Market engines, regimes and scans need 200+
sessions of history, so a short download shows empty market views.

### 3. Turn on the copilot (optional)

```bash
cp Backend/.env.example Backend/.env          # add the keys for the providers you use
cd Backend && uv run nivesh llm-ping          # checks keys, model ids and fallbacks
uv run nivesh ask "Top 5 stocks by 21-day return on the latest date, liquid only"
```

Models are configured per role in `Backend/config/llm.yaml` (override with `NIVESH_LLM_CONFIG`). Model ids drift:
confirm them with `llm-ping`. Hosted models only ever receive market data, schema and docs.

### 4. News and filings events (optional, unvalidated)

```bash
cd Backend
uv run nivesh news-ingest --days 7            # NSE announcements, 1 req/s, immutable per day
uv run nivesh news-extract --limit 200        # needs LLM keys; liquid universe, resumable
uv run nivesh news-study                      # forward excess returns per event type
```

Events carry an `available_from` date (after-close filings count from the next weekday) so time travel never leaks
them. They appear on the stock panel and under `/api/news/*`. No event type is cleared for strategies yet.

## Commands

```text
nivesh ingest | build | quality | pipeline      data lifecycle
nivesh sql "SELECT ..." | views | freshness     explore the data
nivesh serve [--scheduler]                      API (+ daily 18:45 IST job)
nivesh demo-data                                synthetic dataset for the UI and tests
nivesh llm-ping | ask "..."                     copilot from the terminal
nivesh news-ingest | news-extract | news-study  NSE announcements → events → event study
```

## Tests

```bash
cd Backend  && uv run ruff check . && uv run pytest -q          # ~300 tests, no network
cd Frontend && npm test                                          # Vitest (stream parsing)
cd Frontend && npx playwright test                               # browser tests against synthetic data
```

Backend tests never call NSE or a model: the copilot runs against a scripted fake, and NSE downloads against a mock
transport. Browser tests use `nivesh demo-data` and mock the model stream.

## Layout

```
Backend/nivesh/   data/ (ingest, adjust, quality, engine) · analytics.py · backtest/ · copilot/ · api/ · jobs/
Backend/context/  YAML definitions: engines, regimes, events, scans, sectors, strategies, views
Backend/config/   llm.yaml
Frontend/src/     pages/ · components/ · api/ (typed queries) · lib/
data/             gitignored: raw downloads, Parquet, reports, logs, backtests, demo data
```

## Principles

1. Exact queries over LLM calculations. 2. Source evidence over model memory. 3. YAML rules over inferred rules.
4. Reproducible queries over opaque reasoning. 5. Read-only by default. 6. No value at date T may use data after T
(tested for features, engines, sectors, signals and backtests).
