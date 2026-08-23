"""Test QQQ/TDF2050_PROXY/BIL allocations with the existing four market states.

The market-mode rules, confirmations, execution days, data, and transaction
costs are inherited from RetirementProfitBandTDF2050Proxy.  Candidates only
change the target weights and limit profit-band preservation to BULL.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"

# Per state: (QQQ, TDF2050_PROXY, BIL).  Each row must total 100%.
CANDIDATES = {
    "TDF/BIL growth": {
        "BULL": (70, 25, 5), "CAUTION": (55, 30, 15),
        "BEAR": (15, 25, 60), "RECOVERY": (45, 35, 20),
    },
    "TDF/BIL balanced": {
        "BULL": (70, 20, 10), "CAUTION": (50, 25, 25),
        "BEAR": (10, 20, 70), "RECOVERY": (40, 30, 30),
    },
    "TDF/BIL defensive": {
        "BULL": (65, 20, 15), "CAUTION": (45, 20, 35),
        "BEAR": (5, 10, 85), "RECOVERY": (35, 25, 40),
    },
    "TDF/BIL return focus": {
        "BULL": (75, 20, 5), "CAUTION": (60, 25, 15),
        "BEAR": (20, 30, 50), "RECOVERY": (50, 35, 15),
    },
}


def _weights(values):
    qqq, tdf, bil = values
    if qqq + tdf + bil != 100:
        raise ValueError("Each state allocation must total 100%")
    return {"QQQ": f"{qqq}%", "TDF2050_PROXY": f"{tdf}%", "BIL": f"{bil}%"}


def definition_for(allocations):
    definition = deepcopy(load_strategy_definition(SOURCE))
    bull_qqq, bull_tdf, bull_bil = allocations["BULL"]
    definition["assets"]["required"] = ["QQQ", "TDF2050_PROXY", "BIL"]
    definition["parameters"]["canonical_risk_weight"] = bull_qqq / 100

    # Keep QQQ gains in BULL only; preserve the BULL safe-asset split.
    safe_total = bull_tdf + bull_bil
    definition["target"] = [
        {
            "when": "state.market_mode == 'BULL' and variables.qqq_inside_profit_band",
            "weights": {
                "QQQ": "portfolio.weight.QQQ",
                "TDF2050_PROXY": (
                    f"round((1 - portfolio.weight.QQQ) * {bull_tdf / safe_total}, 10)"
                ),
                "BIL": (
                    f"round((1 - portfolio.weight.QQQ) * {bull_bil / safe_total}, 10)"
                ),
            },
        },
        *[
            {"when": f"state.market_mode == '{state}'", "weights": _weights(weights)}
            for state, weights in allocations.items()
        ],
    ]
    definition["rebalance"] = [
        {
            "when": (
                "state.market_mode == 'BULL' and "
                "portfolio.weight.QQQ >= parameters.upper_risk_weight"
            ),
            "days": "state.execution_days",
        },
        {
            "when": (
                "changed(state.market_mode) and not (state.market_mode == 'BULL' "
                "and variables.qqq_inside_profit_band)"
            ),
            "days": "state.execution_days",
        },
        {
            "check": "monthly",
            "when": (
                "not (state.market_mode == 'BULL' and variables.qqq_inside_profit_band) "
                "and target_deviation() >= 5%"
            ),
            "days": "state.execution_days",
        },
    ]
    return definition


def run(name, allocations):
    strategy = DeclarativeStrategy(definition_for(allocations))
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return name, history, len(rebalances), len(trades)


def main():
    runs = [run(name, allocations) for name, allocations in CANDIDATES.items()]
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
            "TransactionCosts": aligned["TransactionCosts"].iloc[-1],
            "Rebalances": rebalances,
            "Trades": trades,
        })
    results = pd.DataFrame(rows).sort_values(["Calmar", "CAGR"], ascending=False)
    results.to_csv(ROOT / "results" / "tdf_bil_state_allocation_comparison.csv", index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Volatility", "TransactionCosts"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
