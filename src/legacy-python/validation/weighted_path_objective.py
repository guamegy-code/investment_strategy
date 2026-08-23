"""Test asymmetric false-negative costs for weekly path-tail prediction."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .multi_horizon_probability import TAIL_CONFIG
from .path_tail_targets import PERIODS, _load_feature_data, forward_path_outcomes
from .precision_thresholds import PROFILE, _wilson_interval, expanding_quantile
from .probability_accuracy import classification_metrics
from .probabilistic_allocation import walk_forward_event_probabilities
from .weekly_path_tail import (
    COOLDOWN_DECISIONS,
    HORIZON_DAYS,
    MINIMUM_MODEL_SAMPLES,
    MINIMUM_THRESHOLD_HISTORY,
    PATH_THRESHOLD,
    _episode_metrics,
    suppress_overlapping_warnings,
)


CLASS_WEIGHTS = (1.0, 2.0, 4.0, 8.0)


def _weighted_sample(data, positive_class_weight):
    config = replace(
        TAIL_CONFIG,
        horizon_days=HORIZON_DAYS,
        minimum_samples=MINIMUM_MODEL_SAMPLES,
        positive_class_weight=positive_class_weight,
    )
    forecast = walk_forward_event_probabilities(
        data,
        config,
        event="PATH_DOWNSIDE",
        threshold=PATH_THRESHOLD,
        feature_columns=PROFILE.columns,
        decision_period="W-FRI",
    )
    decision_mask = ~data.index.to_period("W-FRI").duplicated()
    sample = forecast.loc[decision_mask].copy()
    positions = data.index.get_indexer(sample.index)
    complete = positions + HORIZON_DAYS < len(data)
    sample = sample.iloc[np.flatnonzero(complete)].copy()
    positions = positions[complete]
    outcome, lead = forward_path_outcomes(
        data["QQQ_Close"].to_numpy(dtype=float),
        positions,
        HORIZON_DAYS,
        PATH_THRESHOLD,
    )
    sample["Outcome"] = outcome
    sample["LeadTradingDays"] = lead
    sample = sample.loc[sample["ModelSamples"] >= MINIMUM_MODEL_SAMPLES]
    sample["Q90"] = expanding_quantile(
        sample["EventProbability"], 0.90, MINIMUM_THRESHOLD_HISTORY
    )
    return sample.dropna(subset=["Q90"])


def _common_samples(data):
    samples = {
        weight: _weighted_sample(data, weight)
        for weight in CLASS_WEIGHTS
    }
    common_index = samples[CLASS_WEIGHTS[0]].index
    for sample in samples.values():
        common_index = common_index.intersection(sample.index)
    return {
        weight: sample.loc[common_index].copy()
        for weight, sample in samples.items()
    }


def _metric_rows(samples):
    rows = []
    for weight, sample in samples.items():
        raw_warning = sample["EventProbability"] >= sample["Q90"]
        accepted = suppress_overlapping_warnings(
            raw_warning, COOLDOWN_DECISIONS
        )
        for period, (start, end) in PERIODS.items():
            window = sample.loc[start:end]
            warning = accepted.loc[window.index]
            rank_metrics = classification_metrics(
                window["Outcome"], window["EventProbability"], window["Q90"]
            )
            alert_metrics = classification_metrics(
                window["Outcome"], warning.astype(float), 0.5
            )
            episode_count, detected, episode_recall = _episode_metrics(
                window["Outcome"], warning
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 52.0,
            )
            true_positive = warning & window["Outcome"].astype(bool)
            precision_low, precision_high = _wilson_interval(
                alert_metrics["TP"],
                alert_metrics["TP"] + alert_metrics["FP"],
            )
            base_brier = np.mean(
                (window["BaseEventProbability"] - window["Outcome"]) ** 2
            )
            rows.append({
                "PositiveClassWeight": weight,
                "Period": period,
                "Observations": len(window),
                "EventRate": window["Outcome"].mean(),
                "Warnings": alert_metrics["Warnings"],
                "TP": alert_metrics["TP"],
                "FP": alert_metrics["FP"],
                "FN": alert_metrics["FN"],
                "Precision": alert_metrics["Precision"],
                "PrecisionLow95": precision_low,
                "PrecisionHigh95": precision_high,
                "Recall": alert_metrics["Recall"],
                "EpisodeCount": episode_count,
                "DetectedEpisodes": detected,
                "EpisodeRecall": episode_recall,
                "ROC_AUC": rank_metrics["ROC_AUC"],
                "PR_AUC": rank_metrics["PR_AUC"],
                "BrierScore": rank_metrics["BrierScore"],
                "BrierSkillVsBase": 1.0 - rank_metrics["BrierScore"] / base_brier,
                "FalsePositivesPerYear": alert_metrics["FP"] / years,
                "MeanLeadTradingDays": window.loc[
                    true_positive, "LeadTradingDays"
                ].mean(),
            })
    return pd.DataFrame(rows)


def _selection(metrics):
    development = metrics.loc[
        metrics["Period"] == "DEVELOPMENT_TO_2017"
    ].copy()
    baseline_pr_auc = development.loc[
        development["PositiveClassWeight"] == 1.0, "PR_AUC"
    ].iloc[0]
    development["RankingPass"] = development["PR_AUC"] > baseline_pr_auc
    development["PrecisionPass"] = development["Precision"] >= 0.25
    development["EpisodeRecallPass"] = development["EpisodeRecall"] >= 0.25
    development["FalsePositivePass"] = development["FalsePositivesPerYear"] <= 1.0
    development["SamplePass"] = development["Warnings"] >= 5
    development["Pass"] = development[[
        "RankingPass", "PrecisionPass", "EpisodeRecallPass",
        "FalsePositivePass", "SamplePass",
    ]].all(axis=1)
    development = development.sort_values(
        ["Pass", "PR_AUC", "EpisodeRecall", "Precision"],
        ascending=[False, False, False, False],
    )
    return development[[
        "PositiveClassWeight", "Observations", "EventRate", "Warnings", "TP",
        "FP", "FN", "Precision", "Recall", "EpisodeRecall", "ROC_AUC",
        "PR_AUC", "BrierSkillVsBase", "FalsePositivesPerYear",
        "MeanLeadTradingDays", "RankingPass", "PrecisionPass",
        "EpisodeRecallPass", "FalsePositivePass", "SamplePass", "Pass",
    ]]


def run_weighted_path_objective_validation():
    data = _load_feature_data()
    samples = _common_samples(data)
    metrics = _metric_rows(samples)
    selection = _selection(metrics)
    metrics.to_csv(RESULT_DIR / "weighted_path_objective_metrics.csv", index=False)
    selection.to_csv(
        RESULT_DIR / "weighted_path_objective_selection.csv", index=False
    )
    return {"metrics": metrics, "selection": selection}


if __name__ == "__main__":
    reports = run_weighted_path_objective_validation()
    print("Development objective selection")
    print(reports["selection"].to_string(index=False))
    print("\nValidation and lock")
    columns = [
        "PositiveClassWeight", "Period", "Warnings", "TP", "FP",
        "Precision", "Recall", "EpisodeRecall", "ROC_AUC", "PR_AUC",
        "BrierSkillVsBase", "FalsePositivesPerYear", "MeanLeadTradingDays",
    ]
    future = reports["metrics"].loc[
        reports["metrics"]["Period"].isin(
            ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
        )
    ]
    print(future[columns].to_string(index=False))
