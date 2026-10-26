"""SQLite application state, one row set per user.

Every read and write takes `user_id` (keyword, no default) and filters by it, so one user can never see or change
another user's rows. With sign-in off the user is "local".
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from nivesh.config import settings

_ALERT_LOG = """
CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    key TEXT NOT NULL, rule_id INTEGER, kind TEXT NOT NULL, severity TEXT NOT NULL,
    title TEXT NOT NULL, body TEXT NOT NULL, as_of TEXT NOT NULL,
    delivered_to TEXT, errors TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, read INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, key)
);
"""

_SCHEMA = (
    """
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
CREATE TABLE IF NOT EXISTS saved_queries (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local',
    name TEXT NOT NULL, sql TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT PRIMARY KEY, settings TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
    + _ALERT_LOG
)

OWNED_TABLES = (
    "watchlist",
    "alert_rules",
    "alert_log",
    "copilot_history",
    "paper_accounts",
    "saved_queries",
    "user_settings",
)


def _migrate_alert_log_unique_per_user(con: sqlite3.Connection) -> None:
    """Old databases had UNIQUE(key); dedupe must be per user, so rebuild the table once with UNIQUE(user_id, key)."""
    for index in con.execute("PRAGMA index_list(alert_log)").fetchall():
        if index["unique"] and [c["name"] for c in con.execute(f"PRAGMA index_info('{index['name']}')")] == ["key"]:
            break
    else:
        return
    columns = ", ".join(c["name"] for c in con.execute("PRAGMA table_info(alert_log)"))
    con.executescript(
        "BEGIN;"
        "ALTER TABLE alert_log RENAME TO alert_log_old;"
        f"{_ALERT_LOG}"
        f"INSERT INTO alert_log ({columns}) SELECT {columns} FROM alert_log_old;"
        "DROP TABLE alert_log_old;"
        "COMMIT;"
    )


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
    _migrate_alert_log_unique_per_user(con)
    return con


# ------------------------------------------------------------------------------------------------ watchlist
def add_watch(symbol: str, path: Path | None = None, *, user_id: str) -> dict[str, Any]:
    symbol = symbol.upper()
    with closing(connect(path)) as con:
        with con:
            con.execute("INSERT OR IGNORE INTO watchlist(user_id, symbol) VALUES (?,?)", (user_id, symbol))
        row = con.execute("SELECT * FROM watchlist WHERE user_id=? AND symbol=?", (user_id, symbol)).fetchone()
    return dict(row)


