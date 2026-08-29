"""Validate a strict, sticky defensive tier inside production CAUTION.

The production strategy runs as an independent shadow portfolio.  A candidate
enters DEFENSIVE_CAUTION on the first CAUTION day that simultaneously has a
strong QQQ risk-off reading, intermediate-trend weakness, and the existing SPY
stress confirmation.  Once entered, the tier remains active until production
leaves CAUTION, avoiding daily signal churn.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import (
    COMMISSION,
    GENERAL_COMPARISON_START_DATE,
    RESULT_DIR,
    SLIPPAGE,
)
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.state_conditioned_continuous_overlay import (
    BIL,
    QQQ,
    TDF,
    RecordingDeclarativeStrategy,
    StressScenario,
    _caution_episodes,
    _performance_report,
    _relative_report,
)


ROOT = Path(__file__).resolve().parents[3]
SOURCE = (
    ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)
START_DATE = GENERAL_COMPARISON_START_DATE
SPY = "SPY"


@dataclass(frozen=True)
class DefensiveCautionProfile:
    name: str
    qqq_adjustment: float
    require_intermediate_trend: bool = False
    exit_execution_days: int | None = None


PROFILES = (
    DefensiveCautionProfile("DEFENSIVE_CAUTION_5", 0.05),
    DefensiveCautionProfile(
        "DEFENSIVE_CAUTION_5_FAST_EXIT",
        0.05,
        exit_execution_days=1,
    ),
    DefensiveCautionProfile(
        "DEFENSIVE_CAUTION_INTERMEDIATE_5",
        0.05,
        require_intermediate_trend=True,
        exit_execution_days=1,
    ),
)


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_2X", cost_multiple=2.0),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
    StressScenario("DELAY_2D", signal_delay_days=2),
)


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def defensive_caution_signal(
    point: Mapping[str, Any],
    market: Mapping[str, Mapping[str, Any]],
    require_intermediate_trend: bool = False,
) -> bool:
    """Return the pre-registered one-day strict CAUTION entry signal."""

    if point.get("state") != "CAUTION":
        return False
    risk_off_score = _finite_float(point.get("risk_off_score"))
    qqq_close = _finite_float(market[QQQ].get("Close"))
    qqq_ema55 = _finite_float(market[QQQ].get("EMA55"))
    qqq_roc20 = _finite_float(market[QQQ].get("ROC20"))
    qqq_ema20 = _finite_float(market[QQQ].get("EMA20"))
    qqq_roc60 = _finite_float(market[QQQ].get("ROC60"))
    spy_close = _finite_float(market[SPY].get("Close"))
    spy_ema20 = _finite_float(market[SPY].get("EMA20"))
    spy_roc5 = _finite_float(market[SPY].get("ROC5"))
    values = (
        risk_off_score,
        qqq_close,
        qqq_ema55,
        qqq_roc20,
        spy_close,
        spy_ema20,
        spy_roc5,
    )
    if any(value is None for value in values):
        return False
    base_signal = bool(
        risk_off_score >= 5
        and qqq_close < qqq_ema55
        and qqq_roc20 < 0
        and spy_close < spy_ema20
        and spy_roc5 <= -1
    )
    if not base_signal or not require_intermediate_trend:
        return base_signal
    if qqq_ema20 is None or qqq_roc60 is None:
        return False
    return bool(qqq_ema20 < qqq_ema55 and qqq_roc60 < 0)


class DefensiveCautionOverlayStrategy:
    """Apply a fixed QQQ reduction after strict CAUTION activation."""

    def __init__(
        self,
        profile: DefensiveCautionProfile,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        baseline: DeclarativeStrategy,
        record,
    ):
        self.profile = profile
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
        self.defensive_active = False

    def _desired_target(self, point):
        target = dict(point["target"])
        adjustment = (
            min(float(target[QQQ]), self.profile.qqq_adjustment)
            if self.defensive_active
            else 0.0
        )
        target[QQQ] = round(float(target[QQQ]) - adjustment, 10)
        safe_weight = 1.0 - target[QQQ]
        target[TDF] = round(
            safe_weight * float(point["safe_tdf_share"]),
            10,
        )
        target[BIL] = max(
            0.0,
            round(1.0 - target[QQQ] - target[TDF], 10),
        )
        return target, adjustment

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        production_state = point["state"]
        strict_signal = defensive_caution_signal(
            point,
            market,
            require_intermediate_trend=(
                self.profile.require_intermediate_trend
            ),
        )
        was_active = self.defensive_active

        if production_state != "CAUTION":
            self.defensive_active = False
        elif strict_signal:
            self.defensive_active = True

        activated = not was_active and self.defensive_active
        deactivated = was_active and not self.defensive_active
        target, adjustment = self._desired_target(point)

        self.state = (
            "DEFENSIVE_CAUTION"
            if self.defensive_active
            else production_state
        )
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = TDF if point["safe_tdf_share"] >= 0.5 else BIL
        self.target = target

        baseline_rebalance = bool(point["rebalance"])
        overlay_transition = activated or deactivated
        rebalance = baseline_rebalance or overlay_transition
        reason = point["reason"] if baseline_rebalance else None
        if activated:
            reason = "DEFENSIVE_CAUTION_ENTER"
        elif deactivated:
            reason = "DEFENSIVE_CAUTION_EXIT"

        if activated:
            execution_days = 1
        elif deactivated and self.profile.exit_execution_days is not None:
            execution_days = self.profile.exit_execution_days
        else:
            execution_days = int(point["days"])

        self._record({
            "Date": timestamp,
            "ProductionState": production_state,
            "CandidateState": self.state,
            "StrictSignal": strict_signal,
            "DefensiveActive": self.defensive_active,
            "Activated": activated,
            "Deactivated": deactivated,
            "RiskOffScore": self.risk_off_score,
            "RecoveryScore": self.recovery_score,
            "BaselineQQQTarget": float(point["target"][QQQ]),
            "CandidateQQQTarget": float(target[QQQ]),
            "Adjustment": adjustment,
            "BaselineRebalance": baseline_rebalance,
            "OverlayTransition": overlay_transition,
        })
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": execution_days,
            "reason": reason,
        }


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
        start_date=START_DATE,
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
        strategy = DefensiveCautionOverlayStrategy(
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
            start_date=START_DATE,
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


def _episode_reports(primary):
    episodes = _caution_episodes(primary["schedule"])
    rows = []
    relative_rows = []
    for episode in episodes:
        start = episode["StartDate"]
        end = episode["EndDate"]
        baseline_result = primary["results"][0]
        baseline_sample = baseline_result["history"].loc[start:end]
        if len(baseline_sample) < 2:
            continue
        baseline_portfolio = baseline_sample["Portfolio"]
        baseline_drawdown = (
            baseline_portfolio / baseline_portfolio.cummax() - 1.0
        )
        base_row = {
            **episode,
            "Activated": False,
            "ActivationDate": pd.NaT,
            "ActivationLagDays": None,
            "DefensiveDays": 0,
            "MaxRiskOffScore": None,
            "Strategy": "BASELINE",
            "Return": baseline_portfolio.iloc[-1] / baseline_portfolio.iloc[0] - 1.0,
            "EpisodeMDD": baseline_drawdown.min(),
        }
        rows.append(base_row)
        for result in primary["results"][1:]:
            activity = result["activity"].set_index("Date")
            episode_activity = activity.loc[start:end]
            activation_dates = episode_activity.index[
                episode_activity["Activated"]
            ]
            activated = len(activation_dates) > 0
            activation_date = activation_dates[0] if activated else pd.NaT
            sample = result["history"].loc[start:end]
            portfolio = sample["Portfolio"]
            drawdown = portfolio / portfolio.cummax() - 1.0
            row = {
                **episode,
                "Activated": activated,
                "ActivationDate": activation_date,
                "ActivationLagDays": (
                    int(episode_activity.index.get_loc(activation_date))
                    if activated
                    else None
                ),
                "DefensiveDays": int(
                    episode_activity["DefensiveActive"].sum()
                ),
                "MaxRiskOffScore": episode_activity["RiskOffScore"].max(),
                "Strategy": result["label"],
                "Return": portfolio.iloc[-1] / portfolio.iloc[0] - 1.0,
                "EpisodeMDD": drawdown.min(),
            }
            rows.append(row)
            relative_rows.append({
                "Episode": episode["Episode"],
                "StartDate": start,
                "EndDate": end,
                "Days": episode["Days"],
                "NextState": episode["NextState"],
                "Activated": activated,
                "ActivationDate": activation_date,
                "ActivationLagDays": row["ActivationLagDays"],
                "DefensiveDays": row["DefensiveDays"],
                "Strategy": result["label"],
                "ReturnGap": row["Return"] - base_row["Return"],
                "MDDImprovement": (
                    row["EpisodeMDD"] - base_row["EpisodeMDD"]
                ),
            })
    report = pd.DataFrame(rows)
    relative = pd.DataFrame(relative_rows)
    activated_relative = relative.loc[relative["Activated"]].copy()
    if activated_relative.empty:
        summary = pd.DataFrame()
    else:
        summary = activated_relative.groupby(
            ["Strategy", "NextState"],
            as_index=False,
            dropna=False,
        ).agg(
            Episodes=("Episode", "size"),
            MeanActivationLagDays=("ActivationLagDays", "mean"),
            MeanDefensiveDays=("DefensiveDays", "mean"),
            MeanReturnGap=("ReturnGap", "mean"),
            PositiveReturnShare=("ReturnGap", lambda values: (values > 0).mean()),
            MeanMDDImprovement=("MDDImprovement", "mean"),
            PositiveMDDShare=("MDDImprovement", lambda values: (values > 0).mean()),
        )
    return report, relative, summary


def _activity_summary(primary):
    rows = []
    for result in primary["results"][1:]:
        activity = result["activity"]
        rows.append({
            "Strategy": result["label"],
            "Observations": len(activity),
            "CautionDays": int((activity["ProductionState"] == "CAUTION").sum()),
            "StrictSignalDays": int(activity["StrictSignal"].sum()),
            "DefensiveDays": int(activity["DefensiveActive"].sum()),
            "Activations": int(activity["Activated"].sum()),
            "Deactivations": int(activity["Deactivated"].sum()),
            "AverageBaselineQQQOnDefensiveDays": activity.loc[
                activity["DefensiveActive"], "BaselineQQQTarget"
            ].mean(),
            "AverageCandidateQQQOnDefensiveDays": activity.loc[
                activity["DefensiveActive"], "CandidateQQQTarget"
            ].mean(),
        })
    return pd.DataFrame(rows)


def _rolling_windows(history, years=5):
    """Return complete calendar-year windows for matched comparisons."""
    first_year = max(int(history.index.min().year), int(START_DATE[:4]))
    last_date = history.index.max()
    minimum_observations = 200 * years
    windows = {}
    for start_year in range(first_year, int(last_date.year) - years + 2):
        start = pd.Timestamp(f"{start_year}-01-01")
        planned_end = start + pd.DateOffset(years=years) - pd.Timedelta(days=1)
        if planned_end > last_date:
            continue
        sample = history.loc[start:planned_end]
        if len(sample) >= minimum_observations:
            windows[f"{start_year}_{planned_end.year}"] = (
                start,
                planned_end,
            )
    return windows


def _rolling_summary(relative):
    return relative.groupby("Strategy", as_index=False).agg(
        Windows=("Window", "size"),
        MeanCAGRGap=("CAGRGap", "mean"),
        WorstCAGRGap=("CAGRGap", "min"),
        CAGRNonNegativeShare=("CAGRGap", lambda values: (values >= 0).mean()),
        MeanMDDImprovement=("MDDImprovement", "mean"),
        WorstMDDImprovement=("MDDImprovement", "min"),
        MDDPositiveShare=("MDDImprovement", lambda values: (values > 0).mean()),
        MeanCalmarGap=("CalmarGap", "mean"),
    )


def _stress_reports(scenarios):
    rows = []
    for scenario in scenarios:
        report = _performance_report(
            scenario["results"],
            {"FULL": (None, None)},
        )
        report.insert(0, "Scenario", scenario["scenario"].name)
        rows.append(report)
    report = pd.concat(rows, ignore_index=True)
    relative = _relative_report(report, keys=("Scenario", "Window"))
    return report, relative


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
            "PoCPass": bool(
                (dominance or risk_efficient_tradeoff)
                and period_risk_consistency
                and rolling_mdd_share >= 0.5
                and stress_survival
            ),
        })
    return pd.DataFrame(rows)


def run_defensive_caution_validation():
    scenarios = [_run_scenario(scenario) for scenario in STRESS_SCENARIOS]
    primary = scenarios[0]
    summary = _performance_report(primary["results"])
    relative = _relative_report(summary)
    rolling = _performance_report(
        primary["results"],
        _rolling_windows(primary["results"][0]["history"], years=5),
    )
    rolling_relative = _relative_report(rolling)
    rolling_summary = _rolling_summary(rolling_relative)
    rolling3 = _performance_report(
        primary["results"],
        _rolling_windows(primary["results"][0]["history"], years=3),
    )
    rolling3_relative = _relative_report(rolling3)
    episodes, episode_relative, episode_summary = _episode_reports(primary)
    activity_summary = _activity_summary(primary)
    stress, stress_relative = _stress_reports(scenarios)
    decision = _decision_report(relative, rolling_relative, stress_relative)

    reports = {
        "defensive_caution_summary": summary,
        "defensive_caution_relative": relative,
        "defensive_caution_rolling": rolling,
        "defensive_caution_rolling_relative": rolling_relative,
        "defensive_caution_rolling_summary": rolling_summary,
        "defensive_caution_rolling3": rolling3,
        "defensive_caution_rolling3_relative": rolling3_relative,
        "defensive_caution_rolling3_summary": _rolling_summary(
            rolling3_relative
        ),
        "defensive_caution_episodes": episodes,
        "defensive_caution_episode_relative": episode_relative,
        "defensive_caution_episode_summary": episode_summary,
        "defensive_caution_activity_summary": activity_summary,
        "defensive_caution_stress": stress,
        "defensive_caution_stress_relative": stress_relative,
        "defensive_caution_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_defensive_caution_validation()
    print(output["defensive_caution_summary"].to_string(index=False))
    print("\nRelative to production baseline")
    print(output["defensive_caution_relative"].to_string(index=False))
    print("\nActivity")
    print(output["defensive_caution_activity_summary"].to_string(index=False))
    print("\nDecision")
    print(output["defensive_caution_decision"].to_string(index=False))
