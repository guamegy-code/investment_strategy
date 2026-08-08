"""Walk-forward probability allocation with a pension-compliant 70% cap."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from performance import Performance
from strategy import BaseStrategy, STATIC_RETIREMENT_7030


FEATURE_COLUMNS = (
    "QQQ_ROC20",
    "QQQ_ROC60",
    "QQQ_ROC120",
    "QQQ_EMA200_DISTANCE",
    "QQQ_VOL60",
    "QQQ_DRAWDOWN120",
)


@dataclass(frozen=True)
class ProbabilityConfig:
    horizon_days: int = 63
    minimum_samples: int = 60
    maximum_risk_weight: float = 0.70
    minimum_risk_weight: float = 0.30
    probability_deadband: float = 0.05
    full_defense_probability_gap: float = 0.20
    rebalance_band: float = 0.05
    execution_days: int = 3
    default_up_probability: float = 0.60
    ridge_penalty: float = 1.0
    positive_class_weight: float = 1.0


DEFAULT_CONFIG = ProbabilityConfig()

WINDOWS = {
    "FULL": (None, None),
    "MODEL_ACTIVE": ("2005-01-01", None),
    "EARLY_MODEL_2005_2009": ("2005-01-01", "2009-12-31"),
    "EXPANSION_2010_2017": ("2010-01-01", "2017-12-31"),
    "RECENT_2018_PRESENT": ("2018-01-01", None),
    "GLOBAL_FINANCIAL_CRISIS": ("2007-10-09", "2009-03-09"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
}


def _sigmoid(values):
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def _fit_probability(
    train_x,
    train_y,
    predict_x,
    ridge_penalty,
    positive_class_weight=1.0,
):
    """Fit a small regularized logistic model using training data only."""
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((train_x - mean) / scale, -5.0, 5.0)
    prediction = np.clip((predict_x - mean) / scale, -5.0, 5.0)
    design = np.column_stack((np.ones(len(standardized)), standardized))
    point = np.concatenate(([1.0], prediction))

    if positive_class_weight <= 0.0:
        raise ValueError("positive_class_weight must be positive")
    base_probability = float((train_y.sum() + 1.0) / (len(train_y) + 2.0))
    sample_weight = np.where(train_y > 0.5, positive_class_weight, 1.0)
    weighted_probability = float(
        (np.sum(sample_weight * train_y) + 1.0)
        / (np.sum(sample_weight) + 2.0)
    )
    beta = np.zeros(design.shape[1])
    beta[0] = np.log(weighted_probability / (1.0 - weighted_probability))
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[0, 0] = 0.0

    for _ in range(30):
        fitted = _sigmoid(design @ beta)
        weights = sample_weight * np.maximum(fitted * (1.0 - fitted), 1e-6)
        gradient = design.T @ (sample_weight * (train_y - fitted)) - penalty @ beta
        hessian = (design.T * weights) @ design + penalty
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break

    # Weighted logistic regression shifts the fitted prior. Remove that shift
    # before treating the score as an event probability.
    corrected_logit = point @ beta - np.log(positive_class_weight)
    raw_probability = float(_sigmoid(corrected_logit))
    # Small samples produce overconfident tactical weights. Shrink them toward
    # the observed base rate until ten years of monthly examples accumulate.
    reliability = min(1.0, len(train_y) / 120.0)
    probability = base_probability + reliability * (
        raw_probability - base_probability
    )
    return float(np.clip(probability, 0.05, 0.95)), base_probability


def walk_forward_event_probabilities(
    data,
    config=DEFAULT_CONFIG,
    event="UP",
    threshold=0.0,
    feature_columns=FEATURE_COLUMNS,
    decision_period="M",
):
    """Return causal monthly event forecasts aligned to the daily index.

    A training observation is admitted only after its complete forward return
    is known. Monthly sampling also limits dependence between overlapping
    three-month outcomes.
    """
    if event not in {"UP", "DOWNSIDE", "PATH_DOWNSIDE"}:
        raise ValueError("event must be UP, DOWNSIDE, or PATH_DOWNSIDE")
    frame = data.copy()
    frame["QQQ_EMA200_DISTANCE"] = (
        frame["QQQ_Close"] / frame["QQQ_EMA200"] - 1.0
    )
    decision_mask = ~frame.index.to_period(decision_period).duplicated()
    decision_positions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    features = frame.loc[:, feature_columns].to_numpy(dtype=float)

    probability = pd.Series(np.nan, index=frame.index, dtype=float)
    base_rate = pd.Series(np.nan, index=frame.index, dtype=float)
    samples = pd.Series(0, index=frame.index, dtype=int)

    for position in decision_positions:
        eligible = decision_positions[
            decision_positions + config.horizon_days <= position
        ]
        if len(eligible):
            finite = np.isfinite(features[eligible]).all(axis=1)
            eligible = eligible[finite]
        current_is_valid = np.isfinite(features[position]).all()

        if len(eligible) < config.minimum_samples or not current_is_valid:
            current_probability = config.default_up_probability
            current_base_rate = config.default_up_probability
        else:
            forward_returns = (
                close[eligible + config.horizon_days] / close[eligible] - 1.0
            )
            if event == "UP":
                outcomes = (forward_returns > threshold).astype(float)
            elif event == "PATH_DOWNSIDE":
                path_returns = np.array([
                    close[position + 1:position + config.horizon_days + 1].min()
                    / close[position]
                    - 1.0
                    for position in eligible
                ])
                outcomes = (path_returns <= threshold).astype(float)
            else:
                outcomes = (forward_returns <= threshold).astype(float)
            current_probability, current_base_rate = _fit_probability(
                features[eligible],
                outcomes,
                features[position],
                config.ridge_penalty,
                config.positive_class_weight,
            )
        probability.iloc[position] = current_probability
        base_rate.iloc[position] = current_base_rate
        samples.iloc[position] = len(eligible)

    return pd.DataFrame({
        "EventProbability": probability.ffill(),
        "BaseEventProbability": base_rate.ffill(),
        "ModelSamples": samples.where(decision_mask).ffill().fillna(0).astype(int),
    }, index=frame.index)


def walk_forward_probabilities(data, config=DEFAULT_CONFIG):
    """Backward-compatible wrapper for the probability of a positive return."""
    forecasts = walk_forward_event_probabilities(data, config, event="UP")
    return forecasts.rename(columns={
        "EventProbability": "ProbabilityUp",
        "BaseEventProbability": "BaseUpProbability",
    })


class ProbabilityFeatureBacktest(Backtest):
    """Attach causal probability forecasts to ordinary backtest market data."""

    def __init__(self, *args, probability_config=DEFAULT_CONFIG, **kwargs):
        self.probability_config = probability_config
        super().__init__(*args, **kwargs)

    def load_data(self):
        data = super().load_data()
        forecasts = walk_forward_probabilities(data, self.probability_config)
        return data.join(forecasts)

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"].update({
            "ProbabilityUp": row.get("ProbabilityUp"),
            "BaseUpProbability": row.get("BaseUpProbability"),
            "ModelSamples": row.get("ModelSamples"),
        })
        return market


class ProbabilisticDownsideAllocationStrategy(BaseStrategy):
    """Stay at 70/30 unless the three-month probability becomes unfavorable."""

    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config
        self.target = None
        self.last_signal_month = None
        self.probability_up = None
        self.base_probability = None
        if not 0.0 <= config.minimum_risk_weight <= config.maximum_risk_weight <= 0.70:
            raise ValueError("risk weights must remain between 0% and 70%")

    @property
    def required_tickers(self):
        return ("QQQ", "BND", "BIL")

    def _risk_weight(self, probability_up, base_probability):
        probability_gap = max(
            0.0,
            base_probability - probability_up - self.config.probability_deadband,
        )
        reduction = np.clip(
            probability_gap / self.config.full_defense_probability_gap,
            0.0,
            1.0,
        )
        capacity = (
            self.config.maximum_risk_weight - self.config.minimum_risk_weight
        )
        return float(self.config.maximum_risk_weight - capacity * reduction)

    def _desired_target(self, market):
        qqq = market["QQQ"]
        probability = qqq.get("ProbabilityUp")
        base_probability = qqq.get("BaseUpProbability")
        if probability is None or not np.isfinite(probability):
            probability = self.config.default_up_probability
        if base_probability is None or not np.isfinite(base_probability):
            base_probability = self.config.default_up_probability
        self.probability_up = float(probability)
        self.base_probability = float(base_probability)
        risk_weight = self._risk_weight(
            self.probability_up, self.base_probability
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
            return self._signal(True, "INITIAL_PROBABILITY_TARGET")
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
                f"MONTHLY_PROBABILITY_BAND(p={self.probability_up:.3f},"
                f"base={self.base_probability:.3f})"
            )
        return self._signal(rebalance, reason)


def _run(label, strategy, config):
    backtest = ProbabilityFeatureBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=("QQQ", "BND", "BIL"),
        probability_config=config,
    )
    history, trades, rebalances = backtest.run_all()
    return label, history, trades, rebalances, backtest.data


def _summary(label, history, rebalances):
    metrics = Performance(history).summary()
    qqq_weight = history["Weights"].apply(lambda value: value.get("QQQ", 0.0))
    return {
        "Strategy": label,
        **metrics,
        "AverageRiskWeight": qqq_weight.mean(),
        "MinimumRiskWeight": qqq_weight.min(),
        "MaximumRiskWeight": qqq_weight.max(),
        "MaximumTargetRiskWeight": 0.70,
        "Rebalances": len(rebalances),
    }


def _window_summary(results):
    rows = []
    for label, history, _, rebalances, _ in results:
        del rebalances
        for window, (start, end) in WINDOWS.items():
            sample = history.loc[start:end]
            if len(sample) < 2:
                continue
            metrics = Performance(sample).summary()
            rows.append({
                "Strategy": label,
                "Window": window,
                "StartDate": sample.index.min(),
                "EndDate": sample.index.max(),
                "TotalReturn": (
                    sample["Portfolio"].iloc[-1]
                    / sample["Portfolio"].iloc[0]
                    - 1.0
                ),
                "CAGR": metrics["CAGR"],
                "MDD": metrics["MDD"],
                "Sharpe": metrics["Sharpe"],
                "Calmar": metrics["Calmar"],
            })
    return pd.DataFrame(rows)


def _probability_diagnostics(data, config):
    monthly = data.loc[
        ~data.index.to_period("M").duplicated(),
        ["QQQ_Close", "ProbabilityUp", "BaseUpProbability", "ModelSamples"],
    ].copy()
    monthly["OutcomeUp"] = (
        data["QQQ_Close"].shift(-config.horizon_days)
        .reindex(monthly.index)
        .gt(monthly["QQQ_Close"])
        .astype(float)
    )
    monthly.loc[
        monthly.index > data.index[-config.horizon_days - 1], "OutcomeUp"
    ] = np.nan
    active = monthly.loc[
        (monthly["ModelSamples"] >= config.minimum_samples)
        & monthly["OutcomeUp"].notna()
    ].copy()
    active["SquaredError"] = (
        active["ProbabilityUp"] - active["OutcomeUp"]
    ) ** 2
    active["BaseSquaredError"] = (
        active["BaseUpProbability"] - active["OutcomeUp"]
    ) ** 2
    active["ProbabilityBucket"] = pd.cut(
        active["ProbabilityUp"],
        bins=np.linspace(0.0, 1.0, 6),
        include_lowest=True,
    )
    calibration = active.groupby(
        "ProbabilityBucket", observed=False
    ).agg(
        Forecasts=("OutcomeUp", "size"),
        MeanForecast=("ProbabilityUp", "mean"),
        ActualUpRate=("OutcomeUp", "mean"),
        BrierScore=("SquaredError", "mean"),
        BaseBrierScore=("BaseSquaredError", "mean"),
    ).reset_index()
    calibration["BrierSkillVsBase"] = (
        1.0 - calibration["BrierScore"] / calibration["BaseBrierScore"]
    )
    brier_score = active["SquaredError"].mean()
    base_brier_score = active["BaseSquaredError"].mean()
    overall = pd.DataFrame([{
        "Forecasts": len(active),
        "MeanForecast": active["ProbabilityUp"].mean(),
        "ActualUpRate": active["OutcomeUp"].mean(),
        "BrierScore": brier_score,
        "BaseBrierScore": base_brier_score,
        "BrierSkillVsBase": 1.0 - brier_score / base_brier_score,
    }])
    return overall, calibration


def run_probabilistic_allocation_validation(config=DEFAULT_CONFIG):
    results = [
        _run("STATIC_RETIREMENT_7030", STATIC_RETIREMENT_7030(), config),
        _run(
            "PROBABILISTIC_DOWNSIDE_30_70",
            ProbabilisticDownsideAllocationStrategy(config),
            config,
        ),
    ]
    summary = pd.DataFrame([
        _summary(label, history, rebalances)
        for label, history, _, rebalances, _ in results
    ])
    windows = _window_summary(results)
    overall, calibration = _probability_diagnostics(results[-1][4], config)
    summary.to_csv(RESULT_DIR / "probabilistic_allocation_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "probabilistic_allocation_windows.csv", index=False)
    overall.to_csv(RESULT_DIR / "probability_diagnostics.csv", index=False)
    calibration.to_csv(RESULT_DIR / "probability_calibration.csv", index=False)
    return {
        "summary": summary,
        "windows": windows,
        "diagnostics": overall,
        "calibration": calibration,
    }


if __name__ == "__main__":
    reports = run_probabilistic_allocation_validation()
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nProbability diagnostics")
    print(reports["diagnostics"].to_string(index=False))
    print("\nCalibration")
    print(reports["calibration"].to_string(index=False))
