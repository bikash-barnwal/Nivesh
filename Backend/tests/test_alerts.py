"""Alert rules, de-duplicated delivery, channels and the daily brief."""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import polars as pl
import pytest
from fastapi.testclient import TestClient

from nivesh import alerts, appdb
from nivesh.backtest.engine import load_panel
from nivesh.backtest.spec import get_strategy
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from nivesh.demo import make_demo_dataset

D1, D2 = date(2026, 10, 6), date(2026, 10, 7)


def write(root, name: str, frame: pl.DataFrame) -> None:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(folder / f"{name}.parquet")


def tiny(tmp_path, **tables) -> Engine:
    """An engine over hand-built tables so each rule can be tested on exactly known data."""
    root = tmp_path / "parquet"
    write(root, "stock_features", pl.DataFrame({"symbol": ["AAA", "AAA"], "date": [D1, D2], "close": [1.0, 1.0]}))
    for name, frame in tables.items():
        write(root, name, frame)
    return Engine(root)


class Recorder:
    def __init__(self, name="rec", fail=False):
        self.name, self.fail, self.sent = name, fail, []

    def send(self, subject, body):
        if self.fail:
            raise RuntimeError("channel down")
        self.sent.append((subject, body))


@pytest.fixture(autouse=True)
def _app_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path / "app")


# ---- regime change ----------------------------------------------------------------------------------------------
def regimes(*rows):
    return pl.DataFrame(rows, schema=["date", "regime", "posture"], orient="row")


def test_regime_change_fires_only_when_the_last_two_sessions_differ(tmp_path):
    changed = tiny(tmp_path, market_regime=regimes((D1, "Early Bull", "RISK ON"), (D2, "Correction", "DEFENSIVE")))
    [item] = alerts._regime_change(changed)
    assert item.title == "Market regime: Early Bull → Correction" and item.severity == "WARNING"
    assert item.key == f"regime_change:{D2}"
    calm = tiny(tmp_path / "b", market_regime=regimes((D1, "Early Bull", "RISK ON"), (D2, "Early Bull", "RISK ON")))
    assert alerts._regime_change(calm) == []
    improving = tiny(
        tmp_path / "c", market_regime=regimes((D1, "Correction", "DEFENSIVE"), (D2, "Recovery", "SELECTIVE"))
    )
    assert alerts._regime_change(improving)[0].severity == "INFO"


# ---- market events ----------------------------------------------------------------------------------------------
def test_events_alert_once_on_the_first_day_and_respect_the_severity_floor(tmp_path):
    engines = pl.DataFrame({"date": [D1, D2], "engine": ["x", "x"], "value": [1.0, 2.0]})
    events = pl.DataFrame(
        [
            (D2, "vocal_distribution", "CRITICAL", 80.0, "heavy selling", 1),
            (D2, "volatility_explosion", "WARNING", 90.0, "volatile", 3),  # a continuing streak: not new
            (D2, "breadth_thrust", "INFO", 70.0, "broad", 1),
            (D1, "leadership_collapse", "WARNING", 60.0, "weak", 1),  # yesterday, not the latest session
        ],
        schema=["date", "event", "severity", "confidence", "description", "streak"],
        orient="row",
    )
    engine = tiny(tmp_path, market_engines=engines, market_events=events)
    titles = {i.title for i in alerts._market_events(engine, "WARNING")}
    assert titles == {"CRITICAL: vocal distribution"}
    assert {i.title for i in alerts._market_events(engine, "INFO")} == {
        "CRITICAL: vocal distribution",
        "INFO: breadth thrust",
    }
    assert [i.key for i in alerts._market_events(engine, "CRITICAL")] == [f"market_event:vocal_distribution:{D2}"]


