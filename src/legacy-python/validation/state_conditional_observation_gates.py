"""Test conditional SPY and credit observations across all four states."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "state_conditional_observation_gates.csv"
CREDIT_STRESS = (
    "HYG.close < HYG.ema20 and HYG.roc20 < LQD.roc20 - 1"
)
CREDIT_RECOVERY = (
    "HYG.close > HYG.ema20 and HYG.roc20 >= LQD.roc20 - 1"
)
SPY_RECOVERY = "SPY.close > SPY.ema20 and SPY.roc5 > 0"


def rules(definition):
    return definition["state"]["market_mode"]["rules"]


def find_rule(definition, state, target):
    for rule in rules(definition):
        when = str(rule.get("when", ""))
        if rule.get("set") == target and f"state.market_mode == '{state}'" in when:
            return rule
    raise ValueError(f"{state} to {target} rule was not found")


def candidate(name, *, credit_entry=False, fast_bear=False, guarded_recovery=False):
    definition = deepcopy(load_strategy_definition(SOURCE))
    definition["assets"]["observations"] = ["SPY", "HYG", "LQD"]
    entry = find_rule(definition, "BULL", "CAUTION")
    if credit_entry:
        entry["when"] = (
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 3 and ("
            "(SPY.close < SPY.ema20 and SPY.roc5 <= -1) or "
            f"({CREDIT_STRESS}))"
        )
    if fast_bear:
        structural = find_rule(definition, "CAUTION", "BEAR")
        index = rules(definition).index(structural)
        rules(definition).insert(index, {
            "when": (
                "state.market_mode == 'CAUTION' and "
                f"variables.structural_bear and {CREDIT_STRESS}"
            ),
            "set": "BEAR",
            "confirm": 3,
        })
    if guarded_recovery:
        recovery = find_rule(definition, "BEAR", "RECOVERY")
        recovery["when"] = (
            f"({' '.join(str(recovery['when']).split())}) and "
            f"{SPY_RECOVERY} and {CREDIT_RECOVERY}"
        )
        bull = find_rule(definition, "RECOVERY", "BULL")
        bull["when"] = (
            f"({' '.join(str(bull['when']).split())}) and "
            f"{SPY_RECOVERY} and {CREDIT_RECOVERY}"
        )
    return name, definition


def run(name, definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    early = history.loc[:"2020-12-31"]
    recent = history.loc["2021-01-01":]
    return {
        "Candidate": name,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1] - history["TransactionCosts"].iloc[0],
        "2011-2020 CAGR": Performance(early).cagr(),
        "2011-2020 MDD": Performance(early).mdd(),
        "2021+ CAGR": Performance(recent).cagr(),
        "2021+ MDD": Performance(recent).mdd(),
    }


def main():
    candidates = [
        ("Gate SPY", load_strategy_definition(SOURCE)),
        candidate("SPY or credit entry", credit_entry=True),
        candidate("SPY gate + fast credit bear", fast_bear=True),
        candidate("SPY gate + guarded recovery", guarded_recovery=True),
        candidate(
            "SPY or credit entry + fast bear",
            credit_entry=True,
            fast_bear=True,
        ),
        candidate(
            "All state gates",
            credit_entry=True,
            fast_bear=True,
            guarded_recovery=True,
        ),
    ]
    results = pd.DataFrame([run(name, definition) for name, definition in candidates])
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in (
        "CAGR", "MDD", "Costs", "2011-2020 CAGR", "2011-2020 MDD",
        "2021+ CAGR", "2021+ MDD",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
