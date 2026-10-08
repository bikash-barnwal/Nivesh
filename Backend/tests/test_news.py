import json
from datetime import date, datetime, timedelta

import httpx
import polars as pl
import pytest

from nivesh.copilot.client import LLMError
from nivesh.news import extract, ingest, study
from nivesh.news.schema import DIRECTION, EVENT_TYPES
from tests.fake_llm import FakeLLM


def row(seq: str, symbol: str, stamp: str, desc: str, text: str, file: str = "https://x/a.pdf") -> dict:
    return {"seq_id": seq, "symbol": symbol, "an_dt": stamp, "desc": desc, "attchmntText": text, "attchmntFile": file}


PAYLOAD = [
    row(
        "1", "abc", "08-Oct-2026 10:15:00", "Bagging/Receipt of Orders/Contracts", "Received order worth Rs 450 crore."
    ),
    row(
        "2", "ABC", "08-Oct-2026 10:20:00", "Bagging/Receipt of Orders/Contracts", "Received order worth Rs 450 crore."
    ),
    row("3", "XYZ", "bad date", "Outcome of Board Meeting", "text"),
    row("4", "", "08-Oct-2026 11:00:00", "Outcome of Board Meeting", "no symbol"),
    row("5", "DEF", "08-Oct-2026 16:00:00", "Trading Window", "Closure of trading window"),
]


def test_parse_announcements_normalises_and_drops_bad_rows():
    rows = ingest.parse_announcements({"data": PAYLOAD})
    assert [r["source_id"] for r in rows] == ["nse_ann_1", "nse_ann_2", "nse_ann_5"]
    assert rows[0]["symbol"] == "ABC"
    assert rows[0]["published_at"] == datetime(2026, 10, 8, 10, 15)
    assert ingest.parse_announcements("garbage") == []


def test_build_items_dedupes_same_filing_listed_twice(tmp_path):
    raw, pq = tmp_path / "raw", tmp_path / "pq"
    dest = ingest.raw_path(date(2026, 10, 8), raw)
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"data": PAYLOAD}), encoding="utf-8")
    assert ingest.build_items(raw_dir=raw, parquet_dir=pq) == 2  # rows 1 and 2 share symbol + content
    df = pl.read_parquet(pq / "news_items" / "news_items.parquet")
    assert sorted(df["source_id"].to_list()) == ["nse_ann_1", "nse_ann_5"]


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers=ingest.HEADERS)


def test_fetch_day_stores_json_and_caches_past_days(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"data": PAYLOAD})

    with _client(handler) as client:
        d = date(2026, 10, 1)
        assert ingest.fetch_day(client, d, today=date(2026, 10, 8), raw_dir=tmp_path) == "downloaded"
        assert ingest.fetch_day(client, d, today=date(2026, 10, 8), raw_dir=tmp_path) == "cached"
        assert ingest.fetch_day(client, d, today=d, raw_dir=tmp_path) == "downloaded"  # today refreshes
    assert len(calls) == 2
    assert "from_date=01-10-2026" in calls[0]
    assert not list(tmp_path.rglob("*.part"))


def test_fetch_day_never_stores_html_block_pages(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Access Denied</html>")

    with _client(handler) as client:
        result = ingest.fetch_day(client, date(2026, 10, 1), today=date(2026, 10, 8), raw_dir=tmp_path)
    assert result.startswith("error")
    assert not ingest.raw_path(date(2026, 10, 1), tmp_path).exists()


def test_fetch_day_backs_off_on_403_then_gives_up(tmp_path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(ingest.time, "sleep", sleeps.append)
    with _client(lambda r: httpx.Response(403)) as client:
        result = ingest.fetch_day(client, date(2026, 10, 1), today=date(2026, 10, 8), raw_dir=tmp_path)
    assert result == "error: HTTP 403"
    assert min(sleeps) >= 30  # rate-limit backoff, not the short transient one


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        (datetime(2026, 10, 8, 15, 29, 59), date(2026, 10, 8)),
        (datetime(2026, 10, 8, 15, 30), date(2026, 10, 9)),
        (datetime(2026, 10, 9, 18, 0), date(2026, 10, 12)),  # Friday evening -> Monday
        (datetime(2026, 10, 10, 11, 0), date(2026, 10, 12)),  # Saturday
    ],
)
def test_available_from_never_precedes_publication(stamp, expected):
    assert extract.available_from(stamp) == expected


