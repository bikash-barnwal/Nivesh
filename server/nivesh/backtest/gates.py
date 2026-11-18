"""Anti-overfitting gates a strategy must clear before it may move from `research` to `paper`.

Passing the gates is necessary, not sufficient: it only screens out the most common ways a backtest flatters itself.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from nivesh.backtest.engine import Panel, Result, simulate
from nivesh.backtest.metrics import curve_metrics
from nivesh.backtest.spec import Strategy

OOS_FRACTION = 0.30
MIN_TRADES = 30
OOS_CALMAR_RATIO = 0.5  # OOS Calmar must keep at least this share of in-sample Calmar
MAX_SENSITIVITY_DROP = 0.40  # a +-20% parameter change may not cut Calmar by more than this
MAX_REGIME_PNL_SHARE = 0.60
SENSITIVITY_PARAMS = (
    "exit.hard_stop_pct",
    "exit.time_stop_days",
    "portfolio.max_positions",
    "portfolio.risk_per_trade_pct",
)


def _gate(name: str, passed: bool | None, value: Any, threshold: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "passed": passed, "value": value, "threshold": threshold, "detail": detail}


def _calmar(result: Result) -> float | None:
    return result.metrics.get("calmar")


def _perturb(value: float, factor: float) -> float | int:
    scaled = value * factor
    return max(1, round(scaled)) if isinstance(value, int) else scaled


def evaluate_gates(strategy: Strategy, panel: Panel, base: Result) -> dict[str, Any]:
    dates, equity = base.dates, base.equity
    gates: list[dict[str, Any]] = []

    n_trades = base.metrics.get("trades", 0)
    gates.append(_gate("minimum_trades", n_trades >= MIN_TRADES, n_trades, f">= {MIN_TRADES}"))

    split = int(len(dates) * (1 - OOS_FRACTION))
    if split >= 60 and len(dates) - split >= 60:
        is_m = curve_metrics(dates[:split], equity[:split])
        oos_m = curve_metrics(dates[split - 1 :], equity[split - 1 :])
        is_calmar, oos_calmar = is_m["calmar"], oos_m["calmar"]
        ok = (
            None
            if oos_calmar is None or is_calmar is None
            else (oos_calmar >= OOS_CALMAR_RATIO * is_calmar if is_calmar > 0 else oos_calmar > 0)
        )
        gates.append(
            _gate(
                "out_of_sample",
                ok,
                {"in_sample": is_m, "out_of_sample": oos_m},
                f"OOS Calmar >= {OOS_CALMAR_RATIO:.0%} of in-sample",
                f"split at {dates[split]}",
            )
        )
    else:
        is_m = oos_m = None
        gates.append(_gate("out_of_sample", None, None, "needs >= 60 sessions on each side", "not enough history"))

    base_calmar = _calmar(base)
    sens: list[dict[str, Any]] = []
    for path in SENSITIVITY_PARAMS:
        section, name = path.split(".")
        current = getattr(getattr(strategy, section), name)
        for factor in (0.8, 1.2):
            value = _perturb(current, factor)
            variant = simulate(strategy.with_overrides({path: value}), panel)
            drop = None
            if base_calmar and base_calmar > 0 and _calmar(variant) is not None:
                drop = 1 - _calmar(variant) / base_calmar
            sens.append({"param": path, "value": value, "calmar": _calmar(variant), "drop": drop})
    drops = [s["drop"] for s in sens if s["drop"] is not None]
    gates.append(
        _gate(
            "parameter_sensitivity",
            None if not drops else max(drops) <= MAX_SENSITIVITY_DROP,
            {"worst_drop": max(drops) if drops else None, "variants": sens},
            f"max Calmar drop <= {MAX_SENSITIVITY_DROP:.0%} for +-20% parameters",
        )
    )

    stressed = simulate(strategy.with_overrides({"costs.slippage_bps": strategy.costs.slippage_bps * 2}), panel)
    gates.append(
        _gate(
            "cost_stress",
            (stressed.metrics.get("cagr") or 0) > 0,
            stressed.metrics.get("cagr"),
            "CAGR > 0 with 2x slippage",
        )
    )

    by_regime = base.metrics.get("by_regime", [])
    total_pnl = base.metrics.get("pnl") or 0.0
    if total_pnl > 0 and by_regime:
        top = max(by_regime, key=lambda r: r["pnl"])
        share = top["pnl"] / total_pnl
        gates.append(
            _gate(
                "regime_spread",
                share <= MAX_REGIME_PNL_SHARE,
                {"regime": top["regime"], "share": share},
                f"no regime > {MAX_REGIME_PNL_SHARE:.0%} of P&L",
            )
        )
    else:
        gates.append(
            _gate(
                "regime_spread",
                None,
                None,
                f"no regime > {MAX_REGIME_PNL_SHARE:.0%} of P&L",
                "needs positive total P&L",
            )
        )

    sharpe, cagr, max_dd = base.metrics.get("sharpe"), base.metrics.get("cagr"), base.metrics.get("max_drawdown")
    review = bool((sharpe or 0) > 3 or ((max_dd or -1) > -0.05 and (cagr or 0) > 0.6))
    decided = [g["passed"] for g in gates if g["passed"] is not None]
    return {
        "gates": gates,
        "passed_all": bool(decided) and all(decided) and len(decided) == len(gates),
        "review_required": review,
        "review_reason": "Sharpe > 3, or drawdown under 5% with CAGR above 60%: check for overfitting or bias"
        if review
        else "",
        "oos_fraction": OOS_FRACTION,
    }


def count_passed(report: dict[str, Any]) -> tuple[int, int]:
    gates = report["gates"]
    return sum(1 for g in gates if g["passed"]), len(gates)


__all__ = ["evaluate_gates", "count_passed", "np"]
