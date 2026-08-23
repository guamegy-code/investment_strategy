"""Discrete-time hazard models for the first 5% loss within 21 days."""

from __future__ import annotations

from dataclasses import dataclass, replace

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


BUCKET_END_DAYS = np.array((5, 10, 15, 21))
BUCKET_TIME = np.array((-0.75, -0.25, 0.25, 0.75))


@dataclass(frozen=True)
class HazardProfile:
    name: str
    time_interactions: bool


HAZARD_PROFILES = (
    HazardProfile("DISCRETE_HAZARD", False),
    HazardProfile("DISCRETE_HAZARD_TIME_INTERACTION", True),
)


def first_breach_days(close, positions, horizon_days, threshold):
    close = np.asarray(close, dtype=float)
    result = np.zeros(len(positions), dtype=int)
    for index, position in enumerate(positions):
        path = close[position + 1:position + horizon_days + 1] / close[position] - 1.0
        breach = np.flatnonzero(path <= threshold)
        if len(breach):
            result[index] = int(breach[0] + 1)
    return result


def _standardize(train_x, predict_x):
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    return (
        np.clip((train_x - mean) / scale, -5.0, 5.0),
        np.clip((predict_x - mean) / scale, -5.0, 5.0),
    )


def person_period_design(standardized_x, breach_days, time_interactions):
    """Expand one forecast origin into at-risk rows up to its event bucket."""
    rows = []
    outcomes = []
    feature_count = standardized_x.shape[1]
    for features, breach_day in zip(standardized_x, breach_days):
        event_bucket = (
            int(np.searchsorted(BUCKET_END_DAYS, breach_day, side="left"))
            if breach_day > 0
            else None
        )
        last_bucket = event_bucket if event_bucket is not None else len(BUCKET_END_DAYS) - 1
        for bucket in range(last_bucket + 1):
            bucket_intercepts = np.zeros(len(BUCKET_END_DAYS))
            bucket_intercepts[bucket] = 1.0
            parts = [bucket_intercepts, features]
            if time_interactions:
                parts.append(features * BUCKET_TIME[bucket])
            rows.append(np.concatenate(parts))
            outcomes.append(float(event_bucket == bucket))
    width = len(BUCKET_END_DAYS) + feature_count * (2 if time_interactions else 1)
    return np.asarray(rows).reshape(-1, width), np.asarray(outcomes)


def _prediction_design(standardized_point, time_interactions):
    rows = []
    for bucket in range(len(BUCKET_END_DAYS)):
        bucket_intercepts = np.zeros(len(BUCKET_END_DAYS))
        bucket_intercepts[bucket] = 1.0
        parts = [bucket_intercepts, standardized_point]
        if time_interactions:
            parts.append(standardized_point * BUCKET_TIME[bucket])
        rows.append(np.concatenate(parts))
    return np.asarray(rows)


def _sigmoid(value):
    return 1.0 / (1.0 + np.exp(-np.clip(value, -35.0, 35.0)))


def fit_discrete_hazard(
    train_x,
    breach_days,
    predict_x,
    time_interactions=False,
    ridge_penalty=1.0,
):
    standardized, point = _standardize(train_x, predict_x)
    design, outcome = person_period_design(
        standardized, breach_days, time_interactions
    )
    prediction_design = _prediction_design(point, time_interactions)
    beta = np.zeros(design.shape[1])
    event_rate = (np.sum(breach_days > 0) + 1.0) / (len(breach_days) + 2.0)
    bucket_hazard = 1.0 - (1.0 - event_rate) ** (1.0 / len(BUCKET_END_DAYS))
    beta[:len(BUCKET_END_DAYS)] = np.log(bucket_hazard / (1.0 - bucket_hazard))
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[:len(BUCKET_END_DAYS), :len(BUCKET_END_DAYS)] = 0.0

    for _ in range(30):
        fitted = _sigmoid(design @ beta)
        weights = np.maximum(fitted * (1.0 - fitted), 1e-6)
        gradient = design.T @ (outcome - fitted) - penalty @ beta
        hessian = (design.T * weights) @ design + penalty
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break

    hazards = _sigmoid(prediction_design @ beta)
    probability = 1.0 - np.prod(1.0 - hazards)
    return float(np.clip(probability, 0.01, 0.99)), float(event_rate)


def walk_forward_hazard_probabilities(data, profile):
    frame = data.copy()
    frame["QQQ_EMA200_DISTANCE"] = (
        frame["QQQ_Close"] / frame["QQQ_EMA200"] - 1.0
    )
    decision_mask = ~frame.index.to_period("W-FRI").duplicated()
    decisions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    features = frame.loc[:, PROFILE.columns].to_numpy(dtype=float)
    probability = pd.Series(np.nan, index=frame.index, dtype=float)
    base_probability = pd.Series(np.nan, index=frame.index, dtype=float)
    samples = pd.Series(0, index=frame.index, dtype=int)

    for position in decisions:
        eligible = decisions[decisions + HORIZON_DAYS <= position]
        if len(eligible):
            eligible = eligible[np.isfinite(features[eligible]).all(axis=1)]
        if (
            len(eligible) < MINIMUM_MODEL_SAMPLES
            or not np.isfinite(features[position]).all()
        ):
            current_probability = TAIL_CONFIG.default_up_probability
            current_base = TAIL_CONFIG.default_up_probability
        else:
            breach = first_breach_days(
                close, eligible, HORIZON_DAYS, PATH_THRESHOLD
            )
            current_probability, current_base = fit_discrete_hazard(
                features[eligible],
                breach,
                features[position],
                time_interactions=profile.time_interactions,
            )
            reliability = min(1.0, len(eligible) / 520.0)
            current_probability = current_base + reliability * (
                current_probability - current_base
            )
        probability.iloc[position] = current_probability
        base_probability.iloc[position] = current_base
        samples.iloc[position] = len(eligible)

    return pd.DataFrame({
        "EventProbability": probability.ffill(),
        "BaseEventProbability": base_probability.ffill(),
        "ModelSamples": samples.where(decision_mask).ffill().fillna(0).astype(int),
    }, index=frame.index)