def test_number_in_text_handles_commas_and_tolerance():
    text = "Order of Rs. 1,250.5 crore and 12 percent"
    assert extract.number_in_text(1250.5, text)
    assert extract.number_in_text(12, text)
    assert not extract.number_in_text(450, text)


def item(**kw) -> dict:
    base = {
        "source_id": "nse_ann_1",
        "symbol": "ABC",
        "published_at": datetime(2026, 10, 8, 10, 15),
        "category": "Bagging/Receipt of Orders/Contracts",
        "text": "Received order worth Rs 450 crore.",
        "url": "https://x/a.pdf",
        "content_hash": "h",
    }
    return base | kw


def reply(**kw) -> dict:
    ev = {"event_type": "order_win", "confidence": 0.9, "summary": "Order win", "magnitude": {"value_cr": 450}}
    return {"events": [ev | kw]}


def test_interpret_valid_event_gets_fixed_direction_and_availability():
    events, status, _ = extract.interpret(item(), reply(), "fake/x")
    assert status == "ok"
    e = events[0]
    assert (e["symbol"], e["event_type"], e["direction"]) == ("ABC", "order_win", "positive")
    assert e["value_cr"] == 450 and e["magnitude_grounded"]
    assert e["available_from"] == date(2026, 10, 8)


def test_interpret_drops_amounts_not_in_the_text():
    events, status, _ = extract.interpret(item(), reply(magnitude={"value_cr": 999}), "fake/x")
    assert status == "ok"
    assert events[0]["value_cr"] is None and events[0]["magnitude_grounded"] is False


@pytest.mark.parametrize(
    "bad",
    [
        {"events": [{"event_type": "moon_landing", "confidence": 0.9, "summary": "x y z"}]},
        {"events": [{"event_type": "order_win", "confidence": 1.7, "summary": "Order win"}]},
        {"events": "none"},
        {"nothing": 1},
        None,
    ],
)
def test_interpret_rejects_off_schema_replies(bad):
    events, status, _ = extract.interpret(item(), bad, "fake/x")
    assert events == [] and status == "invalid"


def test_interpret_empty_list_is_none_and_duplicates_collapse():
    assert extract.interpret(item(), {"events": []}, "m")[1] == "none"
    twice = {"events": reply()["events"] * 2}
    assert len(extract.interpret(item(), twice, "m")[0]) == 1


def test_every_event_type_has_a_direction():
    assert set(EVENT_TYPES) == set(DIRECTION)


def seed_items(pq, items: list[dict]) -> None:
    out = pq / "news_items" / "news_items.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(items, schema=ingest.ITEM_COLUMNS).write_parquet(out)


def three_items() -> list[dict]:
    return [
        item(source_id="a", published_at=datetime(2026, 10, 8, 10, 0)),
        item(source_id="b", symbol="DEF", category="Trading Window", published_at=datetime(2026, 10, 8, 11, 0)),
        item(source_id="c", symbol="GHI", text="Routine update", published_at=datetime(2026, 10, 7, 10, 0)),
    ]


def test_run_extraction_is_idempotent_and_skips_routine_filings_without_a_model_call(tmp_path):
    seed_items(tmp_path, three_items())
    llm = FakeLLM({"extract": [reply(), {"events": []}]})
    first = extract.run_extraction(llm, parquet_dir=tmp_path)
    assert first["processed"] == 2 and first["skipped"] == 1 and first["events"] == 1
    assert llm.roles_called == ["extract", "extract"]

    again = extract.run_extraction(FakeLLM({}), parquet_dir=tmp_path)  # any model call would raise
    assert again["processed"] == 0 and again["remaining"] == 0

    events = pl.read_parquet(tmp_path / "stock_events" / "stock_events.parquet")
    assert events["source_id"].to_list() == ["a"]


