"""Select causal tail-warning thresholds using precision-first criteria."""

from __future__ import annotations

from math import sqrt

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import STATIC_RETIREMENT_7030
from .enhanced_probability_features import (
    EnhancedProbabilityBacktest,
    SIGNAL_TICKERS,
    VALIDATION_PROFILES,
)
from .multi_horizon_probability import (
    TAIL_CONFIG,
    TAIL_DEADBAND,
    TAIL_RETURN_THRESHOLD,
)
from .probability_accuracy import classification_metrics


PROFILE = next(
    profile
    for profile in VALIDATION_PROFILES
    if profile.name == "BASE_PLUS_QQQ_VOL_ACCELERATION"
)

MINIMUM_THRESHOLD_HISTORY = 60
QUANTILES = (0.80, 0.85, 0.90, 0.95)
PERIODS = {
    "COMMON": (None, None),
    "PRE_2018": (None, "2017-12-31"),
    "RECENT_2018_PRESENT": ("2018-01-01", None),
}
PASS_PRECISION = 0.25
PASS_RECALL = 0.25
MAX_FALSE_POSITIVES_PER_YEAR = 1.0
MINIMUM_WARNINGS = 5


def _wilson_interval(successes, trials, confidence_z=1.96):
    if trials == 0:
        return np.nan, np.nan
    proportion = successes / trials
    denominator = 1.0 + confidence_z**2 / trials
    center = (
        proportion + confidence_z**2 / (2.0 * trials)
    ) / denominator
    radius = confidence_z / denominator * sqrt(
        proportion * (1.0 - proportion) / trials
        + confidence_z**2 / (4.0 * trials**2)
    )
    return center - radius, center + radius


def expanding_quantile(values, quantile, minimum_history=MINIMUM_THRESHOLD_HISTORY):
    """Use only probability forecasts strictly earlier than each decision."""
    values = pd.Series(values, copy=False)
    thresholds = pd.Series(np.nan, index=values.index, dtype=float)
    for position in range(minimum_history, len(values)):
        thresholds.iloc[position] = values.iloc[:position].quantile(quantile)
    return thresholds


