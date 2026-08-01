"""Run the production strategy and its static benchmarks."""

import pandas as pd

from attribution import DynamicAllocationAttribution
from chart import draw_chart
from config import END_DATE, RESULT_DIR, START_DATE
from runner import Runner
from strategy import (
    ASYMMETRIC_TREND_BAND,
    BASIC_BANG_DIV,
    DynamicRiskAllocationStrategy,
    RETIREMENT_7030_BAND,
    STATIC_703010_BAND,
    STATIC_70_BIL20_GLD10,
    STATIC_70_BND10_BIL10_GLD10,
    STATIC_70_BND5_BIL15_GLD10,
)
from walkforward import WalkForwardProfileComparison


def save_results(results):
    histories = {
        result["strategy"].__class__.__name__: result["history"]
        for result in results
    }
    benchmark = histories.get("STATIC_703010_BAND")

    for result in results:
        name = result["strategy"].__class__.__name__
        result["history"].to_csv(RESULT_DIR / f"{name}_history.csv")
        result["trades"].to_csv(
            RESULT_DIR / f"{name}_trades.csv", index=False
        )
        if name == "DynamicRiskAllocationStrategy":
            attribution = DynamicAllocationAttribution(
                history=result["history"],
                market_data=result["market_data"],
                rebalances=result["rebalances"],
                benchmark_history=benchmark,
            )
            for report_name, report in attribution.all_reports().items():
                report.to_csv(
                    RESULT_DIR / f"{name}_{report_name}.csv", index=False
                )

    static_results = [
        result for result in results
        if result["strategy"].__class__.__name__.startswith("STATIC_")
    ]
    pd.DataFrame([result["summary"] for result in static_results]).to_csv(
        RESULT_DIR / "static_benchmark_comparison.csv", index=False
    )

    alternatives = {
        result["strategy"].__class__.__name__: result["history"]
        for result in static_results
        if result["strategy"].__class__.__name__ != "STATIC_703010_BAND"
    }
    if benchmark is not None and len(alternatives) >= 2:
        comparison = WalkForwardProfileComparison(alternatives, benchmark)
        for report_name, report in comparison.all_reports().items():
            report.to_csv(
                RESULT_DIR / f"static_benchmark_{report_name}.csv",
                index=False,
            )


def main():
    runner = Runner(tickers=("QQQ", "BND", "GLD", "BIL", "QLD"))
    for strategy in (
        DynamicRiskAllocationStrategy(),
        STATIC_703010_BAND(),
        STATIC_70_BIL20_GLD10(),
        STATIC_70_BND10_BIL10_GLD10(),
        STATIC_70_BND5_BIL15_GLD10(),
        BASIC_BANG_DIV(),
        RETIREMENT_7030_BAND(),
        ASYMMETRIC_TREND_BAND(),
    ):
        runner.add_strategy(strategy)
    results = runner.run()

    print("=" * 50)
    print(f"Backtest period: {START_DATE} ~ {END_DATE}")
    print("=" * 50)
    print("\n", runner.summary(), "\n")
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()
