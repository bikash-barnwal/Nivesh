"""End-of-day pipeline: ingest → build → quality → refresh engine views."""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from nivesh.analytics import build_analytics
from nivesh.config import settings
from nivesh.data import build, nse, quality, reference

log = logging.getLogger(__name__)

# Re-check a few recent sessions every run: NSE sometimes publishes late.
LOOKBACK_DAYS = 7
DEFAULT_START = date(2010, 1, 1)


def _log_job(record: dict[str, Any]) -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with (settings.logs_dir / "jobs.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def run_eod_pipeline(start: date | None = None, end: date | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    end = end or date.today()
    if start is None:
        last = nse.last_downloaded("bhav")
        start = (last - timedelta(days=LOOKBACK_DAYS)) if last else DEFAULT_START

    record: dict[str, Any] = {
        "job": "eod_pipeline",
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "start": start,
        "end": end,
    }
    try:
        results = nse.download_range(start, end, nse.ALL_DEFAULT)
        record["ingest"] = dict(Counter(f"{r.dataset}:{r.status}" for r in results))
        record["ingest_errors"] = [f"{r.dataset} {r.day} {r.detail}" for r in results if r.status == "error"][:20]

        years = sorted({d.year for d in nse.weekdays(start, end)})
        record["build"] = {
            "stock_daily_raw": build.build_stock_raw(years),
            "index_daily": build.build_index(years),
            "corporate_actions": build.build_corporate_actions(),
            "reference": reference.build_reference(),
            "stock_daily": build.build_adjusted(),
        }
        record["analytics"] = build_analytics()
        q = quality.run_quality()
        record["quality"] = {"critical_issues": q["critical_issues"], "data_through": q["data_through"]}

        from nivesh.data.engine import get_engine

        get_engine().refresh()
        record["status"] = "ok"
    except Exception as exc:  # job log must capture every failure
        log.exception("EOD pipeline failed")
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_s"] = round(time.perf_counter() - started, 1)
    _log_job(record)
    return record
