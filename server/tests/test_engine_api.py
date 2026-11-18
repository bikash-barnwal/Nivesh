from datetime import date

from fastapi.testclient import TestClient

from nivesh.data import build
from nivesh.data import engine as engine_mod
from nivesh.data.engine import Engine


def _built(split_dataset):
    raw, parquet, days = split_dataset
    build.build_stock_raw(raw_dir=raw, parquet_dir=parquet)
    build.build_index(raw_dir=raw, parquet_dir=parquet)
    build.build_corporate_actions(raw_dir=raw, parquet_dir=parquet)
    build.build_adjusted(parquet_dir=parquet)
    return parquet, days


def test_engine_mounts_views_and_freshness(split_dataset):
    parquet, days = _built(split_dataset)
    eng = Engine(parquet)
    assert {"stock_daily", "stock_daily_raw", "index_daily", "adjust_events", "corporate_actions"} <= set(eng.views())
    assert eng.data_through() == days[-1]
    assert ("year", "BIGINT") in eng.schema("stock_daily_raw")  # hive partition column

    res = eng.query("SELECT symbol, count(*) AS n FROM stock_daily GROUP BY 1 ORDER BY 1")
    assert res.records() == [{"symbol": "ABC", "n": 4}, {"symbol": "XYZ", "n": 4}]


def test_engine_row_cap(split_dataset):
    parquet, _ = _built(split_dataset)
    res = Engine(parquet).query("SELECT * FROM stock_daily", max_rows=3)
    assert len(res.rows) == 3 and res.truncated


def test_engine_empty_dir(tmp_path):
    eng = Engine(tmp_path)
    assert eng.views() == [] and eng.data_through() is None


def test_api_endpoints(split_dataset, monkeypatch):
    parquet, days = _built(split_dataset)
    monkeypatch.setattr(engine_mod, "_engine", Engine(parquet))
    from nivesh.api.main import app

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        fresh = client.get("/api/data/freshness").json()
        assert fresh["data_through"] == days[-1].isoformat()
        names = {v["name"] for v in client.get("/api/data/views").json()}
        assert "stock_daily" in names
    engine_mod.reset_engine()


def test_quality_report(split_dataset, tmp_path):
    parquet, _ = _built(split_dataset)
    from nivesh.data.quality import run_quality

    report = run_quality(parquet_dir=parquet, reports_dir=tmp_path / "reports")
    by_name = {c["name"]: c for c in report["checks"]}
    assert report["critical_issues"] == 0
    assert by_name["adjust_events_applied"]["count"] == 1
    assert report["data_through"] == date(2026, 1, 8)
