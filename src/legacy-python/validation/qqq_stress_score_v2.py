"""Test QQQ-only stress signals without changing the production strategy.

The existing risk-off score captures trend and momentum.  These candidates add
short drawdowns and volatility expansion to suppress weak CAUTION entries while
preserving rapid action during a genuine downside shock.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
RESULT = ROOT / "results" / "qqq_stress_score_v2.csv"


def stress_definition(mode: str):
    definition = deepcopy(load_strategy_definition(SOURCE))
    variables = definition["variables"]
    variables["stress_drawdown_score"] = (
        "count(QQQ.drawdown20 <= -0.04, QQQ.drawdown60 <= -0.08)"
    )
    variables["stress_volatility_score"] = (
        "count(QQQ.vol20 >= QQQ.vol60 * 1.25, QQQ.atr_pct >= 0.022)"
    )
    variables["stress_score_v2"] = (
        "variables.risk_off_score + variables.stress_drawdown_score + "
        "variables.stress_volatility_score"
    )

    for rule in definition["state"]["market_mode"]["rules"]:
        if rule.get("when") != (
            "state.market_mode == 'BULL' and variables.risk_off_score >= 5"
        ):
            continue
        if mode == "filtered":
            rule["when"] = (
                "state.market_mode == 'BULL' and "
                "variables.risk_off_score >= 5 and "
                "variables.stress_score_v2 >= 7"
            )
        elif mode == "fast":
            rule["when"] = (
                "state.market_mode == 'BULL' and "
                "QQQ.close < QQQ.ema20 and variables.stress_score_v2 >= 7"
            )
            rule["confirm"] = 1
        elif mode == "strict":
            rule["when"] = (
                "state.market_mode == 'BULL' and "
                "variables.risk_off_score >= 5 and "
                "variables.stress_score_v2 >= 8"
            )
        return definition
    raise ValueError("BULL to CAUTION rule was not found")


def run(name, definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    return {
        "Candidate": name,
        "Start": history.index.min().date().isoformat(),
        "End": history.index.max().date().isoformat(),
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
        "Trades": len(trades),
        "TransactionCosts": (
            history["TransactionCosts"].iloc[-1]
            - history["TransactionCosts"].iloc[0]
        ),
    }


def main():
    candidates = {"Current": load_strategy_definition(SOURCE)}
    candidates.update({
        "Stress V2 filtered": stress_definition("filtered"),
        "Stress V2 fast": stress_definition("fast"),
        "Stress V2 strict": stress_definition("strict"),
    })
    results = pd.DataFrame(
        [run(name, definition) for name, definition in candidates.items()]
    )
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in ("CAGR", "MDD", "TransactionCosts"):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
