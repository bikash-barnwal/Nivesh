import sqlite3

from nivesh.appdb import add_alert, add_watch, list_alerts, list_watch


def test_local_state_uses_watchlist_and_alerts_only(tmp_path):
    path = tmp_path / "app.sqlite3"
    add_watch("reliance", path)
    add_alert("breakout", "breakout_volume", 80, path)
    assert list_watch(path)[0]["symbol"] == "RELIANCE"
    assert list_alerts(path)[0]["name"] == "breakout"
    with sqlite3.connect(path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "paper_positions" not in tables
    assert "paper_fills" not in tables
