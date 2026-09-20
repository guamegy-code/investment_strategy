"""Validate strategy 28 against strategies 25 and 27.

Strategy 28 keeps strategy 25's normal 100% QQQ allocation, creates a BIL
buffer only during valuation WARNING, and redeploys the entire buffer at the
first -10% drawdown stage.  Deeper stages remain for state and alert tracking,
while DEFENSE, structural-bear, RECOVERY, and BEAR targets retain priority.
This module records fixed-period, rolling-window, cost-stress, event-window,
and local parameter-sensitivity evidence.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
START_DATE = "2012-01-03"
END_DATE = "2026-07-31"
STRATEGY_PATHS = {
    25: ROOT / "strategies/25_qqq_valuation_breakdown_balanced.yaml",
    27: ROOT / "strategies/27_buy_3dip_valuation_defense_80_98_99_100.yaml",
    28: ROOT / "strategies/28_qqq_valuation_warning_dip_buyer.yaml",
}
PERIODS = {
    "FULL": (START_DATE, END_DATE),
    "DEVELOPMENT": (START_DATE, "2020-12-31"),
    "RECENT": ("2021-01-01", END_DATE),
}
EVENT_WINDOWS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "2020_CRASH_RECOVERY": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2022-01-03", "2022-12-30"),
    "2025_2026": ("2025-01-02", END_DATE),
}


def _metrics(history: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    selected = history.loc[start:end]
    performance = Performance(selected)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Volatility": performance.volatility(),
        "Sharpe": performance.sharpe_ratio(),
        "Sortino": performance.sortino_ratio(),
        "Calmar": performance.calmar_ratio(),
        "TotalReturn": selected["Portfolio"].iloc[-1] / selected["Portfolio"].iloc[0] - 1,
    }


def _run(
    definition: Mapping[str, Any],
    *,
    label: str,
    cost_multiple: float = 1.0,
) -> dict[str, Any]:
    strategy = DeclarativeStrategy(deepcopy(definition))
    history, trades, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
        start_date=START_DATE,
        end_date=END_DATE,
    ).run_all()
    return {
        "Label": label,
        "CostMultiple": cost_multiple,
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "TransactionCosts": float(history["TransactionCosts"].iloc[-1]),
        "history": history,
    }


def _strategy28_variant(warning_weight: int, drop_b: float) -> dict[str, Any]:
    definition = load_strategy_definition(STRATEGY_PATHS[28])
    definition["strategy"] = {
        **definition["strategy"],
        "id": f"strategy28-warning-{warning_weight}-drop-{abs(int(drop_b * 100))}",
        "name": f"Strategy 28 warning={warning_weight}% drop={drop_b:.0%}",
    }
    definition["parameters"]["drop_b"] = drop_b
    for rule in definition["target"]:
        if rule.get("when") == "state.defense_mode == 'WARNING'":
            rule["weights"] = {
                "QQQ": f"{warning_weight}%",
                "BIL": f"{100 - warning_weight}%",
            }
            break
    return definition


def _fixed_report(runs: Mapping[int, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for number, result in runs.items():
        for period, (start, end) in PERIODS.items():
            rows.append({
                "Strategy": number,
                "Period": period,
                "Trades": result["Trades"],
                "Rebalances": result["Rebalances"],
                "TransactionCosts": result["TransactionCosts"],
                **_metrics(result["history"], start, end),
            })
    report = pd.DataFrame(rows)
    baseline = report.loc[
        report["Strategy"] == 25,
        ["Period", "CAGR", "MDD", "Sharpe"],
    ].rename(columns={
        "CAGR": "Strategy25CAGR",
        "MDD": "Strategy25MDD",
        "Sharpe": "Strategy25Sharpe",
    })
    report = report.merge(baseline, on="Period", how="left")
    report["CAGRGapVs25"] = report["CAGR"] - report["Strategy25CAGR"]
    report["MDDImprovementVs25"] = report["MDD"] - report["Strategy25MDD"]
    report["SharpeGapVs25"] = report["Sharpe"] - report["Strategy25Sharpe"]
    return report


def _rolling_report(runs: Mapping[int, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    baseline = runs[25]["history"]
    candidate = runs[28]["history"]
    for years in (3, 5):
        for start_year in range(2012, 2026 - years + 1):
            start = f"{start_year}-01-01"
            end = f"{start_year + years - 1}-12-31"
            base_metrics = _metrics(baseline, start, end)
            candidate_metrics = _metrics(candidate, start, end)
            rows.append({
                "Years": years,
                "Window": f"{start_year}_{start_year + years - 1}",
                **candidate_metrics,
                "CAGRGapVs25": candidate_metrics["CAGR"] - base_metrics["CAGR"],
                "MDDImprovementVs25": candidate_metrics["MDD"] - base_metrics["MDD"],
                "SharpeGapVs25": candidate_metrics["Sharpe"] - base_metrics["Sharpe"],
            })
    return pd.DataFrame(rows)


def _rolling_summary(rolling: pd.DataFrame) -> pd.DataFrame:
    return (
        rolling.groupby("Years", as_index=False)
        .agg(
            Windows=("Window", "count"),
            CAGRWins=("CAGRGapVs25", lambda values: int((values > 1e-12).sum())),
            CAGRLosses=("CAGRGapVs25", lambda values: int((values < -1e-12).sum())),
            AverageCAGRGap=("CAGRGapVs25", "mean"),
            WorstCAGRGap=("CAGRGapVs25", "min"),
            MDDWins=("MDDImprovementVs25", lambda values: int((values > 1e-12).sum())),
            MDDLosses=("MDDImprovementVs25", lambda values: int((values < -1e-12).sum())),
            AverageMDDImprovement=("MDDImprovementVs25", "mean"),
            SharpeWins=("SharpeGapVs25", lambda values: int((values > 1e-12).sum())),
            AverageSharpeGap=("SharpeGapVs25", "mean"),
        )
    )


def _cost_stress_report(definitions: Mapping[int, Mapping[str, Any]]) -> pd.DataFrame:
    rows = []
    for cost_multiple in (1.0, 3.0, 5.0):
        for number in (25, 28):
            result = _run(
                definitions[number],
                label=f"{number}_cost_{cost_multiple:g}",
                cost_multiple=cost_multiple,
            )
            rows.append({
                "Strategy": number,
                "CostMultiple": cost_multiple,
                "Trades": result["Trades"],
                "Rebalances": result["Rebalances"],
                "TransactionCosts": result["TransactionCosts"],
                **_metrics(result["history"], START_DATE, END_DATE),
            })
    report = pd.DataFrame(rows)
    baseline = report.loc[
        report["Strategy"] == 25,
        ["CostMultiple", "CAGR", "MDD", "Sharpe"],
    ].rename(columns={
        "CAGR": "Strategy25CAGR",
        "MDD": "Strategy25MDD",
        "Sharpe": "Strategy25Sharpe",
    })
    report = report.merge(baseline, on="CostMultiple", how="left")
    report["CAGRGapVs25"] = report["CAGR"] - report["Strategy25CAGR"]
    report["MDDImprovementVs25"] = report["MDD"] - report["Strategy25MDD"]
    report["SharpeGapVs25"] = report["Sharpe"] - report["Strategy25Sharpe"]
    return report


def _sensitivity_report() -> pd.DataFrame:
    rows = []
    for warning_weight in (65, 70, 75):
        for drop_b in (-0.08, -0.10, -0.12):
            result = _run(
                _strategy28_variant(warning_weight, drop_b),
                label=f"warning_{warning_weight}_drop_{drop_b}",
            )
            rows.append({
                "WarningQQQWeight": warning_weight / 100,
                "FirstDipThreshold": drop_b,
                "Trades": result["Trades"],
                "Rebalances": result["Rebalances"],
                **_metrics(result["history"], START_DATE, END_DATE),
                "RecentCAGR": _metrics(
                    result["history"], *PERIODS["RECENT"]
                )["CAGR"],
                "RecentMDD": _metrics(
                    result["history"], *PERIODS["RECENT"]
                )["MDD"],
            })
    return pd.DataFrame(rows)


def _event_report(runs: Mapping[int, dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for event, (start, end) in EVENT_WINDOWS.items():
        for number in (25, 27, 28):
            rows.append({
                "Event": event,
                "Strategy": number,
                **_metrics(runs[number]["history"], start, end),
            })
    return pd.DataFrame(rows)


def run_validation():
    definitions = {
        number: load_strategy_definition(path)
        for number, path in STRATEGY_PATHS.items()
    }
    runs = {
        number: _run(definition, label=str(number))
        for number, definition in definitions.items()
    }
    fixed = _fixed_report(runs)
    rolling = _rolling_report(runs)
    rolling_summary = _rolling_summary(rolling)
    costs = _cost_stress_report(definitions)
    sensitivity = _sensitivity_report()
    events = _event_report(runs)

    reports = {
        "strategy28_comparison.csv": fixed,
        "strategy28_rolling.csv": rolling,
        "strategy28_rolling_summary.csv": rolling_summary,
        "strategy28_cost_stress.csv": costs,
        "strategy28_sensitivity.csv": sensitivity,
        "strategy28_event_windows.csv": events,
    }
    for filename, report in reports.items():
        report.to_csv(RESULT_DIR / filename, index=False)
    return fixed, rolling_summary, costs, sensitivity, events


if __name__ == "__main__":
    fixed, rolling_summary, costs, sensitivity, events = run_validation()
    print(fixed.to_string(index=False))
    print("\nRolling summary")
    print(rolling_summary.to_string(index=False))
    print("\nCost stress")
    print(costs.to_string(index=False))
    print("\nSensitivity")
    print(sensitivity.to_string(index=False))
    print("\nEvent windows")
    print(events.to_string(index=False))