# ---- scan entries -----------------------------------------------------------------------------------------------
def test_scan_entries_are_new_top_n_names_above_the_score_floor(tmp_path):
    rows = [(D1, "breakout", "OLD", 1, 90.0, "b"), (D2, "breakout", "OLD", 1, 91.0, "b")]
    rows += [(D2, "breakout", "NEW", 2, 88.0, "b"), (D2, "breakout", "WEAK", 3, 50.0, "b")]
    rows += [(D2, "breakout", f"S{i}", 4 + i, 80.0, "b") for i in range(alerts.TOP_N)]  # beyond top N
    rows += [(D2, "other", "ELSE", 1, 99.0, "b")]
    scans = pl.DataFrame(rows, schema=["date", "scan_id", "symbol", "rank", "score", "state"], orient="row")
    engine = tiny(tmp_path, scan_results=scans)
    items = alerts._scan_entries(engine, "breakout", min_score=60)
    assert [i.title for i in items][:1] == ["NEW entered breakout (#2)"]
    assert "OLD" not in {i.key.split(":")[2] for i in items} and "WEAK" not in {i.key.split(":")[2] for i in items}
    assert "ELSE" not in {i.key.split(":")[2] for i in items}
    assert all(int(i.title.split("#")[1].rstrip(")")) <= alerts.TOP_N for i in items)
    assert len(alerts._scan_entries(engine, "breakout", 0)) <= alerts.MAX_ITEMS_PER_RULE


def test_scan_entries_need_a_previous_session_to_compare_with(tmp_path):
    root = tmp_path / "parquet"
    write(root, "stock_features", pl.DataFrame({"symbol": ["A"], "date": [D2], "close": [1.0]}))
    write(
        root,
        "scan_results",
        pl.DataFrame({"date": [D2], "scan_id": ["b"], "symbol": ["A"], "rank": [1], "score": [90.0], "state": ["x"]}),
    )
    assert alerts._scan_entries(Engine(root), "b", 0) == []


# ---- watchlist --------------------------------------------------------------------------------------------------
def test_watchlist_alerts_for_circuits_and_upcoming_actions_only_for_watched_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(
        alerts, "lifecycle", lambda *a, **k: {"state": "TOPPING", "dwell_sessions": 5, "previous_state": "LEAD"}
    )
    circuits = pl.DataFrame({"symbol": ["AAA", "ZZZ"], "series": ["EQ", "EQ"], "date": [D2, D2], "side": ["H", "L"]})
    actions = pl.DataFrame(
        {
            "symbol": ["AAA", "AAA", "AAA"],
            "ex_date": [D2 + timedelta(days=3), D2 + timedelta(days=30), D2 - timedelta(days=1)],
            "purpose": ["BONUS 1:1", "DIVIDEND", "PAST SPLIT"],
        }
    )
    engine = tiny(tmp_path, circuit_hits=circuits, corporate_actions=actions)
    titles = {i.title for i in alerts._watchlist(engine, ["AAA"])}
    assert titles == {"AAA hit its upper circuit", f"AAA: BONUS 1:1 on {D2 + timedelta(days=3)}"}
    assert alerts._watchlist(engine, []) == []


def test_watchlist_reports_a_lifecycle_state_change_on_its_first_day(tmp_path, monkeypatch):
    engine = tiny(tmp_path)
    monkeypatch.setattr(
        alerts, "lifecycle", lambda *a, **k: {"state": "TOPPING", "dwell_sessions": 1, "previous_state": "LEAD"}
    )
    assert [i.title for i in alerts._watchlist(engine, ["AAA"])] == ["AAA: LEAD → TOPPING"]
    monkeypatch.setattr(
        alerts, "lifecycle", lambda *a, **k: {"state": "TOPPING", "dwell_sessions": 4, "previous_state": "LEAD"}
    )
    assert alerts._watchlist(engine, ["AAA"]) == []
    monkeypatch.setattr(
        alerts, "lifecycle", lambda *a, **k: {"state": "UNKNOWN", "dwell_sessions": 1, "previous_state": None}
    )
    assert alerts._watchlist(engine, ["AAA"]) == []


def test_watched_symbols_without_todays_data_are_skipped(tmp_path):
    engine = tiny(tmp_path)
    assert alerts._watchlist(engine, ["NOT_TRADING"]) == []