def test_run_extraction_respects_universe_and_limit(tmp_path):
    seed_items(tmp_path, three_items())
    llm = FakeLLM({"extract": [{"events": []}]})
    out = extract.run_extraction(llm, parquet_dir=tmp_path, universe={"ABC", "GHI"}, limit=1)
    assert out["processed"] == 1 and out["remaining"] == 1
    assert llm.calls[0][1][1]["content"].startswith("Company: ABC")  # newest first


def test_run_extraction_leaves_items_for_retry_when_models_are_down_and_aborts(tmp_path):
    seed_items(tmp_path, [item(source_id=str(i), published_at=datetime(2026, 10, 8, 9, i)) for i in range(6)])
    llm = FakeLLM({"extract": [LLMError("all models failed")]})
    out = extract.run_extraction(llm, parquet_dir=tmp_path)
    assert out["aborted"] is True and out["failed"] == 3 and out["processed"] == 0
    assert len(llm.calls) == 3
    assert not (tmp_path / "news_extracted" / "news_extracted.parquet").exists()


def test_invalid_replies_are_recorded_and_retried_only_on_request(tmp_path):
    seed_items(tmp_path, [item()])
    extract.run_extraction(FakeLLM({"extract": ["not json at all"]}), parquet_dir=tmp_path)
    state = pl.read_parquet(tmp_path / "news_extracted" / "news_extracted.parquet")
    assert state["status"].to_list() == ["invalid"]

    assert extract.run_extraction(FakeLLM({}), parquet_dir=tmp_path)["processed"] == 0
    retried = extract.run_extraction(FakeLLM({"extract": [reply()]}), parquet_dir=tmp_path, retry_invalid=True)
    assert retried["events"] == 1
    assert pl.read_parquet(tmp_path / "news_extracted" / "news_extracted.parquet")["status"].to_list() == ["ok"]


def weekdays(n: int, start: date = date(2026, 1, 5)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def seed_prices(pq, days: list[date], winner_from: date) -> None:
    rows = []
    for sym in ["AAA", "BBB", "CCC", "WIN"]:
        level = 100.0
        for d in days:
            opened = level
            if sym == "WIN" and d > winner_from:
                level *= 1.01
            rows.append(
                {"symbol": sym, "date": d, "series": "EQ", "open": opened, "close": level, "turnover_lacs": 500.0}
            )
    out = pq / "stock_daily" / "stock_daily.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(out)


def seed_events(pq, rows: list[dict]) -> None:
    out = pq / "stock_events" / "stock_events.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    full = [
        {
            "source_id": f"s{i}",
            "symbol": r["symbol"],
            "event_type": "order_win",
            "direction": "positive",
            "event_date": r["available_from"],
            "published_at": datetime.combine(r["available_from"], datetime.min.time()),
            "available_from": r["available_from"],
            "confidence": r.get("confidence", 0.9),
            "summary": "x",
            "value_cr": None,
            "pct_of_revenue": None,
            "magnitude_grounded": True,
            "url": None,
            "model": "fake",
        }
        for i, r in enumerate(rows)
    ]
    pl.DataFrame(full, schema=extract.EVENT_COLUMNS).write_parquet(out)


def test_event_study_measures_excess_return_from_next_open_against_the_market(tmp_path):
    days = weekdays(40)
    signal = days[10]
    seed_prices(tmp_path, days, winner_from=signal)  # WIN starts rising the session after the signal day
    seed_events(tmp_path, [{"symbol": "WIN", "available_from": signal}])
    [res] = study.run_study(parquet_dir=tmp_path)
    assert res["event_type"] == "order_win" and res["n"] == 1
    # entry = open of days[11] = 100 (rise applies to days after signal: days[11] close is 101)
    assert res["mean_excess_1d"] == pytest.approx(0.01 - 0.0025, abs=1e-6)  # baseline: WIN is 1 of 4 stocks
    assert res["mean_excess_5d"] > 0.03
    assert (tmp_path / "stock_event_study" / "stock_event_study.parquet").exists()


