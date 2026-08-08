"""Compare shorter probability horizons and a short-tail composite signal."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import BaseStrategy, STATIC_RETIREMENT_7030
from .probabilistic_allocation import (
    DEFAULT_CONFIG,
    ProbabilityFeatureBacktest,
    ProbabilisticDownsideAllocationStrategy,
    _probability_diagnostics,
    _summary,
    _window_summary,
    walk_forward_event_probabilities,
)


HORIZONS = (21, 42, 63)
DIRECTION_CONFIGS = {
    horizon: replace(DEFAULT_CONFIG, horizon_days=horizon)
    for horizon in HORIZONS
}
TAIL_CONFIG = replace(
    DEFAULT_CONFIG,
    horizon_days=21,
    default_up_probability=0.15,
)
TAIL_RETURN_THRESHOLD = -0.05
DIRECTION_SHARE = 0.60
TAIL_SHARE = 0.40
TAIL_DEADBAND = 0.03
TAIL_FULL_STRESS_GAP = 0.15


class CompositeProbabilityFeatureBacktest(Backtest):
    """Attach 42-day direction and 21-day left-tail probabilities."""

    def load_data(self):
        data = super().load_data()
        direction = walk_forward_event_probabilities(
            data, DIRECTION_CONFIGS[42], event="UP"
        ).rename(columns={
            "EventProbability": "ProbabilityUp42",
            "BaseEventProbability": "BaseUpProbability42",
            "ModelSamples": "DirectionModelSamples",
        })
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
        return data.join(direction).join(tail)

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"].update({
            "ProbabilityUp42": row.get("ProbabilityUp42"),
            "BaseUpProbability42": row.get("BaseUpProbability42"),
            "ProbabilityLoss21": row.get("ProbabilityLoss21"),
            "BaseLossProbability21": row.get("BaseLossProbability21"),
        })
        return market


class CompositeProbabilityAllocationStrategy(BaseStrategy):
    """Blend medium-term direction weakness with one-month tail risk."""

    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config
        self.target = None
        self.last_signal_month = None
        self.direction_stress = 0.0
        self.tail_stress = 0.0

    @property
    def required_tickers(self):
        return ("QQQ", "BND", "BIL")

    @staticmethod
    def _valid(value, fallback):
        return float(value) if value is not None and np.isfinite(value) else fallback

    def _risk_weight(self, probability_up, base_up, probability_loss, base_loss):
        direction_gap = max(
            0.0,
            base_up - probability_up - self.config.probability_deadband,
        )
        self.direction_stress = float(np.clip(
            direction_gap / self.config.full_defense_probability_gap,
            0.0,
            1.0,
        ))
        tail_gap = max(0.0, probability_loss - base_loss - TAIL_DEADBAND)
        self.tail_stress = float(np.clip(
            tail_gap / TAIL_FULL_STRESS_GAP,
            0.0,
            1.0,
        ))
        stress = (
            DIRECTION_SHARE * self.direction_stress
            + TAIL_SHARE * self.tail_stress
        )
        capacity = (
            self.config.maximum_risk_weight - self.config.minimum_risk_weight
        )
        return float(np.clip(
            self.config.maximum_risk_weight - capacity * stress,
            self.config.minimum_risk_weight,
            self.config.maximum_risk_weight,
        ))

    def _desired_target(self, market):
        qqq = market["QQQ"]
        probability_up = self._valid(
            qqq.get("ProbabilityUp42"), self.config.default_up_probability
        )
        base_up = self._valid(
            qqq.get("BaseUpProbability42"), self.config.default_up_probability
        )
        probability_loss = self._valid(
            qqq.get("ProbabilityLoss21"), TAIL_CONFIG.default_up_probability
        )
        base_loss = self._valid(
            qqq.get("BaseLossProbability21"), TAIL_CONFIG.default_up_probability
        )
        risk_weight = self._risk_weight(
            probability_up, base_up, probability_loss, base_loss
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
            return self._signal(True, "INITIAL_COMPOSITE_PROBABILITY_TARGET")
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
                "MONTHLY_COMPOSITE_PROBABILITY_BAND"
                f"(direction={self.direction_stress:.3f},"
                f"tail={self.tail_stress:.3f})"
            )
        return self._signal(rebalance, reason)


def _run_direction(horizon):
    config = DIRECTION_CONFIGS[horizon]
    strategy = ProbabilisticDownsideAllocationStrategy(config)
    backtest = ProbabilityFeatureBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
        probability_config=config,
    )
    history, trades, rebalances = backtest.run_all()
    return (
        f"PROBABILITY_DIRECTION_{horizon}D",
        history,
        trades,
        rebalances,
        backtest.data,
    )


def _run_static():
    backtest = Backtest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
    )
    history, trades, rebalances = backtest.run_all()
    return "STATIC_RETIREMENT_7030", history, trades, rebalances, backtest.data


def _run_composite():
    strategy = CompositeProbabilityAllocationStrategy()
    backtest = CompositeProbabilityFeatureBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
    )
    history, trades, rebalances = backtest.run_all()
    return (
        "COMPOSITE_21D_TAIL_42D_DIRECTION",
        history,
        trades,
        rebalances,
        backtest.data,
    )


def _tail_diagnostics(data):
    monthly = data.loc[
        ~data.index.to_period("M").duplicated(),
        [
            "QQQ_Close",
            "ProbabilityLoss21",
            "BaseLossProbability21",
            "TailModelSamples",
        ],
    ].copy()
    future_close = data["QQQ_Close"].shift(-TAIL_CONFIG.horizon_days)
    monthly["Outcome"] = (
        future_close.reindex(monthly.index) / monthly["QQQ_Close"] - 1.0
        <= TAIL_RETURN_THRESHOLD
    ).astype(float)
    monthly.loc[
        monthly.index > data.index[-TAIL_CONFIG.horizon_days - 1], "Outcome"
    ] = np.nan
    active = monthly.loc[
        (monthly["TailModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & monthly["Outcome"].notna()
    ]
    brier = ((active["ProbabilityLoss21"] - active["Outcome"]) ** 2).mean()
    base_brier = (
        (active["BaseLossProbability21"] - active["Outcome"]) ** 2
    ).mean()
    return pd.DataFrame([{
        "Model": "TAIL_LOSS_21D",
        "Forecasts": len(active),
        "MeanForecast": active["ProbabilityLoss21"].mean(),
        "ActualUpRate": active["Outcome"].mean(),
        "BrierScore": brier,
        "BaseBrierScore": base_brier,
        "BrierSkillVsBase": 1.0 - brier / base_brier,
    }])


def run_multi_horizon_probability_validation():
    results = [_run_static()]
    results.extend(_run_direction(horizon) for horizon in HORIZONS)
    results.append(_run_composite())

    summary = pd.DataFrame([
        _summary(label, history, rebalances)
        for label, history, _, rebalances, _ in results
    ])
    windows = _window_summary(results)
    diagnostics = []
    calibration_parts = []
    for result, horizon in zip(results[1:4], HORIZONS):
        overall, calibration = _probability_diagnostics(
            result[4], DIRECTION_CONFIGS[horizon]
        )
        overall.insert(0, "Model", f"DIRECTION_{horizon}D")
        calibration.insert(0, "Model", f"DIRECTION_{horizon}D")
        diagnostics.append(overall)
        calibration_parts.append(calibration)
    diagnostics.append(_tail_diagnostics(results[-1][4]))
    diagnostics = pd.concat(diagnostics, ignore_index=True)
    diagnostics = diagnostics.rename(columns={"ActualUpRate": "ActualEventRate"})
    calibration = pd.concat(calibration_parts, ignore_index=True)

    summary.to_csv(RESULT_DIR / "multi_horizon_probability_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "multi_horizon_probability_windows.csv", index=False)
    diagnostics.to_csv(
        RESULT_DIR / "multi_horizon_probability_diagnostics.csv", index=False
    )
    calibration.to_csv(
        RESULT_DIR / "multi_horizon_probability_calibration.csv", index=False
    )
    return {
        "summary": summary,
        "windows": windows,
        "diagnostics": diagnostics,
        "calibration": calibration,
    }


if __name__ == "__main__":
    reports = run_multi_horizon_probability_validation()
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nDirection probability diagnostics")
    print(reports["diagnostics"].to_string(index=False))