def remove_watch(symbol: str, path: Path | None = None, *, user_id: str) -> None:
    with closing(connect(path)) as con:
        with con:
            con.execute("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (user_id, symbol.upper()))


def list_watch(path: Path | None = None, *, user_id: str) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute("SELECT * FROM watchlist WHERE user_id=? ORDER BY symbol", (user_id,))
        return [dict(row) for row in rows]


# ------------------------------------------------------------------------------------------------ alerts
def add_alert(
    name: str,
    scan_id: str | None,
    min_score: float,
    path: Path | None = None,
    *,
    user_id: str,
    kind: str = "scan_entry",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO alert_rules(user_id,name,scan_id,min_score,kind,params) VALUES (?,?,?,?,?,?)",
                (user_id, name, scan_id, min_score, kind, json.dumps(params or {})),
            )
        row = con.execute("SELECT * FROM alert_rules WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def delete_alert(rule_id: int, path: Path | None = None, *, user_id: str) -> bool:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("DELETE FROM alert_rules WHERE id=? AND user_id=?", (rule_id, user_id))
    return cur.rowcount > 0


def list_alerts(path: Path | None = None, *, user_id: str) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute("SELECT * FROM alert_rules WHERE user_id=? ORDER BY id DESC", (user_id,))
        return [dict(row) for row in rows]


def list_alert_users(path: Path | None = None) -> list[str]:
    """Users with at least one alert rule; the daily run evaluates each user's rules separately."""
    with closing(connect(path)) as con:
        return [row[0] for row in con.execute("SELECT DISTINCT user_id FROM alert_rules ORDER BY user_id")]


def log_alert(
    key: str,
    rule_id: int | None,
    kind: str,
    severity: str,
    title: str,
    body: str,
    as_of: str,
    path: Path | None = None,
    *,
    user_id: str,
) -> int | None:
    """Insert once per user and dedupe key; returns the new row id, or None if this alert was already logged."""
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO alert_log(user_id, key, rule_id, kind, severity, title, body, as_of) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (user_id, key, rule_id, kind, severity, title, body, as_of),
            )
    return cur.lastrowid if cur.rowcount else None


def finish_alert(
    log_id: int, delivered_to: list[str], errors: list[str], path: Path | None = None, *, user_id: str
) -> None:
    with closing(connect(path)) as con:
        with con:
            con.execute(
                "UPDATE alert_log SET delivered_to=?, errors=? WHERE id=? AND user_id=?",
                (json.dumps(delivered_to), json.dumps(errors), log_id, user_id),
            )


def list_alert_log(limit: int = 50, path: Path | None = None, *, user_id: str) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute(
            "SELECT * FROM alert_log WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_alerts_read(path: Path | None = None, *, user_id: str) -> int:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("UPDATE alert_log SET read=1 WHERE user_id=? AND read=0", (user_id,))
    return cur.rowcount


# ------------------------------------------------------------------------------------------------ copilot history
def add_history(
    question: str, answer: str, evidence: str | None, models: str | None, path: Path | None = None, *, user_id: str
) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO copilot_history(user_id, question, answer, evidence, models) VALUES (?,?,?,?,?)",
                (user_id, question, answer, evidence, models),
            )
        row = con.execute("SELECT * FROM copilot_history WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_history(limit: int = 20, path: Path | None = None, *, user_id: str) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute(
            "SELECT * FROM copilot_history WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------------------------------------ paper accounts
def create_paper_account(
    name: str, strategy_id: str, start_date: str, capital: float, path: Path | None = None, *, user_id: str
) -> dict[str, Any]:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute(
                "INSERT INTO paper_accounts(user_id, name, strategy_id, start_date, capital) VALUES (?,?,?,?,?)",
                (user_id, name, strategy_id, start_date, capital),
            )
        row = con.execute("SELECT * FROM paper_accounts WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def list_paper_accounts(path: Path | None = None, *, user_id: str) -> list[dict[str, Any]]:
    with closing(connect(path)) as con:
        rows = con.execute("SELECT * FROM paper_accounts WHERE user_id=? ORDER BY id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_paper_account(account_id: int, path: Path | None = None, *, user_id: str) -> dict[str, Any] | None:
    with closing(connect(path)) as con:
        row = con.execute("SELECT * FROM paper_accounts WHERE id=? AND user_id=?", (account_id, user_id)).fetchone()
    return dict(row) if row else None


def delete_paper_account(account_id: int, path: Path | None = None, *, user_id: str) -> bool:
    with closing(connect(path)) as con:
        with con:
            cur = con.execute("DELETE FROM paper_accounts WHERE id=? AND user_id=?", (account_id, user_id))
    return cur.rowcount > 0


# ------------------------------------------------------------------------------------------------ settings
def get_settings(path: Path | None = None, *, user_id: str) -> dict[str, Any]:
    with closing(connect(path)) as con:
        row = con.execute("SELECT settings FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return json.loads(row["settings"]) if row else {}


def save_settings(values: dict[str, Any], path: Path | None = None, *, user_id: str) -> dict[str, Any]:
    """Replace the user's settings (callers validate first)."""
    with closing(connect(path)) as con:
        with con:
            con.execute(
                "INSERT INTO user_settings(user_id, settings) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "settings=excluded.settings, updated_at=CURRENT_TIMESTAMP",
                (user_id, json.dumps(values)),
            )
    return values


# ------------------------------------------------------------------------------------------------ ownership
def claim_local_rows(user_id: str, path: Path | None = None) -> dict[str, int]:
    """Move rows created with sign-in off (user "local") to a real user, e.g. the owner's Clerk id."""
    moved = {}
    with closing(connect(path)) as con:
        with con:
            for table in OWNED_TABLES:
                cur = con.execute(f"UPDATE OR IGNORE {table} SET user_id=? WHERE user_id='local'", (user_id,))
                moved[table] = cur.rowcount
    return moved
