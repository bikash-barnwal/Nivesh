"""Persist backtest runs under data/backtests/<run_id>/ and read them back.

Layout per run: run.json (strategy, parameters, data window, timing), metrics.json, gates.json (optional),
equity.parquet (date, equity, cash, invested, drawdown) and trades.parquet.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from nivesh.backtest.engine import Result, load_panel, simulate
from nivesh.backtest.gates import count_passed, evaluate_gates
from nivesh.backtest.spec import Strategy
from nivesh.config import settings

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")  # must start alphanumeric: no "." or ".."


def runs_dir() -> Path:
    return settings.data_dir / "backtests"


def _run_path(run_id: str) -> Path:
    if not _SAFE_ID.match(run_id):
        raise KeyError(run_id)
    root = runs_dir().resolve()
    path = (root / run_id).resolve()
    if path.parent != root or not path.is_dir():  # belt and braces against traversal and symlinks
        raise KeyError(run_id)
    return path


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    raise TypeError(type(o))


def save_run(result: Result, gates: dict[str, Any] | None, started: float) -> str:
    strategy = result.strategy
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(strategy.model_dump_json().encode()).hexdigest()[:8]
    run_id = f"{strategy.id}-{stamp}-{digest}"
    out = runs_dir() / run_id
    out.mkdir(parents=True, exist_ok=True)
    peak = np.maximum.accumulate(result.equity)
    pl.DataFrame(
        {
            "date": result.dates,
            "equity": result.equity,
            "cash": result.cash,
            "invested": result.invested,
            "drawdown": result.equity / peak - 1,
        }
    ).write_parquet(out / "equity.parquet")
    result.trades.write_parquet(out / "trades.parquet")
    run = {
        "run_id": run_id,
        "strategy_id": strategy.id,
        "strategy": strategy.model_dump(),
        "start": result.dates[0],
        "end": result.dates[-1],
        "data_through": result.data_through,
        "created_at": datetime.now(UTC),
        "duration_s": round(time.perf_counter() - started, 2),
        "sessions": len(result.dates),
    }
    (out / "run.json").write_text(json.dumps(run, default=_json_default, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(result.metrics, default=_json_default, indent=2), encoding="utf-8")
    if gates is not None:
        (out / "gates.json").write_text(json.dumps(gates, default=_json_default, indent=2), encoding="utf-8")
    return run_id


def run_and_save(
    strategy: Strategy, start: date | None = None, end: date | None = None, with_gates: bool = True
) -> str:
    started = time.perf_counter()
    panel = load_panel(strategy, None, start, end)
    result = simulate(strategy, panel)
    result.data_through = panel.dates[-1]
    gates = evaluate_gates(strategy, panel, result) if with_gates else None
    return save_run(result, gates, started)


def _read(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def load_run(run_id: str) -> dict[str, Any]:
    path = _run_path(run_id)
    return {
        "run": _read(path / "run.json"),
        "metrics": _read(path / "metrics.json"),
        "gates": _read(path / "gates.json"),
    }


def load_equity(run_id: str) -> pl.DataFrame:
    return pl.read_parquet(_run_path(run_id) / "equity.parquet")


def load_trades(run_id: str) -> pl.DataFrame:
    return pl.read_parquet(_run_path(run_id) / "trades.parquet")


def list_runs() -> list[dict[str, Any]]:
    """All saved runs, newest first, as light summaries."""
    root = runs_dir()
    if not root.exists():
        return []
    out = []
    for path in root.iterdir():
        run, metrics, gates = _read(path / "run.json"), _read(path / "metrics.json"), _read(path / "gates.json")
        if not run or not metrics:
            continue
        passed, total = count_passed(gates) if gates else (None, None)
        out.append(
            {
                "run_id": run["run_id"],
                "strategy_id": run["strategy_id"],
                "status": run["strategy"].get("status"),
                "label": run["strategy"].get("label") or run["strategy_id"],
                "start": run["start"],
                "end": run["end"],
                "created_at": run["created_at"],
                "gates_passed": passed,
                "gates_total": total,
                "gates_all_passed": gates["passed_all"] if gates else None,
                "review_required": gates["review_required"] if gates else None,
                **{
                    k: metrics.get(k) for k in ("cagr", "max_drawdown", "calmar", "sharpe", "trades", "win_rate", "pnl")
                },
            }
        )
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def latest_per_strategy() -> list[dict[str, Any]]:
    seen: set[str] = set()
    latest = []
    for row in list_runs():  # newest first
        if row["strategy_id"] not in seen:
            seen.add(row["strategy_id"])
            latest.append(row)
    return latest


# --- background execution -------------------------------------------------------------------------------------------
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="backtest")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def submit(strategy: Strategy, start: date | None, end: date | None, with_gates: bool) -> str:
    job_id = hashlib.sha1(f"{strategy.id}{time.time_ns()}".encode()).hexdigest()[:12]
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "strategy_id": strategy.id,
            "status": "running",
            "run_id": None,
            "error": None,
        }

    def work() -> None:
        try:
            run_id = run_and_save(strategy, start, end, with_gates)
            update = {"status": "done", "run_id": run_id}
        except Exception as exc:  # reported to the client through the job record
            update = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        with _lock:
            _jobs[job_id].update(update)

    _pool.submit(work)
    return job_id


def job_status(job_id: str) -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None
