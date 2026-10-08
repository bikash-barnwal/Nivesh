from datetime import date, timedelta

import polars as pl

from nivesh.data.align import align_actions, snap_ratio


def bars(symbol: str, start: date, closes_opens: list[tuple[float, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(closes_opens),
            "date": [start + timedelta(days=i) for i in range(len(closes_opens))],
            "open": [float(o) for _, o in closes_opens],
            "close": [float(c) for c, _ in closes_opens],
        }
    )


def action(symbol: str, ex: date, factor: float | None, purpose: str) -> dict:
    return {
        "symbol": symbol,
        "ex_date": ex,
        "price_factor": factor,
        "purpose": purpose,
        "factor_method": "purpose" if factor is not None else "market_implied",
    }


def frame(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows),
        schema={
            "symbol": pl.Utf8,
            "ex_date": pl.Date,
            "price_factor": pl.Float64,
            "purpose": pl.Utf8,
            "factor_method": pl.Utf8,
        },
    )


D = date(2023, 9, 20)
# flat 100, drops to 50 on day 8 (2023-09-28)
SPLIT_BARS = bars("HAL", D, [(100, 100)] * 8 + [(50, 50)] * 5)


def test_duplicate_listing_with_wrong_date_is_applied_once_on_the_real_session():
    acts = frame(
        action("HAL", date(2023, 9, 28), 0.5, "FV SPLT RS 10 TO RS 5"),
        action("HAL", date(2023, 9, 29), 0.5, "FV SPLT RS 10 TO RS 5"),
    )
    events, audit = align_actions(acts, SPLIT_BARS)
    assert events.height == 1
    assert events["ex_date"][0] == date(2023, 9, 28)
    assert events["price_factor"][0] == 0.5
    assert audit["status"].to_list() == ["verified"]


def test_listed_date_off_by_days_is_moved_to_the_session_that_moved():
    acts = frame(action("HAL", date(2023, 9, 30), 0.5, "SPLIT"))
    events, _ = align_actions(acts, SPLIT_BARS)
    assert events["ex_date"].to_list() == [date(2023, 9, 28)]


def test_alternate_factors_pick_the_one_the_market_agrees_with():
    # GRUH style: 0.5 first, corrected to 0.2 later; the market moved to ~0.2
    prices = bars("GRUH", D, [(100, 100)] * 8 + [(20.2, 20.2)] * 4)
    acts = frame(
        action("GRUH", date(2023, 9, 28), 0.5, "BONUS 1:1"),
        action("GRUH", date(2023, 9, 28), 0.2, "FV SPLIT RS10 TO RS2"),
    )
    events, _ = align_actions(acts, prices)
    assert events["price_factor"].to_list() == [0.2]


def test_two_rows_for_bonus_and_split_use_the_product():
    prices = bars("TECHM", D, [(100, 100)] * 8 + [(25, 25)] * 4)
    acts = frame(
        action("TECHM", date(2023, 9, 28), 0.5, "BONUS 1:1"),
        action("TECHM", date(2023, 9, 28), 0.5, "FV SPLIT RS10 TO RS5"),
    )
    events, audit = align_actions(acts, prices)
    assert events["price_factor"].to_list() == [0.25]
    assert audit["status"].to_list() == ["verified"]


def test_half_parsed_combined_text_snaps_to_the_market_ratio():
    prices = bars("NAZARA", D, [(100, 100)] * 8 + [(25.5, 25.5)] * 4)
    acts = frame(action("NAZARA", date(2023, 9, 28), 0.5, "BONUS 1:1"))
    events, audit = align_actions(acts, prices)
    assert events["price_factor"].to_list() == [0.25]
    assert audit["status"].to_list() == ["market_snapped"]


def test_action_with_no_price_evidence_is_skipped_and_audited():
    flat = bars("HAL", D, [(100, 100)] * 13)
    events, audit = align_actions(frame(action("HAL", date(2023, 9, 28), 0.5, "SPLIT")), flat)
    assert events.height == 0
    assert audit["status"].to_list() == ["unverified"]


def test_demerger_row_passes_through_for_the_adjuster():
    acts = frame(action("ABC", date(2023, 9, 28), None, "DEMERGER"))
    events, audit = align_actions(acts, SPLIT_BARS.with_columns(pl.lit("ABC").alias("symbol")))
    assert events["factor_method"].to_list() == ["market_implied"]
    assert audit.height == 0


def test_consolidation_is_verified_upward():
    prices = bars("XYZ", D, [(10, 10)] * 8 + [(100, 100)] * 4)
    events, _ = align_actions(frame(action("XYZ", date(2023, 9, 28), 10.0, "FV CONSOLIDATION RS1 TO RS10")), prices)
    assert events["price_factor"].to_list() == [10.0]


def test_snap_ratio():
    assert snap_ratio(0.26) == 0.25
    assert snap_ratio(0.45) is None
