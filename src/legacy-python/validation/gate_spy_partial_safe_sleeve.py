"""Test partial TDF reductions during strong CAUTION in Gate SPY."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "gate_spy_partial_safe_sleeve.csv"


def definition(safe_share):
    candidate = deepcopy(load_strategy_definition(SOURCE))
    rules = candidate["state"]["safe_tdf_share"]["rules"]
    rules[0]["set"] = f"{safe_share}%"
    rules[1]["when"] = (
        f"state.safe_tdf_share == {safe_share}% and "
        "variables.recovery_score >= 3"
    )
    rules[1]["set"] = "82%"
    return candidate


def run(name, candidate):
    strategy = DeclarativeStrategy(candidate)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    return {
        "Candidate": name,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
        "SafeSleeveEvents": sum(event.get("Reason") == "DECLARATIVE_RULE_4" for event in rebalances),
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1] - history["TransactionCosts"].iloc[0],
    }


def main():
    candidates = [("Current Gate SPY", load_strategy_definition(SOURCE))]
    candidates.extend(
        (f"Strong CAUTION TDF {share}%", definition(share))
        for share in (20, 40, 50, 60)
    )
    results = pd.DataFrame([run(name, candidate) for name, candidate in candidates])
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Costs"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
