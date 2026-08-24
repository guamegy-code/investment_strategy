"""Run the production strategy and its static benchmarks."""

import argparse
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import pandas as pd

from attribution import RetirementAllocationAttribution
from chart import draw_chart
from config import END_DATE, FX_RATE_TICKERS, RESULT_DIR, START_DATE
from downloader import ensure_data_files
from runner import Runner
from rebalance_service import DEFAULT_STRATEGY_CATALOG
from strategy_dsl import load_strategy_directory
from strategy_domain import strategy_display_name, strategy_identity
from strategy_runtime import (
    IncrementalStrategyResults,
    ReloadableStrategyResults,
    StrategyResultDiskCache,
    strategy_calculation_fingerprint,
)


STRATEGY_DIRECTORY = RESULT_DIR.parent / "strategies"
STRATEGY_CACHE_DIRECTORY = RESULT_DIR / ".strategy-cache"
RESULT_CACHE = StrategyResultDiskCache(STRATEGY_CACHE_DIRECTORY)
MODULE_DIRECTORY = Path(__file__).resolve().parent
BACKTEST_ENGINE_FILES = tuple(
    MODULE_DIRECTORY / name
    for name in (
        "backtest.py",
        "config.py",
        "indicators.py",
        "performance.py",
        "portfolio.py",
        "strategy_domain.py",
        "strategy_dsl.py",
    )
)
PREFERRED_STRATEGY_ORDER = (
    "dsl:allocation",
    "dsl:allocation-vxus",
    "dsl:profit-band",
    "dsl:profit-band-vxus",
    "dsl:profit-band-vxus-v2",
    "dsl:profit-band-tdf2050",
    "dsl:band-7030",
    "dsl:band-7030-tdf",
    "dsl:kodex-nasdaq",
    "dsl:time-nasdaq",
    "dsl:koact-nasdaq",
    "dsl:nasdaq-mix",
    "dsl:time-tdf2050-profit-band",
    "dsl:nasdaq-tdf2050-7030",
    "dsl:nasdaq-tdf2050-mix",
    "dsl:static-7030",
    "dsl:trend-band-defense",
)


