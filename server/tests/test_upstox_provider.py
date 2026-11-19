"""Tests for Upstox data provider (resolver, client, candle normalizer).

Covers:
  - Unit tests using mocked JSON (no network required)
  - Candle normalization and validation logic
  - Security: zero order-write endpoint calls
  - Integration smoke tests (skipped when UPSTOX_API_KEY is absent)
"""

from __future__ import annotations

import os
import gzip
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from nivesh.data.upstox_resolver import UpstoxResolver
from nivesh.data.upstox_client import UpstoxClient, _FORBIDDEN_PATHS
from nivesh.data.upstox_candles import normalize_candles, write_candles

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RECORDS = [
    {
        "instrument_key": "NSE_EQ|INE002A01018",
        "trading_symbol": "RELIANCE",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "isin": "INE002A01018",
        "name": "Reliance Industries Limited",
        "lot_size": 1,
        "tick_size": 0.05,
    },
    {
        "instrument_key": "NSE_EQ|INE040A01034",
        "trading_symbol": "HDFCBANK",
        "segment": "NSE_EQ",
        "instrument_type": "EQ",
        "isin": "INE040A01034",
        "name": "HDFC Bank Limited",
        "lot_size": 1,
        "tick_size": 0.05,
    },
    {
        "instrument_key": "BSE_EQ|INE002A01018",
        "trading_symbol": "RELIANCE",
        "segment": "BSE_EQ",  # should NOT be indexed
        "instrument_type": "EQ",
        "isin": "INE002A01018",
        "name": "Reliance Industries (BSE)",
        "lot_size": 1,
        "tick_size": 0.05,
    },
    {
        "instrument_key": "NSE_INDEX|Nifty 50",
        "trading_symbol": "Nifty 50",
        "segment": "NSE_INDEX",
        "instrument_type": "INDEX",
        "isin": None,
        "name": "Nifty 50",
        "lot_size": 1,
        "tick_size": 0.05,
    },
]


@pytest.fixture
def resolver() -> UpstoxResolver:
    r = UpstoxResolver()
    r.load_from_records(SAMPLE_RECORDS)
    return r


def _make_candle(ts: datetime, o=100.0, h=110.0, l=90.0, c=105.0, v=1000):
    return [ts.isoformat(), o, h, l, c, v, 0]


IST = timezone(timedelta(hours=5, minutes=30))

SAMPLE_CANDLES = [
    _make_candle(datetime(2026, 10, 23, 9, 15, tzinfo=IST), 2800.0, 2850.0, 2790.0, 2830.0, 10000),
    _make_candle(datetime(2026, 10, 23, 9, 16, tzinfo=IST), 2830.0, 2860.0, 2820.0, 2845.0, 8000),
    _make_candle(datetime(2026, 10, 23, 9, 17, tzinfo=IST), 2845.0, 2870.0, 2840.0, 2855.0, 7500),
]


# ---------------------------------------------------------------------------
# Resolver unit tests
# ---------------------------------------------------------------------------

class TestUpstoxResolver:
    def test_resolve_symbol(self, resolver: UpstoxResolver) -> None:
        key = resolver.resolve_symbol("RELIANCE")
        assert key == "NSE_EQ|INE002A01018"

    def test_resolve_symbol_case_insensitive(self, resolver: UpstoxResolver) -> None:
        key = resolver.resolve_symbol("reliance")
        assert key == "NSE_EQ|INE002A01018"

    def test_resolve_isin(self, resolver: UpstoxResolver) -> None:
        key = resolver.resolve_isin("INE002A01018")
        assert key == "NSE_EQ|INE002A01018"

    def test_bse_segment_not_indexed(self, resolver: UpstoxResolver) -> None:
        # BSE_EQ segment must not appear under NSE symbol lookup
        assert resolver.resolve_isin("INE002A01018") == "NSE_EQ|INE002A01018"
        # Verify that BSE key itself is not in the details map
        assert resolver.get_details("BSE_EQ|INE002A01018") is None

    def test_unknown_symbol_returns_none(self, resolver: UpstoxResolver) -> None:
        assert resolver.resolve_symbol("DOESNOTEXIST") is None

    def test_index_indexed_by_symbol(self, resolver: UpstoxResolver) -> None:
        key = resolver.resolve_symbol("Nifty 50")
        assert key == "NSE_INDEX|Nifty 50"

    def test_all_symbols(self, resolver: UpstoxResolver) -> None:
        symbols = resolver.all_symbols()
        assert "RELIANCE" in symbols
        assert "HDFCBANK" in symbols

    def test_load_from_gz_file(self) -> None:
        """Verify load() correctly reads a gzipped JSON file."""
        r = UpstoxResolver()
        with tempfile.NamedTemporaryFile(suffix=".json.gz", delete=False) as f:
            tmp = Path(f.name)
        with gzip.open(tmp, "wt", encoding="utf-8") as fout:
            json.dump(SAMPLE_RECORDS, fout)
        try:
            r.load(tmp)
            assert r.resolve_symbol("RELIANCE") == "NSE_EQ|INE002A01018"
        finally:
            tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Candle normalizer unit tests
