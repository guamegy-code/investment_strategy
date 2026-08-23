"""Predict the onset of a new 21-day path-loss episode."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .discrete_hazard import (
    HAZARD_PROFILES,
    first_breach_days,
    fit_discrete_hazard,
    walk_forward_hazard_probabilities,
)
from .multi_horizon_probability import TAIL_CONFIG
from .path_tail_targets import PERIODS, _load_feature_data
from .precision_thresholds import PROFILE, _wilson_interval, expanding_quantile
from .probability_accuracy import classification_metrics
from .weekly_path_tail import (
    COOLDOWN_DECISIONS,
    HORIZON_DAYS,
    MINIMUM_MODEL_SAMPLES,
    MINIMUM_THRESHOLD_HISTORY,
    PATH_THRESHOLD,
    suppress_overlapping_warnings,
)


MODELS = ("PATH_HAZARD_CONTROL", "EPISODE_ONSET_HAZARD")


def episode_onset_labels(path_events):
    """Split path events into first/onset and already-active observations."""
    event = np.asarray(path_events, dtype=bool)
    previous = np.r_[False, event[:-1]]
    onset = event & ~previous
    ongoing = event & previous
    return onset, ongoing


def onset_training_targets(breach_days):
    """Censor ongoing episodes and retain breach timing only for onsets."""
    breach_days = np.asarray(breach_days, dtype=int)
    onset, ongoing = episode_onset_labels(breach_days > 0)
    eligible = ~ongoing
    targets = np.where(onset[eligible], breach_days[eligible], 0)
    return eligible, targets


def walk_forward_onset_probabilities(data):
    frame = data.copy()
    frame["QQQ_EMA200_DISTANCE"] = frame["QQQ_Close"] / frame["QQQ_EMA200"] - 1.0
    decision_mask = ~frame.index.to_period("W-FRI").duplicated()
    decisions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    features = frame.loc[:, PROFILE.columns].to_numpy(dtype=float)
    complete_decision = decisions + HORIZON_DAYS < len(frame)
    all_breach = np.zeros(len(decisions), dtype=int)
    all_breach[complete_decision] = first_breach_days(
        close, decisions[complete_decision], HORIZON_DAYS, PATH_THRESHOLD
    )
    onset, ongoing = episode_onset_labels(all_breach > 0)

    probability = pd.Series(np.nan, index=frame.index, dtype=float)
    base_probability = pd.Series(np.nan, index=frame.index, dtype=float)
    samples = pd.Series(0, index=frame.index, dtype=int)

    for decision_number, position in enumerate(decisions):
        resolved = np.arange(decision_number)[
            decisions[:decision_number] + HORIZON_DAYS <= position
        ]
        if len(resolved):
            resolved = resolved[
                np.isfinite(features[decisions[resolved]]).all(axis=1)
                & ~ongoing[resolved]
            ]
        if (
            len(resolved) < MINIMUM_MODEL_SAMPLES
            or not np.isfinite(features[position]).all()
        ):
            current_probability = TAIL_CONFIG.default_up_probability
            current_base = TAIL_CONFIG.default_up_probability
        else:
            train_positions = decisions[resolved]
            train_breach = np.where(onset[resolved], all_breach[resolved], 0)
            current_probability, current_base = fit_discrete_hazard(
                features[train_positions], train_breach, features[position]
            )
            reliability = min(1.0, len(resolved) / 520.0)
            current_probability = current_base + reliability * (
                current_probability - current_base
            )
        probability.iloc[position] = current_probability
        base_probability.iloc[position] = current_base
        samples.iloc[position] = len(resolved)

    return pd.DataFrame({
        "EventProbability": probability.ffill(),
        "BaseEventProbability": base_probability.ffill(),
        "ModelSamples": samples.where(decision_mask).ffill().fillna(0).astype(int),
    }, index=frame.index)


def _model_sample(data, model_name):
    profile = next(item for item in HAZARD_PROFILES if item.name == "DISCRETE_HAZARD")
    if model_name == "PATH_HAZARD_CONTROL":
        forecast = walk_forward_hazard_probabilities(data, profile)
    elif model_name == "EPISODE_ONSET_HAZARD":
        forecast = walk_forward_onset_probabilities(data)
    else:
        raise ValueError(f"unknown onset model: {model_name}")

    decision_mask = ~data.index.to_period("W-FRI").duplicated()
    decisions = np.flatnonzero(decision_mask)
    complete = decisions + HORIZON_DAYS < len(data)
    decision_positions = decisions[complete]
    sample = forecast.iloc[decision_positions].copy()
    breach = first_breach_days(
        data["QQQ_Close"].to_numpy(dtype=float),
        decision_positions,
        HORIZON_DAYS,
        PATH_THRESHOLD,
    )
    onset, ongoing = episode_onset_labels(breach > 0)
    sample["Outcome"] = onset.astype(int)
    sample["EvaluationEligible"] = ~ongoing
    sample["LeadTradingDays"] = np.where(onset, breach.astype(float), np.nan)
    sample = sample.loc[sample["ModelSamples"] >= MINIMUM_MODEL_SAMPLES]
    sample["Q90"] = expanding_quantile(
        sample["EventProbability"], 0.90, MINIMUM_THRESHOLD_HISTORY
    )
    return sample.dropna(subset=["Q90"])


def _common_samples(data):
    samples = {model: _model_sample(data, model) for model in MODELS}
    common_index = samples[MODELS[0]].index.intersection(samples[MODELS[1]].index)
    return {model: sample.loc[common_index] for model, sample in samples.items()}


def _metric_rows(samples):
    rows = []
    for model, sample in samples.items():
        raw = sample["EventProbability"] >= sample["Q90"]
        warning = suppress_overlapping_warnings(raw, COOLDOWN_DECISIONS)
        for period, (start, end) in PERIODS.items():
            full_window = sample.loc[start:end]
            window = full_window.loc[full_window["EvaluationEligible"]]
            predicted = warning.loc[window.index]
            ranking = classification_metrics(
                window["Outcome"], window["EventProbability"], window["Q90"]
            )
            alerts = classification_metrics(
                window["Outcome"], predicted.astype(float), 0.5
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 52.0,
            )
            precision_low, precision_high = _wilson_interval(
                alerts["TP"], alerts["TP"] + alerts["FP"]
            )
            true_positive = predicted & window["Outcome"].astype(bool)
            base_brier = np.mean(
                (window["BaseEventProbability"] - window["Outcome"]) ** 2
            )
            rows.append({
                "Model": model,
                "Period": period,
                "Observations": len(window),
                "ExcludedOngoing": len(full_window) - len(window),
                "OnsetEvents": int(window["Outcome"].sum()),
                "Warnings": alerts["Warnings"],
                "TP": alerts["TP"],
                "FP": alerts["FP"],
                "FN": alerts["FN"],
                "Precision": alerts["Precision"],
                "PrecisionLow95": precision_low,
                "PrecisionHigh95": precision_high,
                "OnsetRecall": alerts["Recall"],
                "ROC_AUC": ranking["ROC_AUC"],
                "PR_AUC": ranking["PR_AUC"],
                "BrierSkillVsBase": 1.0 - ranking["BrierScore"] / base_brier,
                "FalsePositivesPerYear": alerts["FP"] / years,
                "MeanLeadTradingDays": window.loc[
                    true_positive, "LeadTradingDays"
                ].mean(),
            })
    return pd.DataFrame(rows)


def _development_selection(metrics):
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"].copy()
    control = development.loc[
        development["Model"] == "PATH_HAZARD_CONTROL"
    ].iloc[0]
    development["RankingPass"] = development["PR_AUC"] > control["PR_AUC"]
    development["PrecisionPass"] = development["Precision"] >= 0.25
    development["OnsetRecallPass"] = development["OnsetRecall"] >= 0.25
    development["FalsePositivePass"] = development["FalsePositivesPerYear"] <= 1.0
    development["SamplePass"] = development["Warnings"] >= 5
    development["Pass"] = (
        (development["Model"] == "EPISODE_ONSET_HAZARD")
        & development[[
            "RankingPass", "PrecisionPass", "OnsetRecallPass",
            "FalsePositivePass", "SamplePass",
        ]].all(axis=1)
    )
    return development.sort_values(["Pass", "PR_AUC"], ascending=[False, False])


def run_episode_onset_hazard_validation():
    data = _load_feature_data()
    samples = _common_samples(data)
    metrics = _metric_rows(samples)
    selection = _development_selection(metrics)
    metrics.to_csv(RESULT_DIR / "episode_onset_hazard_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "episode_onset_hazard_selection.csv", index=False)
    return {"metrics": metrics, "selection": selection}


if __name__ == "__main__":
    reports = run_episode_onset_hazard_validation()
    columns = [
        "Model", "Observations", "ExcludedOngoing", "OnsetEvents", "Warnings",
        "TP", "FP", "Precision", "OnsetRecall", "ROC_AUC", "PR_AUC",
        "FalsePositivesPerYear", "MeanLeadTradingDays", "RankingPass",
        "PrecisionPass", "OnsetRecallPass", "FalsePositivePass", "SamplePass", "Pass",
    ]
    print("Development onset-model selection")
    print(reports["selection"][columns].to_string(index=False))
    if reports["selection"]["Pass"].any():
        print("\nValidation and lock")
        future = reports["metrics"].loc[
            reports["metrics"]["Period"].isin(
                ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
            )
        ]
        print(future.to_string(index=False))
    else:
        print("\nNo development candidate passed; later periods were not used for selection.")
