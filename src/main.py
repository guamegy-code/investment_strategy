"""Run the production strategy and its static benchmarks."""

import pandas as pd

from attribution import DynamicAllocationAttribution
from chart import draw_chart
from config import END_DATE, RESULT_DIR, START_DATE
from pension_strategies import (
    PensionKodexStrategy,
    PensionKoActStrategy,
    PensionNasdaqMixStrategy,
    PensionTimeStrategy,
)
from runner import Runner
from strategy import (
    PensionBlendedRiskAllocationStrategy,
    PensionBlendedVXUSSubstitutionStrategy,
    PensionRiskAllocationStrategy,
    PensionVXUSSubstitutionStrategy,
    STATIC_PENSION_7030,
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
)


def save_results(results):
    histories = {
        result["strategy"].__class__.__name__: result["history"]
        for result in results
    }
    benchmark = histories.get("STATIC_70_BND10_BIL10_GLD10")

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


def build_runner():
    """Configure the active strategies shown in the application."""
    strategies = (
        PensionRiskAllocationStrategy(),
        PensionBlendedRiskAllocationStrategy(),
        PensionVXUSSubstitutionStrategy(),
        PensionBlendedVXUSSubstitutionStrategy(),
        STATIC_PENSION_7030(),
        PensionNasdaqMixStrategy(),
        PensionKodexStrategy(),
        PensionTimeStrategy(),
        PensionKoActStrategy(),
#        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2(),
    )
    required_tickers = tuple(dict.fromkeys(
        ticker
        for strategy in strategies
        for ticker in strategy.required_tickers
    ))
    runner = Runner(
        tickers=required_tickers,
        use_strategy_tickers=True,
    )
    for strategy in strategies:
        runner.add_strategy(strategy)
    return runner


def main():
    runner = build_runner()
    results = runner.run()

    print("=" * 50)
    print(f"Backtest period: {START_DATE} ~ {END_DATE}")
    print("=" * 50)
    print("\n", runner.summary(), "\n")
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()
