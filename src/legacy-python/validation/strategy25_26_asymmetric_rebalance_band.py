"""Validate asymmetric QQQ rebalance bands for strategies 25 and 26.

The production DSL exposes ``target_deviation()`` as an unsigned maximum
absolute asset-weight difference.  This experiment gives the band a direction
by measuring ``current QQQ weight - target QQQ weight``.  QQQ overweight is
still sold at +7.5%p, while QQQ underweight is bought only after crossing a
configurable negative threshold.  Material target changes caused by strategy
state transitions retain the production 7.5%p absolute-deviation safeguard.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
START_DATE = "2012-01-03"
END_DATE = "2026-07-31"
UPPER_BAND = 0.075
# A smaller positive magnitude means a lower bound closer to zero: for
# example, 0.03 represents a signed lower trigger of -3%p.
LOWER_BANDS = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.075)
STRATEGIES = {
    25: ROOT / "strategies" / "25_qqq_valuation_breakdown_balanced.yaml",
    26: ROOT / "strategies" / "26_band_7030_tdf_valuation_defense.yaml",
}
PERIODS = {
    "FULL": (START_DATE, END_DATE),
    "DEVELOPMENT": (START_DATE, "2020-12-31"),
    "RECENT": ("2021-01-01", END_DATE),
}


class AsymmetricQQQBandStrategy(DeclarativeStrategy):
    """Use a signed QQQ deviation while preserving state-transition trades."""

    def __init__(
        self,
        definition: Mapping[str, Any],
        *,
        upper_band: float = UPPER_BAND,
        lower_band: float,
    ):
        super().__init__(definition)
        self.upper_band = float(upper_band)
        self.lower_band = float(lower_band)
        self.band_observations: list[dict[str, Any]] = []

    def _should_rebalance(
        self,
        date: Any,
        market: Mapping[str, Any],
        portfolio: Any,
        target: Mapping[str, float],
    ) -> tuple[bool, str | None, int | None]:
        # Both production strategies have one daily rebalance rule.  Retain the
        # first-evaluation behavior of DeclarativeStrategy/Backtest.
        period = self._period(date, "daily")
        previous_period = self._last_rebalance_periods.get(0)
        self._last_rebalance_periods[0] = period
        if not self._evaluated_once or period == previous_period:
            return False, None, None

        prices = {
            ticker: market[ticker].get("Close")
            for ticker in self.holding_tickers
            if ticker in market and market[ticker].get("Close") is not None
        }
        current = portfolio.weights(prices) if prices else {}
        qqq_deviation = (
            float(current.get("QQQ", 0.0)) - float(target.get("QQQ", 0.0))
        )
        maximum_absolute_deviation = max(
            (
                abs(float(current.get(ticker, 0.0)) - float(goal))
                for ticker, goal in target.items()
            ),
            default=0.0,
        )
        previous_target = self.target or {}
        target_changed = bool(previous_target) and any(
            abs(
                float(previous_target.get(ticker, 0.0))
                - float(target.get(ticker, 0.0))
            )
            > 1e-12
            for ticker in set(previous_target) | set(target)
        )

        # State changes can alter only the safe sleeves in strategy 26.  Keep
        # production's absolute 7.5%p trigger for those allocation decisions;
        # apply asymmetry only to passive drift around an unchanged target.
        transition_trigger = (
            target_changed and maximum_absolute_deviation >= UPPER_BAND
        )
        upper_trigger = qqq_deviation >= self.upper_band
        lower_trigger = qqq_deviation <= -self.lower_band
        rebalance = transition_trigger or upper_trigger or lower_trigger
        if rebalance:
            if transition_trigger:
                trigger = "TARGET_CHANGE"
            elif upper_trigger:
                trigger = "UPPER_QQQ_BAND"
            else:
                trigger = "LOWER_QQQ_BAND"
            self.band_observations.append({
                "Date": pd.Timestamp(date),
                "Trigger": trigger,
                "QQQDeviation": qqq_deviation,
                "MaximumAbsoluteDeviation": maximum_absolute_deviation,
                "TargetQQQWeight": float(target.get("QQQ", 0.0)),
                "CurrentQQQWeight": float(current.get("QQQ", 0.0)),
            })
            return True, trigger, None
        return False, None, None


def _run(
    strategy_number: int,
    lower_band: float | None,
    *,
    cost_multiple: float = 1.0,
):
    definition = load_strategy_definition(STRATEGIES[strategy_number])
    if lower_band is None:
        strategy = DeclarativeStrategy(definition)
        variant = "PRODUCTION_UNSIGNED_7.5"
    else:
        strategy = AsymmetricQQQBandStrategy(
            deepcopy(definition), lower_band=lower_band
        )
        variant = f"ASYMMETRIC_+7.5_-{lower_band * 100:g}"
    history, trades, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
        start_date=START_DATE,
        end_date=END_DATE,
    ).run_all()
    trigger_counts = pd.Series(
        [event["Trigger"] for event in getattr(strategy, "band_observations", [])],
        dtype="object",
    ).value_counts()
    return {
        "Strategy": strategy_number,
        "Variant": variant,
        "LowerBand": lower_band,
        "CostMultiple": cost_multiple,
        "history": history,
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "UpperBandEvents": int(trigger_counts.get("UPPER_QQQ_BAND", 0)),
        "LowerBandEvents": int(trigger_counts.get("LOWER_QQQ_BAND", 0)),
        "TargetChangeEvents": int(trigger_counts.get("TARGET_CHANGE", 0)),
        "TransactionCosts": float(history["TransactionCosts"].iloc[-1]),
    }


def _metrics(history: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    selected = history.loc[start:end]
    performance = Performance(selected)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Sharpe": performance.sharpe_ratio(),
        "Calmar": performance.calmar_ratio(),
    }


def _fixed_report(runs: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for result in runs:
        for period, (start, end) in PERIODS.items():
            rows.append({
                key: value
                for key, value in result.items()
                if key != "history"
            } | {
                "Period": period,
                **_metrics(result["history"], start, end),
            })
    report = pd.DataFrame(rows)
    baseline = report.loc[
        report["Variant"] == "PRODUCTION_UNSIGNED_7.5",
        ["Strategy", "Period", "CAGR", "MDD", "Sharpe"],
    ].rename(columns={
        "CAGR": "BaselineCAGR",
        "MDD": "BaselineMDD",
        "Sharpe": "BaselineSharpe",
    })
    report = report.merge(baseline, on=["Strategy", "Period"], how="left")
    report["CAGRGap"] = report["CAGR"] - report["BaselineCAGR"]
    report["MDDImprovement"] = report["MDD"] - report["BaselineMDD"]
    report["SharpeGap"] = report["Sharpe"] - report["BaselineSharpe"]
    return report


def _rolling_report(runs: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for result in runs:
        if result["LowerBand"] is None:
            continue
        baseline = next(
            item
            for item in runs
            if item["Strategy"] == result["Strategy"]
            and item["LowerBand"] is None
        )
        for years in (3, 5):
            for start_year in range(2012, 2026 - years + 1):
                start = f"{start_year}-01-01"
                end = f"{start_year + years - 1}-12-31"
                candidate_metrics = _metrics(result["history"], start, end)
                baseline_metrics = _metrics(baseline["history"], start, end)
                rows.append({
                    "Strategy": result["Strategy"],
                    "Variant": result["Variant"],
                    "LowerBand": result["LowerBand"],
                    "Years": years,
                    "Window": f"{start_year}_{start_year + years - 1}",
                    **candidate_metrics,
                    "CAGRGap": (
                        candidate_metrics["CAGR"] - baseline_metrics["CAGR"]
                    ),
                    "MDDImprovement": (
                        candidate_metrics["MDD"] - baseline_metrics["MDD"]
                    ),
                    "SharpeGap": (
                        candidate_metrics["Sharpe"] - baseline_metrics["Sharpe"]
                    ),
                })
    return pd.DataFrame(rows)


def _rolling_summary(rolling: pd.DataFrame) -> pd.DataFrame:
    return (
        rolling.groupby(["Strategy", "LowerBand", "Years"], as_index=False)
        .agg(
            Windows=("Window", "count"),
            CAGRWins=("CAGRGap", lambda values: int((values > 1e-12).sum())),
            CAGRLosses=("CAGRGap", lambda values: int((values < -1e-12).sum())),
            AverageCAGRGap=("CAGRGap", "mean"),
            WorstCAGRGap=("CAGRGap", "min"),
            MDDWins=(
                "MDDImprovement", lambda values: int((values > 1e-12).sum())
            ),
            MDDLosses=(
                "MDDImprovement", lambda values: int((values < -1e-12).sum())
            ),
            AverageMDDImprovement=("MDDImprovement", "mean"),
            SharpeWins=("SharpeGap", lambda values: int((values > 1e-12).sum())),
            AverageSharpeGap=("SharpeGap", "mean"),
        )
    )


def _cost_stress_report() -> pd.DataFrame:
    rows = []
    for strategy_number in STRATEGIES:
        for cost_multiple in (1.0, 3.0, 5.0):
            for lower_band in (None, 0.04):
                result = _run(
                    strategy_number,
                    lower_band,
                    cost_multiple=cost_multiple,
                )
                rows.append({
                    key: value
                    for key, value in result.items()
                    if key != "history"
                } | _metrics(result["history"], START_DATE, END_DATE))
    report = pd.DataFrame(rows)
    baseline = report.loc[
        report["LowerBand"].isna(),
        ["Strategy", "CostMultiple", "CAGR", "MDD", "Sharpe"],
    ].rename(columns={
        "CAGR": "BaselineCAGR",
        "MDD": "BaselineMDD",
        "Sharpe": "BaselineSharpe",
    })
    report = report.merge(
        baseline, on=["Strategy", "CostMultiple"], how="left"
    )
    report["CAGRGap"] = report["CAGR"] - report["BaselineCAGR"]
    report["MDDImprovement"] = report["MDD"] - report["BaselineMDD"]
    report["SharpeGap"] = report["Sharpe"] - report["BaselineSharpe"]
    return report


def run_validation():
    runs = [
        _run(strategy_number, lower_band)
        for strategy_number in STRATEGIES
        for lower_band in (None, *LOWER_BANDS)
    ]
    fixed = _fixed_report(runs)
    rolling = _rolling_report(runs)
    rolling_summary = _rolling_summary(rolling)
    cost_stress = _cost_stress_report()
    fixed.to_csv(
        RESULT_DIR / "strategy25_26_asymmetric_rebalance_band_fixed.csv",
        index=False,
    )
    rolling.to_csv(
        RESULT_DIR / "strategy25_26_asymmetric_rebalance_band_rolling.csv",
        index=False,
    )
    rolling_summary.to_csv(
        RESULT_DIR / "strategy25_26_asymmetric_rebalance_band_rolling_summary.csv",
        index=False,
    )
    cost_stress.to_csv(
        RESULT_DIR / "strategy25_26_asymmetric_rebalance_band_cost_stress.csv",
        index=False,
    )
    return fixed, rolling_summary, cost_stress


if __name__ == "__main__":
    fixed_report, rolling_summary_report, cost_stress_report = run_validation()
    columns = [
        "Strategy", "Period", "Variant", "CAGR", "CAGRGap", "MDD",
        "MDDImprovement", "Sharpe", "SharpeGap", "Rebalances",
        "UpperBandEvents", "LowerBandEvents", "TargetChangeEvents",
    ]
    print(fixed_report[columns].to_string(index=False))
    print("\nRolling-window summary")
    print(rolling_summary_report.to_string(index=False))
    print("\nCost stress for the -4%p lower band")
    print(
        cost_stress_report[[
            "Strategy", "CostMultiple", "Variant", "CAGR", "CAGRGap",
            "MDD", "MDDImprovement", "Sharpe", "SharpeGap", "Rebalances",
        ]].to_string(index=False)
    )
