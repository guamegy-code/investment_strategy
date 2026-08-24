"""Reduce Gate SPY safe-sleeve churn without changing its four market states."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "gate_spy_safe_sleeve_hysteresis.csv"


def definition(exit_score, exit_confirm):
    candidate = deepcopy(load_strategy_definition(SOURCE))
    exit_rule = candidate["state"]["safe_tdf_share"]["rules"][0]
    exit_rule["when"] = (
        "state.market_mode == 'CAUTION' and "
        "state.safe_tdf_share > 0 and "
        f"variables.risk_off_score >= {exit_score}"
    )
    exit_rule["confirm"] = exit_confirm
    return candidate


def run(name, candidate):
    strategy = DeclarativeStrategy(candidate)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    safe_events = sum(
        event.get("Reason") == "DECLARATIVE_RULE_4" for event in rebalances
    )
    return {
        "Candidate": name,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
        "SafeSleeveEvents": safe_events,
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1] - history["TransactionCosts"].iloc[0],
    }


def main():
    candidates = [("Current Gate SPY", load_strategy_definition(SOURCE))]
    candidates.extend(
        (f"Exit score {score}, confirm {confirm}", definition(score, confirm))
        for score, confirm in ((5, 2), (6, 1), (6, 2), (6, 3), (7, 1))
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
