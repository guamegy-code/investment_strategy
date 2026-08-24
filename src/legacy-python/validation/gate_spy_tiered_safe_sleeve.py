"""Test tiered TDF-to-BIL defense for Gate SPY TDF 100."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "13_profit_band_tdf2050_gate_spy_tdf100.yaml"
RESULT = ROOT / "results" / "gate_spy_tiered_safe_sleeve.csv"


def tiered_definition(source, mild_tdf_share):
    candidate = deepcopy(source)
    candidate["state"]["safe_tdf_share"]["rules"] = [
        {
            "when": (
                "state.market_mode == 'CAUTION' "
                "and state.safe_tdf_share > 0 "
                "and variables.risk_off_score >= 6"
            ),
            "set": "0%",
        },
        {
            "when": (
                "state.market_mode == 'CAUTION' "
                "and state.safe_tdf_share == 100% "
                "and variables.risk_off_score >= 5"
            ),
            "set": f"{mild_tdf_share}%",
        },
        {
            "when": (
                "state.safe_tdf_share < 100% "
                "and variables.recovery_score >= 3"
            ),
            "set": "100%",
            "confirm": 2,
        },
        {"otherwise": True, "set": "= state.safe_tdf_share"},
    ]
    return candidate


def period_metrics(history, start=None, end=None):
    subset = history
    if start is not None:
        subset = subset.loc[subset.index >= pd.Timestamp(start)]
    if end is not None:
        subset = subset.loc[subset.index <= pd.Timestamp(end)]
    performance = Performance(subset)
    return performance.cagr(), performance.mdd()


def run(name, definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    performance = Performance(history)
    early_cagr, early_mdd = period_metrics(history, end="2020-12-31")
    recent_cagr, recent_mdd = period_metrics(history, start="2021-01-01")
    return {
        "Candidate": name,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Sharpe": performance.sharpe_ratio(),
        "Rebalances": len(rebalances),
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1],
        "2011-2020 CAGR": early_cagr,
        "2011-2020 MDD": early_mdd,
        "2021+ CAGR": recent_cagr,
        "2021+ MDD": recent_mdd,
    }


def main():
    source = load_strategy_definition(SOURCE)
    candidates = [("Current binary 100/0", source)]
    candidates.extend(
        (
            f"Score 5 TDF {share}% / score 6 TDF 0%",
            tiered_definition(source, share),
        )
        for share in (20, 40, 50, 60, 75, 80)
    )
    results = pd.DataFrame([run(name, definition) for name, definition in candidates])
    results = results.sort_values(["Calmar", "CAGR"], ascending=False)
    RESULT.parent.mkdir(exist_ok=True)
    results.to_csv(RESULT, index=False)
    display = results.copy()
    for column in (
        "CAGR",
        "MDD",
        "Costs",
        "2011-2020 CAGR",
        "2011-2020 MDD",
        "2021+ CAGR",
        "2021+ MDD",
    ):
        display[column] = display[column].map(lambda value: f"{value:.3%}")
    for column in ("Calmar", "Sharpe"):
        display[column] = display[column].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
