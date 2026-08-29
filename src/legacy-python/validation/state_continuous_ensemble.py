"""PoC for blending the production state strategy with a continuous expert.

The production strategy remains an independent shadow book.  Outside BEAR,
10% or 20% of its QQQ target is blended with the pre-existing downside-only
continuous trend/volatility allocation.  BEAR stays at the production target
so the experiment cannot weaken the strategy's hard defensive state.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping

import numpy as np
import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from validation.state_conditioned_continuous_overlay import (
    BIL,
    QQQ,
    REBALANCE_BAND,
    SOURCE,
    TDF,
    STRESS_SCENARIOS,
    RecordingDeclarativeStrategy,
    _performance_report,
    _relative_report,
    _rolling_windows,
    _stress_report,
)
from strategy_dsl import load_strategy_definition


MINIMUM_EXPERT_QQQ_WEIGHT = 0.20
MAXIMUM_EXPERT_QQQ_WEIGHT = 0.70
TARGET_QQQ_VOLATILITY = 0.25
EXPERT_FIELDS = ("Close", "EMA200", "ROC60", "ROC120", "ROC252", "VOL60")


@dataclass(frozen=True)
class EnsembleProfile:
    name: str
    continuous_share: float


PROFILES = (
    EnsembleProfile("STATE_CONTINUOUS_ENSEMBLE_10", 0.10),
    EnsembleProfile("STATE_CONTINUOUS_ENSEMBLE_20", 0.20),
)


def _clip_signal(value: float, scale: float) -> float:
    return float(np.clip(value / scale, -1.0, 1.0))


def continuous_expert_weight(qqq: Mapping[str, Any]) -> float:
    """Return the frozen downside-only continuous allocation from prior work."""

    values = (
        qqq.get("Close"),
        qqq.get("EMA200"),
        qqq.get("ROC60"),
        qqq.get("ROC120"),
        qqq.get("ROC252"),
        qqq.get("VOL60"),
    )
    if any(value is None for value in values):
        return MAXIMUM_EXPERT_QQQ_WEIGHT
    close, ema200, roc60, roc120, roc252, volatility = (
        float(value) for value in values
    )
    if not all(isfinite(value) for value in values) or ema200 <= 0.0:
        return MAXIMUM_EXPERT_QQQ_WEIGHT

    signals = (
        _clip_signal(roc60, 15.0),
        _clip_signal(roc120, 25.0),
        _clip_signal(roc252, 40.0),
        _clip_signal(close / ema200 - 1.0, 0.15),
    )
    trend_score = float(np.mean(signals))
    volatility_multiplier = float(np.clip(
        TARGET_QQQ_VOLATILITY / max(volatility, 0.01),
        0.25,
        1.0,
    ))
    volatility_stress = 1.0 / volatility_multiplier
    reduction = float(np.clip(
        max(0.0, -trend_score) * volatility_stress,
        0.0,
        1.0,
    ))
    tactical_capacity = (
        MAXIMUM_EXPERT_QQQ_WEIGHT - MINIMUM_EXPERT_QQQ_WEIGHT
    )
    return float(np.clip(
        MAXIMUM_EXPERT_QQQ_WEIGHT - tactical_capacity * reduction,
        MINIMUM_EXPERT_QQQ_WEIGHT,
        MAXIMUM_EXPERT_QQQ_WEIGHT,
    ))


def _with_expert_fields(required_market_fields):
    result = {
        ticker: tuple(fields)
        for ticker, fields in required_market_fields.items()
    }
    existing = list(result.get(QQQ, ()))
    folded = {field.casefold() for field in existing}
    for field in EXPERT_FIELDS:
        if field.casefold() not in folded:
            existing.append(field)
            folded.add(field.casefold())
    result[QQQ] = tuple(existing)
    return result


class StateContinuousEnsembleStrategy:
    """Blend a bounded continuous expert into the shadow production target."""

    def __init__(self, profile, schedule, baseline, record):
        self.profile = profile
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self._record = record
        self.strategy_id = f"poc:{profile.name.casefold()}"
        self.display_name = profile.name
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = baseline.holding_tickers
        self.observation_tickers = baseline.observation_tickers
        self.required_tickers = baseline.required_tickers
        self.required_market_fields = _with_expert_fields(
            baseline.required_market_fields
        )
        self.risk_asset_tickers = baseline.risk_asset_tickers

        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.expert_qqq_weight = MAXIMUM_EXPERT_QQQ_WEIGHT
        self.residual = 0.0
        self._previous_residual = 0.0
        self._last_ensemble_week = None

    def _desired_target(self, point, market):
        baseline_qqq = float(point["target"][QQQ])
        self.expert_qqq_weight = continuous_expert_weight(market[QQQ])
        if point["state"] == "BEAR":
            ensemble_qqq = baseline_qqq
        else:
            share = self.profile.continuous_share
            ensemble_qqq = (
                (1.0 - share) * baseline_qqq
                + share * self.expert_qqq_weight
            )
        ensemble_qqq = round(float(np.clip(ensemble_qqq, 0.0, 1.0)), 10)
        self.residual = ensemble_qqq - baseline_qqq
        safe_weight = 1.0 - ensemble_qqq
        tdf_weight = round(
            safe_weight * float(point["safe_tdf_share"]),
            10,
        )
        return {
            QQQ: ensemble_qqq,
            TDF: tdf_weight,
            BIL: max(0.0, round(1.0 - ensemble_qqq - tdf_weight, 10)),
        }

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
        weekly_check = week != self._last_ensemble_week
        self._last_ensemble_week = week
        residual_active = abs(self.residual) > 1e-12 or abs(
            self._previous_residual
        ) > 1e-12
        residual_removed = (
            abs(self._previous_residual) > 1e-12
            and abs(self.residual) <= 1e-12
        )
        ensemble_rebalance = (
            residual_active
            and deviation >= REBALANCE_BAND
            and (weekly_check or residual_removed)
        )
        baseline_rebalance = bool(point["rebalance"])
        rebalance = baseline_rebalance or ensemble_rebalance
        reason = point["reason"] if baseline_rebalance else None
        if ensemble_rebalance and not baseline_rebalance:
            reason = (
                "STATE_CONTINUOUS_ENSEMBLE"
                if abs(self.residual) > 1e-12
                else "STATE_CONTINUOUS_ENSEMBLE_REMOVED"
            )

        self.target = target
        self._record({
            "Date": timestamp,
            "State": self.state,
            "BaselineQQQTarget": float(point["target"][QQQ]),
            "ExpertQQQTarget": self.expert_qqq_weight,
            "EnsembleQQQTarget": target[QQQ],
            "Residual": self.residual,
            "TargetDeviation": deviation,
            "EnsembleRebalance": bool(ensemble_rebalance),
            "BaselineRebalance": bool(baseline_rebalance),
        })
        self._previous_residual = self.residual
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": int(point["days"]),
            "reason": reason,
        }


def _run_scenario(scenario):
    definition = load_strategy_definition(SOURCE)
    schedule = {}

    def record_baseline(date, point):
        schedule[date] = point

    recorder = RecordingDeclarativeStrategy(definition, record_baseline)
    recorder.required_market_fields = _with_expert_fields(
        recorder.required_market_fields
    )
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
        "activity": pd.DataFrame(),
    }]
    for profile in PROFILES:
        activity = []
        strategy = StateContinuousEnsembleStrategy(
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


def _activity_reports(primary):
    frames = []
    for result in primary["results"]:
        if result["label"] == "BASELINE":
            continue
        activity = result["activity"].copy()
        activity.insert(0, "Strategy", result["label"])
        frames.append(activity)
    daily = pd.concat(frames, ignore_index=True)
    daily["AbsoluteResidual"] = daily["Residual"].abs()
    summary = daily.groupby(["Strategy", "State"], as_index=False).agg(
        Observations=("Date", "size"),
        AverageBaselineQQQ=("BaselineQQQTarget", "mean"),
        AverageExpertQQQ=("ExpertQQQTarget", "mean"),
        AverageEnsembleQQQ=("EnsembleQQQTarget", "mean"),
        AverageResidual=("Residual", "mean"),
        AverageAbsoluteResidual=("AbsoluteResidual", "mean"),
        MaximumAbsoluteResidual=("AbsoluteResidual", "max"),
        EnsembleRebalances=("EnsembleRebalance", "sum"),
    )
    return daily, summary


def _state_attribution(primary):
    schedule = primary["schedule"]
    rows = []
    for result in primary["results"]:
        returns = result["history"]["Portfolio"].pct_change()
        state = pd.Series(
            {date: point["state"] for date, point in schedule.items()},
            name="State",
        ).reindex(returns.index)
        for name in ("BULL", "CAUTION", "BEAR", "RECOVERY"):
            sample = returns.loc[state == name].dropna()
            if sample.empty:
                continue
            threshold = sample.quantile(0.05)
            rows.append({
                "Strategy": result["label"],
                "State": name,
                "Observations": len(sample),
                "AnnualizedMeanReturn": sample.mean() * 252.0,
                "AnnualizedVolatility": sample.std() * np.sqrt(252.0),
                "NegativeReturnShare": (sample < 0.0).mean(),
                "DailyES5": sample.loc[sample <= threshold].mean(),
            })
    report = pd.DataFrame(rows)
    relative_rows = []
    for state, group in report.groupby("State"):
        baseline = group.loc[group["Strategy"] == "BASELINE"].iloc[0]
        for _, candidate in group.loc[group["Strategy"] != "BASELINE"].iterrows():
            relative_rows.append({
                "Strategy": candidate["Strategy"],
                "State": state,
                "AnnualizedMeanReturnGap": (
                    candidate["AnnualizedMeanReturn"]
                    - baseline["AnnualizedMeanReturn"]
                ),
                "AnnualizedVolatilityImprovement": (
                    baseline["AnnualizedVolatility"]
                    - candidate["AnnualizedVolatility"]
                ),
                "NegativeReturnShareImprovement": (
                    baseline["NegativeReturnShare"]
                    - candidate["NegativeReturnShare"]
                ),
                "DailyES5Improvement": (
                    candidate["DailyES5"] - baseline["DailyES5"]
                ),
            })
    return report, pd.DataFrame(relative_rows)


def _decision_report(relative, rolling_relative, stress_relative, activity):
    indexed = relative.set_index(["Strategy", "Window"])
    stress = stress_relative.set_index(["Strategy", "Scenario"])
    rows = []
    for profile in PROFILES:
        name = profile.name
        full = indexed.loc[(name, "FULL")]
        development = indexed.loc[(name, "DEVELOPMENT_PRE2021")]
        recent = indexed.loc[(name, "RECENT_2021_PRESENT")]
        rolling = rolling_relative.loc[rolling_relative["Strategy"] == name]
        bear = activity.loc[
            (activity["Strategy"] == name) & (activity["State"] == "BEAR")
        ]
        bear_preserved = bool(
            bear.empty or bear["MaximumAbsoluteResidual"].max() <= 1e-12
        )
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
            bear_preserved
            and (dominance or risk_efficient_tradeoff)
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
            "BearTargetPreserved": bear_preserved,
            "DominancePass": dominance,
            "RiskEfficientTradeoffPass": risk_efficient_tradeoff,
            "PeriodRiskConsistencyPass": period_risk_consistency,
            "StressSurvivalPass": stress_survival,
            "PoCPass": poc_pass,
        })
    return pd.DataFrame(rows)


def run_state_continuous_ensemble_validation():
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
    activity_daily, activity_summary = _activity_reports(primary)
    state_attribution, state_relative = _state_attribution(primary)
    stress, stress_relative = _stress_report(scenarios)
    decisions = _decision_report(
        relative,
        rolling_relative,
        stress_relative,
        activity_summary,
    )
    reports = {
        "state_continuous_ensemble_summary": summary,
        "state_continuous_ensemble_relative": relative,
        "state_continuous_ensemble_rolling": rolling,
        "state_continuous_ensemble_rolling_relative": rolling_relative,
        "state_continuous_ensemble_rolling_summary": rolling_summary,
        "state_continuous_ensemble_activity_daily": activity_daily,
        "state_continuous_ensemble_activity_summary": activity_summary,
        "state_continuous_ensemble_state_attribution": state_attribution,
        "state_continuous_ensemble_state_relative": state_relative,
        "state_continuous_ensemble_stress": stress,
        "state_continuous_ensemble_stress_relative": stress_relative,
        "state_continuous_ensemble_decision": decisions,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_state_continuous_ensemble_validation()
    print(output["state_continuous_ensemble_summary"].to_string(index=False))
    print("\nRelative to production baseline")
    print(output["state_continuous_ensemble_relative"].to_string(index=False))
    print("\nDecision")
    print(output["state_continuous_ensemble_decision"].to_string(index=False))
