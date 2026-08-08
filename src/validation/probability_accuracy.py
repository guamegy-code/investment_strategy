"""Compare probability models before applying portfolio allocation rules."""

from __future__ import annotations

from functools import reduce

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import STATIC_RETIREMENT_7030
from .external_probability_features import (
    ALL_TICKERS,
    CREDIT_COLUMNS,
    LOCAL_COLUMNS,
    VIX_COLUMNS,
    ExternalFeatureProfile,
    ExternalProbabilityBacktest,
    PROFILES as EXTERNAL_PROFILES,
    download_external_signals,
)
from .multi_horizon_probability import (
    TAIL_CONFIG,
    TAIL_DEADBAND,
    TAIL_RETURN_THRESHOLD,
)
from .probabilistic_allocation import FEATURE_COLUMNS, walk_forward_event_probabilities


ACCURACY_PROFILES = (
    ExternalFeatureProfile("BASE", FEATURE_COLUMNS),
    ExternalFeatureProfile("VOL_ACCEL", LOCAL_COLUMNS),
    ExternalFeatureProfile("VOL_ACCEL_PLUS_VIX", LOCAL_COLUMNS + VIX_COLUMNS),
    ExternalFeatureProfile(
        "VOL_ACCEL_PLUS_CREDIT", LOCAL_COLUMNS + CREDIT_COLUMNS
    ),
    ExternalFeatureProfile(
        "VOL_ACCEL_PLUS_VIX_CREDIT",
        LOCAL_COLUMNS + VIX_COLUMNS + CREDIT_COLUMNS,
    ),
)

PERIODS = {
    "COMMON": (None, None),
    "PRE_2018": (None, "2017-12-31"),
    "RECENT_2018_PRESENT": ("2018-01-01", None),
}


def _safe_divide(numerator, denominator):
    return float(numerator / denominator) if denominator else np.nan


def _roc_auc(outcome, probability):
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    positives = int(outcome.sum())
    negatives = len(outcome) - positives
    if positives == 0 or negatives == 0:
        return np.nan
    ranks = pd.Series(probability).rank(method="average").to_numpy()
    positive_rank_sum = ranks[outcome == 1].sum()
    return float(
        (positive_rank_sum - positives * (positives + 1) / 2.0)
        / (positives * negatives)
    )


def _average_precision(outcome, probability):
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    positives = int(outcome.sum())
    if positives == 0:
        return np.nan
    order = np.argsort(-probability, kind="stable")
    ranked = outcome[order]
    precision = np.cumsum(ranked) / np.arange(1, len(ranked) + 1)
    return float(precision[ranked == 1].sum() / positives)


def _top_fraction_lift(outcome, probability, fraction):
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    base_rate = outcome.mean()
    if len(outcome) == 0 or base_rate == 0:
        return np.nan
    count = max(1, int(np.ceil(len(outcome) * fraction)))
    selected = outcome[np.argsort(-probability, kind="stable")[:count]]
    return float(selected.mean() / base_rate)


def classification_metrics(outcome, probability, threshold):
    """Return imbalance-aware classification and probability diagnostics."""
    outcome = np.asarray(outcome, dtype=int)
    probability = np.asarray(probability, dtype=float)
    if np.isscalar(threshold):
        threshold = np.full(len(outcome), float(threshold))
    else:
        threshold = np.asarray(threshold, dtype=float)
    predicted = probability >= threshold
    positive = outcome == 1
    negative = ~positive
    tp = int(np.sum(predicted & positive))
    fp = int(np.sum(predicted & negative))
    tn = int(np.sum(~predicted & negative))
    fn = int(np.sum(~predicted & positive))
    recall = _safe_divide(tp, tp + fn)
    specificity = _safe_divide(tn, tn + fp)
    precision = _safe_divide(tp, tp + fp)
    f1 = (
        _safe_divide(2.0 * precision * recall, precision + recall)
        if np.isfinite(precision) and np.isfinite(recall)
        else np.nan
    )
    clipped = np.clip(probability, 1e-9, 1.0 - 1e-9)
    log_loss = -np.mean(
        outcome * np.log(clipped) + (1 - outcome) * np.log(1.0 - clipped)
    )
    return {
        "Observations": len(outcome),
        "EventRate": outcome.mean(),
        "Warnings": int(predicted.sum()),
        "WarningRate": predicted.mean(),
        "TP": tp,
        "FP": fp,
        "TN": tn,
        "FN": fn,
        "Accuracy": (tp + tn) / len(outcome),
        "NaiveAccuracy": max(outcome.mean(), 1.0 - outcome.mean()),
        "BalancedAccuracy": np.nanmean((recall, specificity)),
        "Precision": precision,
        "Recall": recall,
        "Specificity": specificity,
        "F1": f1,
        "ROC_AUC": _roc_auc(outcome, probability),
        "PR_AUC": _average_precision(outcome, probability),
        "BrierScore": np.mean((probability - outcome) ** 2),
        "LogLoss": log_loss,
        "Top10PctLift": _top_fraction_lift(outcome, probability, 0.10),
        "Top20PctLift": _top_fraction_lift(outcome, probability, 0.20),
    }