def _model_sample(data, model_name):
    if model_name == "LOGISTIC_BASELINE":
        config = replace(
            TAIL_CONFIG,
            horizon_days=HORIZON_DAYS,
            minimum_samples=MINIMUM_MODEL_SAMPLES,
        )
        forecast = walk_forward_event_probabilities(
            data,
            config,
            event="PATH_DOWNSIDE",
            threshold=PATH_THRESHOLD,
            feature_columns=PROFILE.columns,
            decision_period="W-FRI",
        )
    else:
        profile = next(item for item in HAZARD_PROFILES if item.name == model_name)
        forecast = walk_forward_hazard_probabilities(data, profile)
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
    model_names = ("LOGISTIC_BASELINE",) + tuple(p.name for p in HAZARD_PROFILES)
    samples = {name: _model_sample(data, name) for name in model_names}
    common_index = samples[model_names[0]].index
    for sample in samples.values():
        common_index = common_index.intersection(sample.index)
    return {name: sample.loc[common_index] for name, sample in samples.items()}


def _metrics(samples):
    rows = []
    for model, sample in samples.items():
        raw = sample["EventProbability"] >= sample["Q90"]
        accepted = suppress_overlapping_warnings(raw, COOLDOWN_DECISIONS)
        for period, (start, end) in PERIODS.items():
            window = sample.loc[start:end]
            warning = accepted.loc[window.index]
            ranking = classification_metrics(
                window["Outcome"], window["EventProbability"], window["Q90"]
            )
            alerts = classification_metrics(
                window["Outcome"], warning.astype(float), 0.5
            )
            episode_count, detected, episode_recall = _episode_metrics(
                window["Outcome"], warning
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 52.0,
            )
            precision_low, precision_high = _wilson_interval(
                alerts["TP"], alerts["TP"] + alerts["FP"]
            )
            true_positive = warning & window["Outcome"].astype(bool)
            base_brier = np.mean(
                (window["BaseEventProbability"] - window["Outcome"]) ** 2
            )
            rows.append({
                "Model": model,
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
                "ROC_AUC": ranking["ROC_AUC"],
                "PR_AUC": ranking["PR_AUC"],
                "BrierScore": ranking["BrierScore"],
                "BrierSkillVsBase": 1.0 - ranking["BrierScore"] / base_brier,
                "FalsePositivesPerYear": alerts["FP"] / years,
                "MeanLeadTradingDays": window.loc[
                    true_positive, "LeadTradingDays"
                ].mean(),
            })
    return pd.DataFrame(rows)


def _selection(metrics):
    development = metrics.loc[
        metrics["Period"] == "DEVELOPMENT_TO_2017"
    ].copy()
    baseline_pr = development.loc[
        development["Model"] == "LOGISTIC_BASELINE", "PR_AUC"
    ].iloc[0]
    development["RankingPass"] = development["PR_AUC"] > baseline_pr
    development["PrecisionPass"] = development["Precision"] >= 0.25
    development["EpisodeRecallPass"] = development["EpisodeRecall"] >= 0.25
    development["FalsePositivePass"] = development["FalsePositivesPerYear"] <= 1.0
    development["SamplePass"] = development["Warnings"] >= 5
    development["Pass"] = development[[
        "RankingPass", "PrecisionPass", "EpisodeRecallPass",
        "FalsePositivePass", "SamplePass",
    ]].all(axis=1)
    return development[[
        "Model", "Observations", "EventRate", "Warnings", "TP", "FP", "FN",
        "Precision", "Recall", "EpisodeRecall", "ROC_AUC", "PR_AUC",
        "BrierSkillVsBase", "FalsePositivesPerYear", "MeanLeadTradingDays",
        "RankingPass", "PrecisionPass", "EpisodeRecallPass",
        "FalsePositivePass", "SamplePass", "Pass",
    ]].sort_values(["Pass", "PR_AUC"], ascending=[False, False])


def run_discrete_hazard_validation():
    data = _load_feature_data()
    samples = _common_samples(data)
    metrics = _metrics(samples)
    selection = _selection(metrics)
    metrics.to_csv(RESULT_DIR / "discrete_hazard_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "discrete_hazard_selection.csv", index=False)
    return {"metrics": metrics, "selection": selection}


if __name__ == "__main__":
    reports = run_discrete_hazard_validation()
    print("Development hazard selection")
    print(reports["selection"].to_string(index=False))
    print("\nValidation and lock")
    columns = [
        "Model", "Period", "Warnings", "TP", "FP", "Precision", "Recall",
        "EpisodeRecall", "ROC_AUC", "PR_AUC", "BrierSkillVsBase",
        "FalsePositivesPerYear", "MeanLeadTradingDays",
    ]
    future = reports["metrics"].loc[
        reports["metrics"]["Period"].isin(
            ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
        )
    ]
    print(future[columns].to_string(index=False))
