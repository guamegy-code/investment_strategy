"""Run the production strategy and its static benchmarks."""

import pandas as pd

from attribution import RetirementAllocationAttribution
from chart import draw_chart
from config import END_DATE, FX_RATE_TICKERS, RESULT_DIR, START_DATE
from downloader import ensure_data_files
from pension_strategies import (
    KodexNasdaqAllocationStrategy,
    KoActNasdaqAllocationStrategy,
    NasdaqProductMixAllocationStrategy,
    TimeNasdaqAllocationStrategy,
)
from runner import Runner
from strategy import (
    RetirementAllocationVXUSStrategy,
    RetirementAllocationStrategy,
    EXPANDING_RISK_FORECAST_30_70,
    STATIC_RETIREMENT_7030,
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
)

from experimental_strategies import (
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED,
    RetirementAllocationProfitBandStrategy,
    RetirementAllocationProfitBandVXUSStrategy,
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
        if name == "RetirementAllocationStrategy":
            attribution = RetirementAllocationAttribution(
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
        RetirementAllocationStrategy(),
        RetirementAllocationVXUSStrategy(),
        RetirementAllocationProfitBandStrategy(),
        RetirementAllocationProfitBandVXUSStrategy(),
        #EXPANDING_RISK_FORECAST_30_70(),
        STATIC_RETIREMENT_7030(),
        #NasdaqProductMixAllocationStrategy(),
        #KodexNasdaqAllocationStrategy(),
        #TimeNasdaqAllocationStrategy(),
        #KoActNasdaqAllocationStrategy(),
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2(),
        #ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED(),
    )
    required_tickers = tuple(dict.fromkeys(
        ticker
        for strategy in strategies
        for ticker in strategy.required_tickers
    ))
    runner = Runner(
        tickers=required_tickers,
        backtest_options={
            "start_date": START_DATE,
            "end_date": END_DATE,
        },
        use_strategy_tickers=True,
    )
    for strategy in strategies:
        runner.add_strategy(strategy)
    return runner


def ensure_runner_data(runner):
    """Download missing strategy and exchange-rate CSV files."""
    required_tickers = tuple(dict.fromkeys(
        (*(runner.tickers or ()), *FX_RATE_TICKERS)
    ))
    return ensure_data_files(required_tickers, data_dir=runner.data_dir)


def main():
    runner = build_runner()
    ensure_runner_data(runner)
    results = runner.run()
    summary = runner.summary()
    displayed_period = runner.backtest_period()

    print("=" * 50)
    print(f"Backtest period: {displayed_period}")
    print("=" * 50)
    print("\n", summary, "\n")
    save_results(results)
    draw_chart(results)


if __name__ == "__main__":
    main()
