"""Test a QQQ weight cap before a full BULL to CAUTION state change."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "12_profit_band_tdf2050_gate_spy.yaml"
RESULT = ROOT / "results" / "pre_caution_buffer.csv"


def replace_risk_weight(value, parameter):
    if isinstance(value, str):
        return value.replace("state.risk_weight", f"parameters.{parameter}")
    if isinstance(value, dict):
        return {key: replace_risk_weight(item, parameter) for key, item in value.items()}
    return value


def buffered_definition(cap, score):
    definition = deepcopy(load_strategy_definition(SOURCE))
    parameter = f"prebuffer_cap_{round(cap * 10):d}"
    definition["parameters"][parameter] = cap / 100
    definition["variables"]["pre_caution_warning"] = (
        "QQQ.close < QQQ.ema20 and "
        f"variables.risk_off_score >= {score} and "
        "SPY.close < SPY.ema20 and SPY.roc5 <= 0"
    )
    weights = replace_risk_weight(deepcopy(definition["target"][-1]["weights"]), parameter)
    definition["target"].insert(0, {
        "when": (
            "state.market_mode == 'BULL' and variables.pre_caution_warning and "
            f"portfolio.weight.QQQ > parameters.{parameter}"
        ),
        "weights": weights,
    })
    definition["rebalance"].insert(0, {
        "when": (
            "state.market_mode == 'BULL' and variables.pre_caution_warning and "
            f"portfolio.weight.QQQ > parameters.{parameter}"
        ),
        "days": 1,
    })
    return definition


def run(name, definition):
    strategy = DeclarativeStrategy(definition)
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
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1] - history["TransactionCosts"].iloc[0],
    }


def main():
    candidates = [("Gate SPY", load_strategy_definition(SOURCE))]
    candidates.extend(
        (f"Buffer {cap}% / score {score}", buffered_definition(cap, score))
        for cap in (72.5, 75.0)
        for score in (2, 3)
    )
    results = pd.DataFrame([run(name, definition) for name, definition in candidates])
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "Costs"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
