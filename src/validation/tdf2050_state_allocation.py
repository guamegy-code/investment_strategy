"""Compare QQQ/TDF2050 allocations while retaining the existing four states.

This experiment deliberately keeps market-state signals, confirmation periods,
fees, and execution rules unchanged.  Only the state-specific QQQ targets and
the scope of the profit band differ between candidates.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"

# QQQ weights.  TDF2050_PROXY receives the remaining weight.
CANDIDATES = {
    "Current (band in BULL/CAUTION)": (70, 70, 20, 40, True),
    "State only (70/70/20/40)": (70, 70, 20, 40, False),
    "Defensive (70/50/10/40)": (70, 50, 10, 40, False),
    "Balanced (70/55/15/45)": (70, 55, 15, 45, False),
    "Moderate growth (75/60/20/45)": (75, 60, 20, 45, False),
    "Growth (80/65/20/50)": (80, 65, 20, 50, False),
}


def definition_for(weights, keep_caution_band):
    """Return an in-memory definition with only allocation logic changed."""
    bull, caution, bear, recovery, _ = weights
    definition = deepcopy(load_strategy_definition(SOURCE))
    definition["parameters"]["canonical_risk_weight"] = bull / 100

    definition["state"]["risk_weight"]["rules"] = [
        {"when": "state.market_mode == 'BEAR'", "set": f"{bear}%"},
        {"when": "state.market_mode == 'RECOVERY'", "set": f"{recovery}%"},
        {"when": "state.market_mode == 'CAUTION'", "set": f"{caution}%"},
        {"when": "state.market_mode == 'BULL'", "set": f"{bull}%"},
    ]

    if not keep_caution_band:
        definition["target"][0]["when"] = (
            "state.market_mode == 'BULL' and variables.qqq_inside_profit_band"
        )
        definition["rebalance"][0]["when"] = (
            "state.market_mode == 'BULL' and "
            "portfolio.weight.QQQ >= parameters.upper_risk_weight"
        )
        for rule in definition["rebalance"][1:]:
            rule["when"] = rule["when"].replace(
                "state.market_mode in ['BULL', 'CAUTION']",
                "state.market_mode == 'BULL'",
            )
    return definition


def summarize(name, weights):
    definition = definition_for(weights, weights[4])
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    peak = history["Portfolio"].cummax()
    drawdown = history["Portfolio"] / peak - 1
    trough = drawdown.idxmin()
    recent = history.loc["2022-01-01":]
    early = history.loc[:"2021-12-31"]
    return {
        "Candidate": name,
        "QQQ B/C/BE/R": "/".join(f"{value}%" for value in weights[:4]),
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Volatility": performance.volatility(),
        "End value": history["Portfolio"].iloc[-1],
        "MDD trough": trough.strftime("%Y-%m-%d"),
        "Rebalances": len(rebalances),
        "Trades": len(trades),
        "2012-21 CAGR": Performance(early).cagr(),
        "2012-21 MDD": Performance(early).mdd(),
        "2022+ CAGR": Performance(recent).cagr(),
        "2022+ MDD": Performance(recent).mdd(),
    }


def main():
    results = pd.DataFrame(
        summarize(name, weights) for name, weights in CANDIDATES.items()
    )
    results = results.sort_values(["Calmar", "CAGR"], ascending=False)
    results.to_csv(ROOT / "results" / "tdf2050_state_allocation_comparison.csv", index=False)
    display = results.copy()
    for column in (
        "CAGR", "MDD", "Calmar", "Volatility",
        "2012-21 CAGR", "2012-21 MDD", "2022+ CAGR", "2022+ MDD",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}" if column != "Calmar" else f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
