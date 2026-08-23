"""Compare the TDF2050 candidates and VXUS strategy over one common period."""

from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.tdf2050_state_allocation import CANDIDATES, definition_for


ROOT = Path(__file__).resolve().parents[2]
VXUS_SOURCE = ROOT / "strategies" / "05_profit_band_vxus_v2.yaml"


def run_tdf(name, weights):
    strategy = DeclarativeStrategy(definition_for(weights, weights[4]))
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return name, history, len(rebalances), len(trades)


def run_vxus():
    strategy = DeclarativeStrategy(load_strategy_definition(VXUS_SOURCE))
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return "VXUS profit band", history, len(rebalances), len(trades)


def main():
    runs = [run_vxus()]
    runs.extend(run_tdf(name, weights) for name, weights in CANDIDATES.items())
    common_start = max(history.index.min() for _, history, _, _ in runs)
    common_end = min(history.index.max() for _, history, _, _ in runs)

    rows = []
    for name, history, rebalances, trades in runs:
        aligned = history.loc[common_start:common_end]
        performance = Performance(aligned)
        rows.append({
            "Candidate": name,
            "Start": common_start.strftime("%Y-%m-%d"),
            "End": common_end.strftime("%Y-%m-%d"),
            "CAGR": performance.cagr(),
            "MDD": performance.mdd(),
            "Calmar": performance.calmar_ratio(),
            "Volatility": performance.volatility(),
            "Rebalances": rebalances,
            "Trades": trades,
        })

    results = pd.DataFrame(rows).sort_values(["Calmar", "CAGR"], ascending=False)
    results.to_csv(ROOT / "results" / "tdf2050_vs_vxus_aligned.csv", index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Volatility"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
