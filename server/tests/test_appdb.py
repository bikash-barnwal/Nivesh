import sqlite3

from nivesh import appdb
from nivesh.appdb import add_alert, add_watch, list_alerts, list_watch


def test_local_state_uses_watchlist_and_alerts_only(tmp_path):
    path = tmp_path / "app.sqlite3"
    add_watch("reliance", path, user_id="local")
    add_alert("breakout", "breakout_volume", 80, path, user_id="local")
    assert list_watch(path, user_id="local")[0]["symbol"] == "RELIANCE"
    assert list_alerts(path, user_id="local")[0]["name"] == "breakout"
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "paper_positions" not in tables
    assert "paper_fills" not in tables


def test_every_table_is_private_to_its_user(tmp_path):
    path = tmp_path / "app.sqlite3"
    add_watch("TCS", path, user_id="alice")
    rule = add_alert("mine", None, 0, path, user_id="alice", kind="regime_change")
    account = appdb.create_paper_account("acct", "trend_stack_v1", "2024-01-01", 1e5, path, user_id="alice")
    appdb.add_history("q", "a", None, None, path, user_id="alice")
    appdb.log_alert("k", rule["id"], "regime_change", "INFO", "t", "b", "2024-01-01", path, user_id="alice")

    assert list_watch(path, user_id="bob") == []
    assert list_alerts(path, user_id="bob") == []
    assert appdb.list_paper_accounts(path, user_id="bob") == []
    assert appdb.list_history(path=path, user_id="bob") == []
    assert appdb.list_alert_log(path=path, user_id="bob") == []
    assert appdb.get_paper_account(account["id"], path, user_id="bob") is None
    assert appdb.delete_paper_account(account["id"], path, user_id="bob") is False
    assert appdb.delete_alert(rule["id"], path, user_id="bob") is False
    assert appdb.mark_alerts_read(path, user_id="bob") == 0
    appdb.remove_watch("TCS", path, user_id="bob")
    assert [w["symbol"] for w in list_watch(path, user_id="alice")] == ["TCS"]
    assert appdb.get_paper_account(account["id"], path, user_id="alice") is not None
    assert appdb.list_alert_users(path) == ["alice"]


def test_alert_dedupe_is_per_user(tmp_path):
    path = tmp_path / "app.sqlite3"
    args = ("same-key", None, "regime_change", "INFO", "t", "b", "2024-01-01", path)
    assert appdb.log_alert(*args, user_id="alice") is not None
    assert appdb.log_alert(*args, user_id="alice") is None
    assert appdb.log_alert(*args, user_id="bob") is not None


def test_old_database_with_global_alert_key_is_migrated(tmp_path):
    path = tmp_path / "app.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE alert_log (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL DEFAULT 'local', "
            "key TEXT NOT NULL UNIQUE, rule_id INTEGER, kind TEXT NOT NULL, severity TEXT NOT NULL, "
            "title TEXT NOT NULL, body TEXT NOT NULL, as_of TEXT NOT NULL, delivered_to TEXT, errors TEXT, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, read INTEGER NOT NULL DEFAULT 0)"
        )
        con.execute(
            "INSERT INTO alert_log(key, kind, severity, title, body, as_of) VALUES ('k','x','INFO','t','b','2024')"
        )
    args = ("k", None, "x", "INFO", "t", "b", "2024", path)
    assert appdb.log_alert(*args, user_id="bob") is not None, "same key, other user: allowed after migration"
    assert [r["key"] for r in appdb.list_alert_log(path=path, user_id="local")] == ["k"], "old rows kept"


def test_claim_local_moves_rows_to_a_user(tmp_path):
    path = tmp_path / "app.sqlite3"
    add_watch("INFY", path, user_id="local")
    add_alert("r", None, 0, path, user_id="local", kind="regime_change")
    moved = appdb.claim_local_rows("user_owner", path)
    assert moved["watchlist"] == 1 and moved["alert_rules"] == 1
    assert list_watch(path, user_id="local") == []
    assert [w["symbol"] for w in list_watch(path, user_id="user_owner")] == ["INFY"]
