"""Select one hazard-warning quantile under a development precision constraint."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .discrete_hazard import HAZARD_PROFILES, walk_forward_hazard_probabilities
from .path_tail_targets import PERIODS, _load_feature_data, forward_path_outcomes
from .precision_thresholds import _wilson_interval, expanding_quantile
from .probability_accuracy import classification_metrics
from .weekly_path_tail import (
    COOLDOWN_DECISIONS,
    HORIZON_DAYS,
    MINIMUM_MODEL_SAMPLES,
    MINIMUM_THRESHOLD_HISTORY,
    PATH_THRESHOLD,
    _episode_metrics,
    suppress_overlapping_warnings,
)


QUANTILE_GRID = tuple(np.arange(0.50, 1.00, 0.05).round(2))
MINIMUM_PRECISION = 0.25
MINIMUM_WARNINGS = 5


def build_candidate_thresholds(probability, quantiles=QUANTILE_GRID):
    """Build causal thresholds; each row only uses earlier forecasts."""
    probability = pd.Series(probability, copy=False)
    return pd.DataFrame({
        f"Q{int(round(quantile * 100)):02d}": expanding_quantile(
            probability, quantile, MINIMUM_THRESHOLD_HISTORY
        )
        for quantile in quantiles
    }, index=probability.index)


def select_development_quantile(metrics):
    """Select one predeclared quantile without consulting later periods."""
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"].copy()
    development["PrecisionPass"] = development["Precision"] >= MINIMUM_PRECISION
    development["SamplePass"] = development["Warnings"] >= MINIMUM_WARNINGS
    development["Eligible"] = development["PrecisionPass"] & development["SamplePass"]
    development["Selected"] = False
    eligible = development.loc[development["Eligible"]].sort_values(
        ["EpisodeRecall", "FalsePositivesPerYear", "Precision", "Quantile"],
        ascending=[False, True, False, False],
    )
    if not eligible.empty:
        development.loc[eligible.index[0], "Selected"] = True
    return development.sort_values(
        ["Selected", "Eligible", "EpisodeRecall", "Precision"],
        ascending=[False, False, False, False],
    )


def _hazard_sample(data):
    profile = next(item for item in HAZARD_PROFILES if item.name == "DISCRETE_HAZARD")
    forecast = walk_forward_hazard_probabilities(data, profile)
    decision_mask = ~data.index.to_period("W-FRI").duplicated()
    sample = forecast.loc[decision_mask].copy()
    positions = data.index.get_indexer(sample.index)
    complete = positions + HORIZON_DAYS < len(data)
    sample = sample.iloc[np.flatnonzero(complete)].copy()
    positions = positions[complete]
    outcome, lead = forward_path_outcomes(
        data["QQQ_Close"].to_numpy(dtype=float), positions, HORIZON_DAYS, PATH_THRESHOLD
    )
    sample["Outcome"] = outcome
    sample["LeadTradingDays"] = lead
    return sample.loc[sample["ModelSamples"] >= MINIMUM_MODEL_SAMPLES]


def _metric_rows(sample, thresholds):
    common_index = thresholds.dropna().index
    sample = sample.loc[common_index]
    rows = []
    for label in thresholds:
        quantile = int(label[1:]) / 100.0
        raw = sample["EventProbability"] >= thresholds.loc[common_index, label]
        warning = suppress_overlapping_warnings(raw, COOLDOWN_DECISIONS)
        for period, (start, end) in PERIODS.items():
            window = sample.loc[start:end]
            predicted = warning.loc[window.index]
            alerts = classification_metrics(
                window["Outcome"], predicted.astype(float), 0.5
            )
            episode_count, detected, episode_recall = _episode_metrics(
                window["Outcome"], predicted
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 52.0,
            )
            precision_low, precision_high = _wilson_interval(
                alerts["TP"], alerts["TP"] + alerts["FP"]
            )
            true_positive = predicted & window["Outcome"].astype(bool)
            rows.append({
                "Threshold": label,
                "Quantile": quantile,
                "Period": period,
                "Observations": len(window),
                "EventRate": window["Outcome"].mean(),
                "Warnings": alerts["Warnings"],
                "TP": alerts["TP"],
                "FP": alerts["FP"],
                "FN": alerts["FN"],
                "Precision": alerts["Precision"],
                "PrecisionLow95": precision_low,
                "PrecisionHigh95": precision_high,
                "Recall": alerts["Recall"],
                "EpisodeCount": episode_count,
                "DetectedEpisodes": detected,
                "EpisodeRecall": episode_recall,
                "FalsePositivesPerYear": alerts["FP"] / years,
                "MeanLeadTradingDays": window.loc[
                    true_positive, "LeadTradingDays"
                ].mean(),
            })
    return pd.DataFrame(rows)


def run_constrained_hazard_threshold_validation():
    data = _load_feature_data()
    sample = _hazard_sample(data)
    thresholds = build_candidate_thresholds(sample["EventProbability"])
    metrics = _metric_rows(sample, thresholds)
    selection = select_development_quantile(metrics)
    selected = selection.loc[selection["Selected"], "Threshold"]
    fixed_metrics = (
        metrics.loc[metrics["Threshold"] == selected.iloc[0]].copy()
        if len(selected)
        else metrics.iloc[0:0].copy()
    )
    metrics.to_csv(RESULT_DIR / "constrained_hazard_threshold_metrics.csv", index=False)
    selection.to_csv(
        RESULT_DIR / "constrained_hazard_threshold_selection.csv", index=False
    )
    fixed_metrics.to_csv(
        RESULT_DIR / "constrained_hazard_threshold_fixed.csv", index=False
    )
    return {"metrics": metrics, "selection": selection, "fixed": fixed_metrics}


if __name__ == "__main__":
    reports = run_constrained_hazard_threshold_validation()
    columns = [
        "Threshold", "Warnings", "TP", "FP", "Precision", "EpisodeRecall",
        "FalsePositivesPerYear", "PrecisionPass", "SamplePass", "Eligible", "Selected",
    ]
    print("Development-only quantile selection")
    print(reports["selection"][columns].to_string(index=False))
    print("\nFixed quantile evaluation")
    fixed_columns = [
        "Threshold", "Period", "Warnings", "TP", "FP", "Precision", "Recall",
        "EpisodeRecall", "FalsePositivesPerYear", "MeanLeadTradingDays",
    ]
    print(reports["fixed"][fixed_columns].to_string(index=False))
