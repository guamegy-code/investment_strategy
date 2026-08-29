"""Apply the strict DEFENSIVE_CAUTION experiment to strategy 15.

The production strategy remains an independent shadow portfolio.  During a
strict CAUTION tier the candidate moves five percentage points from QQQ to BIL,
leaving the 30% TDF sleeve unchanged.  Strategy 15's structural-bear target has
priority and therefore remains 100% BIL while its ten-day confirmation runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.defensive_caution_overlay import (
    DefensiveCautionProfile,
    START_DATE,
    _activity_summary,
    _episode_reports,
    _rolling_windows,
    defensive_caution_signal,
)
from validation.state_conditioned_continuous_overlay import (
    BIL,
    QQQ,
    TDF,
    RecordingDeclarativeStrategy,
    StressScenario,
    _performance_report,
    _relative_report,
)


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
REBALANCE_BAND = 0.075


PROFILES = (
    DefensiveCautionProfile("STRATEGY15_DEFENSIVE_CAUTION_5", 0.05),
    DefensiveCautionProfile(
        "STRATEGY15_DEFENSIVE_CAUTION_INTERMEDIATE_5",
        0.05,
        require_intermediate_trend=True,
    ),
)


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("COST_5X", cost_multiple=5.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
    StressScenario("DELAY_2D", signal_delay_days=2),
)


class Strategy15DefensiveCautionOverlay:
    """Move 5%p QQQ to BIL during a sticky strict CAUTION tier."""

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
        adjustment = 0.0
        if self.defensive_active and point["state"] == "CAUTION":
            adjustment = min(
                float(target.get(QQQ, 0.0)),
                self.profile.qqq_adjustment,
            )
            target[QQQ] = round(float(target[QQQ]) - adjustment, 10)
            target[BIL] = round(float(target.get(BIL, 0.0)) + adjustment, 10)
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

        prices = {
            ticker: market[ticker]["Close"]
            for ticker in self.holding_tickers
        }
        current = portfolio.weights(prices)
        target_deviation = max(
            abs(float(current.get(ticker, 0.0)) - float(weight))
            for ticker, weight in target.items()
        )
        candidate_band_rebalance = target_deviation >= REBALANCE_BAND
        baseline_rebalance = bool(point["rebalance"])
        overlay_transition = activated or deactivated
        rebalance = (
            baseline_rebalance
            or overlay_transition
            or candidate_band_rebalance
        )

        reason = point["reason"] if baseline_rebalance else None
        if activated:
            reason = "STRATEGY15_DEFENSIVE_CAUTION_ENTER"
        elif deactivated:
            reason = "STRATEGY15_DEFENSIVE_CAUTION_EXIT"
        elif candidate_band_rebalance and not baseline_rebalance:
            reason = "STRATEGY15_DEFENSIVE_CAUTION_BAND"

        self.state = (
            "DEFENSIVE_CAUTION"
            if self.defensive_active
            else production_state
        )
        self.target = target
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = BIL if target.get(BIL, 0.0) > 0.0 else TDF
        self._record({
            "Date": timestamp,
            "ProductionState": production_state,
            "CandidateState": self.state,
            "StrictSignal": strict_signal,
            "DefensiveActive": self.defensive_active,
            "Adjusted": adjustment > 0.0,
            "Activated": activated,
            "Deactivated": deactivated,
            "RiskOffScore": self.risk_off_score,
            "RecoveryScore": self.recovery_score,
            "BaselineQQQTarget": float(point["target"][QQQ]),
            "CandidateQQQTarget": float(target[QQQ]),
            "Adjustment": adjustment,
            "TargetDeviation": target_deviation,
            "BaselineRebalance": baseline_rebalance,
            "CandidateBandRebalance": candidate_band_rebalance,
            "OverlayTransition": overlay_transition,
        })
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": 1,
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
        strategy = Strategy15DefensiveCautionOverlay(
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
        cost5 = stress.loc[(name, "COST_5X")]
        delay2 = stress.loc[(name, "DELAY_2D")]
        stress_survival = (
            cost5["CAGRGap"] >= -0.003
            and delay2["CAGRGap"] >= -0.003
            and cost5["MDDImprovement"] >= 0.0
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
            "Cost5xCAGRGap": cost5["CAGRGap"],
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


def run_strategy15_defensive_caution_validation():
    scenarios = [_run_scenario(scenario) for scenario in STRESS_SCENARIOS]
    primary = scenarios[0]
    summary = _performance_report(primary["results"])
    relative = _relative_report(summary)

    rolling5 = _performance_report(
        primary["results"],
        _rolling_windows(primary["results"][0]["history"], years=5),
    )
    rolling5_relative = _relative_report(rolling5)
    rolling3 = _performance_report(
        primary["results"],
        _rolling_windows(primary["results"][0]["history"], years=3),
    )
    rolling3_relative = _relative_report(rolling3)
    episodes, episode_relative, episode_summary = _episode_reports(primary)
    activity_summary = _activity_summary(primary)
    for result in primary["results"][1:]:
        activity = result["activity"]
        activity_summary.loc[
            activity_summary["Strategy"] == result["label"],
            "AdjustedDays",
        ] = int(activity["Adjusted"].sum())
    stress, stress_relative = _stress_reports(scenarios)
    decision = _decision_report(relative, rolling5_relative, stress_relative)

    reports = {
        "strategy15_defensive_caution_summary": summary,
        "strategy15_defensive_caution_relative": relative,
        "strategy15_defensive_caution_rolling5": rolling5,
        "strategy15_defensive_caution_rolling5_relative": rolling5_relative,
        "strategy15_defensive_caution_rolling5_summary": _rolling_summary(
            rolling5_relative
        ),
        "strategy15_defensive_caution_rolling3": rolling3,
        "strategy15_defensive_caution_rolling3_relative": rolling3_relative,
        "strategy15_defensive_caution_rolling3_summary": _rolling_summary(
            rolling3_relative
        ),
        "strategy15_defensive_caution_episodes": episodes,
        "strategy15_defensive_caution_episode_relative": episode_relative,
        "strategy15_defensive_caution_episode_summary": episode_summary,
        "strategy15_defensive_caution_activity_summary": activity_summary,
        "strategy15_defensive_caution_stress": stress,
        "strategy15_defensive_caution_stress_relative": stress_relative,
        "strategy15_defensive_caution_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_strategy15_defensive_caution_validation()
    print(output["strategy15_defensive_caution_summary"].to_string(index=False))
    print("\nRelative to strategy 15")
    print(output["strategy15_defensive_caution_relative"].to_string(index=False))
    print("\nActivity")
    print(
        output["strategy15_defensive_caution_activity_summary"].to_string(
            index=False
        )
    )
    print("\nDecision")
    print(output["strategy15_defensive_caution_decision"].to_string(index=False))
