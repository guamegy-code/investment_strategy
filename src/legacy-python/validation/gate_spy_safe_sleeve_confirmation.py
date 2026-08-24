"""Confirm full TDF exits with broad-market or credit weakness."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "gate_spy_safe_sleeve_confirmation.csv"
SPY_WEAK = "SPY.close < SPY.ema20 and SPY.roc5 <= -1"
SPY_SHARP = "SPY.close < SPY.ema20 and SPY.roc5 <= -2"
CREDIT_WEAK = "HYG.close < HYG.ema20 and HYG.roc20 < LQD.roc20 - 1"


def definition(name, confirmation):
    candidate = deepcopy(load_strategy_definition(SOURCE))
    if "HYG" in confirmation:
        candidate["assets"]["observations"] = ["SPY", "HYG", "LQD"]
    exit_rule = candidate["state"]["safe_tdf_share"]["rules"][0]
    exit_rule["when"] = (
        "state.market_mode == 'CAUTION' and state.safe_tdf_share > 0 and "
        f"variables.risk_off_score >= 5 and ({confirmation})"
    )
    return name, candidate


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
    candidates.extend([
        definition("TDF exit + SPY weak", SPY_WEAK),
        definition("TDF exit + SPY sharp", SPY_SHARP),
        definition("TDF exit + credit weak", CREDIT_WEAK),
        definition("TDF exit + SPY or credit", f"{SPY_WEAK} or {CREDIT_WEAK}"),
    ])
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
