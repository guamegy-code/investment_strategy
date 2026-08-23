"""Run long-history and crisis-window comparisons on proxy-extended data."""

import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from .extended_data import ASSETS, build_extended_data
from performance import Performance
from runner import Runner
from strategy import (
    RetirementAllocationStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


WINDOWS = {
    # QQQ history from March 1999 is retained as indicator warm-up.  Portfolio
    # measurement begins when the gold-futures proxy becomes available.
    "FULL_EXTENDED": ("2000-08-30", None),
    "DOTCOM_UNWIND": ("2000-08-30", "2002-10-09"),
    "GLOBAL_FINANCIAL_CRISIS": ("2007-10-09", "2009-03-09"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
    "POST_2010": ("2010-01-01", None),
}


def _window_report(results):
    rows = []
    for result in results:
        name = result["strategy"].__class__.__name__
        history = result["history"]
        for window, (start, end) in WINDOWS.items():
            sample = history.loc[start:end]
            if len(sample) < 2:
                continue
            row = Performance(sample).summary()
            costs = sample.get("TransactionCosts")
            window_costs = (
                costs.iloc[-1] - costs.iloc[0]
                if costs is not None else 0.0
            )
            row.update({
                "Window": window,
                "Strategy": name,
                "StartDate": sample.index.min(),
                "EndDate": sample.index.max(),
                "Observations": len(sample),
                "TotalReturn": sample["Portfolio"].iloc[-1]
                / sample["Portfolio"].iloc[0] - 1,
                "TransactionCosts": window_costs,
            })
            rows.append(row)
    columns = [
        "Window", "Strategy", "StartDate", "EndDate", "Observations",
        "TotalReturn", "CAGR", "MDD", "Volatility", "Sharpe", "Sortino",
        "Calmar", "TransactionCosts", "Start", "End",
    ]
    return pd.DataFrame(rows)[columns]


def _relative_report(windows):
    dynamic_name = "RetirementAllocationStrategy"
    benchmark_name = "STATIC_70_BND10_BIL10_GLD10"
    dynamic = windows.loc[windows["Strategy"] == dynamic_name].set_index("Window")
    benchmark = windows.loc[
        windows["Strategy"] == benchmark_name
    ].set_index("Window")
    common = dynamic.index.intersection(benchmark.index)
    return pd.DataFrame({
        "Window": common,
        "TotalReturnGap": (
            dynamic.loc[common, "TotalReturn"]
            - benchmark.loc[common, "TotalReturn"]
        ).values,
        "CAGRGap": (
            dynamic.loc[common, "CAGR"] - benchmark.loc[common, "CAGR"]
        ).values,
        # Positive means the dynamic strategy had a shallower drawdown.
        "MDDImprovement": (
            dynamic.loc[common, "MDD"] - benchmark.loc[common, "MDD"]
        ).values,
        "SharpeGap": (
            dynamic.loc[common, "Sharpe"] - benchmark.loc[common, "Sharpe"]
        ).values,
    })


def run_extended_stress(download=True):
    if download:
        build_extended_data()
    runner = Runner(data_dir=EXTENDED_DATA_DIR, tickers=ASSETS)
    for strategy in (
        RetirementAllocationStrategy(),
        STATIC_70_BND10_BIL10_GLD10(),
    ):
        runner.add_strategy(strategy)
    results = runner.run()
    windows = _window_report(results)
    relative = _relative_report(windows)
    full = windows.loc[windows["Window"] == "FULL_EXTENDED"].drop(
        columns=["Window"]
    ).reset_index(drop=True)
    full.to_csv(RESULT_DIR / "extended_stress_full_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "extended_stress_windows.csv", index=False)
    relative.to_csv(RESULT_DIR / "extended_stress_relative.csv", index=False)
    for result in results:
        name = result["strategy"].__class__.__name__
        result["history"].to_csv(
            RESULT_DIR / f"extended_{name}_history.csv"
        )
        pd.DataFrame(result["rebalances"]).to_csv(
            RESULT_DIR / f"extended_{name}_rebalances.csv", index=False
        )
    return full, windows


if __name__ == "__main__":
    full_summary, window_summary = run_extended_stress()
    print(full_summary.to_string(index=False))
    print("\nCrisis windows")
    print(window_summary.to_string(index=False))
