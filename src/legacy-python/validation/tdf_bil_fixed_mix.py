"""Robustness test for fixed TDF/BIL safe-sleeve mixes.

The four market states, QQQ targets (70/70/0/50), selective rebalancing, and
77.5% profit-band cap remain unchanged.  Only the fixed TDF share within the
non-QQQ sleeve varies from 0% to 100%.
"""

from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.tdf_bil_retirement_insight import definition_for


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SOURCE = ROOT / "strategies" / "07_band_7030_bnd.yaml"
VXUS_SOURCE = ROOT / "strategies" / "05_profit_band_vxus_v2.yaml"
BASE_CONFIG = {
    "canonical_qqq": 0.70,
    "recovery_qqq": 0.50,
    "upper_qqq": 0.775,
    "effective_cap": False,
}


def fixed_definition(tdf_share):
    definition = definition_for(BASE_CONFIG)
    definition["state"]["safe_tdf_share"] = {
        "initial": f"{tdf_share * 100}%",
        "rules": [],
    }
    definition["rebalance"] = [
        rule for rule in definition["rebalance"]
        if "changed(state.safe_tdf_share)" not in rule["when"]
    ]
    return definition


def run_definition(definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return history, len(trades), len(rebalances)


def period_metrics(history, start, end):
    period = history.loc[start:end]
    performance = Performance(period)
    return {
        "Start": period.index.min(),
        "End": period.index.max(),
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Volatility": performance.volatility(),
    }


def main():
    benchmark, benchmark_trades, benchmark_rebalances = run_definition(
        load_strategy_definition(BENCHMARK_SOURCE)
    )
    runs = {"Retirement 70/30 Band": benchmark}
    counts = {
        "Retirement 70/30 Band": (benchmark_trades, benchmark_rebalances)
    }
    vxus, vxus_trades, vxus_rebalances = run_definition(
        load_strategy_definition(VXUS_SOURCE)
    )
    runs["VXUS2 reference"] = vxus
    counts["VXUS2 reference"] = (vxus_trades, vxus_rebalances)
    for percentage in range(0, 101, 10):
        name = f"Fixed TDF {percentage}% / BIL {100 - percentage}%"
        history, trades, rebalances = run_definition(
            fixed_definition(percentage / 100)
        )
        runs[name] = history
        counts[name] = (trades, rebalances)

    common_start = max(
        pd.Timestamp("2011-03-29"),
        *(history.index.min() for history in runs.values()),
    )
    common_end = min(history.index.max() for history in runs.values())
    summary_rows = []
    for name, history in runs.items():
        full = period_metrics(history, common_start, common_end)
        later = period_metrics(history, "2021-01-01", common_end)
        trades, rebalances = counts[name]
        summary_rows.append({
            "Candidate": name,
            **full,
            "LaterCAGR": later["CAGR"],
            "LaterMDD": later["MDD"],
            "TransactionCosts": (
                history.loc[common_start:common_end, "TransactionCosts"].iloc[-1]
                - history.loc[common_start:common_end, "TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })

    rolling_rows = []
    for year in range(2012, 2024):
        start = pd.Timestamp(year=year, month=1, day=1)
        end = pd.Timestamp(year=year + 2, month=12, day=31)
        benchmark_metrics = period_metrics(benchmark, start, end)
        for name, history in runs.items():
            if name == "Retirement 70/30 Band":
                continue
            candidate = period_metrics(history, start, end)
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                **candidate,
                "CAGRGap": candidate["CAGR"] - benchmark_metrics["CAGR"],
                "MDDImprovement": candidate["MDD"] - benchmark_metrics["MDD"],
            })

    summary = pd.DataFrame(summary_rows)
    rolling = pd.DataFrame(rolling_rows)
    robustness = []
    for name, group in rolling.groupby("Candidate"):
        robustness.append({
            "Candidate": name,
            "Windows": len(group),
            "CAGRWins": int((group["CAGRGap"] > 0).sum()),
            "MDDWins": int((group["MDDImprovement"] > 0).sum()),
            "BothWins": int(((group["CAGRGap"] > 0) & (group["MDDImprovement"] > 0)).sum()),
            "AverageCAGRGap": group["CAGRGap"].mean(),
            "WorstCAGRGap": group["CAGRGap"].min(),
            "AverageMDDImprovement": group["MDDImprovement"].mean(),
            "WorstMDDImprovement": group["MDDImprovement"].min(),
        })
    robustness = pd.DataFrame(robustness)
    summary = summary.merge(robustness, on="Candidate", how="left")
    summary = summary.sort_values(["BothWins", "Calmar", "CAGR"], ascending=False)

    summary.to_csv(ROOT / "results" / "tdf_bil_fixed_mix_summary.csv", index=False)
    rolling.to_csv(ROOT / "results" / "tdf_bil_fixed_mix_rolling.csv", index=False)
    columns = [
        "Candidate", "CAGR", "MDD", "Calmar", "LaterCAGR", "LaterMDD",
        "BothWins", "AverageCAGRGap", "AverageMDDImprovement",
        "TransactionCosts",
    ]
    display = summary.loc[summary["Candidate"] != "Retirement 70/30 Band", columns].copy()
    for column in (
        "CAGR", "MDD", "LaterCAGR", "LaterMDD", "AverageCAGRGap",
        "AverageMDDImprovement", "TransactionCosts",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
