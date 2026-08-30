"""Strict promotion gate for strategy 16's BIL-sleeve rotation.

The baseline and candidate share the exact same strategy-16 state machine.
The baseline keeps BIL by suspending rotation on every evaluation; the
candidate uses the production rotation configuration unchanged.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from backtest import Backtest
from config import COMMISSION, DATA_DIR, GENERAL_COMPARISON_START_DATE, PROJECT_ROOT, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


STRATEGY_PATH = PROJECT_ROOT / "strategies" / "16_state_conditioned_cross_asset_rotation.yaml"
START_DATE = GENERAL_COMPARISON_START_DATE


@dataclass(frozen=True)
class Scenario:
    name: str
    cost_multiple: float = 1.0
    signal_delay_days: int = 0


SCENARIOS = (
    Scenario("BASE"),
    Scenario("COST_5X", cost_multiple=5.0),
    Scenario("DELAY_1D", signal_delay_days=1),
    Scenario("COST_5X_DELAY_1D", cost_multiple=5.0, signal_delay_days=1),
)


def _baseline_definition(candidate: dict) -> dict:
    """Keep all candidate data, valuation and state mechanics identical."""
    baseline = deepcopy(candidate)
    baseline["strategy"] = {
        **baseline["strategy"],
        "id": "state-conditioned-cross-asset-bil-baseline",
        "name": "State-conditioned BIL baseline",
    }
    baseline["rotation"]["suspend_when"] = "True"
    return baseline


def _run(definition: dict, scenario: Scenario) -> dict:
    strategy = DeclarativeStrategy(definition)
    backtest = Backtest(
        strategy,
        data_dir=DATA_DIR,
        tickers=strategy.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.signal_delay_days,
        start_date=START_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    metrics = Performance(history).summary()
    return {
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "TransactionCosts": metrics["TransactionCosts"],
    }


def _window_cagr(history: pd.DataFrame, years: int) -> pd.Series:
    values = history["Portfolio"]
    rows = {}
    for end in values.index:
        start = end - pd.DateOffset(years=years)
        prior = values.loc[:start]
        if prior.empty:
            continue
        start_date = prior.index[-1]
        elapsed = (end - start_date).days / 365.25
        if elapsed < years * 0.98:
            continue
        rows[end] = (values.loc[end] / values.loc[start_date]) ** (1 / elapsed) - 1
    return pd.Series(rows, name=f"CAGR_{years}Y")


def _comparison(baseline: dict, candidate: dict, scenario: Scenario) -> tuple[dict, pd.DataFrame]:
    full_gap = candidate["CAGR"] - baseline["CAGR"]
    mdd_improvement = candidate["MDD"] - baseline["MDD"]
    row = {
        "Scenario": scenario.name,
        "FullCAGRGap": full_gap,
        "MDDImprovement": mdd_improvement,
        "TransactionCostGap": candidate["TransactionCosts"] - baseline["TransactionCosts"],
        "RebalanceGap": len(candidate["rebalances"]) - len(baseline["rebalances"]),
        "TradeGap": len(candidate["trades"]) - len(baseline["trades"]),
    }
    rolling_rows = []
    for years in (3, 5):
        base = _window_cagr(baseline["history"], years)
        rotated = _window_cagr(candidate["history"], years)
        gap = rotated.sub(base, fill_value=float("nan")).dropna()
        row.update({
            f"Latest{years}YCAGRGap": gap.iloc[-1],
            f"Min{years}YRollingCAGRGap": gap.min(),
            f"Positive{years}YRollingShare": (gap > 0.0).mean(),
        })
        rolling_rows.extend({
            "Scenario": scenario.name,
            "WindowYears": years,
            "EndDate": date,
            "CAGRGap": value,
        } for date, value in gap.items())
    return row, pd.DataFrame(rolling_rows)


def run_rotation_promotion_gate() -> dict[str, pd.DataFrame]:
    candidate_definition = load_strategy_definition(STRATEGY_PATH)
    baseline_definition = _baseline_definition(candidate_definition)
    summary_rows, rolling_reports = [], []
    for scenario in SCENARIOS:
        baseline = _run(baseline_definition, scenario)
        candidate = _run(candidate_definition, scenario)
        summary, rolling = _comparison(baseline, candidate, scenario)
        summary_rows.append(summary)
        rolling_reports.append(rolling)

    summary = pd.DataFrame(summary_rows).set_index("Scenario")
    base = summary.loc["BASE"]
    strict = bool(
        base["FullCAGRGap"] > 0.0
        and base["Latest3YCAGRGap"] > 0.0
        and base["Latest5YCAGRGap"] > 0.0
        and base["Min3YRollingCAGRGap"] > 0.0
        and base["Min5YRollingCAGRGap"] > 0.0
        and base["MDDImprovement"] >= 0.0
        and (summary.loc[["COST_5X", "DELAY_1D", "COST_5X_DELAY_1D"], "FullCAGRGap"] > 0.0).all()
        and (summary.loc[["COST_5X", "DELAY_1D", "COST_5X_DELAY_1D"], "MDDImprovement"] >= 0.0).all()
    )
    decision = pd.DataFrame([{
        "FullCAGRHigher": base["FullCAGRGap"] > 0.0,
        "Latest3YCAGRHigher": base["Latest3YCAGRGap"] > 0.0,
        "Latest5YCAGRHigher": base["Latest5YCAGRGap"] > 0.0,
        "Every3YRollingCAGRHigher": base["Min3YRollingCAGRGap"] > 0.0,
        "Every5YRollingCAGRHigher": base["Min5YRollingCAGRGap"] > 0.0,
        "MDDNotWorse": base["MDDImprovement"] >= 0.0,
        "Cost5xAndDelayKeepAdvantage": bool((summary.loc[["COST_5X", "DELAY_1D", "COST_5X_DELAY_1D"], "FullCAGRGap"] > 0.0).all()),
        "Cost5xAndDelayMDDNotWorse": bool((summary.loc[["COST_5X", "DELAY_1D", "COST_5X_DELAY_1D"], "MDDImprovement"] >= 0.0).all()),
        "PromotionPass": strict,
    }])
    reports = {
        "rotation_promotion_gate_summary": summary.reset_index(),
        "rotation_promotion_gate_rolling": pd.concat(rolling_reports, ignore_index=True),
        "rotation_promotion_gate_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    result = run_rotation_promotion_gate()
    print(result["rotation_promotion_gate_summary"].to_string(index=False))
    print("\nDecision")
    print(result["rotation_promotion_gate_decision"].to_string(index=False))
