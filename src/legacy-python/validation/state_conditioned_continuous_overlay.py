"""PoC for a bounded continuous overlay inside the CAUTION state.

The production strategy is run as an independent shadow portfolio.  Its
daily state, target, and rebalance decisions form an uncontaminated baseline
for the overlay.  This matters because the production profit-band target
depends on the portfolio weights produced by the strategy itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = (
    ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)

QQQ = "QQQ"
TDF = "TDF2050_PROXY"
BIL = "BIL"
REBALANCE_BAND = 0.05


@dataclass(frozen=True)
class OverlayProfile:
    name: str
    maximum_adjustment: float


PROFILES = (
    OverlayProfile("CAUTION_OVERLAY_5", 0.05),
    OverlayProfile("CAUTION_OVERLAY_10", 0.10),
)


@dataclass(frozen=True)
class StressScenario:
    name: str
    cost_multiple: float = 1.0
    signal_delay_days: int = 0


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_2X", cost_multiple=2.0),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
    StressScenario("DELAY_2D", signal_delay_days=2),
)


WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
}


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return float(max(low, min(high, value)))


def caution_severity(qqq: Mapping[str, Any]) -> float:
    """Combine trend depth and volatility using pre-registered scales."""

    close = qqq.get("Close")
    ema55 = qqq.get("EMA55")
    volatility = qqq.get("VOL60")
    values = (close, ema55, volatility)
    if any(value is None for value in values):
        return 0.0
    close, ema55, volatility = (float(value) for value in values)
    if not all(isfinite(value) for value in (close, ema55, volatility)):
        return 0.0
    if ema55 <= 0.0:
        return 0.0

    trend_depth = _clip((1.0 - close / ema55) / 0.10)
    volatility_stress = _clip((volatility - 0.15) / 0.15)
    return 0.5 * trend_depth + 0.5 * volatility_stress


class RecordingDeclarativeStrategy(DeclarativeStrategy):
    """Record causal daily production targets while running the baseline."""

    def __init__(self, definition, record):
        super().__init__(definition)
        # StrategyEngine snapshots ``__dict__`` after every evaluation.  Keep
        # the growing schedule outside the strategy and retain only a callback
        # reference here so runtime-state capture stays constant-size.
        self._record = record

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        self._record(pd.Timestamp(date), {
            "state": self.state,
            "target": signal["target"].copy(),
            "rebalance": bool(signal["rebalance"]),
            "days": int(signal["days"]),
            "reason": signal.get("reason"),
            "risk_off_score": getattr(self, "risk_off_score", None),
            "recovery_score": getattr(self, "recovery_score", None),
            "safe_tdf_share": float(getattr(self, "safe_tdf_share", 1.0)),
        })
        return signal


class StateConditionedContinuousOverlayStrategy:
    """Apply a small continuous QQQ reduction only while CAUTION is active."""

    def __init__(
        self,
        profile: OverlayProfile,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        baseline: DeclarativeStrategy,
        record,
    ):
        self.profile = profile
        # As above, closures keep the growing research data out of the
        # strategy runtime snapshot without weakening engine isolation.
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self._record = record
        self.strategy_id = f"poc:{profile.name.casefold()}"
        self.display_name = profile.name
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = baseline.holding_tickers
        self.observation_tickers = baseline.observation_tickers
        self.required_tickers = baseline.required_tickers
        self.required_market_fields = baseline.required_market_fields
        self.risk_asset_tickers = baseline.risk_asset_tickers

        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.severity = 0.0
        self.adjustment = 0.0
        self._previous_adjustment = 0.0
        self._last_overlay_week = None

    def _desired_target(self, point, market):
        target = dict(point["target"])
        self.severity = (
            caution_severity(market[QQQ])
            if point["state"] == "CAUTION"
            else 0.0
        )
        self.adjustment = min(
            float(target[QQQ]),
            self.profile.maximum_adjustment * self.severity,
        )
        target[QQQ] = round(float(target[QQQ]) - self.adjustment, 10)
        safe_weight = 1.0 - target[QQQ]
        target[TDF] = round(
            safe_weight * float(point["safe_tdf_share"]),
            10,
        )
        target[BIL] = max(
            0.0,
            round(1.0 - target[QQQ] - target[TDF], 10),
        )
        return target

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        self.state = point["state"]
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = TDF if point["safe_tdf_share"] >= 0.5 else BIL

        target = self._desired_target(point, market)
        prices = {
            ticker: market[ticker]["Close"]
            for ticker in self.holding_tickers
        }
        current = portfolio.weights(prices)
        deviation = max(
            abs(float(current.get(ticker, 0.0)) - weight)
            for ticker, weight in target.items()
        )

        week = timestamp.to_period("W")
        weekly_check = week != self._last_overlay_week
        self._last_overlay_week = week
        overlay_active = self.adjustment > 0.0 or self._previous_adjustment > 0.0
        overlay_removed = self._previous_adjustment > 0.0 and self.adjustment == 0.0
        overlay_rebalance = (
            overlay_active
            and deviation >= REBALANCE_BAND
            and (weekly_check or overlay_removed)
        )
        baseline_rebalance = bool(point["rebalance"])
        rebalance = baseline_rebalance or overlay_rebalance
        reason = point["reason"] if baseline_rebalance else None
        if overlay_rebalance and not baseline_rebalance:
            reason = (
                "CAUTION_CONTINUOUS_OVERLAY"
                if self.adjustment > 0.0
                else "CAUTION_CONTINUOUS_OVERLAY_REMOVED"
            )

        self.target = target
        self._record({
            "Date": timestamp,
            "State": self.state,
            "Severity": self.severity,
            "Adjustment": self.adjustment,
            "BaselineQQQTarget": float(point["target"][QQQ]),
            "OverlayQQQTarget": float(target[QQQ]),
            "TargetDeviation": deviation,
            "OverlayRebalance": bool(overlay_rebalance),
            "BaselineRebalance": bool(baseline_rebalance),
        })
        self._previous_adjustment = self.adjustment
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": int(point["days"]),
            "reason": reason,
        }


def _event_turnover(event):
    target = event["Target"]
    previous = event.get("PreWeights", {})
    return sum(
        abs(float(target.get(ticker, 0.0)) - float(previous.get(ticker, 0.0)))
        for ticker in target
    ) / 2.0


def _events_in_window(events, start, end):
    selected = []
    for event in events:
        date = pd.Timestamp(event["Date"])
        if start is not None and date < pd.Timestamp(start):
            continue
        if end is not None and date > pd.Timestamp(end):
            continue
        selected.append(event)
    return selected


def _trades_in_window(trades, start, end):
    if trades.empty:
        return trades
    dates = pd.to_datetime(trades["Date"])
    mask = pd.Series(True, index=trades.index)
    if start is not None:
        mask &= dates >= pd.Timestamp(start)
    if end is not None:
        mask &= dates <= pd.Timestamp(end)
    return trades.loc[mask]


def _daily_expected_shortfall(history, probability=0.05):
    returns = history["Portfolio"].pct_change().dropna()
    if returns.empty:
        return float("nan")
    threshold = returns.quantile(probability)
    return float(returns.loc[returns <= threshold].mean())


def _performance_row(result, window, start, end):
    sample = result["history"].loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    events = _events_in_window(result["rebalances"], start, end)
    trades = _trades_in_window(result["trades"], start, end)
    costs = sample["TransactionCosts"]
    return {
        "Strategy": result["label"],
        "Window": window,
        "StartDate": sample.index.min(),
        "EndDate": sample.index.max(),
        "Observations": len(sample),
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Volatility": metrics["Volatility"],
        "Sharpe": metrics["Sharpe"],
        "Sortino": metrics["Sortino"],
        "Calmar": metrics["Calmar"],
        "DailyES5": _daily_expected_shortfall(sample),
        "TransactionCosts": float(costs.iloc[-1] - costs.iloc[0]),
        "Rebalances": len(events),
        "Trades": len(trades),
        "TargetTurnover": sum(_event_turnover(event) for event in events),
    }


def _performance_report(results, windows=WINDOWS):
    rows = []
    for result in results:
        for window, (start, end) in windows.items():
            row = _performance_row(result, window, start, end)
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def _relative_report(report, keys=("Window",)):
    rows = []
    for key_values, group in report.groupby(list(keys), dropna=False):
        if not isinstance(key_values, tuple):
            key_values = (key_values,)
        baseline = group.loc[group["Strategy"] == "BASELINE"].iloc[0]
        for _, candidate in group.loc[group["Strategy"] != "BASELINE"].iterrows():
            row = dict(zip(keys, key_values))
            row.update({
                "Strategy": candidate["Strategy"],
                "CAGRGap": candidate["CAGR"] - baseline["CAGR"],
                "MDDImprovement": candidate["MDD"] - baseline["MDD"],
                "SharpeGap": candidate["Sharpe"] - baseline["Sharpe"],
                "CalmarGap": candidate["Calmar"] - baseline["Calmar"],
                "DailyES5Improvement": (
                    candidate["DailyES5"] - baseline["DailyES5"]
                ),
                "TransactionCostDelta": (
                    candidate["TransactionCosts"]
                    - baseline["TransactionCosts"]
                ),
                "RebalanceDelta": candidate["Rebalances"] - baseline["Rebalances"],
                "TradeDelta": candidate["Trades"] - baseline["Trades"],
                "TargetTurnoverDelta": (
                    candidate["TargetTurnover"] - baseline["TargetTurnover"]
                ),
            })
            rows.append(row)
    return pd.DataFrame(rows)


def _run_scenario(scenario: StressScenario):
    definition = load_strategy_definition(SOURCE)
    schedule = {}

    def record_baseline(date, point):
        schedule[date] = point

    recorder = RecordingDeclarativeStrategy(definition, record_baseline)
    baseline_backtest = Backtest(
        recorder,
        tickers=recorder.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.signal_delay_days,
    )
    history, trades, rebalances = baseline_backtest.run_all()
    results = [{
        "label": "BASELINE",
        "strategy": recorder,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }]
    for profile in PROFILES:
        activity = []
        strategy = StateConditionedContinuousOverlayStrategy(
            profile,
            schedule,
            recorder,
            activity.append,
        )
        backtest = Backtest(
            strategy,
            tickers=strategy.required_tickers,
            commission=COMMISSION * scenario.cost_multiple,
            slippage=SLIPPAGE * scenario.cost_multiple,
            signal_delay_days=scenario.signal_delay_days,
        )
        candidate_history, candidate_trades, candidate_rebalances = (
            backtest.run_all()
        )
        results.append({
            "label": profile.name,
            "strategy": strategy,
            "history": candidate_history,
            "trades": candidate_trades,
            "rebalances": candidate_rebalances,
            "activity": pd.DataFrame(activity),
        })
    return {
        "scenario": scenario,
        "results": results,
        "schedule": schedule,
        "data": baseline_backtest.data,
    }


def _rolling_windows(history, years=5):
    first_year = int(history.index.min().year)
    last_year = int(history.index.max().year)
    windows = {}
    for start_year in range(first_year, last_year - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        planned_end = start + pd.DateOffset(years=years) - pd.Timedelta(days=1)
        end = min(planned_end, history.index.max())
        sample = history.loc[start:end]
        if len(sample) >= 1000:
            windows[f"{start_year}_{end.year}"] = (start, end)
    return windows


def _caution_episodes(schedule):
    items = list(schedule.items())
    episodes = []
    start_index = None
    for index, (date, point) in enumerate(items):
        caution = point["state"] == "CAUTION"
        if caution and start_index is None:
            start_index = index
        final_item = index == len(items) - 1
        if start_index is not None and (not caution or final_item):
            end_index = index if caution and final_item else index - 1
            next_state = None if final_item and caution else point["state"]
            episodes.append({
                "Episode": len(episodes) + 1,
                "StartDate": items[start_index][0],
                "EndDate": items[end_index][0],
                "Days": end_index - start_index + 1,
                "NextState": next_state,
            })
            start_index = None
    return episodes


def _episode_report(primary):
    episodes = _caution_episodes(primary["schedule"])
    severity_by_date = {
        date: caution_severity({
            "Close": row["QQQ_Close"],
            "EMA55": row["QQQ_EMA55"],
            "VOL60": row["QQQ_VOL60"],
        })
        for date, row in primary["data"].iterrows()
    }
    rows = []
    for episode in episodes:
        severities = [
            severity_by_date[date]
            for date in severity_by_date
            if episode["StartDate"] <= date <= episode["EndDate"]
        ]
        for result in primary["results"]:
            sample = result["history"].loc[
                episode["StartDate"]:episode["EndDate"]
            ]
            if len(sample) < 2:
                continue
            portfolio = sample["Portfolio"]
            drawdown = portfolio / portfolio.cummax() - 1.0
            rows.append({
                **episode,
                "Strategy": result["label"],
                "MaxSeverity": max(severities, default=0.0),
                "Return": portfolio.iloc[-1] / portfolio.iloc[0] - 1.0,
                "EpisodeMDD": drawdown.min(),
            })
    report = pd.DataFrame(rows)
    relative_rows = []
    for episode, group in report.groupby("Episode"):
        baseline = group.loc[group["Strategy"] == "BASELINE"].iloc[0]
        for _, candidate in group.loc[group["Strategy"] != "BASELINE"].iterrows():
            relative_rows.append({
                "Episode": episode,
                "StartDate": candidate["StartDate"],
                "EndDate": candidate["EndDate"],
                "Days": candidate["Days"],
                "NextState": candidate["NextState"],
                "MaxSeverity": candidate["MaxSeverity"],
                "Strategy": candidate["Strategy"],
                "ReturnGap": candidate["Return"] - baseline["Return"],
                "MDDImprovement": (
                    candidate["EpisodeMDD"] - baseline["EpisodeMDD"]
                ),
            })
    relative = pd.DataFrame(relative_rows)
    summary = relative.groupby("Strategy", as_index=False).agg(
        Episodes=("Episode", "size"),
        MeanReturnGap=("ReturnGap", "mean"),
        MedianReturnGap=("ReturnGap", "median"),
        PositiveReturnShare=("ReturnGap", lambda values: (values > 0).mean()),
        MeanMDDImprovement=("MDDImprovement", "mean"),
        PositiveMDDShare=("MDDImprovement", lambda values: (values > 0).mean()),
    )
    return report, relative, summary


def _signal_diagnostics(primary):
    data = primary["data"]
    schedule = primary["schedule"]
    closes = data["QQQ_Close"]
    rows = []
    for index, date in enumerate(data.index):
        if schedule[date]["state"] != "CAUTION":
            continue
        severity = caution_severity({
            "Close": data.at[date, "QQQ_Close"],
            "EMA55": data.at[date, "QQQ_EMA55"],
            "VOL60": data.at[date, "QQQ_VOL60"],
        })
        bucket_index = min(int(severity / 0.25), 3)
        bucket = ("0_025", "025_050", "050_075", "075_100")[bucket_index]
        for horizon in (5, 20, 60):
            if index + horizon >= len(data):
                continue
            path = closes.iloc[index + 1:index + horizon + 1]
            start = closes.iloc[index]
            rows.append({
                "Date": date,
                "Horizon": horizon,
                "Severity": severity,
                "Bucket": bucket,
                "ForwardReturn": closes.iloc[index + horizon] / start - 1.0,
                "MaxAdverseExcursion": min(float((path / start - 1.0).min()), 0.0),
            })
    daily = pd.DataFrame(rows)
    summary = daily.groupby(["Horizon", "Bucket"], as_index=False).agg(
        Observations=("Date", "size"),
        MeanSeverity=("Severity", "mean"),
        AverageReturn=("ForwardReturn", "mean"),
        MedianReturn=("ForwardReturn", "median"),
        NegativeReturnShare=("ForwardReturn", lambda values: (values < 0).mean()),
        P10Return=("ForwardReturn", lambda values: values.quantile(0.10)),
        AverageMAE=("MaxAdverseExcursion", "mean"),
        P10MAE=("MaxAdverseExcursion", lambda values: values.quantile(0.10)),
    )
    return daily, summary


def _stress_report(scenarios):
    rows = []
    for scenario in scenarios:
        report = _performance_report(
            scenario["results"], {"FULL": (None, None)}
        )
        report.insert(0, "Scenario", scenario["scenario"].name)
        rows.append(report)
    report = pd.concat(rows, ignore_index=True)
    return report, _relative_report(report, keys=("Scenario", "Window"))


def _decision_report(relative, rolling_relative, stress_relative):
    indexed = relative.set_index(["Strategy", "Window"])
    stress = stress_relative.set_index(["Strategy", "Scenario"])
    rows = []
    for profile in PROFILES:
        name = profile.name
        full = indexed.loc[(name, "FULL")]
        development = indexed.loc[(name, "DEVELOPMENT_PRE2021")]
        recent = indexed.loc[(name, "RECENT_2021_PRESENT")]
        rolling = rolling_relative.loc[rolling_relative["Strategy"] == name]
        dominance = (
            full["CAGRGap"] >= 0.0
            and full["MDDImprovement"] >= 0.0
            and full["CalmarGap"] >= 0.0
        )
        risk_efficient_tradeoff = (
            full["CAGRGap"] >= -0.003
            and full["MDDImprovement"] >= 0.01
            and full["CalmarGap"] >= 0.0
        )
        period_risk_consistency = (
            development["MDDImprovement"] >= 0.0
            and recent["MDDImprovement"] >= 0.0
        )
        rolling_mdd_share = float((rolling["MDDImprovement"] > 0.0).mean())
        cost3 = stress.loc[(name, "COST_3X")]
        delay2 = stress.loc[(name, "DELAY_2D")]
        stress_survival = (
            cost3["CAGRGap"] >= -0.003
            and delay2["CAGRGap"] >= -0.003
            and cost3["MDDImprovement"] >= 0.0
            and delay2["MDDImprovement"] >= 0.0
        )
        poc_pass = (
            (dominance or risk_efficient_tradeoff)
            and period_risk_consistency
            and rolling_mdd_share >= 0.5
            and stress_survival
        )
        rows.append({
            "Strategy": name,
            "FullCAGRGap": full["CAGRGap"],
            "FullMDDImprovement": full["MDDImprovement"],
            "FullCalmarGap": full["CalmarGap"],
            "DevelopmentMDDImprovement": development["MDDImprovement"],
            "RecentMDDImprovement": recent["MDDImprovement"],
            "RollingMDDPositiveShare": rolling_mdd_share,
            "RollingCAGRNonNegativeShare": float(
                (rolling["CAGRGap"] >= 0.0).mean()
            ),
            "Cost3xCAGRGap": cost3["CAGRGap"],
            "Delay2dCAGRGap": delay2["CAGRGap"],
            "DominancePass": dominance,
            "RiskEfficientTradeoffPass": risk_efficient_tradeoff,
            "PeriodRiskConsistencyPass": period_risk_consistency,
            "StressSurvivalPass": stress_survival,
            "PoCPass": poc_pass,
        })
    return pd.DataFrame(rows)


def run_state_conditioned_overlay_validation():
    scenarios = [_run_scenario(scenario) for scenario in STRESS_SCENARIOS]
    primary = scenarios[0]

    summary = _performance_report(primary["results"])
    relative = _relative_report(summary)
    rolling = _performance_report(
        primary["results"],
        _rolling_windows(primary["results"][0]["history"]),
    )
    rolling_relative = _relative_report(rolling)
    rolling_summary = rolling_relative.groupby("Strategy", as_index=False).agg(
        Windows=("Window", "size"),
        MeanCAGRGap=("CAGRGap", "mean"),
        WorstCAGRGap=("CAGRGap", "min"),
        CAGRNonNegativeShare=("CAGRGap", lambda values: (values >= 0).mean()),
        MeanMDDImprovement=("MDDImprovement", "mean"),
        WorstMDDImprovement=("MDDImprovement", "min"),
        MDDPositiveShare=("MDDImprovement", lambda values: (values > 0).mean()),
        MeanCalmarGap=("CalmarGap", "mean"),
    )
    episodes, episode_relative, episode_summary = _episode_report(primary)
    signal_daily, signal_summary = _signal_diagnostics(primary)
    stress, stress_relative = _stress_report(scenarios)
    decisions = _decision_report(relative, rolling_relative, stress_relative)

    reports = {
        "state_conditioned_overlay_summary": summary,
        "state_conditioned_overlay_relative": relative,
        "state_conditioned_overlay_rolling": rolling,
        "state_conditioned_overlay_rolling_relative": rolling_relative,
        "state_conditioned_overlay_rolling_summary": rolling_summary,
        "state_conditioned_overlay_episodes": episodes,
        "state_conditioned_overlay_episode_relative": episode_relative,
        "state_conditioned_overlay_episode_summary": episode_summary,
        "state_conditioned_overlay_signal_daily": signal_daily,
        "state_conditioned_overlay_signal_summary": signal_summary,
        "state_conditioned_overlay_stress": stress,
        "state_conditioned_overlay_stress_relative": stress_relative,
        "state_conditioned_overlay_decision": decisions,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_state_conditioned_overlay_validation()
    print(output["state_conditioned_overlay_summary"].to_string(index=False))
    print("\nRelative to production baseline")
    print(output["state_conditioned_overlay_relative"].to_string(index=False))
    print("\nDecision")
    print(output["state_conditioned_overlay_decision"].to_string(index=False))
