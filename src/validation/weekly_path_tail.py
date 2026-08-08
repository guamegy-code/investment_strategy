"""Evaluate weekly path-tail warnings with overlap-aware event metrics."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .multi_horizon_probability import TAIL_CONFIG
from .path_tail_targets import (
    PERIODS,
    _load_feature_data,
    forward_path_outcomes,
)
from .precision_thresholds import PROFILE, _wilson_interval, expanding_quantile
from .probability_accuracy import classification_metrics
from .probabilistic_allocation import walk_forward_event_probabilities


HORIZON_DAYS = 21
PATH_THRESHOLD = -0.05
MINIMUM_MODEL_SAMPLES = 260
MINIMUM_THRESHOLD_HISTORY = 260
COOLDOWN_DECISIONS = 3


def suppress_overlapping_warnings(raw_warning, cooldown_decisions=COOLDOWN_DECISIONS):
    """Keep one warning, then suppress the next overlapping weekly decisions."""
    raw_warning = pd.Series(raw_warning, copy=False).astype(bool)
    accepted = pd.Series(False, index=raw_warning.index)
    cooldown = 0
    for position, warning in enumerate(raw_warning):
        if cooldown:
            cooldown -= 1
            continue
        if warning:
            accepted.iloc[position] = True
            cooldown = cooldown_decisions
    return accepted


def episode_labels(outcome):
    """Group consecutive positive weekly labels into distinct risk episodes."""
    outcome = pd.Series(outcome, copy=False).astype(bool)
    starts = outcome & ~outcome.shift(1, fill_value=False)
    identifiers = starts.cumsum().where(outcome, 0).astype(int)
    return identifiers


def _weekly_sample():
    data = _load_feature_data()
    config = replace(
        TAIL_CONFIG,
        horizon_days=HORIZON_DAYS,
        minimum_samples=MINIMUM_MODEL_SAMPLES,
    )
    forecasts = walk_forward_event_probabilities(
        data,
        config,
        event="PATH_DOWNSIDE",
        threshold=PATH_THRESHOLD,
        feature_columns=PROFILE.columns,
        decision_period="W-FRI",
    )
    decision_mask = ~data.index.to_period("W-FRI").duplicated()
    sample = forecasts.loc[decision_mask].copy()
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
    sample["Q95"] = expanding_quantile(
        sample["EventProbability"], 0.95, MINIMUM_THRESHOLD_HISTORY
    )
    return sample.dropna(subset=["Q90", "Q95"])


def _episode_metrics(outcome, predicted):
    episodes = episode_labels(outcome)
    episode_ids = sorted(set(episodes) - {0})
    detected = sum(bool(predicted.loc[episodes == episode].any()) for episode in episode_ids)
    return len(episode_ids), detected, detected / len(episode_ids) if episode_ids else np.nan


def _metric_rows(sample):
    rows = []
    for threshold_name in ("Q90", "Q95"):
        raw = sample["EventProbability"] >= sample[threshold_name]
        variants = {
            "RAW": raw,
            "COOLDOWN_4W": suppress_overlapping_warnings(raw),
        }
        for rule, prediction in variants.items():
            for period, (start, end) in PERIODS.items():
                window = sample.loc[start:end]
                predicted = prediction.loc[window.index]
                metrics = classification_metrics(
                    window["Outcome"], predicted.astype(float), 0.5
                )
                episode_count, detected_episodes, episode_recall = _episode_metrics(
                    window["Outcome"], predicted
                )
                precision_low, precision_high = _wilson_interval(
                    metrics["TP"], metrics["TP"] + metrics["FP"]
                )
                years = max(
                    (window.index.max() - window.index.min()).days / 365.25,
                    1.0 / 52.0,
                )
                true_positive = predicted & window["Outcome"].astype(bool)
                rows.append({
                    "Threshold": threshold_name,
                    "SignalRule": rule,
                    "Period": period,
                    **metrics,
                    "EpisodeCount": episode_count,
                    "DetectedEpisodes": detected_episodes,
                    "EpisodeRecall": episode_recall,
                    "FalsePositivesPerYear": metrics["FP"] / years,
                    "PrecisionLow95": precision_low,
                    "PrecisionHigh95": precision_high,
                    "MeanLeadTradingDays": window.loc[
                        true_positive, "LeadTradingDays"
                    ].mean(),
                })
    return pd.DataFrame(rows)


def _selection(metrics):
    development = metrics.loc[
        (metrics["Period"] == "DEVELOPMENT_TO_2017")
        & (metrics["SignalRule"] == "COOLDOWN_4W")
    ].copy()
    development["PrecisionPass"] = development["Precision"] >= 0.25
    development["EpisodeRecallPass"] = development["EpisodeRecall"] >= 0.25
    development["FalsePositivePass"] = development["FalsePositivesPerYear"] <= 1.0
    development["SamplePass"] = development["Warnings"] >= 5
    development["Pass"] = development[[
        "PrecisionPass", "EpisodeRecallPass", "FalsePositivePass", "SamplePass"
    ]].all(axis=1)
    return development[[
        "Threshold", "Observations", "EventRate", "Warnings", "TP", "FP", "FN",
        "Precision", "PrecisionLow95", "PrecisionHigh95", "Recall",
        "EpisodeCount", "DetectedEpisodes", "EpisodeRecall",
        "FalsePositivesPerYear", "MeanLeadTradingDays", "PrecisionPass",
        "EpisodeRecallPass", "FalsePositivePass", "SamplePass", "Pass",
    ]]


def _signals(sample):
    rows = []
    for threshold_name in ("Q90", "Q95"):
        raw = sample["EventProbability"] >= sample[threshold_name]
        accepted = suppress_overlapping_warnings(raw)
        for date in accepted.index[accepted]:
            row = sample.loc[date]
            rows.append({
                "Threshold": threshold_name,
                "SignalDate": date,
                "Probability": row["EventProbability"],
                "Cutoff": row[threshold_name],
                "Outcome": bool(row["Outcome"]),
                "LeadTradingDays": row["LeadTradingDays"],
            })
    return pd.DataFrame(rows)


def run_weekly_path_tail_validation():
    sample = _weekly_sample()
    metrics = _metric_rows(sample)
    selection = _selection(metrics)
    signals = _signals(sample)
    metrics.to_csv(RESULT_DIR / "weekly_path_tail_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "weekly_path_tail_selection.csv", index=False)
    signals.to_csv(RESULT_DIR / "weekly_path_tail_signals.csv", index=False)
    return {"metrics": metrics, "selection": selection, "signals": signals}


if __name__ == "__main__":
    reports = run_weekly_path_tail_validation()
    print("Development gate")
    print(reports["selection"].to_string(index=False))
    print("\nValidation and lock")
    columns = [
        "Threshold", "SignalRule", "Period", "Observations", "EventRate",
        "Warnings", "TP", "FP", "Precision", "Recall", "EpisodeCount",
        "DetectedEpisodes", "EpisodeRecall", "FalsePositivesPerYear",
        "MeanLeadTradingDays",
    ]
    future = reports["metrics"].loc[
        (reports["metrics"]["SignalRule"] == "COOLDOWN_4W")
        & reports["metrics"]["Period"].isin(
            ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
        )
    ]
    print(future[columns].to_string(index=False))
