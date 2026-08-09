"""Validate a modest upper profit band for the retirement strategy."""

from __future__ import annotations

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, RESULT_DIR, START_DATE
from experimental_strategies import (
    RetirementAllocationAsymmetricProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
)
from performance import Performance
from strategy import (
    RetirementAllocationVXUSStrategy,
    RetirementAllocationStrategy,
)


COMMON_END_DATE = "2026-07-31"
CAPS = (0.71, 0.725, 0.75, 0.775, 0.80, 0.825, 0.85, 0.875, 0.90)
FIXED_WINDOWS = (
    ("FULL", START_DATE, COMMON_END_DATE),
    ("2012_2022", "2012-01-01", "2022-12-31"),
    ("2024_PLUS", "2024-01-01", COMMON_END_DATE),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", COMMON_END_DATE),
)
FAMILIES = (
    (
        "BASE",
        RetirementAllocationStrategy,
        RetirementAllocationAsymmetricProfitBandStrategy,
    ),
    (
        "VXUS",
        RetirementAllocationVXUSStrategy,
        RetirementAllocationProfitBandVXUSStrategy,
    ),
)


def _run(factory, start_date, end_date):
    strategy = factory()
    with contextlib.redirect_stdout(io.StringIO()):
        history, trades, rebalances = Backtest(
            strategy,
            data_dir=DATA_DIR,
            tickers=strategy.required_tickers,
            start_date=start_date,
            end_date=end_date,
        ).run_all()
    metrics = Performance(history).summary()
    qqq_weights = history["Weights"].map(lambda item: item.get("QQQ", 0.0))
    reasons = [event.get("Reason") or "" for event in rebalances]
    return {
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "UpperBandEvents": sum("UPPER_QQQ_BAND" in reason for reason in reasons),
        "MaximumQQQWeight": qqq_weights.max(),
    }


def _factory(strategy_class, cap):
    return lambda: strategy_class(upper_risk_weight=cap)


def _fixed_report():
    return pd.DataFrame([
        {
            "Family": family,
            "Window": window,
            "Variant": label,
            **_run(factory, start, end),
        }
        for family, baseline_class, candidate_class in FAMILIES
        for window, start, end in FIXED_WINDOWS
        for label, factory in (
            (("CURRENT", baseline_class),)
            + tuple(
                (f"CAP_{cap:.1%}", _factory(candidate_class, cap))
                for cap in CAPS
            )
        )
    ])


def _rolling_report():
    rows = []
    for family, baseline_class, candidate_class in FAMILIES:
        for year in range(pd.Timestamp(START_DATE).year, 2025):
            start = f"{year}-01-01"
            end = f"{year + 2}-12-31"
            baseline = _run(baseline_class, start, end)
            for cap in CAPS:
                candidate = _run(_factory(candidate_class, cap), start, end)
                rows.append({
                    "Family": family,
                    "Window": f"{year}_{year + 2}",
                    "UpperRiskWeight": cap,
                    **candidate,
                    "CAGRGap": candidate["CAGR"] - baseline["CAGR"],
                    "MDDImprovement": candidate["MDD"] - baseline["MDD"],
                    "SharpeGap": candidate["Sharpe"] - baseline["Sharpe"],
                })
    return pd.DataFrame(rows)


def run_retirement_asymmetric_profit_band_validation():
    fixed = _fixed_report()
    rolling = _rolling_report()
    fixed.to_csv(
        RESULT_DIR / "retirement_asymmetric_profit_band_fixed.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "retirement_asymmetric_profit_band_rolling.csv", index=False
    )
    return {"fixed": fixed, "rolling": rolling}


if __name__ == "__main__":
    reports = run_retirement_asymmetric_profit_band_validation()
    print(reports["fixed"].to_string(index=False))
    print("\nRolling three-year summary")
    print(
        reports["rolling"].groupby(["Family", "UpperRiskWeight"]).agg(
            Windows=("Window", "count"),
            CAGRWins=("CAGRGap", lambda values: (values > 0.0).sum()),
            AverageCAGRGap=("CAGRGap", "mean"),
            WorstCAGRGap=("CAGRGap", "min"),
            MDDWins=("MDDImprovement", lambda values: (values > 0.0).sum()),
            AverageMDDImprovement=("MDDImprovement", "mean"),
            SharpeWins=("SharpeGap", lambda values: (values > 0.0).sum()),
            AverageSharpeGap=("SharpeGap", "mean"),
        ).to_string()
    )