# ---------------------------------------------------------------------------

class TestNormalizeCandles:
    def test_basic_normalization(self) -> None:
        df = normalize_candles(SAMPLE_CANDLES, "RELIANCE", "NSE_EQ|INE002A01018")
        assert len(df) == 3
        assert df["symbol"][0] == "RELIANCE"
        assert df["instrument_key"][0] == "NSE_EQ|INE002A01018"

    def test_ascending_sort(self) -> None:
        """Candles are returned descending from Upstox; normalizer must sort ascending."""
        reversed_candles = list(reversed(SAMPLE_CANDLES))
        df = normalize_candles(reversed_candles, "RELIANCE", "NSE_EQ|INE002A01018")
        ts = df["timestamp"].to_list()
        assert ts == sorted(ts)

    def test_empty_input(self) -> None:
        df = normalize_candles([], "RELIANCE", "NSE_EQ|INE002A01018")
        assert len(df) == 0

    def test_negative_price_raises(self) -> None:
        bad = [_make_candle(datetime(2026, 10, 23, 9, 15, tzinfo=IST), o=-1.0, h=10.0, l=0.5, c=5.0)]
        with pytest.raises(ValueError, match="Non-positive"):
            normalize_candles(bad, "TESTBAD", "NSE_EQ|TEST")

    def test_volume_is_int(self) -> None:
        df = normalize_candles(SAMPLE_CANDLES, "RELIANCE", "NSE_EQ|INE002A01018")
        assert df["volume"].dtype == pl.Int64

    def test_write_and_read_parquet(self) -> None:
        from datetime import date
        df = normalize_candles(SAMPLE_CANDLES, "RELIANCE", "NSE_EQ|INE002A01018")
        with tempfile.TemporaryDirectory() as tmpdir:
            out = write_candles(df, "RELIANCE", date(2026, 10, 23), Path(tmpdir))
            loaded = pl.read_parquet(out)
        assert len(loaded) == 3
        assert "timestamp" in loaded.columns


# ---------------------------------------------------------------------------
# Client security tests
# ---------------------------------------------------------------------------

class TestForbiddenEndpoints:
    def test_forbidden_paths_non_empty(self) -> None:
        assert len(_FORBIDDEN_PATHS) >= 4

    def test_order_place_in_forbidden(self) -> None:
        assert "/v2/order/place" in _FORBIDDEN_PATHS

    def test_order_modify_in_forbidden(self) -> None:
        assert "/v2/order/modify" in _FORBIDDEN_PATHS

    def test_order_cancel_in_forbidden(self) -> None:
        assert "/v2/order/cancel" in _FORBIDDEN_PATHS


# ---------------------------------------------------------------------------
# Integration smoke tests (require UPSTOX_API_KEY in environment)
# ---------------------------------------------------------------------------

UPSTOX_AVAILABLE = bool(os.environ.get("UPSTOX_API_KEY"))

pytestmark_integration = pytest.mark.skipif(
    not UPSTOX_AVAILABLE,
    reason="UPSTOX_API_KEY not set; skipping live integration test",
)


@pytestmark_integration
def test_live_ltp_reliance() -> None:
    """Live smoke test: LTP for RELIANCE must return a positive price."""
    with UpstoxClient() as client:
        data = client.get_ltp(["NSE_EQ|INE002A01018"])
    assert data, "Expected non-empty LTP response"
    record = next(iter(data.values()))
    assert record.get("last_price", 0) > 0, "last_price should be positive"


@pytestmark_integration
def test_live_historical_1m_reliance() -> None:
    """Live smoke test: 1m candles for RELIANCE on 2026-10-23 must have >=5 candles."""
    from datetime import date
    with UpstoxClient() as client:
        candles = client.get_historical_1m(
            "NSE_EQ|INE002A01018",
            from_date=date(2026, 10, 23),
            to_date=date(2026, 10, 23),
        )
    assert len(candles) >= 5, f"Expected >=5 candles, got {len(candles)}"

    df = normalize_candles(candles, "RELIANCE", "NSE_EQ|INE002A01018")
    assert len(df) >= 5
    assert df["close"].min() > 0