def save_results(results, *, all_results=None):
    all_results = tuple(all_results if all_results is not None else results)
    histories = {
        strategy_display_name(result["strategy"]): result["history"]
        for result in all_results
    }
    benchmark = histories.get("STATIC_70_BND10_BIL10_GLD10")

    for result in results:
        name = strategy_display_name(result["strategy"])
        history_path = RESULT_DIR / f"{name}_history.csv"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        result["history"].to_csv(history_path)
        result["trades"].to_csv(
            RESULT_DIR / f"{name}_trades.csv", index=False
        )
        (RESULT_DIR / f"{name}_rebalances.json").write_text(
            json.dumps(result["rebalances"], ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        if strategy_identity(result["strategy"]) == "dsl:allocation":
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
        result for result in all_results
        if strategy_display_name(result["strategy"]).startswith("STATIC_")
    ]
    pd.DataFrame([result["summary"] for result in static_results]).to_csv(
        RESULT_DIR / "static_benchmark_comparison.csv", index=False
    )


def load_active_strategies():
    """Load and order every enabled declarative strategy."""
    declarative_strategies = list(
        load_strategy_directory(
            STRATEGY_DIRECTORY,
            strategy_resolver=DEFAULT_STRATEGY_CATALOG.create,
        )
    )
    preferred_order = {
        name: index for index, name in enumerate(PREFERRED_STRATEGY_ORDER)
    }
    strategies = tuple(sorted(
        declarative_strategies,
        key=lambda strategy: preferred_order.get(
            strategy_identity(strategy), len(preferred_order)
        ),
    ))
    if not strategies:
        raise ValueError(
            f"No enabled YAML strategies found in {STRATEGY_DIRECTORY}"
        )
    return strategies


def build_runner(strategies=None):
    """Configure the active strategies shown in the application."""
    strategies = tuple(strategies or load_active_strategies())
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


@lru_cache(maxsize=1)
def backtest_engine_fingerprint():
    """Fingerprint Python calculation code for persistent cache invalidation."""
    digest = sha256()
    for path in BACKTEST_ENGINE_FILES:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def strategy_result_cache_key(strategy, runner):
    """Return a key covering strategy logic, data files, and run options."""
    digest = sha256()
    digest.update(strategy_calculation_fingerprint(strategy).encode("ascii"))
    digest.update(backtest_engine_fingerprint().encode("ascii"))
    digest.update(json.dumps(
        runner.backtest_options,
        sort_keys=True,
        default=str,
    ).encode("utf-8"))
    for ticker in runner.tickers_for(strategy):
        path = runner.data_dir / f"{ticker}.csv"
        stat = path.stat()
        digest.update(str(ticker).encode("utf-8"))
        digest.update(str(stat.st_size).encode("ascii"))
        digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return digest.hexdigest()


def run_with_result_cache(runner):
    """Restore valid results and calculate only cache misses."""
    cached_by_id = {}
    keys_by_id = {}
    pending = []
    for strategy in runner.strategies:
        identity = strategy_identity(strategy)
        cache_key = strategy_result_cache_key(strategy, runner)
        keys_by_id[identity] = cache_key
        cached = RESULT_CACHE.load(strategy, cache_key)
        if cached is None:
            pending.append(strategy)
        else:
            cached_by_id[identity] = cached

    calculated = execute_strategies(pending, ensure_data=False)
    calculated_by_id = {
        strategy_identity(result["strategy"]): result for result in calculated
    }
    for identity, result in calculated_by_id.items():
        RESULT_CACHE.save(result, keys_by_id[identity])

    merged = tuple(
        calculated_by_id.get(strategy_identity(strategy))
        or cached_by_id[strategy_identity(strategy)]
        for strategy in runner.strategies
    )
    runner.results = list(merged)
    return merged, calculated


def execute_strategy_suite():
    """Load YAML strategies, prepare data, run backtests, and persist results."""
    runner = build_runner()
    ensure_runner_data(runner)
    results, calculated = run_with_result_cache(runner)
    if calculated:
        save_results(calculated, all_results=results)
    return runner, results


def execute_strategies(
    strategies, *, ensure_data=True, cache_results=False,
):
    """Backtest only the supplied strategies for an incremental web reload."""
    strategies = tuple(strategies)
    if not strategies:
        return ()
    runner = build_runner(strategies)
    if ensure_data:
        ensure_runner_data(runner)
    results = tuple(runner.run())
    if cache_results:
        for result in results:
            RESULT_CACHE.save(
                result,
                strategy_result_cache_key(result["strategy"], runner),
            )
    return results


def ensure_runner_data(runner):
    """Download missing strategy and exchange-rate CSV files."""
    required_tickers = tuple(dict.fromkeys(
        (*(runner.tickers or ()), *FX_RATE_TICKERS)
    ))
    required_market_fields = {}
    for strategy in runner.strategies:
        for ticker, fields in getattr(
            strategy, "required_market_fields", {}
        ).items():
            required_market_fields.setdefault(ticker, set()).update(fields)
    return ensure_data_files(
        required_tickers,
        data_dir=runner.data_dir,
        required_market_fields=required_market_fields,
    )


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
    runner, results = execute_strategy_suite()
    summary = runner.summary()
    displayed_period = runner.backtest_period()

    print("=" * 50)
    print(f"Backtest period: {displayed_period}")
    print("=" * 50)
    print("\n", summary, "\n")
    if args.chart_backend in {"matplotlib", "both"}:
        draw_chart(results)
    if args.chart_backend in {"dash", "both"}:
        from research_web import run_research_web
        incremental_results = IncrementalStrategyResults(
            runner.strategies,
            results,
            strategy_loader=load_active_strategies,
            executor=lambda strategies: execute_strategies(
                strategies, cache_results=True
            ),
            publisher=lambda changed, merged: save_results(
                changed, all_results=merged
            ),
        )

        result_store = ReloadableStrategyResults(
            results,
            strategy_directory=STRATEGY_DIRECTORY,
            loader=incremental_results.reload,
        )
        run_research_web(
            results,
            host=args.host,
            port=args.port,
            result_store=result_store,
        )


if __name__ == "__main__":
    main()
