from datetime import date

import httpx

from nivesh.data import nse
from tests.conftest import BHAV_HEADER


def _transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        for key, resp in responses.items():
            if key in str(request.url):
                return resp
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_weekdays_skips_weekends():
    days = list(nse.weekdays(date(2026, 10, 2), date(2026, 10, 6)))  # Fri..Tue
    assert days == [date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)]


def test_download_writes_valid_and_marks_old_missing(tmp_path):
    ok = httpx.Response(200, content=(BHAV_HEADER + "\n").encode())
    transport = _transport({"sec_bhavdata_full_05012026": ok})
    results = nse.download_range(date(2026, 1, 5), date(2026, 1, 6), ["bhav"], raw_dir=tmp_path, transport=transport)
    status = {r.day: r.status for r in results}
    assert status == {date(2026, 1, 5): "downloaded", date(2026, 1, 6): "missing"}
    assert (tmp_path / "bhav" / "2026" / "20260105.csv").exists()
    assert (tmp_path / "bhav" / "2026" / "20260106.missing").exists()

    again = nse.download_range(date(2026, 1, 5), date(2026, 1, 6), ["bhav"], raw_dir=tmp_path, transport=transport)
    assert {r.status for r in again} == {"cached", "skipped"}


def test_html_response_is_rejected(tmp_path):
    html = httpx.Response(200, content=b"<html>Access Denied</html>")
    transport = _transport({"sec_bhavdata_full": html})
    results = nse.download_range(date(2026, 1, 5), date(2026, 1, 5), ["bhav"], raw_dir=tmp_path, transport=transport)
    assert results[0].status == "error"
    assert not any(tmp_path.rglob("*.csv"))


def test_recent_404_not_marked_missing(tmp_path):
    with httpx.Client(transport=_transport({})) as client:
        res = nse.fetch_day(client, nse.DATASETS["bhav"], date(2026, 10, 6), today=date(2026, 10, 7), raw_dir=tmp_path)
    assert res.status == "missing"
    assert not any(tmp_path.rglob("*.missing"))
