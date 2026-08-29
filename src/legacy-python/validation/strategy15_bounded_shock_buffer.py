"""Validate two non-sticky QQQ defenses for strategy 15.

The candidates retain strategy 15 as a shadow baseline.  Both move a fixed
amount from the current QQQ holding to BIL, and unwind only that overlay sleeve
without allowing the unwind order to take QQQ above 70%.  The first candidate
waits for one day of downside confirmation.  The second requires the baseline
portfolio itself to have a 20-day drawdown of at least four percent.
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
from validation.strategy15_shock_buffer_overlay import (
    fast_shock_signal,
    recovery_reset_ready,
)


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
QQQ_UNWIND_CAP = 0.70
PORTFOLIO_DRAWDOWN20_GATE = -0.04


@dataclass(frozen=True)
class BoundedBufferProfile:
    name: str
    qqq_adjustment: float = 0.025
    hold_days: int = 5
    confirmation_days: int = 0
    early_recovery_exit: bool = False
    require_portfolio_drawdown: bool = False


PROFILES = (
    BoundedBufferProfile(
        "STRATEGY15_CONFIRMED_PAIR_BUFFER_2P5",
        confirmation_days=1,
        early_recovery_exit=True,
    ),
    BoundedBufferProfile(
        "STRATEGY15_PORTFOLIO_DD_GATE_PAIR_BUFFER_2P5",
        require_portfolio_drawdown=True,
    ),
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


def _qqq_below_ema20(market: Mapping[str, Mapping[str, Any]]) -> bool:
    close = _finite_float(market[QQQ].get("Close"))
    ema20 = _finite_float(market[QQQ].get("EMA20"))
    return bool(close is not None and ema20 is not None and close < ema20)


class BaselineScheduleStrategy(RecordingDeclarativeStrategy):
    """Record causal strategy-15 portfolio value beside its daily schedule."""

    def __init__(self, definition, record, record_value):
        super().__init__(definition, record)
        self._record_value = record_value

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        prices = {
            ticker: market[ticker]["Close"]
            for ticker in self.holding_tickers
        }
        self._record_value(pd.Timestamp(date), portfolio.value(prices))
        return signal


class Strategy15BoundedShockBuffer:
    """A 2.5%p QQQ-to-BIL overlay with capped pair-only unwinds."""

    def __init__(
        self,
        profile: BoundedBufferProfile,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        baseline: DeclarativeStrategy,
        baseline_drawdown20: Mapping[pd.Timestamp, float],
        record,
    ):
        self.profile = profile
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        # The full baseline drawdown map is read-only research input.  Keep it
        # behind a closure so StrategyEngine snapshots do not deepcopy several
        # thousand observations on every trading day.
        self._baseline_dd20_for_date = (
            lambda date: baseline_drawdown20.get(pd.Timestamp(date))
        )
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
        self.armed = True
        self.buffer_active = False
        self.hold_day = 0
        self.entry_adjustment = 0.0
        self.entry_target = None
        self.entry_reference_close = None
        self.pending_reference_close = None

    @staticmethod
    def _copy_weights(current: Mapping[str, float], tickers) -> dict:
        return {ticker: float(current.get(ticker, 0.0)) for ticker in tickers}

    def _entry_target(self, current: Mapping[str, float]) -> tuple[dict, float]:
        """Move exactly the requested percentage points from current QQQ to BIL."""

        target = self._copy_weights(current, self.holding_tickers)
        adjustment = min(
            max(target.get(QQQ, 0.0), 0.0), self.profile.qqq_adjustment
        )
        target[QQQ] = round(target[QQQ] - adjustment, 10)
        target[BIL] = round(target.get(BIL, 0.0) + adjustment, 10)
        return target, adjustment

    def _capped_pair_unwind(
        self,
        current: Mapping[str, float],
    ) -> tuple[dict, float]:
        """Reverse only the overlay sleeve, never buying QQQ past 70%."""

        target = self._copy_weights(current, self.holding_tickers)
        capacity = max(0.0, QQQ_UNWIND_CAP - target.get(QQQ, 0.0))
        adjustment = min(
            self.entry_adjustment,
            max(target.get(BIL, 0.0), 0.0),
            capacity,
        )
        target[QQQ] = round(target[QQQ] + adjustment, 10)
        target[BIL] = round(target[BIL] - adjustment, 10)
        return target, adjustment

    def _baseline_target_with_buffer(self, point: Mapping[str, Any]) -> dict:
        """Let a concurrent production rebalance happen without dropping the sleeve."""

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

    def _raw_signal(self, point, market) -> tuple[bool, bool, bool]:
        strict = defensive_caution_signal(point, market)
        fast = fast_shock_signal(market)
        return strict or fast, strict, fast

    def _portfolio_drawdown20(self, timestamp: pd.Timestamp) -> float | None:
        return _finite_float(self._baseline_dd20_for_date(timestamp))

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
        qqq_close = _finite_float(market[QQQ].get("Close"))
        raw_signal, strict_signal, fast_signal = self._raw_signal(point, market)
        baseline_dd20 = self._portfolio_drawdown20(timestamp)
        baseline_defense = float(production_target.get(QQQ, 0.0)) <= 0.0
        eligible_state = production_state in {"BULL", "CAUTION"}
        drawdown_gate = (
            not self.profile.require_portfolio_drawdown
            or (
                baseline_dd20 is not None
                and baseline_dd20 <= PORTFOLIO_DRAWDOWN20_GATE
            )
        )
        eligible = bool(
            eligible_state
            and not baseline_defense
            and float(current.get(QQQ, 0.0)) > 0.0
            and drawdown_gate
        )
        entry_signal = raw_signal and eligible
        was_active = self.buffer_active
        activated = False
        deactivated = False
        confirmed = False
        exit_reason = None
        unwind_adjustment = 0.0

        if was_active:
            self.hold_day += 1
            if baseline_defense:
                self.buffer_active = False
                deactivated = True
                exit_reason = "STRUCTURAL_DEFENSE"
            elif (
                self.profile.early_recovery_exit
                and qqq_close is not None
                and self.entry_reference_close is not None
                and qqq_close >= self.entry_reference_close
            ):
                self.buffer_active = False
                deactivated = True
                exit_reason = "RECOVERY"
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
            if baseline_defense:
                self.pending_reference_close = None
            elif self.pending_reference_close is not None:
                reference_close = self.pending_reference_close
                self.pending_reference_close = None
                confirmed = bool(
                    self.armed
                    and eligible
                    and qqq_close is not None
                    and qqq_close < reference_close
                    and _qqq_below_ema20(market)
                )
                if confirmed:
                    self.entry_target, self.entry_adjustment = self._entry_target(
                        current
                    )
                    self.buffer_active = self.entry_adjustment > 0.0
                    activated = self.buffer_active
                    if activated:
                        self.armed = False
                        self.hold_day = 1
                        self.entry_reference_close = reference_close
            elif self.armed and entry_signal:
                if self.profile.confirmation_days:
                    self.pending_reference_close = qqq_close
                else:
                    self.entry_target, self.entry_adjustment = self._entry_target(
                        current
                    )
                    self.buffer_active = self.entry_adjustment > 0.0
                    activated = self.buffer_active
                    if activated:
                        self.armed = False
                        self.hold_day = 1
                        self.entry_reference_close = qqq_close

        baseline_rebalance = bool(point["rebalance"])
        if deactivated and exit_reason != "STRUCTURAL_DEFENSE":
            target, unwind_adjustment = self._capped_pair_unwind(current)
        elif self.buffer_active and baseline_rebalance:
            target = self._baseline_target_with_buffer(point)
        elif self.buffer_active:
            target = self.entry_target.copy()
        else:
            target = dict(production_target)

        overlay_transition = activated or deactivated
        needs_unwind_trade = deactivated and unwind_adjustment > 1e-8
        rebalance = baseline_rebalance or activated or needs_unwind_trade
        reason = point["reason"] if baseline_rebalance else None
        if activated:
            reason = (
                "STRATEGY15_BOUNDED_BUFFER_ENTER_CONFIRMED"
                if confirmed
                else "STRATEGY15_BOUNDED_BUFFER_ENTER_DD_GATE"
            )
        elif needs_unwind_trade:
            reason = f"STRATEGY15_BOUNDED_BUFFER_EXIT_{exit_reason}"

        self.state = "BOUNDED_BUFFER" if self.buffer_active else production_state
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
            "RawSignal": raw_signal,
            "PortfolioDD20": baseline_dd20,
            "DrawdownGate": drawdown_gate,
            "Pending": self.pending_reference_close is not None,
            "Confirmed": confirmed,
            "Armed": self.armed,
            "BufferActive": self.buffer_active,
            "HoldDay": self.hold_day,
            "Activated": activated,
            "Deactivated": deactivated,
            "ExitReason": exit_reason,
            "ActualQQQWeight": float(current.get(QQQ, 0.0)),
            "EntryAdjustment": self.entry_adjustment if activated else 0.0,
            "UnwindAdjustment": unwind_adjustment,
            "ResidualBIL": (
                self.entry_adjustment - unwind_adjustment
                if deactivated else 0.0
            ),
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


def _baseline_drawdown20(history: pd.DataFrame) -> dict[pd.Timestamp, float]:
    values = history["Portfolio"]
    drawdown = values / values.rolling(20).max() - 1.0
    return {pd.Timestamp(date): float(value) for date, value in drawdown.items()}


def _run_scenario(scenario: StressScenario):
    definition = load_strategy_definition(SOURCE)
    schedule = {}
    recorder = BaselineScheduleStrategy(
        definition,
        lambda date, point: schedule.__setitem__(date, point),
        lambda _date, _value: None,
    )
    baseline_backtest = Backtest(
        recorder,
        tickers=recorder.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.signal_delay_days,
        start_date=START_DATE,
    )
    history, trades, rebalances = baseline_backtest.run_all()
    baseline_dd20 = _baseline_drawdown20(history)
    results = [{
        "label": "BASELINE",
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }]
    for profile in PROFILES:
        activity = []
        strategy = Strategy15BoundedShockBuffer(
            profile,
            schedule,
            recorder,
            baseline_dd20,
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
            "history": candidate_history,
            "trades": candidate_trades,
            "rebalances": candidate_rebalances,
            "activity": pd.DataFrame(activity),
        })
    return {"scenario": scenario, "results": results, "schedule": schedule}


def _activity_summary(primary):
    rows = []
    for result in primary["results"][1:]:
        activity = result["activity"]
        entries = activity.loc[activity["Activated"]]
        exits = activity.loc[activity["Deactivated"]]
        rows.append({
            "Strategy": result["label"],
            "Observations": len(activity),
            "RawSignalDays": int(activity["RawSignal"].sum()),
            "PendingDays": int(activity["Pending"].sum()),
            "ConfirmedDays": int(activity["Confirmed"].sum()),
            "PortfolioDDGateDays": int(activity["DrawdownGate"].sum()),
            "BufferDays": int(activity["BufferActive"].sum()),
            "Activations": int(activity["Activated"].sum()),
            "Deactivations": int(activity["Deactivated"].sum()),
            "RecoveryExits": int((activity["ExitReason"] == "RECOVERY").sum()),
            "TTLExits": int((activity["ExitReason"] == "TTL").sum()),
            "AverageEntryQQQWeight": entries["ActualQQQWeight"].mean(),
            "AverageEntryAdjustment": entries["EntryAdjustment"].mean(),
            "AverageUnwindAdjustment": exits["UnwindAdjustment"].mean(),
            "ResidualBILAtExit": exits["ResidualBIL"].sum(),
        })
    return pd.DataFrame(rows)


def _period_metrics(history: pd.DataFrame, start: str, end: str):
    sample = history.loc[pd.Timestamp(start):pd.Timestamp(end)]
    if len(sample) < 2:
        return None
    values = sample["Portfolio"]
    return {
        "Return": float(values.iloc[-1] / values.iloc[0] - 1.0),
        "MDD": float((values / values.iloc[0] - 1.0).min()),
    }


def _event_reports(primary):
    rows = []
    baseline = primary["results"][0]
    for name, start, end in EVENT_WINDOWS:
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
            entries = activity.loc[activity["Activated"]]
            rows.append({
                "Event": name,
                "StartDate": start,
                "EndDate": end,
                "Strategy": result["label"],
                "ActivationDate": (
                    entries.iloc[0]["Date"] if not entries.empty else pd.NaT
                ),
                "ActivationCount": int(activity["Activated"].sum()),
                "BufferDays": int(activity["BufferActive"].sum()),
                "ReturnGap": candidate_metrics["Return"] - baseline_metrics["Return"],
                "MDDImprovement": candidate_metrics["MDD"] - baseline_metrics["MDD"],
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


def _decision_report(relative, rolling_relative, stress_relative, activity, events):
    indexed = relative.set_index(["Strategy", "Window"])
    stress = stress_relative.set_index(["Strategy", "Scenario"])
    activity_indexed = activity.set_index("Strategy")
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
        activation_count = int(activity_indexed.loc[name, "Activations"])
        rows.append({
            "Strategy": name,
            "Activations": activation_count,
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
                activation_count <= 30
                and full["CAGRGap"] >= -0.0015
                and full["MDDImprovement"] >= 0.0
                and full["CalmarGap"] >= 0.0
                and development["MDDImprovement"] >= 0.0
                and recent["MDDImprovement"] >= 0.0
                and int((event_rows["MDDImprovement"] > 0.0).sum()) >= 3
                and float((rolling["MDDImprovement"] > 0.0).mean()) >= 0.5
                and cost5["CAGRGap"] >= -0.0015
                and cost5["MDDImprovement"] >= 0.0
                and delay2["CAGRGap"] >= -0.0015
                and delay2["MDDImprovement"] >= 0.0
            ),
        })
    return pd.DataFrame(rows)


def run_strategy15_bounded_shock_buffer_validation():
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
    decision = _decision_report(
        relative, rolling5_relative, stress_relative, activity, events
    )
    reports = {
        "strategy15_bounded_buffer_summary": summary,
        "strategy15_bounded_buffer_relative": relative,
        "strategy15_bounded_buffer_rolling5": rolling5,
        "strategy15_bounded_buffer_rolling5_relative": rolling5_relative,
        "strategy15_bounded_buffer_rolling5_summary": _rolling_summary(
            rolling5_relative
        ),
        "strategy15_bounded_buffer_rolling3": rolling3,
        "strategy15_bounded_buffer_rolling3_relative": rolling3_relative,
        "strategy15_bounded_buffer_rolling3_summary": _rolling_summary(
            rolling3_relative
        ),
        "strategy15_bounded_buffer_activity": activity,
        "strategy15_bounded_buffer_events": events,
        "strategy15_bounded_buffer_stress": stress,
        "strategy15_bounded_buffer_stress_relative": stress_relative,
        "strategy15_bounded_buffer_decision": decision,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_strategy15_bounded_shock_buffer_validation()
    print(output["strategy15_bounded_buffer_summary"].to_string(index=False))
    print("\nRelative to strategy 15")
    print(output["strategy15_bounded_buffer_relative"].to_string(index=False))
    print("\nActivity")
    print(output["strategy15_bounded_buffer_activity"].to_string(index=False))
    print("\nEvent results")
    print(output["strategy15_bounded_buffer_events"].to_string(index=False))
    print("\nDecision")
    print(output["strategy15_bounded_buffer_decision"].to_string(index=False))