def _load_common_data():
    download_external_signals(refresh=False)
    backtest = ExternalProbabilityBacktest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=ALL_TICKERS,
        feature_profile=EXTERNAL_PROFILES[0],
    )
    return backtest.data


def _forecast_sample(data, profile, event):
    is_tail = event == "TAIL_LOSS_21D"
    forecasts = walk_forward_event_probabilities(
        data,
        TAIL_CONFIG,
        event="DOWNSIDE" if is_tail else "UP",
        threshold=TAIL_RETURN_THRESHOLD if is_tail else 0.0,
        feature_columns=profile.columns,
    )
    monthly_mask = ~data.index.to_period("M").duplicated()
    sample = forecasts.loc[monthly_mask].copy()
    close = data["QQQ_Close"]
    sample["ForwardReturn"] = (
        close.shift(-TAIL_CONFIG.horizon_days).reindex(sample.index)
        / close.reindex(sample.index)
        - 1.0
    )
    sample["Outcome"] = (
        sample["ForwardReturn"] <= TAIL_RETURN_THRESHOLD
        if is_tail
        else sample["ForwardReturn"] > 0.0
    ).astype(float)
    sample.loc[
        sample.index > data.index[-TAIL_CONFIG.horizon_days - 1], "Outcome"
    ] = np.nan
    return sample.loc[
        (sample["ModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & sample["Outcome"].notna()
    ]


def _common_samples(data, event):
    samples = {
        profile.name: _forecast_sample(data, profile, event)
        for profile in ACCURACY_PROFILES
    }
    common_index = reduce(
        pd.Index.intersection,
        (sample.index for sample in samples.values()),
    )
    return {
        name: sample.loc[common_index].copy()
        for name, sample in samples.items()
    }


def _metrics_report(samples, event):
    rows = []
    for model, sample in samples.items():
        for period, (start, end) in PERIODS.items():
            window = sample.loc[start:end]
            threshold = (
                window["BaseEventProbability"] + TAIL_DEADBAND
                if event == "TAIL_LOSS_21D"
                else 0.50
            )
            metrics = classification_metrics(
                window["Outcome"], window["EventProbability"], threshold
            )
            base_brier = np.mean(
                (window["BaseEventProbability"] - window["Outcome"]) ** 2
            )
            metrics["BrierSkillVsBase"] = (
                1.0 - metrics["BrierScore"] / base_brier
            )
            rows.append({
                "Model": model,
                "Event": event,
                "Period": period,
                "StartDate": window.index.min(),
                "EndDate": window.index.max(),
                **metrics,
            })
    return pd.DataFrame(rows)


def _calibration_report(samples, event):
    rows = []
    for model, sample in samples.items():
        buckets = pd.qcut(
            sample["EventProbability"],
            q=5,
            duplicates="drop",
        )
        grouped = sample.assign(ProbabilityBucket=buckets).groupby(
            "ProbabilityBucket", observed=False
        )
        for bucket, group in grouped:
            rows.append({
                "Model": model,
                "Event": event,
                "ProbabilityBucket": str(bucket),
                "Forecasts": len(group),
                "MeanForecast": group["EventProbability"].mean(),
                "ActualEventRate": group["Outcome"].mean(),
            })
    return pd.DataFrame(rows)


def run_probability_accuracy_validation():
    data = _load_common_data()
    direction_samples = _common_samples(data, "UP_21D")
    tail_samples = _common_samples(data, "TAIL_LOSS_21D")
    metrics = pd.concat(
        (
            _metrics_report(direction_samples, "UP_21D"),
            _metrics_report(tail_samples, "TAIL_LOSS_21D"),
        ),
        ignore_index=True,
    )
    calibration = pd.concat(
        (
            _calibration_report(direction_samples, "UP_21D"),
            _calibration_report(tail_samples, "TAIL_LOSS_21D"),
        ),
        ignore_index=True,
    )
    metrics.to_csv(RESULT_DIR / "probability_accuracy_metrics.csv", index=False)
    calibration.to_csv(
        RESULT_DIR / "probability_accuracy_calibration.csv", index=False
    )
    return {"metrics": metrics, "calibration": calibration}


if __name__ == "__main__":
    reports = run_probability_accuracy_validation()
    columns = [
        "Model", "Event", "Period", "Observations", "EventRate",
        "Accuracy", "NaiveAccuracy", "BalancedAccuracy", "Precision",
        "Recall", "Specificity", "F1", "ROC_AUC", "PR_AUC",
        "Top10PctLift", "Top20PctLift", "BrierSkillVsBase",
        "TP", "FP", "TN", "FN",
    ]
    print(reports["metrics"][columns].to_string(index=False))
