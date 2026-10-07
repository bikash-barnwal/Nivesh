from datetime import date

from nivesh.api import intelligence


class FakeResult:
    columns = ["date", "value"]
    truncated = False

    def records(self):
        return [{"date": date(2024, 1, 2), "value": 1}]


class FakeEngine:
    def views(self):
        return ["market_engines"]

    def data_through(self):
        return date(2024, 1, 2)

    def query(self, sql, params=None, **kwargs):
        return FakeResult()


def test_state_snapshot_uses_envelope_and_one_date(monkeypatch):
    monkeypatch.setattr(intelligence, "get_engine", lambda: FakeEngine())
    result = intelligence.engines()
    assert result["as_of"] == date(2024, 1, 2)
    assert result["data"]
    assert {"as_of", "data_through", "version", "data", "provenance"} <= result.keys()