# ---- evaluate / dispatch ----------------------------------------------------------------------------------------
def test_disabled_rules_and_unknown_kinds_are_ignored(tmp_path):
    engine = tiny(tmp_path, market_regime=regimes((D1, "A", "RISK ON"), (D2, "B", "DEFENSIVE")))
    rules = [
        {"id": 1, "kind": "regime_change", "enabled": 0, "params": "{}"},
        {"id": 2, "kind": "no_such_kind", "enabled": 1, "params": "{}"},
        {"id": 3, "kind": "regime_change", "enabled": 1, "params": "{}"},
    ]
    items = alerts.evaluate(engine, rules, [])
    assert [i.rule_id for i in items] == [3]


def test_dispatch_dedupes_across_runs_and_isolates_channel_failures(tmp_path):
    item = alerts.AlertItem("k1", "regime_change", "WARNING", "Title", "Body", D2, rule_id=7)
    good, bad = Recorder("good"), Recorder("bad", fail=True)
    first = alerts.dispatch([item], [bad, good])
    assert first == {"new": 1, "duplicates": 0, "sent": 1, "send_errors": 1}
    assert good.sent == [("Title", "Body")]
    second = alerts.dispatch([item], [good])
    assert second["duplicates"] == 1 and second["new"] == 0 and len(good.sent) == 1, "never re-sent"
    [row] = appdb.list_alert_log()
    assert json.loads(row["delivered_to"]) == ["good"] and "channel down" in json.loads(row["errors"])[0]
    assert row["title"] == "Title" and row["as_of"] == str(D2) and row["rule_id"] == 7


def test_run_alerts_with_no_rules_does_nothing_and_with_defaults_uses_the_inbox(tmp_path):
    engine = tiny(tmp_path, market_regime=regimes((D1, "A", "RISK ON"), (D2, "B", "DEFENSIVE")))
    assert alerts.run_alerts(engine, channels=[])["evaluated"] == 0
    appdb.add_alert("Regime changes", None, 0, kind="regime_change")
    stats = alerts.run_alerts(engine, channels=[])
    assert stats["new"] == 1 and appdb.list_alert_log()[0]["title"].startswith("Market regime")
    assert alerts.run_alerts(engine, channels=[])["duplicates"] == 1


# ---- channels ---------------------------------------------------------------------------------------------------
def test_telegram_posts_the_message_and_truncates_long_text():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["json"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    channel = alerts.TelegramChannel("TKN", "42", client=httpx.Client(transport=httpx.MockTransport(handler)))
    channel.send("Subject", "x" * 5000)
    assert seen["url"] == "https://api.telegram.org/botTKN/sendMessage"
    assert (
        seen["json"]["chat_id"] == "42"
        and len(seen["json"]["text"]) == 4000
        and seen["json"]["text"].startswith("Subject")
    )


def test_telegram_http_errors_are_raised_so_dispatch_can_record_them():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False})))
    with pytest.raises(httpx.HTTPStatusError):
        alerts.TelegramChannel("bad", "1", client=client).send("s", "b")


