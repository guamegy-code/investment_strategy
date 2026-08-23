"""Multi-barrier ordinal hazard model for downside-path warnings."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .discrete_hazard import (
    BUCKET_END_DAYS,
    _metrics,
    _model_sample,
    _selection,
    _sigmoid,
    _standardize,
)
from .multi_horizon_probability import TAIL_CONFIG
from .path_tail_targets import _load_feature_data, forward_path_outcomes
from .precision_thresholds import PROFILE, expanding_quantile
from .weekly_path_tail import (
    HORIZON_DAYS,
    MINIMUM_MODEL_SAMPLES,
    MINIMUM_THRESHOLD_HISTORY,
    PATH_THRESHOLD,
)


BARRIERS = np.array((-0.03, -0.05, -0.08))
MODEL_NAME = "ORDINAL_MULTI_BARRIER_HAZARD"


def multi_barrier_breach_days(close, positions, horizon_days, barriers=BARRIERS):
    """Return each origin's first breach day for every nested loss barrier."""
    close = np.asarray(close, dtype=float)
    result = np.zeros((len(positions), len(barriers)), dtype=int)
    for row, position in enumerate(positions):
        path = close[position + 1:position + horizon_days + 1] / close[position] - 1.0
        for column, barrier in enumerate(barriers):
            breach = np.flatnonzero(path <= barrier)
            if len(breach):
                result[row, column] = int(breach[0] + 1)
    return result


def ordinal_person_period_design(standardized_x, breach_days):
    """Stack barrier-specific risk sets while sharing feature coefficients."""
    rows = []
    outcomes = []
    feature_count = standardized_x.shape[1]
    barrier_count = breach_days.shape[1]
    for features, origin_breaches in zip(standardized_x, breach_days):
        for barrier_index, breach_day in enumerate(origin_breaches):
            event_bucket = (
                int(np.searchsorted(BUCKET_END_DAYS, breach_day, side="left"))
                if breach_day > 0
                else None
            )
            last_bucket = event_bucket if event_bucket is not None else len(BUCKET_END_DAYS) - 1
            for bucket in range(last_bucket + 1):
                bucket_intercepts = np.zeros(len(BUCKET_END_DAYS))
                bucket_intercepts[bucket] = 1.0
                # The shallowest barrier is the reference category. Omitting its
                # dummy avoids collinearity with the complete bucket intercepts.
                barrier_offsets = np.zeros(barrier_count - 1)
                if barrier_index:
                    barrier_offsets[barrier_index - 1] = 1.0
                rows.append(np.concatenate((bucket_intercepts, barrier_offsets, features)))
                outcomes.append(float(event_bucket == bucket))
    width = len(BUCKET_END_DAYS) + barrier_count - 1 + feature_count
    return np.asarray(rows).reshape(-1, width), np.asarray(outcomes)


def _ordinal_prediction_design(standardized_point, barrier_count):
    designs = []
    for barrier_index in range(barrier_count):
        rows = []
        for bucket in range(len(BUCKET_END_DAYS)):
            bucket_intercepts = np.zeros(len(BUCKET_END_DAYS))
            bucket_intercepts[bucket] = 1.0
            barrier_offsets = np.zeros(barrier_count - 1)
            if barrier_index:
                barrier_offsets[barrier_index - 1] = 1.0
            rows.append(
                np.concatenate((bucket_intercepts, barrier_offsets, standardized_point))
            )
        designs.append(np.asarray(rows))
    return designs


def fit_ordinal_hazard(train_x, breach_days, predict_x, ridge_penalty=1.0):
    """Fit a proportional-feature hazard model and return nested probabilities."""
    standardized, point = _standardize(train_x, predict_x)
    design, outcome = ordinal_person_period_design(standardized, breach_days)
    prediction_designs = _ordinal_prediction_design(point, breach_days.shape[1])
    beta = np.zeros(design.shape[1])
    pooled_rate = (np.sum(breach_days > 0) + 1.0) / (breach_days.size + 2.0)
    bucket_hazard = 1.0 - (1.0 - pooled_rate) ** (1.0 / len(BUCKET_END_DAYS))
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

    probabilities = np.array([
        1.0 - np.prod(1.0 - _sigmoid(prediction_design @ beta))
        for prediction_design in prediction_designs
    ])
    # Nested loss events require monotonically non-increasing probabilities.
    probabilities = np.minimum.accumulate(probabilities)
    base_probabilities = (np.sum(breach_days > 0, axis=0) + 1.0) / (
        len(breach_days) + 2.0
    )
    base_probabilities = np.minimum.accumulate(base_probabilities)
    return np.clip(probabilities, 0.01, 0.99), np.clip(base_probabilities, 0.01, 0.99)


