"""Stress-test transaction costs and delayed signal execution."""

import pandas as pd

from config import COMMISSION, EXTENDED_DATA_DIR, RESULT_DIR, SLIPPAGE
from extended_data import ASSETS
from extended_stress import _window_report
from runner import Runner
from strategy import (
    DynamicRiskAllocationStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


COST_MULTIPLIERS = (1, 2, 3)
DELAYS = (0, 1, 2, 3)
ASYMMETRIC_COST_MULTIPLIERS = (1, 3)
ASYMMETRIC_BEAR_DELAYS = (1, 2, 3)


def _run_pair(cost_multiplier, delay, bear_delay=None):
    options = {
        "commission": COMMISSION * cost_multiplier,
        "slippage": SLIPPAGE * cost_multiplier,
        "signal_delay_days": delay,
        "bear_signal_delay_days": bear_delay,
    }
    runner = Runner(
        data_dir=EXTENDED_DATA_DIR,
        tickers=ASSETS,
        backtest_options=options,
    )
    runner.add_strategy(DynamicRiskAllocationStrategy())
    runner.add_strategy(STATIC_70_BND10_BIL10_GLD10())
    results = runner.run()
    return results, _window_report(results)


def _scenario_rows(results, windows, scenario, cost, delay, bear_delay):
    rows = []
    for result in results:
        name = result["strategy"].__class__.__name__
        selected = windows[
            (windows["Strategy"] == name)
            & windows["Window"].isin(("FULL_EXTENDED", "POST_2010"))
        ]
        for _, metrics in selected.iterrows():
            rows.append({
                "Scenario": scenario,
                "CostMultiplier": cost,
                "AllSignalExtraDelayDays": delay,
                "BearExtraDelayDays": bear_delay,
                "Strategy": name,
                "Window": metrics["Window"],
                "CAGR": metrics["CAGR"],
                "MDD": metrics["MDD"],
                "Sharpe": metrics["Sharpe"],
                "Calmar": metrics["Calmar"],
                "TransactionCosts": metrics["TransactionCosts"],
                "Rebalances": len(result["rebalances"]),
                "Trades": len(result["trades"]),
            })
    return rows


def _comparison(summary):
    dynamic_name = "DynamicRiskAllocationStrategy"
    benchmark_name = "STATIC_70_BND10_BIL10_GLD10"
    keys = [
        "Scenario", "CostMultiplier", "AllSignalExtraDelayDays",
        "BearExtraDelayDays", "Window",
    ]
    dynamic = summary[summary["Strategy"] == dynamic_name].set_index(keys)
    benchmark = summary[summary["Strategy"] == benchmark_name].set_index(keys)
    common = dynamic.index.intersection(benchmark.index)
    output = dynamic.loc[common, [
        "CAGR", "MDD", "Sharpe", "Calmar", "TransactionCosts",
        "Rebalances", "Trades",
    ]].copy()
    output.columns = [f"Dynamic{column}" for column in output.columns]
    output["BenchmarkCAGR"] = benchmark.loc[common, "CAGR"]
    output["BenchmarkMDD"] = benchmark.loc[common, "MDD"]
    output["BenchmarkSharpe"] = benchmark.loc[common, "Sharpe"]
    output["CAGRGap"] = output["DynamicCAGR"] - output["BenchmarkCAGR"]
    output["MDDImprovement"] = output["DynamicMDD"] - output["BenchmarkMDD"]
    output["SharpeGap"] = output["DynamicSharpe"] - output["BenchmarkSharpe"]
    return output.reset_index()


def _criteria(comparison):
    post = comparison[comparison["Window"] == "POST_2010"].copy()
    baseline = post[
        (post["Scenario"] == "SYMMETRIC")
        & (post["CostMultiplier"] == 1)
        & (post["AllSignalExtraDelayDays"] == 0)
    ].iloc[0]
    post["CAGRDragVsBaseline"] = baseline["DynamicCAGR"] - post["DynamicCAGR"]
    post["MDDDegradationVsBaseline"] = baseline["DynamicMDD"] - post["DynamicMDD"]
    post["BeatsBenchmarkCAGR"] = post["CAGRGap"] >= 0
    post["BeatsBenchmarkMDD"] = post["MDDImprovement"] >= 0
    post["SharpeAtLeast080"] = post["DynamicSharpe"] >= 0.80
    post["CAGRDragWithin030PctPoint"] = post["CAGRDragVsBaseline"] <= 0.003
    post["MDDDegradationWithin3PctPoint"] = (
        post["MDDDegradationVsBaseline"] <= 0.03
    )
    checks = [
        "BeatsBenchmarkCAGR", "BeatsBenchmarkMDD", "SharpeAtLeast080",
        "CAGRDragWithin030PctPoint", "MDDDegradationWithin3PctPoint",
    ]
    post["PassedAll"] = post[checks].all(axis=1)
    return post


def run_execution_stress():
    rows = []
    crisis_rows = []
    for cost in COST_MULTIPLIERS:
        for delay in DELAYS:
            results, windows = _run_pair(cost, delay)
            rows.extend(_scenario_rows(
                results, windows, "SYMMETRIC", cost, delay, None
            ))
            tagged = windows.copy()
            tagged.insert(0, "Scenario", "SYMMETRIC")
            tagged.insert(1, "CostMultiplier", cost)
            tagged.insert(2, "AllSignalExtraDelayDays", delay)
            tagged.insert(3, "BearExtraDelayDays", None)
            crisis_rows.append(tagged)

    for cost in ASYMMETRIC_COST_MULTIPLIERS:
        for bear_delay in ASYMMETRIC_BEAR_DELAYS:
            results, windows = _run_pair(cost, 0, bear_delay)
            rows.extend(_scenario_rows(
                results, windows, "BEAR_ONLY_DELAY", cost, 0, bear_delay
            ))
            tagged = windows.copy()
            tagged.insert(0, "Scenario", "BEAR_ONLY_DELAY")
            tagged.insert(1, "CostMultiplier", cost)
            tagged.insert(2, "AllSignalExtraDelayDays", 0)
            tagged.insert(3, "BearExtraDelayDays", bear_delay)
            crisis_rows.append(tagged)

    summary = pd.DataFrame(rows)
    comparison = _comparison(summary)
    criteria = _criteria(comparison)
    crisis = pd.concat(crisis_rows, ignore_index=True)
    summary.to_csv(RESULT_DIR / "execution_stress_summary.csv", index=False)
    comparison.to_csv(
        RESULT_DIR / "execution_stress_comparison.csv", index=False
    )
    criteria.to_csv(RESULT_DIR / "execution_stress_criteria.csv", index=False)
    crisis.to_csv(RESULT_DIR / "execution_stress_windows.csv", index=False)
    return comparison, criteria, crisis


if __name__ == "__main__":
    comparison_report, criteria_report, _ = run_execution_stress()
    print(criteria_report[[
        "Scenario", "CostMultiplier", "AllSignalExtraDelayDays",
        "BearExtraDelayDays", "DynamicCAGR", "DynamicMDD", "DynamicSharpe",
        "CAGRGap", "MDDImprovement", "CAGRDragVsBaseline",
        "MDDDegradationVsBaseline", "PassedAll",
    ]].to_string(index=False))
