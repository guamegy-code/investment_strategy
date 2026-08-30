"""Path-isolated GLD overlay experiments for strategy 15.

The production strategy 15 path is recorded first.  Candidate overlays then
use that frozen state/target/rebalance schedule.  On an overlay-only state
change, the candidate transfers only QQQ <-> GLD and leaves the other sleeves
at their current weights.  This prevents a small overlay from accidentally
resetting strategy 15's 7.5% band path.

This module is research-only; it does not alter a production YAML strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "15_band_7030_tdf_state_bil.yaml"
START_DATE = "2013-01-04"
QQQ = "QQQ"
TDF = "TDF2050_PROXY"
BIL = "BIL"
GLD = "GLD"
REBALANCE_BAND = 0.075


@dataclass(frozen=True)
class OverlayProfile:
    name: str
    mode: str
    weight: float = 0.0
    confirmation_days: int = 5
    minimum_hold_days: int = 20


PROFILES = (
    OverlayProfile("BASELINE", "none"),
    OverlayProfile("STATIC_GLD_2_5", "static", 0.025),
    OverlayProfile("STATIC_GLD_5", "static", 0.05),
    OverlayProfile("CONDITIONAL_GLD_2_5", "conditional", 0.025),
    OverlayProfile("CONDITIONAL_GLD_5", "conditional", 0.05),
)


@dataclass(frozen=True)
class StressScenario:
    name: str
    cost_multiple: float = 1.0
    signal_delay_days: int = 0


STRESS_SCENARIOS = (
    StressScenario("BASE_1X_DELAY0"),
    StressScenario("COST_3X", cost_multiple=3.0),
    StressScenario("DELAY_1D", signal_delay_days=1),
)


def _configure_krw_local_signals(strategy: DeclarativeStrategy) -> DeclarativeStrategy:
    """Value foreign holdings in KRW while retaining local-price signals."""

    strategy.foreign_asset_tickers = tuple(strategy.holding_tickers)
    strategy.valuation_currency = "KRW"
    strategy.valuation_fx_ticker = "KRW=X"
    strategy.valuation_signal_currency = "LOCAL"
    strategy.required_tickers = tuple(
        dict.fromkeys((*strategy.required_tickers, "KRW=X"))
    )
    return strategy


class RecordingStrategy(DeclarativeStrategy):
    """Record production decisions without storing a growing runtime state."""

    def __init__(self, definition: Mapping[str, Any], record):
        super().__init__(definition)
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
        })
        return signal


def _field(market: Mapping[str, Any], ticker: str, name: str) -> float | None:
    value = market.get(ticker, {}).get(name)
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if pd.notna(value) else None


def conditional_gld_signal(market: Mapping[str, Any]) -> bool:
    """The pre-registered 2-of-3 local-price GLD relative-strength signal."""

    gld = market.get(GLD, {})
    qqq = market.get(QQQ, {})
    close = _field(market, GLD, "Close")
    ema200 = _field(market, GLD, "EMA200")
    if close is None or ema200 is None or close <= ema200:
        return False
    comparisons = []
    for horizon in ("ROC60", "ROC120", "ROC252"):
        gold = _field(market, GLD, horizon)
        equity = _field(market, QQQ, horizon)
        if gold is None or equity is None:
            return False
        comparisons.append(gold > equity)
    return sum(comparisons) >= 2


def _target_with_overlay(
    baseline_target: Mapping[str, float],
    active: bool,
    weight: float,
) -> dict[str, float]:
    target = dict(baseline_target)
    target[GLD] = 0.0
    if active and float(target.get(QQQ, 0.0)) > 0.0:
        moved = min(float(target[QQQ]), float(weight))
        target[QQQ] = round(float(target[QQQ]) - moved, 10)
        target[GLD] = round(moved, 10)
    return target


def _transfer_current_weights(
    current: Mapping[str, float], *, to_gld: bool, weight: float
) -> dict[str, float]:
    """Transfer only the overlay delta, preserving every other sleeve."""

    target = {str(ticker): float(value) for ticker, value in current.items()}
    target.setdefault(GLD, 0.0)
    target.setdefault(QQQ, 0.0)
    if to_gld:
        moved = min(float(weight), max(0.0, target[QQQ]))
        target[QQQ] -= moved
        target[GLD] += moved
    else:
        moved = min(float(weight), max(0.0, target[GLD]))
        target[GLD] -= moved
        target[QQQ] += moved
    return {
        ticker: round(max(0.0, value), 10)
        for ticker, value in target.items()
    }


class IsolatedGoldOverlayStrategy:
    """Apply a static or confirmed GLD sleeve to a frozen strategy-15 path."""

    def __init__(
        self,
        profile: OverlayProfile,
        schedule: Mapping[pd.Timestamp, Mapping[str, Any]],
        baseline: DeclarativeStrategy,
        record,
    ):
        self.profile = profile
        self._point_for_date = lambda date: schedule[pd.Timestamp(date)]
        self._record = record
        self.strategy_id = f"poc:isolated-{profile.name.casefold()}"
        self.display_name = profile.name
        self.STRATEGY_VERSION = "1"
        self.holding_tickers = (*baseline.holding_tickers, GLD)
        self.observation_tickers = baseline.observation_tickers
        self.required_tickers = tuple(
            dict.fromkeys((*self.holding_tickers, *self.observation_tickers, "KRW=X"))
        )
        fields = {
            ticker: tuple(values)
            for ticker, values in baseline.required_market_fields.items()
        }
        fields[GLD] = tuple(dict.fromkeys(
            (*fields.get(GLD, ()), "CLOSE", "EMA200", "ROC60", "ROC120", "ROC252")
        ))
        self.required_market_fields = fields
        self.foreign_asset_tickers = tuple(baseline.holding_tickers) + (GLD,)
        self.valuation_currency = "KRW"
        self.valuation_fx_ticker = "KRW=X"
        self.valuation_signal_currency = "LOCAL"
        self.risk_asset_tickers = baseline.risk_asset_tickers

        self.state = None
        self.target = None
        self.risk_off_score = None
        self.recovery_score = None
        self.safe_asset = None
        self.overlay_active = False
        self._signal_streak = 0
        self._exit_streak = 0
        self._hold_days = 0

    def _update_state(self, point, market) -> tuple[bool, bool, bool]:
        previous = self.overlay_active
        base_allows = float(point["target"].get(QQQ, 0.0)) > 0.0
        if self.profile.mode == "none":
            self.overlay_active = False
        elif self.profile.mode == "static":
            self.overlay_active = base_allows
        else:
            signal = base_allows and conditional_gld_signal(market)
            if not base_allows:
                self.overlay_active = False
                self._signal_streak = 0
                self._exit_streak = 0
                self._hold_days = 0
            elif not self.overlay_active:
                self._signal_streak = self._signal_streak + 1 if signal else 0
                if self._signal_streak >= self.profile.confirmation_days:
                    self.overlay_active = True
                    self._hold_days = 0
                    self._exit_streak = 0
            else:
                self._hold_days += 1
                self._exit_streak = (
                    self._exit_streak + 1 if not signal else 0
                )
                if (
                    self._hold_days >= self.profile.minimum_hold_days
                    and self._exit_streak >= self.profile.confirmation_days
                ):
                    self.overlay_active = False
                    self._signal_streak = 0
                    self._exit_streak = 0
                    self._hold_days = 0
        return previous, self.overlay_active, previous != self.overlay_active

    def evaluate(self, date, market, portfolio):
        timestamp = pd.Timestamp(date)
        point = self._point_for_date(timestamp)
        previous, active, transition = self._update_state(point, market)
        baseline_target = dict(point["target"])
        target = _target_with_overlay(
            baseline_target,
            active,
            self.profile.weight,
        )
        baseline_rebalance = bool(point["rebalance"])
        reason = point.get("reason") if baseline_rebalance else None

        prices = {
            ticker: market[ticker]["Close"]
            for ticker in self.holding_tickers
        }
        current = portfolio.weights(prices)
        portfolio_empty = sum(
            max(0.0, float(value)) for value in current.values()
        ) <= 1e-10

        # On a signal-only transition, preserve strategy 15's actual path and
        # trade only the QQQ/GLD delta.  A baseline event still uses its frozen
        # target, with the overlay applied on top.
        if portfolio_empty:
            # Backtest anchors can start after the production strategy's first
            # rebalance.  Always seed the candidate with the frozen target so
            # an overlay does not leave the portfolio in cash indefinitely.
            reason = f"{self.profile.name}_INITIAL"
        elif transition and not baseline_rebalance:
            target = _transfer_current_weights(
                current,
                to_gld=active,
                weight=self.profile.weight,
            )
            reason = (
                f"{self.profile.name}_ENTER"
                if active
                else f"{self.profile.name}_EXIT"
            )

        self.state = point["state"]
        self.target = target
        self.risk_off_score = point.get("risk_off_score")
        self.recovery_score = point.get("recovery_score")
        self.safe_asset = BIL if target.get(BIL, 0.0) > 0.0 else TDF
        self._record({
            "Date": timestamp,
            "ProductionState": point["state"],
            "OverlayActive": active,
            "OverlayTransition": transition,
            "BaselineRebalance": baseline_rebalance,
            "BaselineQQQTarget": float(baseline_target.get(QQQ, 0.0)),
            "CandidateQQQTarget": float(target.get(QQQ, 0.0)),
            "CandidateGLDTarget": float(target.get(GLD, 0.0)),
            "HoldDays": self._hold_days,
        })
        return {
            "rebalance": (
                baseline_rebalance
                or portfolio_empty
                or (transition and self.profile.mode != "none")
            ),
            "target": target.copy(),
            "days": int(point["days"]),
            "reason": reason,
        }


def _run_scenario(scenario: StressScenario):
    definition = load_strategy_definition(SOURCE)
    schedule: dict[pd.Timestamp, Mapping[str, Any]] = {}
    recorder = RecordingStrategy(definition, lambda date, point: schedule.__setitem__(date, point))
    _configure_krw_local_signals(recorder)
    baseline_backtest = Backtest(
        recorder,
        tickers=recorder.required_tickers,
        commission=COMMISSION * scenario.cost_multiple,
        slippage=SLIPPAGE * scenario.cost_multiple,
        signal_delay_days=scenario.signal_delay_days,
        start_date=START_DATE,
    )
    baseline_history, baseline_trades, baseline_rebalances = baseline_backtest.run_all()
    results = [{
        "profile": PROFILES[0],
        "history": baseline_history,
        "trades": baseline_trades,
        "rebalances": baseline_rebalances,
        "activity": pd.DataFrame(),
    }]
    for profile in PROFILES[1:]:
        activity = []
        candidate = IsolatedGoldOverlayStrategy(
            profile,
            schedule,
            recorder,
            activity.append,
        )
        backtest = Backtest(
            candidate,
            tickers=candidate.required_tickers,
            commission=COMMISSION * scenario.cost_multiple,
            slippage=SLIPPAGE * scenario.cost_multiple,
            signal_delay_days=scenario.signal_delay_days,
            start_date=START_DATE,
        )
        history, trades, rebalances = backtest.run_all()
        results.append({
            "profile": profile,
            "history": history,
            "trades": trades,
            "rebalances": rebalances,
            "activity": pd.DataFrame(activity),
        })
    return results


def _metrics(history: pd.DataFrame) -> dict[str, float]:
    summary = Performance(history).summary()
    return {
        key: float(summary[key])
        for key in ("CAGR", "MDD", "Volatility", "Sharpe", "Calmar", "TransactionCosts")
    }


def _event_attribution(
    baseline_history: pd.DataFrame,
    candidate_history: pd.DataFrame,
    activity: pd.DataFrame,
    scenario: str,
    strategy: str,
) -> list[dict[str, Any]]:
    """Attribute each active overlay episode to its realized path return."""

    if activity.empty:
        return []
    activity = activity.copy()
    activity["Date"] = pd.to_datetime(activity["Date"])
    activity = activity.sort_values("Date").set_index("Date")
    active = activity["OverlayActive"].astype(bool)
    starts = list(activity.index[active & ~active.shift(1, fill_value=False)])
    ends = list(activity.index[~active & active.shift(1, fill_value=False)])
    rows = []
    for ordinal, start in enumerate(starts, 1):
        future_ends = [date for date in ends if date > start]
        end = future_ends[0] if future_ends else candidate_history.index.max()
        common = baseline_history.index.intersection(candidate_history.index)
        window = common[(common >= start) & (common <= end)]
        if len(window) < 2:
            continue
        base_start = float(baseline_history.loc[window[0], "Portfolio"])
        cand_start = float(candidate_history.loc[window[0], "Portfolio"])
        base_end = float(baseline_history.loc[window[-1], "Portfolio"])
        cand_end = float(candidate_history.loc[window[-1], "Portfolio"])
        base_return = base_end / base_start - 1.0
        candidate_return = cand_end / cand_start - 1.0
        rows.append({
            "Scenario": scenario,
            "Strategy": strategy,
            "Episode": ordinal,
            "StartDate": window[0],
            "EndDate": window[-1],
            "Observations": len(window),
            "BaselineReturn": base_return,
            "CandidateReturn": candidate_return,
            "ReturnGap": candidate_return - base_return,
        })
    return rows


def run_isolated_gold_overlay_validation() -> dict[str, pd.DataFrame]:
    summary_rows = []
    relative_rows = []
    activity_rows = []
    event_rows = []
    stress_rows = []
    for scenario in STRESS_SCENARIOS:
        results = _run_scenario(scenario)
        baseline = results[0]
        base_metrics = _metrics(baseline["history"])
        for result in results:
            profile = result["profile"]
            values = _metrics(result["history"])
            summary_rows.append({
                "Scenario": scenario.name,
                "Strategy": profile.name,
                **values,
                "Rebalances": len(result["rebalances"]),
                "Trades": len(result["trades"]),
            })
            if profile.name != "BASELINE":
                relative_rows.append({
                    "Scenario": scenario.name,
                    "Strategy": profile.name,
                    "CAGRGap": values["CAGR"] - base_metrics["CAGR"],
                    "MDDImprovement": values["MDD"] - base_metrics["MDD"],
                    "SharpeGap": values["Sharpe"] - base_metrics["Sharpe"],
                    "CalmarGap": values["Calmar"] - base_metrics["Calmar"],
                    "TransactionCostGap": values["TransactionCosts"] - base_metrics["TransactionCosts"],
                    "RebalanceGap": len(result["rebalances"]) - len(baseline["rebalances"]),
                    "TradeGap": len(result["trades"]) - len(baseline["trades"]),
                })
                activity = result["activity"]
                if not activity.empty:
                    activity["Date"] = pd.to_datetime(activity["Date"])
                    activity = activity.set_index("Date")
                    active = activity["OverlayActive"].astype(bool)
                    starts = active & ~active.shift(1, fill_value=False)
                    ends = ~active & active.shift(1, fill_value=False)
                    activity_rows.extend(
                        {
                            "Scenario": scenario.name,
                            "Strategy": profile.name,
                            "Date": date,
                            "Event": "ENTER" if starts.loc[date] else "EXIT",
                        }
                        for date in activity.index[starts | ends]
                    )
                    event_rows.extend(_event_attribution(
                        baseline["history"],
                        result["history"],
                        activity.reset_index(),
                        scenario.name,
                        profile.name,
                    ))
        if scenario.name != "BASE_1X_DELAY0":
            for row in relative_rows:
                if row["Scenario"] == scenario.name:
                    stress_rows.append(row.copy())

    reports = {
        "strategy15_isolated_gold_summary": pd.DataFrame(summary_rows),
        "strategy15_isolated_gold_relative": pd.DataFrame(relative_rows),
        "strategy15_isolated_gold_activity": pd.DataFrame(activity_rows),
        "strategy15_isolated_gold_event_attribution": pd.DataFrame(event_rows),
        "strategy15_isolated_gold_stress": pd.DataFrame(stress_rows),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_isolated_gold_overlay_validation()
    print(output["strategy15_isolated_gold_summary"].to_string(index=False))
    print("\nRelative")
    print(output["strategy15_isolated_gold_relative"].to_string(index=False))