def walk_forward_ordinal_probabilities(data):
    frame = data.copy()
    frame["QQQ_EMA200_DISTANCE"] = frame["QQQ_Close"] / frame["QQQ_EMA200"] - 1.0
    decision_mask = ~frame.index.to_period("W-FRI").duplicated()
    decisions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    features = frame.loc[:, PROFILE.columns].to_numpy(dtype=float)
    probability = pd.Series(np.nan, index=frame.index, dtype=float)
    shallow_probability = pd.Series(np.nan, index=frame.index, dtype=float)
    severe_probability = pd.Series(np.nan, index=frame.index, dtype=float)
    base_probability = pd.Series(np.nan, index=frame.index, dtype=float)
    samples = pd.Series(0, index=frame.index, dtype=int)

    target_index = int(np.flatnonzero(np.isclose(BARRIERS, PATH_THRESHOLD))[0])
    for position in decisions:
        eligible = decisions[decisions + HORIZON_DAYS <= position]
        if len(eligible):
            eligible = eligible[np.isfinite(features[eligible]).all(axis=1)]
        if len(eligible) < MINIMUM_MODEL_SAMPLES or not np.isfinite(features[position]).all():
            current = np.repeat(TAIL_CONFIG.default_up_probability, len(BARRIERS))
            current_base = current.copy()
        else:
            breach = multi_barrier_breach_days(close, eligible, HORIZON_DAYS)
            current, current_base = fit_ordinal_hazard(
                features[eligible], breach, features[position]
            )
            reliability = min(1.0, len(eligible) / 520.0)
            current = current_base + reliability * (current - current_base)
            current = np.minimum.accumulate(current)
        probability.iloc[position] = current[target_index]
        shallow_probability.iloc[position] = current[0]
        severe_probability.iloc[position] = current[-1]
        base_probability.iloc[position] = current_base[target_index]
        samples.iloc[position] = len(eligible)

    return pd.DataFrame({
        "EventProbability": probability.ffill(),
        "ShallowEventProbability": shallow_probability.ffill(),
        "SevereEventProbability": severe_probability.ffill(),
        "BaseEventProbability": base_probability.ffill(),
        "ModelSamples": samples.where(decision_mask).ffill().fillna(0).astype(int),
    }, index=frame.index)


def _ordinal_sample(data):
    forecast = walk_forward_ordinal_probabilities(data)
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
    sample = sample.loc[sample["ModelSamples"] >= MINIMUM_MODEL_SAMPLES]
    sample["Q90"] = expanding_quantile(
        sample["EventProbability"], 0.90, MINIMUM_THRESHOLD_HISTORY
    )
    return sample.dropna(subset=["Q90"])


def _common_samples(data):
    samples = {
        "LOGISTIC_BASELINE": _model_sample(data, "LOGISTIC_BASELINE"),
        "DISCRETE_HAZARD": _model_sample(data, "DISCRETE_HAZARD"),
        MODEL_NAME: _ordinal_sample(data),
    }
    common_index = samples["LOGISTIC_BASELINE"].index
    for sample in samples.values():
        common_index = common_index.intersection(sample.index)
    return {name: sample.loc[common_index] for name, sample in samples.items()}


def run_ordinal_hazard_validation():
    data = _load_feature_data()
    samples = _common_samples(data)
    metrics = _metrics(samples)
    selection = _selection(metrics)
    metrics.to_csv(RESULT_DIR / "ordinal_hazard_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "ordinal_hazard_selection.csv", index=False)
    return {"metrics": metrics, "selection": selection}


if __name__ == "__main__":
    reports = run_ordinal_hazard_validation()
    print("Development ordinal-hazard selection")
    print(reports["selection"].to_string(index=False))
    print("\nValidation and lock (reported, not used for selection)")
    columns = [
        "Model", "Period", "Warnings", "TP", "FP", "Precision", "Recall",
        "EpisodeRecall", "ROC_AUC", "PR_AUC", "BrierSkillVsBase",
        "FalsePositivesPerYear", "MeanLeadTradingDays",
    ]
    future = reports["metrics"].loc[
        reports["metrics"]["Period"].isin(("VALIDATION_2018_2022", "LOCK_2023_PRESENT"))
    ]
    print(future[columns].to_string(index=False))