def test_event_study_ignores_low_confidence_and_events_without_future_prices(tmp_path):
    days = weekdays(40)
    seed_prices(tmp_path, days, winner_from=days[10])
    seed_events(
        tmp_path,
        [
            {"symbol": "WIN", "available_from": days[10], "confidence": 0.3},
            {"symbol": "WIN", "available_from": days[-1]},  # no next session to enter on
        ],
    )
    assert study.run_study(parquet_dir=tmp_path) == []


def test_news_api_hides_events_before_their_availability_date_and_rejects_bad_input(tmp_path):
    from fastapi.testclient import TestClient

    from nivesh.config import settings
    from nivesh.data import engine as engine_mod
    from nivesh.data.engine import Engine

    days = weekdays(30)
    seed_prices(tmp_path, days, winner_from=days[10])
    seed_events(
        tmp_path, [{"symbol": "WIN", "available_from": days[10]}, {"symbol": "WIN", "available_from": days[20]}]
    )
    study.run_study(parquet_dir=tmp_path)
    old_engine, old_dir = engine_mod._engine, settings.data_dir
    settings.data_dir = tmp_path.parent
    engine_mod._engine = Engine(tmp_path)
    try:
        from nivesh.api.main import app

        with TestClient(app) as c:
            allrows = c.get("/api/news/events", params={"symbol": "win"}).json()["data"]
            assert len(allrows) == 2
            early = c.get("/api/news/events", params={"as_of": days[15].isoformat()}).json()["data"]
            assert [r["available_from"] for r in early] == [days[10].isoformat()]
            assert c.get("/api/news/events", params={"limit": 0}).status_code == 422
            assert c.get("/api/news/events", params={"symbol": "x' OR 1=1 --"}).json()["data"] == []
            assert c.get("/api/news/study").json()["data"][0]["event_type"] == "order_win"
            assert c.get("/api/news/status").json()["data"]["events_by_type"] == {"order_win": 2}
    finally:
        engine_mod._engine, settings.data_dir = old_engine, old_dir


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


PDF_URL = "https://nsearchives.nseindia.com/corporate/order.pdf"


def test_boilerplate_detection_matches_real_nse_headlines():
    from nivesh.news.attachments import is_boilerplate

    assert is_boilerplate("Union Bank of India has informed the Exchange about Credit Rating")
    assert is_boilerplate(
        "Larsen & Toubro Limited has informed the Exchange about Bagging/Receiving of orders/contracts"
    )
    assert not is_boilerplate(
        "LIC has informed the Exchange regarding Appointment of Mr X as Director w.e.f. October 07, 2026. " * 3
    )
    assert not is_boilerplate("Received order worth Rs 450 crore.")


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (PDF_URL, True),
        ("https://www.nseindia.com/a.pdf", True),
        ("http://nsearchives.nseindia.com/a.pdf", False),
        ("https://evilnseindia.com/a.pdf", False),
        ("https://nseindia.com.evil.io/a.pdf", False),
        ("file:///etc/passwd", False),
    ],
)
def test_only_https_nse_hosts_are_fetched(url, ok):
    from nivesh.news.attachments import allowed_url

    assert allowed_url(url) is ok


def test_pdf_text_reads_text_and_rejects_non_pdfs():
    from nivesh.news.attachments import pdf_text

    assert "450 crore" in pdf_text(make_pdf("Order worth Rs 450 crore"))
    assert pdf_text(b"<html>blocked</html>") == ""
    assert pdf_text(b"%PDF-1.4 garbage") == ""


