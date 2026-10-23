"""Align corporate-action rows with what the market actually did.

NSE lists the same action several times (corrected dates, re-worded purposes), sometimes bundles a bonus and a split
in one text that only half parses, and sometimes the listed ex-date is not the session the price changed. Applying
every row blindly double-adjusts or misses the move, so each cluster of rows is checked against the prices:

1. rows for one symbol whose ex-dates are within `CLUSTER_DAYS` form one cluster (one real event);
2. hypotheses are every distinct parsed factor plus their product (bonus + split announced as two rows);
3. the (hypothesis, session) pair whose open / previous close matches best, within `TOLERANCE`, is adopted;
4. otherwise, if one session shows a clear move that snaps to a ratio an issuer would really use, that ratio is
   adopted (`market_snapped`);
5. otherwise nothing is applied and the cluster is reported as `unverified`.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from itertools import product

import polars as pl

CLUSTER_DAYS = 7
WINDOW_BEFORE = 3
WINDOW_AFTER = 10
TOLERANCE = math.log(1.25)
SNAP_TOLERANCE = 0.08
MIN_MOVE = math.log(1.3)  # a move smaller than this is never snapped to a split ratio

_SPLITS = [1 / n for n in (2, 3, 4, 5, 8, 10, 20, 50, 100)]
_BONUS = [b / (a + b) for a in range(1, 4) for b in range(1, 4)]
NICE_RATIOS = sorted(
    {round(x, 6) for x in _SPLITS + _BONUS}
    | {round(x * y, 6) for x, y in product(_BONUS, _SPLITS)}
    | {float(n) for n in range(2, 11)}
)

ACTION_COLUMNS = {
    "symbol": pl.Utf8,
    "ex_date": pl.Date,
    "price_factor": pl.Float64,
    "purpose": pl.Utf8,
    "factor_method": pl.Utf8,
}
AUDIT_COLUMNS = {
    "symbol": pl.Utf8,
    "listed_ex_date": pl.Date,
    "session": pl.Date,
    "status": pl.Utf8,
    "factor": pl.Float64,
    "implied": pl.Float64,
    "purpose": pl.Utf8,
}


def snap_ratio(value: float, tolerance: float = SNAP_TOLERANCE) -> float | None:
    """The closest ratio an issuer would really use, if `value` is within `tolerance` of it."""
    best = min(NICE_RATIOS, key=lambda r: abs(math.log(value / r)))
    return best if abs(math.log(value / best)) <= tolerance else None


def _clusters(rows: list[dict]) -> list[list[dict]]:
    out: list[list[dict]] = []
    for row in sorted(rows, key=lambda r: r["ex_date"]):
        if out and (row["ex_date"] - out[-1][-1]["ex_date"]).days <= CLUSTER_DAYS:
            out[-1].append(row)
        else:
            out.append([row])
    return out


def _hypotheses(cluster: list[dict]) -> list[float]:
    """Each distinct factor, plus the product over distinct purposes (a bonus and a split listed separately)."""
    hyps = sorted({round(r["price_factor"], 8) for r in cluster})
    per_purpose = {r["purpose"]: r["price_factor"] for r in cluster}
    combined = round(math.prod(per_purpose.values()), 8)
    if len(per_purpose) > 1 and combined not in hyps:
        hyps.append(combined)
    return hyps


def _resolve(
    cluster: list[dict], sessions: list[tuple[date, float]]
) -> tuple[str, date | None, float | None, float | None]:
    """(status, session, factor, implied) for one cluster; `sessions` is (date, open / prev close) in the window."""
    listed = [r["ex_date"] for r in cluster]
    hyps = _hypotheses(cluster)
    best = None
    for hyp in hyps:
        for session, implied in sessions:
            err = abs(math.log(implied / hyp))
            tie = min(abs((session - d).days) for d in listed)
            key = (err, tie)
            if best is None or key < best[0]:
                best = (key, hyp, session, implied)
    if best is not None and best[0][0] <= TOLERANCE:
        _, hyp, session, implied = best
        return "verified", session, hyp, implied

    if sessions:
        wants_up = any(h > 1 for h in hyps)
        session, implied = max(sessions, key=lambda s: s[1]) if wants_up else min(sessions, key=lambda s: s[1])
        if abs(math.log(implied)) >= MIN_MOVE and (implied > 1) == wants_up:
            snapped = snap_ratio(implied)
            if snapped is not None:
                return "market_snapped", session, snapped, implied
    return "unverified", None, None, None


def align_actions(actions: pl.DataFrame, bars: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Return (events to apply, audit).

    `actions`: symbol, ex_date, price_factor, purpose, factor_method (symbols already on the current ticker).
    `bars`: symbol, date, open, close, one row per symbol and session.
    Events are keyed on the session the price changed; rows with no parsed factor (demergers) pass through unchanged
    so the adjuster can still derive them from the ex-date open.
    """
    passthrough = actions.filter(pl.col("price_factor").is_null()).select(list(ACTION_COLUMNS))
    parsed = actions.filter(pl.col("price_factor").is_not_null())

    implied = (
        bars.sort(["symbol", "date"])
        .with_columns(pl.col("close").shift(1).over("symbol").alias("prev_close"))
        .filter(pl.col("prev_close") > 0)
        .filter(pl.col("open") > 0)
        .select("symbol", "date", (pl.col("open") / pl.col("prev_close")).alias("implied"))
    )
    by_symbol = {
        k[0]: sorted(zip(g["date"].to_list(), g["implied"].to_list(), strict=True))
        for k, g in implied.group_by("symbol")
    }

    events: list[dict] = []
    audit: list[dict] = []
    for symbol, group in parsed.group_by("symbol"):
        symbol = symbol[0]
        rows = group.to_dicts()
        series = by_symbol.get(symbol, [])
        for cluster in _clusters(rows):
            lo = min(r["ex_date"] for r in cluster) - timedelta(days=WINDOW_BEFORE)
            hi = max(r["ex_date"] for r in cluster) + timedelta(days=WINDOW_AFTER)
            window = [(d, v) for d, v in series if lo <= d <= hi]
            status, session, factor, imp = _resolve(cluster, window)
            purpose = " | ".join(sorted({r["purpose"] for r in cluster}))
            audit.append(
                {
                    "symbol": symbol,
                    "listed_ex_date": cluster[0]["ex_date"],
                    "session": session,
                    "status": status,
                    "factor": factor,
                    "implied": imp,
                    "purpose": purpose,
                }
            )
            if session is not None and factor is not None:
                events.append(
                    {
                        "symbol": symbol,
                        "ex_date": session,
                        "price_factor": factor,
                        "purpose": purpose,
                        "factor_method": status,
                    }
                )
    ev = pl.DataFrame(events, schema=ACTION_COLUMNS) if events else pl.DataFrame(schema=ACTION_COLUMNS)
    au = pl.DataFrame(audit, schema=AUDIT_COLUMNS) if audit else pl.DataFrame(schema=AUDIT_COLUMNS)
    return pl.concat([ev, passthrough], how="vertical_relaxed"), au
