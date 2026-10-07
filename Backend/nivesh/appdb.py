"""SQLite v0.x application state for the single local user."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from nivesh.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    user_id TEXT NOT NULL DEFAULT 'local', symbol TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL, scan_id TEXT, min_score REAL NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS saved_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL, sql TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or (settings.data_dir / "app.sqlite3")
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def add_watch(symbol: str, path: Path | None = None) -> dict[str, Any]:
    symbol = symbol.upper()
    with closing(connect(path)) as con:
        with con:
            con.execute("INSERT OR IGNORE INTO watchlist(symbol) VALUES (?)", (symbol,))
        row = con.execute("SELECT * FROM watchlist WHERE user_id=? AND symbol=?", ("local", symbol)).fetchone()
    return dict(row)


def remove_watch(symbol: str, path: Path | None = None) -> None:
    with closing(connect(path)) as con:
        with con:
            con.execute("DELETE FROM watchlist WHERE user_id=? AND symbol=?", ("local", symbol.upper()))


def list_watch(path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        return [dict(row) for row in con.execute("SELECT * FROM watchlist WHERE user_id=? ORDER BY symbol", ("local",))]


def add_alert(name: str, scan_id: str | None, min_score: float, path: Path | None = None) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO alert_rules(name,scan_id,min_score) VALUES (?,?,?)", (name, scan_id, min_score)
            )
        row = con.execute("SELECT * FROM alert_rules WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_alerts(path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        return [
            dict(row) for row in con.execute("SELECT * FROM alert_rules WHERE user_id=? ORDER BY id DESC", ("local",))
        ]
