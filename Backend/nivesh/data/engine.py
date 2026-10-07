"""Read-side DuckDB engine: mounts every Parquet dataset as a view and runs bounded queries."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from nivesh.config import settings


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool = False
    elapsed_ms: float = 0.0
    sql: str = ""

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, r, strict=True)) for r in self.rows]


@dataclass
class ViewInfo:
    name: str
    path: str
    columns: list[tuple[str, str]] = field(default_factory=list)


def _sql_str(p: Path) -> str:
    return p.as_posix().replace("'", "''")


class Engine:
    """In-memory DuckDB with one view per folder under the Parquet root.

    Views are recreated by `refresh()` after each pipeline run. Queries use a cursor each, so
    the engine is safe to share across FastAPI worker threads.
    """

    def __init__(
        self, parquet_dir: Path | None = None, *, memory_limit: str | None = None, threads: int | None = None
    ) -> None:
        self.parquet_dir = parquet_dir or settings.parquet_dir
        self._con = duckdb.connect(":memory:")
        self._con.execute(f"SET memory_limit = '{memory_limit or settings.duckdb_memory_limit}'")
        if threads:
            self._con.execute(f"SET threads = {int(threads)}")
        self._lock = threading.Lock()
        self._views: dict[str, ViewInfo] = {}
        self._freshness: dict[str, date | None] = {}
        self.refresh()

    # ------------------------------------------------------------------ views
    def refresh(self) -> None:
        with self._lock:
            found: dict[str, ViewInfo] = {}
            if self.parquet_dir.exists():
                for folder in sorted(p for p in self.parquet_dir.iterdir() if p.is_dir()):
                    if not any(folder.rglob("*.parquet")):
                        continue
                    hive = any("=" in p.name for p in folder.iterdir() if p.is_dir())
                    glob = _sql_str(folder / "**" / "*.parquet")
                    name = folder.name
                    self._con.execute(
                        f'CREATE OR REPLACE VIEW "{name}" AS SELECT * FROM read_parquet('
                        f"'{glob}', hive_partitioning = {str(hive).lower()}, union_by_name = true)"
                    )
                    cols = self._con.execute(f'DESCRIBE "{name}"').fetchall()
                    found[name] = ViewInfo(name, folder.as_posix(), [(c[0], c[1]) for c in cols])
            for stale in set(self._views) - set(found):
                self._con.execute(f'DROP VIEW IF EXISTS "{stale}"')
            self._views = found
            self._freshness = {
                name: self._con.execute(f'SELECT max(date) FROM "{name}"').fetchone()[0]
                for name, info in found.items()
                if any(c == "date" for c, _ in info.columns)
            }

    def views(self) -> list[str]:
        return list(self._views)

    def view_info(self, name: str) -> ViewInfo:
        return self._views[name]

    def schema(self, name: str) -> list[tuple[str, str]]:
        return self._views[name].columns

    # -------------------------------------------------------------- freshness
    def freshness(self) -> dict[str, date | None]:
        return dict(self._freshness)

    def data_through(self) -> date | None:
        """Latest session with equity data: the temporal boundary for every answer."""
        return self._freshness.get("stock_daily") or self._freshness.get("stock_daily_raw")

    # ----------------------------------------------------------------- query
    def query(
        self, sql: str, params: list[Any] | None = None, *, timeout_s: float | None = None, max_rows: int | None = None
    ) -> QueryResult:
        import time

        timeout_s = timeout_s or settings.query_timeout_s
        cur = self._con.cursor()
        timer = threading.Timer(timeout_s, cur.interrupt)
        start = time.perf_counter()
        timer.start()
        try:
            rel = cur.execute(sql, params or [])
            columns = [d[0] for d in rel.description] if rel.description else []
            if max_rows is None:
                rows = rel.fetchall()
                truncated = False
            else:
                rows = rel.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
        finally:
            timer.cancel()
            cur.close()
        return QueryResult(columns, rows, truncated, (time.perf_counter() - start) * 1000, sql)

    def close(self) -> None:
        self._con.close()


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = Engine()
        return _engine


def reset_engine() -> None:
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.close()
        _engine = None