def test_channels_are_enabled_only_by_environment_variables(monkeypatch):
    for var in ("NIVESH_TELEGRAM_TOKEN", "NIVESH_TELEGRAM_CHAT_ID", "NIVESH_SMTP_HOST", "NIVESH_EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    assert alerts.configured_channels() == []
    monkeypatch.setenv("NIVESH_TELEGRAM_TOKEN", "t")
    assert alerts.configured_channels() == [], "a token without a chat id is not enough"
    monkeypatch.setenv("NIVESH_TELEGRAM_CHAT_ID", "1")
    monkeypatch.setenv("NIVESH_SMTP_HOST", "smtp.test")
    monkeypatch.setenv("NIVESH_EMAIL_TO", "me@test")
    assert [c.name for c in alerts.configured_channels()] == ["telegram", "email"]


# ---- integration on the demo market -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def market(tmp_path_factory):
    base = tmp_path_factory.mktemp("alertmarket")
    parquet = base / "parquet"
    days = make_demo_dataset(parquet, sessions=420, symbols=50)
    return Engine(parquet), days, base


def test_strategy_signals_are_todays_candidates_minus_yesterdays(market):
    engine, days, _ = market
    strategy = get_strategy("leaders_pullback_v1")
    panel = load_panel(strategy, engine.parquet_dir, days[-2], days[-1])
    today = {s["symbol"] for s in panel.signals.get(1, [])}
    yesterday = {s["symbol"] for s in panel.signals.get(0, [])}
    items = alerts._strategy_signals(engine, strategy.id)
    new = today - yesterday
    symbols = {i.key.split(":")[2] for i in items}
    assert symbols <= new, "only candidates that were not already candidates yesterday"
    assert len(items) == min(len(new), alerts.MAX_ITEMS_PER_RULE)
    assert all(i.as_of == days[-1] for i in items)
    assert alerts._strategy_signals(engine, "nope_v9") == []


def test_brief_summarises_regime_sectors_and_scans_from_the_tables(market):
    engine, days, _ = market
    brief = alerts.build_brief(engine)
    regime = engine.query("SELECT regime FROM market_regime ORDER BY date DESC LIMIT 1").rows[0][0]
    assert brief["as_of"] == days[-1] and regime in brief["markdown"]
    assert "**Sector leaders:**" in brief["markdown"] and "**Scans:**" in brief["markdown"]
    assert brief["markdown"].endswith("_Educational analytics, not investment advice._")
    earlier = alerts.build_brief(engine, as_of=days[300])
    assert earlier["as_of"] == days[300] and earlier["markdown"] != brief["markdown"]


def test_brief_without_data_says_so(tmp_path):
    empty = Engine(tmp_path / "nothing")
    assert "No analytics are available yet" in alerts.build_brief(empty)["markdown"]


# ---- API --------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(market):
    engine, _, base = market
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = base
    engine_mod._engine = engine
    from nivesh.api.main import app

    with TestClient(app) as c:
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


def test_rule_validation_rejects_incomplete_or_unknown_rules(client):
    bad = [
        {"name": "x", "kind": "scan_entry"},
        {"name": "x", "kind": "strategy_signal"},
        {"name": "x", "kind": "market_event", "severity_min": "LOUD"},
        {"name": "", "kind": "regime_change"},
        {"name": "x", "kind": "nope"},
        {"name": "x", "kind": "scan_entry", "scan_id": "a", "min_score": 500},
    ]
    for body in bad:
        assert client.post("/api/alerts", json=body).status_code == 422, body
    assert (
        client.post("/api/alerts", json={"name": "x", "kind": "strategy_signal", "strategy_id": "nope_v9"}).status_code
        == 404
    )


def test_rule_lifecycle_defaults_run_inbox_and_read(client):
    created = client.post(
        "/api/alerts", json={"name": "Breakouts", "kind": "scan_entry", "scan_id": "breakout_volume", "min_score": 70}
    ).json()["data"]
    assert created["kind"] == "scan_entry" and created["scan_id"] == "breakout_volume"
    defaults = client.post("/api/alerts/defaults").json()["data"]
    assert {d["kind"] for d in defaults} == {"regime_change", "market_event", "watchlist"}
    assert client.post("/api/alerts/defaults").json()["data"] == [], "defaults are added only once"
    listing = client.get("/api/alerts").json()
    assert "in-app inbox" in listing["channels"] and set(listing["kinds"]) == set(alerts.KINDS)
    assert next(r for r in listing["data"] if r["kind"] == "market_event")["params"] == {"severity_min": "WARNING"}

    first = client.post("/api/alerts/run").json()["data"]
    second = client.post("/api/alerts/run").json()["data"]
    assert first["rules"] >= 4 and second["new"] == 0
    inbox = client.get("/api/alerts/inbox").json()
    assert inbox["unread"] == len(inbox["data"]) == first["new"]
    assert client.post("/api/alerts/inbox/read").json()["marked"] == first["new"]
    assert client.get("/api/alerts/inbox").json()["unread"] == 0

    assert client.delete(f"/api/alerts/{created['id']}").json()["status"] == "deleted"
    assert client.delete(f"/api/alerts/{created['id']}").status_code == 404


def test_brief_text_endpoint(client):
    body = client.get("/api/brief/text").json()
    assert body["data"]["title"].startswith("Nivesh daily brief") and "Regime" in body["data"]["markdown"]
    assert client.get("/api/brief/text?as_of=2020-06-01").status_code == 200
