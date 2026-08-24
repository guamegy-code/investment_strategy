"""Test two-stage observation gates for the TDF 2050 profit-band strategy.

QQQ is the primary signal.  SPY is examined only after QQQ has shown an
initial warning, so broad-market data confirms a developing drawdown rather
than continuously creating an independent trading signal.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "conditional_observation_gate.csv"


def caution_entry_rule(definition):
    for rule in definition["state"]["market_mode"]["rules"]:
        when = str(rule.get("when", ""))
        if rule.get("set") == "CAUTION" and "state.market_mode == 'BULL'" in when:
            return rule
    raise ValueError("BULL to CAUTION rule was not found")


def caution_exit_rule(definition):
    for rule in definition["state"]["market_mode"]["rules"]:
        when = str(rule.get("when", ""))
        if rule.get("set") == "BULL" and "state.market_mode == 'CAUTION'" in when:
            return rule
    raise ValueError("CAUTION to BULL rule was not found")


def candidate(name, entry, *, entry_confirm=1, guarded_exit=False):
    definition = deepcopy(load_strategy_definition(SOURCE))
    entry_rule = caution_entry_rule(definition)
    entry_rule["when"] = entry
    entry_rule["confirm"] = entry_confirm
    if guarded_exit:
        exit_rule = caution_exit_rule(definition)
        exit_rule["when"] = (
            "state.market_mode == 'CAUTION' and "
            "variables.recovery_score >= 4 and "
            "SPY.close > SPY.ema20 and SPY.roc5 > 0"
        )
        exit_rule["confirm"] = 2
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
        ("Current Stress SPY", load_strategy_definition(SOURCE)),
        candidate(
            "Gate 3 + SPY weak, confirm 1",
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 3 and SPY.close < SPY.ema20 and "
            "SPY.roc5 <= -1",
        ),
        candidate(
            "Gate 4 + SPY weak, confirm 1",
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 4 and SPY.close < SPY.ema20 and "
            "SPY.roc5 <= 0",
        ),
        candidate(
            "Gate 4 + SPY sharp, confirm 1",
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 4 and SPY.close < SPY.ema20 and "
            "SPY.roc5 <= -2",
        ),
        candidate(
            "Gate 4 + SPY weak, confirm 2",
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 4 and SPY.close < SPY.ema20 and "
            "SPY.roc5 <= 0",
            entry_confirm=2,
        ),
        candidate(
            "Gate 4 + SPY weak, guarded exit",
            "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
            "variables.risk_off_score >= 4 and SPY.close < SPY.ema20 and "
            "SPY.roc5 <= 0",
            guarded_exit=True,
        ),
    ]
    results = pd.DataFrame([run(name, definition) for name, definition in candidates])
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Costs", "2011-2020 CAGR", "2011-2020 MDD", "2021+ CAGR", "2021+ MDD"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
