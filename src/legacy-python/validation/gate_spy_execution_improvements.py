"""Evaluate execution-only improvements for Gate SPY TDF 100."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_domain import StrategyEvaluation
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "13_profit_band_tdf2050_gate_spy_tdf100.yaml"
RESULT = ROOT / "results" / "gate_spy_execution_improvements.csv"


def same_target(left, right, tolerance=1e-6):
    if left is None or right is None:
        return False
    tickers = set(left) | set(right)
    return all(
        abs(float(left.get(ticker, 0.0)) - float(right.get(ticker, 0.0)))
        <= tolerance
        for ticker in tickers
    )


class DeduplicatingBacktest(Backtest):
    """Keep an in-flight order when a new signal has the same target."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.suppressed_rebalances = 0
        self.suppressed_events = []

    def _queue_rebalance(self, signal, signal_date):
        if isinstance(signal, StrategyEvaluation):
            target = signal.target_weights
        else:
            target = signal["target"]
        delayed_target = (
            self.delayed_rebalance["target"]
            if self.delayed_rebalance is not None
            else None
        )
        if same_target(target, self.portfolio.pending_target) or same_target(
            target, delayed_target
        ):
            self.suppressed_rebalances += 1
            self.suppressed_events.append({
                "Date": signal_date,
                "Reason": getattr(signal, "reason", None)
                if isinstance(signal, StrategyEvaluation)
                else signal.get("reason"),
                "RemainingDays": self.portfolio.remaining_days,
                "Target": dict(target),
            })
            return
        super()._queue_rebalance(signal, signal_date)


def with_safe_execution_days(source, exit_days, restore_days):
    candidate = deepcopy(source)
    safe_rule_index = next(
        index
        for index, rule in enumerate(candidate["rebalance"])
        if "changed(state.safe_tdf_share)" in rule.get("when", "")
        and "changed(state.bnd_capacity)" in rule.get("when", "")
    )
    candidate["rebalance"][safe_rule_index : safe_rule_index + 1] = [
        {
            "when": (
                "changed(state.safe_tdf_share) "
                "and state.safe_tdf_share == 0"
            ),
            "days": exit_days,
        },
        {
            "when": (
                "changed(state.safe_tdf_share) "
                "and state.safe_tdf_share > 0"
            ),
            "days": restore_days,
        },
        {"when": "changed(state.bnd_capacity)", "days": 1},
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


def run_candidate(name, definition, deduplicate=False):
    strategy = DeclarativeStrategy(definition)
    backtest_class = DeduplicatingBacktest if deduplicate else Backtest
    backtest = backtest_class(strategy, tickers=strategy.required_tickers)
    history, trades, rebalances = backtest.run_all()
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
        "Suppressed": getattr(backtest, "suppressed_rebalances", 0),
        "2011-2020 CAGR": early_cagr,
        "2011-2020 MDD": early_mdd,
        "2021+ CAGR": recent_cagr,
        "2021+ MDD": recent_mdd,
    }


def main():
    source = load_strategy_definition(SOURCE)
    candidates = [
        ("Current 1/1", source, False),
        ("Current 1/1 + dedup", source, True),
    ]
    for exit_days, restore_days in (
        (1, 2),
        (1, 3),
        (1, 5),
        (2, 1),
        (2, 2),
        (3, 1),
        (3, 3),
    ):
        definition = with_safe_execution_days(source, exit_days, restore_days)
        label = f"Safe exit {exit_days} / restore {restore_days}"
        candidates.append((label, definition, False))
        candidates.append((f"{label} + dedup", definition, True))

    results = pd.DataFrame(
        [
            run_candidate(name, definition, deduplicate)
            for name, definition, deduplicate in candidates
        ]
    ).sort_values(["Calmar", "CAGR"], ascending=False)
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
