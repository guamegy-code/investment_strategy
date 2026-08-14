"""Run the production strategy and its static benchmarks."""

import argparse
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
from strategy_dsl import load_strategy_directory
from strategy_domain import strategy_display_name
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
        strategy_display_name(result["strategy"]): result["history"]
        for result in results
    }
    benchmark = histories.get("STATIC_70_BND10_BIL10_GLD10")

    for result in results:
        name = strategy_display_name(result["strategy"])
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
        if strategy_display_name(result["strategy"]).startswith("STATIC_")
    ]
    pd.DataFrame([result["summary"] for result in static_results]).to_csv(
        RESULT_DIR / "static_benchmark_comparison.csv", index=False
    )


def build_runner():
    """Configure the active strategies shown in the application."""
    python_strategies = (
        RetirementAllocationStrategy(),
        RetirementAllocationVXUSStrategy(),
        RetirementAllocationProfitBandStrategy(),
        RetirementAllocationProfitBandVXUSStrategy(),
        KodexNasdaqAllocationStrategy(),
        TimeNasdaqAllocationStrategy(),
        KoActNasdaqAllocationStrategy(),
        NasdaqProductMixAllocationStrategy(),
        #EXPANDING_RISK_FORECAST_30_70(),
        STATIC_RETIREMENT_7030(),
        ASYMMETRIC_TREND_BAND_ADD_DEFENSE2(),
        #ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED(),
    )
    declarative_strategies = tuple(
        load_strategy_directory(RESULT_DIR.parent / "strategies")
    )
    strategies = (*python_strategies, *declarative_strategies)
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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chart-backend",
        choices=("matplotlib", "dash", "both"),
        default="matplotlib",
        help="결과 표시 방식 (기본값: matplotlib). both는 Matplotlib 창을 닫은 뒤 Dash를 시작합니다.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Dash 서버 주소")
    parser.add_argument("--port", type=int, default=8050, help="Dash 서버 포트")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
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
    if args.chart_backend in {"matplotlib", "both"}:
        draw_chart(results)
    if args.chart_backend in {"dash", "both"}:
        from research_web import run_research_web

        run_research_web(results, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
