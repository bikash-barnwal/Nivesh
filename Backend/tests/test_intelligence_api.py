"""API tests against a data directory built from the synthetic market (no mocks of the engine)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from nivesh import analytics
from nivesh.config import settings
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine
from tests.synth import make_dataset

ENVELOPE_KEYS = {"as_of", "data_through", "version", "data", "provenance"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("api")
    parquet = root / "parquet"
    days = make_dataset(parquet)
    analytics.build_analytics(parquet_dir=parquet)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = root  # app.sqlite3 and audit log go to the temp dir
    engine_mod._engine = Engine(parquet)
    from nivesh.api.main import app

    with TestClient(app) as c:
        c.days = days
        yield c
    engine_mod._engine, settings.data_dir = old_engine, old_dir


GET_ENDPOINTS = [
    "/health",
    "/api/data/freshness",
    "/api/catalog",
    "/api/market/overview",
    "/api/market/breadth",
    "/api/market/engines",
    "/api/market/regime-history?sessions=10",
    "/api/market/events",
    "/api/market/events/log",
    "/api/market/analogues",
    "/api/scans",
    "/api/scans/early_entry?top=3",
    "/api/stocks/search?q=S00",
    "/api/stocks/S001",
    "/api/stocks/S001/features",
    "/api/stocks/S001/ohlcv?limit=5",
    "/api/brief",
    "/api/watchlist",
    "/api/alerts",
    "/api/strategies",
]


@pytest.mark.parametrize("path", GET_ENDPOINTS)
def test_every_get_endpoint_returns_200(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    if path.startswith("/api/") and path != "/api/data/freshness":
        assert ENVELOPE_KEYS <= response.json().keys()


def test_engines_state_is_one_row_per_engine_with_sparkline(client):
    body = client.get("/api/market/engines?sparkline=20").json()
    rows = body["data"]
    assert sorted(r["engine"] for r in rows) == sorted(analytics.ENGINES)
    assert len({r["date"] for r in rows}) == 1
    assert all(0 <= r["value"] <= 100 for r in rows)
    assert all(1 <= len(r["spark"]) <= 20 for r in rows)


def test_state_endpoints_support_as_of_and_snap_to_earlier_session(client):
    target = client.days[450]
    weekend = target + timedelta(days=(5 - target.weekday()) % 7 or 6)  # a non-session day after the target
    exact = client.get(f"/api/market/engines?as_of={target}").json()
    snapped = client.get(f"/api/market/engines?as_of={weekend}").json()
    assert date.fromisoformat(exact["as_of"]) == target
    assert date.fromisoformat(snapped["as_of"]) <= weekend
    assert len(snapped["data"]) == 9


def test_as_of_before_data_exists_returns_empty_not_error(client):
    body = client.get("/api/market/engines?as_of=2019-01-01").json()
    assert body["data"] == [] and body["as_of"] is None


def test_overview_has_regime_and_templated_narrative(client):
    data = client.get("/api/market/overview").json()["data"]
    assert data["regime"]["regime"] in data["narrative"]
    assert data["regime"]["posture"] in data["narrative"]


def test_events_include_outcomes_playbook_and_baseline(client):
    # find a date on which at least one event fires
    log = client.get("/api/market/events/log?sessions=400").json()["data"]
    assert log
    fired_on = max(r["date"] for r in log)
    body = client.get(f"/api/market/events?as_of={fired_on}").json()
    assert "baseline" in body
    for event in body["data"]:
        assert {"outcomes", "recent_occurrences", "actions"} <= event.keys()
        assert event["actions"]  # playbook text resolved from YAML
    severities = [e["severity"] for e in body["data"]]
    order = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
    assert severities == sorted(severities, key=order.get)


def test_event_outcomes_never_use_returns_observed_after_as_of(client):
    log = client.get("/api/market/events/log?sessions=400").json()["data"]
    early = sorted({r["date"] for r in log})[len({r["date"] for r in log}) // 3]
    body = client.get(f"/api/market/events?as_of={early}").json()
    for event in body["data"]:
        for horizon in ("1w", "1m", "3m"):
            n_late = event["outcomes"].get(f"n_{horizon}", 0)
            assert n_late <= event["outcomes"].get("episodes", 0)


def test_analogues_summary_and_as_of(client):
    body = client.get("/api/market/analogues").json()
    assert 1 <= body["summary"]["matches"] <= 5
    assert all("regime_then" in row for row in body["data"])
    earlier = client.get(f"/api/market/analogues?as_of={client.days[420]}").json()
    assert earlier["as_of"] <= str(client.days[420])


def test_stock_state_returns_single_latest_row_and_history_is_bounded(client):
    panel = client.get("/api/stocks/S001").json()["data"]
    assert len(panel) == 1
    history = client.get("/api/stocks/S001/features?limit=25").json()["data"]
    assert len(history) == 25
    dates = [r["date"] for r in history]
    assert dates == sorted(dates, reverse=True)


def test_scans_return_top_n_for_one_date(client):
    rows = client.get("/api/scans/breakout_volume?top=3").json()["data"]
    assert len(rows) <= 3
    assert len({r["date"] for r in rows}) <= 1
    assert [r["rank"] for r in rows] == sorted(r["rank"] for r in rows)


def test_unbuilt_view_is_a_404_not_a_500(client, monkeypatch):
    engine = engine_mod.get_engine()
    monkeypatch.setattr(engine, "views", lambda: [v for v in Engine.views(engine) if v != "sector_daily"])
    assert client.get("/api/sectors").status_code == 404


COPILOT_REJECTED = [
    "SELECT * FROM read_csv('C:/Windows/win.ini')",
    "COPY stock_daily TO 'x.csv'",
    "ATTACH 'x.db'",
    "SELECT 1; DROP TABLE stock_daily",
    "PRAGMA database_list",
    "SELECT * FROM information_schema.tables",
]


@pytest.mark.parametrize("sql", COPILOT_REJECTED)
def test_copilot_sql_rejects_unsafe_statements(client, sql):
    assert client.post("/api/copilot/sql", json={"sql": sql}).status_code == 400


def test_copilot_sql_accepts_cte_and_bounds_rows(client, monkeypatch):
    monkeypatch.setattr(settings, "max_result_rows", 7)
    body = client.post(
        "/api/copilot/sql",
        json={"sql": "WITH x AS (SELECT symbol, close FROM stock_daily) SELECT * FROM x"},
    ).json()
    assert len(body["data"]) == 7
    assert body["provenance"]["truncated"] is True


def test_watchlist_roundtrip_uses_local_user(client):
    assert client.post("/api/watchlist/infy").json()["symbol"] == "INFY"
    assert [r["symbol"] for r in client.get("/api/watchlist").json()["data"]] == ["INFY"]
    client.delete("/api/watchlist/infy")
    assert client.get("/api/watchlist").json()["data"] == []
