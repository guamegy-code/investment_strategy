"""Validate a pure one-month left-tail probability overlay."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import BaseStrategy, STATIC_RETIREMENT_7030
from .multi_horizon_probability import (
    TAIL_CONFIG,
    TAIL_DEADBAND,
    TAIL_FULL_STRESS_GAP,
    TAIL_RETURN_THRESHOLD,
    _run_composite,
    _run_direction,
    _tail_diagnostics,
)
from .probabilistic_allocation import (
    DEFAULT_CONFIG,
    _summary,
    _window_summary,
    walk_forward_event_probabilities,
)


@dataclass(frozen=True)
class TailOverlayProfile:
    name: str
    minimum_risk_weight: float


PROFILES = (
    TailOverlayProfile("TAIL_OVERLAY_FLOOR_50", 0.50),
    TailOverlayProfile("TAIL_OVERLAY_FLOOR_40", 0.40),
    TailOverlayProfile("TAIL_OVERLAY_FLOOR_30", 0.30),
)


class TailRiskFeatureBacktest(Backtest):
    """Attach only the causal 21-day probability of a loss beyond 5%."""

    def load_data(self):
        data = super().load_data()
        tail = walk_forward_event_probabilities(
            data,
            TAIL_CONFIG,
            event="DOWNSIDE",
            threshold=TAIL_RETURN_THRESHOLD,
        ).rename(columns={
            "EventProbability": "ProbabilityLoss21",
            "BaseEventProbability": "BaseLossProbability21",
            "ModelSamples": "TailModelSamples",
        })
        return data.join(tail)

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"].update({
            "ProbabilityLoss21": row.get("ProbabilityLoss21"),
            "BaseLossProbability21": row.get("BaseLossProbability21"),
            "TailModelSamples": row.get("TailModelSamples"),
        })
        return market


class TailRiskOverlayStrategy(BaseStrategy):
    """Reduce 70% risk exposure only when short-horizon tail risk rises."""

    def __init__(self, profile, config=DEFAULT_CONFIG):
        self.profile = profile
        self.config = config
        self.target = None
        self.last_signal_month = None
        self.probability_loss = None
        self.base_loss_probability = None
        self.tail_stress = 0.0
        if not 0.0 <= profile.minimum_risk_weight <= 0.70:
            raise ValueError("minimum risk weight must be between 0% and 70%")

    @property
    def required_tickers(self):
        return ("QQQ", "BND", "BIL")

    def _risk_weight(self, probability_loss, base_loss_probability):
        excess_probability = max(
            0.0,
            base_loss_probability + TAIL_DEADBAND,
        )
        excess_probability = max(0.0, probability_loss - excess_probability)
        self.tail_stress = float(np.clip(
            excess_probability / TAIL_FULL_STRESS_GAP,
            0.0,
            1.0,
        ))
        capacity = 0.70 - self.profile.minimum_risk_weight
        return float(np.clip(
            0.70 - capacity * self.tail_stress,
            self.profile.minimum_risk_weight,
            0.70,
        ))

    def _desired_target(self, market):
        qqq = market["QQQ"]
        probability = qqq.get("ProbabilityLoss21")
        base_probability = qqq.get("BaseLossProbability21")
        self.probability_loss = (
            float(probability)
            if probability is not None and np.isfinite(probability)
            else TAIL_CONFIG.default_up_probability
        )
        self.base_loss_probability = (
            float(base_probability)
            if base_probability is not None and np.isfinite(base_probability)
            else TAIL_CONFIG.default_up_probability
        )
        risk_weight = self._risk_weight(
            self.probability_loss, self.base_loss_probability
        )
        safe_weight = 1.0 - risk_weight
        return {
            "QQQ": risk_weight,
            "BND": safe_weight / 2.0,
            "BIL": safe_weight / 2.0,
        }

    def _signal(self, rebalance, reason):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": self.config.execution_days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        if self.target is None:
            self.target = self._desired_target(market)
            self.last_signal_month = month
            return self._signal(True, "INITIAL_TAIL_RISK_TARGET")
        if month == self.last_signal_month:
            return self._signal(False, None)

        self.last_signal_month = month
        desired = self._desired_target(market)
        prices = {ticker: market[ticker]["Close"] for ticker in desired}
        current = portfolio.weights(prices)
        self.target = desired
        rebalance = any(
            abs(current.get(ticker, 0.0) - weight)
            >= self.config.rebalance_band
            for ticker, weight in desired.items()
        )
        reason = None
        if rebalance:
            reason = (
                f"MONTHLY_TAIL_RISK_BAND(p={self.probability_loss:.3f},"
                f"base={self.base_loss_probability:.3f},"
                f"stress={self.tail_stress:.3f})"
            )
        return self._signal(rebalance, reason)


def _run_static():
    backtest = Backtest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
    )
    history, trades, rebalances = backtest.run_all()
    return "STATIC_RETIREMENT_7030", history, trades, rebalances, backtest.data


def _run_profile(profile):
    strategy = TailRiskOverlayStrategy(profile)
    backtest = TailRiskFeatureBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
    )
    history, trades, rebalances = backtest.run_all()
    return profile.name, history, trades, rebalances, backtest.data


def _activation_report(results):
    rows = []
    for profile, result in zip(PROFILES, results):
        data = result[4]
        monthly = data.loc[~data.index.to_period("M").duplicated()]
        excess = (
            monthly["ProbabilityLoss21"]
            - monthly["BaseLossProbability21"]
            - TAIL_DEADBAND
        ).clip(lower=0.0)
        stress = (excess / TAIL_FULL_STRESS_GAP).clip(0.0, 1.0)
        target = 0.70 - (0.70 - profile.minimum_risk_weight) * stress
        active = monthly["TailModelSamples"] >= TAIL_CONFIG.minimum_samples
        rows.append({
            "Strategy": profile.name,
            "ActiveForecastMonths": int(active.sum()),
            "DefenseMonths": int(((stress > 0.0) & active).sum()),
            "FullDefenseMonths": int(((stress >= 1.0) & active).sum()),
            "AverageTargetRiskWeight": target[active].mean(),
            "MinimumTargetRiskWeight": target[active].min(),
        })
    return pd.DataFrame(rows)


def _defense_event_report(data):
    monthly = data.loc[
        ~data.index.to_period("M").duplicated(),
        [
            "QQQ_Close",
            "ProbabilityLoss21",
            "BaseLossProbability21",
            "TailModelSamples",
        ],
    ].copy()
    monthly["ExcessProbability"] = (
        monthly["ProbabilityLoss21"]
        - monthly["BaseLossProbability21"]
        - TAIL_DEADBAND
    )
    monthly["Stress"] = (
        monthly["ExcessProbability"].clip(lower=0.0)
        / TAIL_FULL_STRESS_GAP
    ).clip(0.0, 1.0)
    future_close = data["QQQ_Close"].shift(-TAIL_CONFIG.horizon_days)
    monthly["ForwardReturn21D"] = (
        future_close.reindex(monthly.index) / monthly["QQQ_Close"] - 1.0
    )
    monthly["TailLossOccurred"] = (
        monthly["ForwardReturn21D"] <= TAIL_RETURN_THRESHOLD
    )
    events = monthly.loc[
        (monthly["TailModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & (monthly["Stress"] > 0.0)
    ].copy()
    events.index.name = "SignalDate"
    return events.reset_index()


def run_tail_risk_overlay_validation():
    static = _run_static()
    tail_results = [_run_profile(profile) for profile in PROFILES]
    comparison = [
        static,
        *tail_results,
        _run_direction(63),
        _run_composite(),
    ]
    summary = pd.DataFrame([
        _summary(label, history, rebalances)
        for label, history, _, rebalances, _ in comparison
    ])
    windows = _window_summary(comparison)
    diagnostics = _tail_diagnostics(tail_results[0][4])
    activation = _activation_report(tail_results)
    defense_events = _defense_event_report(tail_results[0][4])

    summary.to_csv(RESULT_DIR / "tail_risk_overlay_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "tail_risk_overlay_windows.csv", index=False)
    diagnostics.to_csv(RESULT_DIR / "tail_risk_overlay_diagnostics.csv", index=False)
    activation.to_csv(RESULT_DIR / "tail_risk_overlay_activation.csv", index=False)
    defense_events.to_csv(
        RESULT_DIR / "tail_risk_overlay_defense_events.csv", index=False
    )
    return {
        "summary": summary,
        "windows": windows,
        "diagnostics": diagnostics,
        "activation": activation,
        "defense_events": defense_events,
    }


if __name__ == "__main__":
    reports = run_tail_risk_overlay_validation()
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nTail probability diagnostics")
    print(reports["diagnostics"].to_string(index=False))
    print("\nActivation")
    print(reports["activation"].to_string(index=False))
    print("\nDefense events")
    print(reports["defense_events"].to_string(index=False))