def _load_forecast_sample():
    backtest = EnhancedProbabilityBacktest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=SIGNAL_TICKERS,
        feature_profile=PROFILE,
    )
    data = backtest.data
    monthly_mask = ~data.index.to_period("M").duplicated()
    sample = data.loc[
        monthly_mask,
        [
            "QQQ_Close",
            "ProbabilityLoss21",
            "BaseLossProbability21",
            "TailModelSamples",
            "QQQ_DRAWDOWN120",
            "QQQ_REBOUND20",
            "QQQ_ROC5",
            "QQQ_VOL_ACCELERATION",
        ],
    ].copy()
    future = data["QQQ_Close"].shift(-TAIL_CONFIG.horizon_days)
    sample["ForwardReturn21D"] = (
        future.reindex(sample.index) / sample["QQQ_Close"] - 1.0
    )
    sample["Outcome"] = (
        sample["ForwardReturn21D"] <= TAIL_RETURN_THRESHOLD
    ).astype(float)
    sample.loc[
        sample.index > data.index[-TAIL_CONFIG.horizon_days - 1], "Outcome"
    ] = np.nan
    return sample.loc[
        (sample["TailModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & sample["Outcome"].notna()
    ].copy()


def build_causal_thresholds(sample):
    probability = sample["ProbabilityLoss21"]
    thresholds = pd.DataFrame(index=sample.index)
    thresholds["BASE_PLUS_3PP"] = (
        sample["BaseLossProbability21"] + TAIL_DEADBAND
    )
    for quantile in QUANTILES:
        label = f"EXPANDING_Q{int(quantile * 100)}"
        thresholds[label] = expanding_quantile(probability, quantile)
    thresholds["HYBRID_BASE_Q90"] = pd.concat(
        (thresholds["BASE_PLUS_3PP"], thresholds["EXPANDING_Q90"]),
        axis=1,
    ).max(axis=1)
    # All candidates are compared on the exact same dates after a causal
    # percentile history exists.
    return thresholds.dropna()


def _threshold_metrics(sample, thresholds):
    common = sample.loc[thresholds.index]
    rows = []
    for rule in thresholds:
        for period, (start, end) in PERIODS.items():
            window = common.loc[start:end]
            cutoff = thresholds.loc[window.index, rule]
            metrics = classification_metrics(
                window["Outcome"],
                window["ProbabilityLoss21"],
                cutoff,
            )
            precision_low, precision_high = _wilson_interval(
                metrics["TP"], metrics["TP"] + metrics["FP"]
            )
            recall_low, recall_high = _wilson_interval(
                metrics["TP"], metrics["TP"] + metrics["FN"]
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 12.0,
            )
            rows.append({
                "Rule": rule,
                "Period": period,
                "StartDate": window.index.min(),
                "EndDate": window.index.max(),
                **metrics,
                "PrecisionLow95": precision_low,
                "PrecisionHigh95": precision_high,
                "RecallLow95": recall_low,
                "RecallHigh95": recall_high,
                "FalsePositivesPerYear": metrics["FP"] / years,
            })
    return pd.DataFrame(rows)


def _selection_report(metrics):
    common = metrics.loc[metrics["Period"] == "COMMON"].copy()
    common["PrecisionPass"] = common["Precision"] >= PASS_PRECISION
    common["RecallPass"] = common["Recall"] >= PASS_RECALL
    common["FalsePositivePass"] = (
        common["FalsePositivesPerYear"] <= MAX_FALSE_POSITIVES_PER_YEAR
    )
    common["SamplePass"] = common["Warnings"] >= MINIMUM_WARNINGS
    common["Pass"] = common[[
        "PrecisionPass", "RecallPass", "FalsePositivePass", "SamplePass"
    ]].all(axis=1)
    return common[[
        "Rule", "Observations", "EventRate", "Warnings", "TP", "FP", "FN",
        "Precision", "PrecisionLow95", "PrecisionHigh95", "Recall",
        "RecallLow95", "RecallHigh95", "BalancedAccuracy", "ROC_AUC",
        "PR_AUC", "FalsePositivesPerYear", "PrecisionPass", "RecallPass",
        "FalsePositivePass", "SamplePass", "Pass",
    ]]


def _signal_report(sample, thresholds):
    rows = []
    for rule in thresholds:
        warnings = sample.loc[thresholds.index].copy()
        warnings["Threshold"] = thresholds[rule]
        warnings = warnings.loc[
            warnings["ProbabilityLoss21"] >= warnings["Threshold"]
        ]
        for date, event in warnings.iterrows():
            rows.append({
                "Rule": rule,
                "SignalDate": date,
                "ProbabilityLoss21": event["ProbabilityLoss21"],
                "Threshold": event["Threshold"],
                "ForwardReturn21D": event["ForwardReturn21D"],
                "TailLossOccurred": bool(event["Outcome"]),
                "Drawdown120": event["QQQ_DRAWDOWN120"],
                "Rebound20": event["QQQ_REBOUND20"],
                "ROC5": event["QQQ_ROC5"],
                "VolatilityAcceleration": event["QQQ_VOL_ACCELERATION"],
            })
    return pd.DataFrame(rows)


def run_precision_threshold_validation():
    sample = _load_forecast_sample()
    thresholds = build_causal_thresholds(sample)
    metrics = _threshold_metrics(sample, thresholds)
    selection = _selection_report(metrics)
    signals = _signal_report(sample, thresholds)
    metrics.to_csv(RESULT_DIR / "precision_threshold_metrics.csv", index=False)
    selection.to_csv(
        RESULT_DIR / "precision_threshold_selection.csv", index=False
    )
    signals.to_csv(RESULT_DIR / "precision_threshold_signals.csv", index=False)
    return {
        "metrics": metrics,
        "selection": selection,
        "signals": signals,
    }


if __name__ == "__main__":
    reports = run_precision_threshold_validation()
    print(reports["selection"].to_string(index=False))
    print("\nPeriod stability")
    columns = [
        "Rule", "Period", "Warnings", "TP", "FP", "FN", "Precision",
        "Recall", "BalancedAccuracy", "FalsePositivesPerYear",
    ]
    print(reports["metrics"][columns].to_string(index=False))