def test_fetcher_downloads_once_caches_and_never_stores_unreadable_files(tmp_path):
    from nivesh.news.attachments import AttachmentFetcher

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("order.pdf"):
            return httpx.Response(200, content=make_pdf("Order worth Rs 450 crore"))
        return httpx.Response(200, content=b"PK\x03\x04 zip")

    with AttachmentFetcher(raw_dir=tmp_path, transport=httpx.MockTransport(handler), rate=1000) as fetch:
        assert "450 crore" in fetch({"url": PDF_URL})
        assert "450 crore" in fetch({"url": PDF_URL})
        assert fetch({"url": "https://nsearchives.nseindia.com/corporate/x.zip"}) == ""
        assert fetch({"url": "https://nsearchives.nseindia.com/corporate/x.zip"}) == ""
        assert fetch({"url": "https://example.com/a.pdf"}) == ""  # disallowed host: no request
        assert fetch({"url": None}) == ""
    assert len(calls) == 2


def test_fetcher_signals_retry_later_on_transient_errors_without_caching(tmp_path, monkeypatch):
    from nivesh.news import attachments
    from nivesh.news.attachments import AttachmentFetcher, AttachmentUnavailable

    monkeypatch.setattr(attachments.time, "sleep", lambda s: None)
    with AttachmentFetcher(
        raw_dir=tmp_path, transport=httpx.MockTransport(lambda r: httpx.Response(403)), rate=1000
    ) as f:
        with pytest.raises(AttachmentUnavailable):
            f({"url": PDF_URL})
    assert not list(tmp_path.rglob("*.txt"))


def test_fetcher_skips_oversize_downloads(tmp_path):
    from nivesh.news.attachments import MAX_BYTES, AttachmentFetcher

    big = httpx.Response(200, content=b"%PDF" + b"0" * (MAX_BYTES + 1))
    with AttachmentFetcher(raw_dir=tmp_path, transport=httpx.MockTransport(lambda r: big), rate=1000) as f:
        assert f({"url": PDF_URL}) == ""


def headline(**kw) -> dict:
    return item(text="Acme Ltd has informed the Exchange about Bagging/Receiving of orders/contracts", **kw)


def test_headline_only_items_are_read_from_their_attachment_and_amounts_grounded_there(tmp_path):
    seed_items(tmp_path, [headline()])
    llm = FakeLLM({"extract": [reply()]})  # value_cr 450 appears only in the attachment
    out = extract.run_extraction(llm, parquet_dir=tmp_path, fetch_text=lambda it: "Order worth Rs 450 crore awarded")
    assert out["events"] == 1
    assert "Order worth Rs 450 crore" in llm.calls[0][1][1]["content"]
    ev = pl.read_parquet(tmp_path / "stock_events" / "stock_events.parquet")
    assert ev["value_cr"].to_list() == [450.0] and ev["magnitude_grounded"].to_list() == [True]


def test_headline_only_items_without_text_are_skipped_not_sent_to_the_model(tmp_path):
    seed_items(tmp_path, [headline()])
    out = extract.run_extraction(FakeLLM({}), parquet_dir=tmp_path, fetch_text=lambda it: "")
    assert out["skipped"] == 1 and out["processed"] == 0
    state = pl.read_parquet(tmp_path / "news_extracted" / "news_extracted.parquet")
    assert state["reason"].to_list() == ["no text"]
    seed_items(tmp_path, [headline(source_id="z")])
    assert extract.run_extraction(FakeLLM({}), parquet_dir=tmp_path)["skipped"] == 1  # no fetcher at all


def test_unavailable_attachment_defers_the_item_without_tripping_the_model_abort(tmp_path):
    from nivesh.news.attachments import AttachmentUnavailable

    def down(it):
        raise AttachmentUnavailable("HTTP 403")

    seed_items(tmp_path, [headline(source_id=str(i), published_at=datetime(2026, 10, 8, 9, i)) for i in range(5)])
    out = extract.run_extraction(FakeLLM({}), parquet_dir=tmp_path, fetch_text=down)
    assert out["deferred"] == 5 and out["aborted"] is False and out["remaining"] == 5
    assert not (tmp_path / "news_extracted" / "news_extracted.parquet").exists()
