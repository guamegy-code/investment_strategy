"""Test TDF-specific filters on Gate SPY TDF 100 safe-sleeve exits."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "13_profit_band_tdf2050_gate_spy_tdf100.yaml"
RESULT = ROOT / "results" / "gate_spy_tdf_exit_filter.csv"
BASE_EXIT = (
    "state.market_mode == 'CAUTION' and state.safe_tdf_share > 0 "
    "and variables.risk_off_score >= 5"
)


def candidate(source, name, exit_filter, restore_filter=None):
    definition = deepcopy(source)
    rules = definition["state"]["safe_tdf_share"]["rules"]
    rules[0]["when"] = f"{BASE_EXIT} and ({exit_filter})"
    if restore_filter:
        rules[1]["when"] = (
            "state.safe_tdf_share == 0 "
            "and variables.recovery_score >= 3 "
            f"and ({restore_filter})"
        )
    return name, definition


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
    safe_events = sum(
        "DECLARATIVE_RULE_4" == (event.get("Reason") or "")
        for event in rebalances
    )
    return {
        "Candidate": name,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Sharpe": performance.sharpe_ratio(),
        "Rebalances": len(rebalances),
        "SafeSleeveEvents": safe_events,
        "Trades": len(trades),
        "Costs": history["TransactionCosts"].iloc[-1],
        "2011-2020 CAGR": early_cagr,
        "2011-2020 MDD": early_mdd,
        "2021+ CAGR": recent_cagr,
        "2021+ MDD": recent_mdd,
    }


def main():
    source = load_strategy_definition(SOURCE)
    below_ema20 = "TDF2050_PROXY.close < TDF2050_PROXY.ema20"
    weaker_than_bil = "TDF2050_PROXY.roc20 < BIL.roc20"
    short_weakness = "TDF2050_PROXY.roc5 < 0"
    candidates = [("Current", source)]
    candidates.extend([
        candidate(source, "TDF below EMA20", below_ema20),
        candidate(source, "TDF weaker than BIL 20D", weaker_than_bil),
        candidate(
            source,
            "TDF trend or relative weak",
            f"{below_ema20} or {weaker_than_bil}",
        ),
        candidate(
            source,
            "TDF trend and relative weak",
            f"{below_ema20} and {weaker_than_bil}",
        ),
        candidate(
            source,
            "TDF weakness score 2/3",
            (
                "count("
                f"{below_ema20}, {weaker_than_bil}, {short_weakness}"
                ") >= 2"
            ),
        ),
        candidate(
            source,
            "TDF below EMA20; restore above",
            below_ema20,
            "TDF2050_PROXY.close > TDF2050_PROXY.ema20",
        ),
    ])

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
