"""Validate a short-lived QQQ shock buffer on top of strategy 15.

This is deliberately a shadow portfolio, not a production-rule change.  The
overlay sells a fixed amount of the *current* QQQ holding into BIL when either
the existing strict CAUTION signal fires or a fast QQQ shock is detected.  It
then restores the production target after a fixed five-trading-day holding
period.  Re-arming requires both a BULL return and a recovery reset, so one
CAUTION episode cannot repeatedly fire the buffer.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.defensive_caution_overlay import (
    START_DATE,
    _rolling_windows,
    defensive_caution_signal,
)
from validation.state_conditioned_continuous_overlay import (
    BIL,
    QQQ,
    RecordingDeclarativeStrategy,
    StressScenario,
    WINDOWS,
    _performance_report,
    _relative_report,
)


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
FAST_SHOCK_DRAWDOWN20 = -0.05
FAST_SHOCK_ROC5 = -4.0
REARM_DRAWDOWN20 = -0.03


@dataclass(frozen=True)
class ShockBufferProfile:
    name: str
    qqq_adjustment: float
    hold_days: int = 5


PROFILES = (
    ShockBufferProfile("STRATEGY15_SHOCK_BUFFER_2P5_5D", 0.025),
    ShockBufferProfile("STRATEGY15_SHOCK_BUFFER_5P0_5D", 0.05),
)


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("COST_5X", cost_multiple=5.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
    StressScenario("DELAY_2D", signal_delay_days=2),
)


EVENT_WINDOWS = (
    ("COVID_2020", "2020-02-19", "2020-03-23"),
    ("TARIFF_2025", "2025-02-19", "2025-04-08"),
    ("Q1_2026", "2026-01-28", "2026-03-30"),
    ("SUMMER_2026", "2026-06-02", "2026-07-29"),
)


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if isfinite(number) else None


def fast_shock_signal(market: Mapping[str, Mapping[str, Any]]) -> bool:
    """Return the pre-registered exception for a fast QQQ selloff.

    The strict CAUTION signal misses a shock that begins before the production
    state can move out of BULL.  This exception is intentionally limited to a
    five-day buffer, rather than creating a second persistent regime.
    """

    qqq = market[QQQ]
    drawdown20 = _finite_float(qqq.get("DRAWDOWN20"))
    roc5 = _finite_float(qqq.get("ROC5"))
    return bool(
        drawdown20 is not None
        and roc5 is not None
        and drawdown20 <= FAST_SHOCK_DRAWDOWN20
        and roc5 <= FAST_SHOCK_ROC5
    )


def recovery_reset_ready(market: Mapping[str, Mapping[str, Any]]) -> bool:
    """Require a shallow drawdown and a close above EMA20 before re-arming."""

    qqq = market[QQQ]
    close = _finite_float(qqq.get("Close"))
    ema20 = _finite_float(qqq.get("EMA20"))
    drawdown20 = _finite_float(qqq.get("DRAWDOWN20"))
    return bool(
        close is not None
        and ema20 is not None
        and drawdown20 is not None
        and close > ema20
        and drawdown20 > REARM_DRAWDOWN20
    )


class Strategy15ShockBufferOverlay:
    """Sell actual QQQ exposure for one bounded, non-sticky shock buffer."""

    def __init__(
        self,
        profile: ShockBufferProfile,
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
        self.required_market_fields = {
            ticker: tuple(fields)
            for ticker, fields in baseline.required_market_fields.items()
        }
        qqq_fields = self.required_market_fields.get(QQQ, ())
        self.required_market_fields[QQQ] = tuple(
            dict.fromkeys((*qqq_fields, "DRAWDOWN20"))
        )
        self.risk_asset_tickers = baseline.risk_asset_tickers

        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.buffer_active = False
        self.armed = True
        self.hold_day = 0
        self.entry_target = None
        self.entry_adjustment = 0.0

    def _entry_target(self, current: Mapping[str, float]) -> tuple[dict, float]:
        """Sell the requested percentage points from the actual QQQ weight."""

        target = {
            ticker: float(current.get(ticker, 0.0))
            for ticker in self.holding_tickers
        }
        adjustment = min(
            max(target.get(QQQ, 0.0), 0.0),
            self.profile.qqq_adjustment,
        )
        target[QQQ] = round(target[QQQ] - adjustment, 10)
        target[BIL] = round(target.get(BIL, 0.0) + adjustment, 10)
        return target, adjustment

    def _baseline_target_with_buffer(self, point: Mapping[str, Any]) -> dict:
        """Keep a simultaneous baseline rebalance while retaining the buffer."""

        target = {
            ticker: float(weight)
            for ticker, weight in point["target"].items()
        }
        adjustment = min(
            max(target.get(QQQ, 0.0), 0.0), self.entry_adjustment
        )
        target[QQQ] = round(target[QQQ] - adjustment, 10)
        target[BIL] = round(target.get(BIL, 0.0) + adjustment, 10)
        return target

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        production_state = point["state"]
        production_target = point["target"]
        prices = {
            ticker: market[ticker]["Close"]
            for ticker in self.holding_tickers
        }
        current = portfolio.weights(prices)
        strict_signal = defensive_caution_signal(point, market)
        fast_signal = fast_shock_signal(market)
        baseline_defense = float(production_target.get(QQQ, 0.0)) <= 0.0
        eligible_state = production_state in {"BULL", "CAUTION"}
        entry_signal = (
            eligible_state
            and not baseline_defense
            and (strict_signal or fast_signal)
            and float(current.get(QQQ, 0.0)) > 0.0
        )
        was_active = self.buffer_active
        activated = False
        deactivated = False
        exit_reason = None

        if was_active:
            self.hold_day += 1
            if baseline_defense:
                self.buffer_active = False
                deactivated = True
                exit_reason = "STRUCTURAL_DEFENSE"
            elif self.hold_day > self.profile.hold_days:
                self.buffer_active = False
                deactivated = True
                exit_reason = "TTL"
        else:
            if (
                production_state == "BULL"
                and recovery_reset_ready(market)
            ):
                self.armed = True
            if self.armed and entry_signal:
                self.entry_target, self.entry_adjustment = self._entry_target(
                    current
                )
                self.buffer_active = self.entry_adjustment > 0.0
                activated = self.buffer_active
                if activated:
                    self.armed = False
                    self.hold_day = 1

        baseline_rebalance = bool(point["rebalance"])
        if self.buffer_active and baseline_rebalance:
            target = self._baseline_target_with_buffer(point)
        elif self.buffer_active:
            target = self.entry_target.copy()
        else:
            target = dict(production_target)

        overlay_transition = activated or deactivated
        rebalance = baseline_rebalance or overlay_transition
        reason = point["reason"] if baseline_rebalance else None
        if activated:
            signal_kind = "STRICT" if strict_signal else "FAST_SHOCK"
            reason = f"STRATEGY15_SHOCK_BUFFER_ENTER_{signal_kind}"
        elif deactivated:
            reason = f"STRATEGY15_SHOCK_BUFFER_EXIT_{exit_reason}"

        self.state = (
            "SHOCK_BUFFER"
            if self.buffer_active
            else production_state
        )
        self.target = target
        self.risk_off_score = point["risk_off_score"]
        self.recovery_score = point["recovery_score"]
        self.safe_asset = BIL if target.get(BIL, 0.0) > 0.0 else None
        self._record({
            "Date": timestamp,
            "ProductionState": production_state,
            "CandidateState": self.state,
            "StrictSignal": strict_signal,
            "FastShockSignal": fast_signal,
            "EntrySignal": entry_signal,
            "Armed": self.armed,
            "BufferActive": self.buffer_active,
            "HoldDay": self.hold_day,
            "Activated": activated,
            "Deactivated": deactivated,
            "ExitReason": exit_reason,
            "ActualQQQWeight": float(current.get(QQQ, 0.0)),
            "EntryAdjustment": self.entry_adjustment if activated else 0.0,
            "BaselineQQQTarget": float(production_target.get(QQQ, 0.0)),
            "CandidateQQQTarget": float(target.get(QQQ, 0.0)),
            "BaselineRebalance": baseline_rebalance,
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
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }]
    for profile in PROFILES:
        activity = []
        strategy = Strategy15ShockBufferOverlay(
            profile, schedule, recorder, activity.append
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
            "history": candidate_history,
            "trades": candidate_trades,
            "rebalances": candidate_rebalances,
            "activity": pd.DataFrame(activity),
        })
    return {
        "scenario": scenario,
        "results": results,
        "schedule": schedule,
    }


def _activity_summary(primary):
    rows = []
    for result in primary["results"][1:]:
        activity = result["activity"]
        entries = activity.loc[activity["Activated"]]
        rows.append({
            "Strategy": result["label"],
            "Observations": len(activity),
            "StrictSignalDays": int(activity["StrictSignal"].sum()),
            "FastShockSignalDays": int(activity["FastShockSignal"].sum()),
            "BufferDays": int(activity["BufferActive"].sum()),
            "Activations": int(activity["Activated"].sum()),
            "Deactivations": int(activity["Deactivated"].sum()),
            "TTLExits": int((activity["ExitReason"] == "TTL").sum()),
            "AverageEntryQQQWeight": entries["ActualQQQWeight"].mean(),
            "AverageActualAdjustment": entries["EntryAdjustment"].mean(),
        })
    return pd.DataFrame(rows)


def _period_metrics(history: pd.DataFrame, start: str, end: str):
    sample = history.loc[pd.Timestamp(start):pd.Timestamp(end)]
    if len(sample) < 2:
        return None
    values = sample["Portfolio"]
    return {
        "StartValue": float(values.iloc[0]),
        "EndValue": float(values.iloc[-1]),
        "Return": float(values.iloc[-1] / values.iloc[0] - 1.0),
        "MDD": float((values / values.iloc[0] - 1.0).min()),
    }


def _event_reports(primary):
    rows = []
    for name, start, end in EVENT_WINDOWS:
        baseline = primary["results"][0]
        baseline_metrics = _period_metrics(baseline["history"], start, end)
        if baseline_metrics is None:
            continue
        for result in primary["results"][1:]:
            candidate_metrics = _period_metrics(result["history"], start, end)
            activity = result["activity"]
            activity = activity.loc[
                (activity["Date"] >= pd.Timestamp(start))
                & (activity["Date"] <= pd.Timestamp(end))
            ]
            activations = activity.loc[activity["Activated"]]
            rows.append({
                "Event": name,
                "StartDate": start,
                "EndDate": end,
                "Strategy": result["label"],
                "ActivationDate": (
                    activations.iloc[0]["Date"]
                    if not activations.empty else pd.NaT
                ),
                "ActivationCount": int(activity["Activated"].sum()),
                "BufferDays": int(activity["BufferActive"].sum()),
                "ReturnGap": candidate_metrics["Return"] - baseline_metrics["Return"],
                "MDDImprovement": (
                    candidate_metrics["MDD"] - baseline_metrics["MDD"]
                ),
            })
    return pd.DataFrame(rows)


def _stress_reports(scenarios):
    reports = []
    for scenario in scenarios:
        report = _performance_report(
            scenario["results"], {"FULL": (None, None)}
        )
        report.insert(0, "Scenario", scenario["scenario"].name)
        reports.append(report)
    report = pd.concat(reports, ignore_index=True)
    return report, _relative_report(report, keys=("Scenario", "Window"))


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


def _decision_report(relative, rolling_relative, stress_relative, events):
    indexed = relative.set_index(["Strategy", "Window"])
    stress = stress_relative.set_index(["Strategy", "Scenario"])
    rows = []
    for profile in PROFILES:
        name = profile.name
        full = indexed.loc[(name, "FULL")]
        development = indexed.loc[(name, "DEVELOPMENT_PRE2021")]
        recent = indexed.loc[(name, "RECENT_2021_PRESENT")]
        rolling = rolling_relative.loc[rolling_relative["Strategy"] == name]
        cost5 = stress.loc[(name, "COST_5X")]
        delay2 = stress.loc[(name, "DELAY_2D")]
        event_rows = events.loc[events["Strategy"] == name]
        rows.append({
            "Strategy": name,
            "FullCAGRGap": full["CAGRGap"],
            "FullMDDImprovement": full["MDDImprovement"],
            "FullCalmarGap": full["CalmarGap"],
            "DevelopmentMDDImprovement": development["MDDImprovement"],
            "RecentMDDImprovement": recent["MDDImprovement"],
            "RollingMDDPositiveShare": float(
                (rolling["MDDImprovement"] > 0.0).mean()
            ),
            "EventMDDPositiveCount": int(
                (event_rows["MDDImprovement"] > 0.0).sum()
            ),
            "Cost5xCAGRGap": cost5["CAGRGap"],
            "Cost5xMDDImprovement": cost5["MDDImprovement"],
            "Delay2dCAGRGap": delay2["CAGRGap"],
            "Delay2dMDDImprovement": delay2["MDDImprovement"],
            "EfficiencyPass": bool(
                full["CAGRGap"] >= -0.0015
                and full["MDDImprovement"] >= 0.003
                and full["CalmarGap"] >= 0.0
                and int((event_rows["MDDImprovement"] > 0.0).sum()) >= 3
                and float((rolling["MDDImprovement"] > 0.0).mean()) >= 0.5
                and cost5["CAGRGap"] >= -0.0015
                and cost5["MDDImprovement"] >= 0.0
                and delay2["CAGRGap"] >= -0.0015
                and delay2["MDDImprovement"] >= 0.0
            ),
        })
    return pd.DataFrame(rows)


def run_strategy15_shock_buffer_validation():
    scenarios = [_run_scenario(scenario) for scenario in STRESS_SCENARIOS]
    primary = scenarios[0]
    summary = _performance_report(primary["results"], WINDOWS)
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
    activity = _activity_summary(primary)
    events = _event_reports(primary)
    stress, stress_relative = _stress_reports(scenarios)
    decision = _decision_report(relative, rolling5_relative, stress_relative, events)

    reports = {
        "strategy15_shock_buffer_summary": summary,
        "strategy15_shock_buffer_relative": relative,
        "strategy15_shock_buffer_rolling5": rolling5,
        "strategy15_shock_buffer_rolling5_relative": rolling5_relative,
        "strategy15_shock_buffer_rolling5_summary": _rolling_summary(
            rolling5_relative
        ),
        "strategy15_shock_buffer_rolling3": rolling3,
        "strategy15_shock_buffer_rolling3_relative": rolling3_relative,
        "strategy15_shock_buffer_rolling3_summary": _rolling_summary(
            rolling3_relative
        ),
        "strategy15_shock_buffer_activity": activity,
        "strategy15_shock_buffer_events": events,
        "strategy15_shock_buffer_stress": stress,
        "strategy15_shock_buffer_stress_relative": stress_relative,
        "strategy15_shock_buffer_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_strategy15_shock_buffer_validation()
    print(output["strategy15_shock_buffer_summary"].to_string(index=False))
    print("\nRelative to strategy 15")
    print(output["strategy15_shock_buffer_relative"].to_string(index=False))
    print("\nActivity")
    print(output["strategy15_shock_buffer_activity"].to_string(index=False))
    print("\nEvent results")
    print(output["strategy15_shock_buffer_events"].to_string(index=False))
    print("\nDecision")
    print(output["strategy15_shock_buffer_decision"].to_string(index=False))
