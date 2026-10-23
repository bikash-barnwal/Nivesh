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
CREATE TABLE IF NOT EXISTS copilot_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    question TEXT NOT NULL, answer TEXT NOT NULL, evidence TEXT, models TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS paper_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL, strategy_id TEXT NOT NULL, start_date TEXT NOT NULL, capital REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    key TEXT NOT NULL UNIQUE, rule_id INTEGER, kind TEXT NOT NULL, severity TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, as_of TEXT NOT NULL,
    delivered_to TEXT, errors TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, read INTEGER NOT NULL DEFAULT 0
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
    existing = {row["name"] for row in con.execute("PRAGMA table_info(alert_rules)")}
    for column, ddl in (("kind", "TEXT NOT NULL DEFAULT 'scan_entry'"), ("params", "TEXT NOT NULL DEFAULT '{}'")):
        if column not in existing:
            con.execute(f"ALTER TABLE alert_rules ADD COLUMN {column} {ddl}")
    con.commit()
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


def add_alert(
    name: str,
    scan_id: str | None,
    min_score: float,
    path: Path | None = None,
    *,
    kind: str = "scan_entry",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import json

    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO alert_rules(name,scan_id,min_score,kind,params) VALUES (?,?,?,?,?)",
                (name, scan_id, min_score, kind, json.dumps(params or {})),
            )
        row = con.execute("SELECT * FROM alert_rules WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_alert(rule_id: int, path: Path | None = None) -> bool:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("DELETE FROM alert_rules WHERE id=? AND user_id=?", (rule_id, "local"))
    return cur.rowcount > 0


def log_alert(
    key: str,
    rule_id: int | None,
    kind: str,
    severity: str,
    title: str,
    body: str,
    as_of: str,
    path: Path | None = None,
) -> int | None:
    """Insert once per dedupe key; returns the new row id, or None if this alert was already logged."""
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO alert_log(key, rule_id, kind, severity, title, body, as_of) "
                "VALUES (?,?,?,?,?,?,?)",
                (key, rule_id, kind, severity, title, body, as_of),
            )
    return cur.lastrowid if cur.rowcount else None


def finish_alert(log_id: int, delivered_to: list[str], errors: list[str], path: Path | None = None) -> None:
    import json

    with closing(connect(path)) as con:
        with con:
            con.execute(
                "UPDATE alert_log SET delivered_to=?, errors=? WHERE id=?",
                (json.dumps(delivered_to), json.dumps(errors), log_id),
            )


def list_alert_log(limit: int = 50, path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute(
            "SELECT * FROM alert_log WHERE user_id=? ORDER BY id DESC LIMIT ?", ("local", limit)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_alerts_read(path: Path | None = None) -> int:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("UPDATE alert_log SET read=1 WHERE user_id=? AND read=0", ("local",))
    return cur.rowcount


def list_alerts(path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        return [
            dict(row) for row in con.execute("SELECT * FROM alert_rules WHERE user_id=? ORDER BY id DESC", ("local",))
        ]


def add_history(
    question: str, answer: str, evidence: str | None, models: str | None, path: Path | None = None
) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO copilot_history(question, answer, evidence, models) VALUES (?,?,?,?)",
                (question, answer, evidence, models),
            )
        row = con.execute("SELECT * FROM copilot_history WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_history(limit: int = 20, path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute(
            "SELECT * FROM copilot_history WHERE user_id=? ORDER BY id DESC LIMIT ?", ("local", limit)
        ).fetchall()
    return [dict(r) for r in rows]


def create_paper_account(
    name: str, strategy_id: str, start_date: str, capital: float, path: Path | None = None
) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO paper_accounts(name, strategy_id, start_date, capital) VALUES (?,?,?,?)",
                (name, strategy_id, start_date, capital),
            )
        row = con.execute("SELECT * FROM paper_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_paper_accounts(path: Path | None = None) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute("SELECT * FROM paper_accounts WHERE user_id=? ORDER BY id DESC", ("local",)).fetchall()
    return [dict(r) for r in rows]


def get_paper_account(account_id: int, path: Path | None = None) -> dict[str, Any] | None:
    with closing(connect(path)) as con:
        row = con.execute("SELECT * FROM paper_accounts WHERE id=? AND user_id=?", (account_id, "local")).fetchone()
    return dict(row) if row else None


def delete_paper_account(account_id: int, path: Path | None = None) -> bool:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("DELETE FROM paper_accounts WHERE id=? AND user_id=?", (account_id, "local"))
    return cur.rowcount > 0
