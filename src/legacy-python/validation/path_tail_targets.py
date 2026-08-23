"""Compare terminal-return and forward-path drawdown prediction targets."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import STATIC_RETIREMENT_7030
from .enhanced_probability_features import EnhancedProbabilityBacktest, SIGNAL_TICKERS
from .multi_horizon_probability import TAIL_CONFIG
from .precision_thresholds import (
    MINIMUM_THRESHOLD_HISTORY,
    PROFILE,
    _wilson_interval,
    expanding_quantile,
)
from .probability_accuracy import classification_metrics
from .probabilistic_allocation import walk_forward_event_probabilities


@dataclass(frozen=True)
class TargetDefinition:
    name: str
    horizon_days: int
    threshold: float
    event: str


TARGETS = (
    TargetDefinition("TERMINAL_21D_MINUS_5", 21, -0.05, "DOWNSIDE"),
    TargetDefinition("PATH_21D_MINUS_5", 21, -0.05, "PATH_DOWNSIDE"),
    TargetDefinition("PATH_21D_MINUS_7_5", 21, -0.075, "PATH_DOWNSIDE"),
    TargetDefinition("PATH_42D_MINUS_10", 42, -0.10, "PATH_DOWNSIDE"),
)

PERIODS = {
    "DEVELOPMENT_TO_2017": (None, "2017-12-31"),
    "VALIDATION_2018_2022": ("2018-01-01", "2022-12-31"),
    "LOCK_2023_PRESENT": ("2023-01-01", None),
    "COMMON": (None, None),
}


def forward_path_outcomes(close, positions, horizon_days, threshold):
    """Return path-event labels and first barrier-breach trading-day offsets."""
    close = np.asarray(close, dtype=float)
    outcomes = []
    leads = []
    for position in positions:
        path = close[position + 1:position + horizon_days + 1] / close[position] - 1.0
        breaches = np.flatnonzero(path <= threshold)
        outcomes.append(bool(len(breaches)))
        leads.append(float(breaches[0] + 1) if len(breaches) else np.nan)
    return np.asarray(outcomes, dtype=int), np.asarray(leads, dtype=float)


def _load_feature_data():
    backtest = EnhancedProbabilityBacktest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=SIGNAL_TICKERS,
        feature_profile=PROFILE,
    )
    return backtest.data


def _target_sample(data, target):
    config = replace(TAIL_CONFIG, horizon_days=target.horizon_days)
    forecast = walk_forward_event_probabilities(
        data,
        config,
        event=target.event,
        threshold=target.threshold,
        feature_columns=PROFILE.columns,
    )
    decision_mask = ~data.index.to_period("M").duplicated()
    sample = forecast.loc[decision_mask].copy()
    positions = data.index.get_indexer(sample.index)
    complete = positions + target.horizon_days < len(data)
    sample = sample.iloc[np.flatnonzero(complete)].copy()
    positions = positions[complete]
    close = data["QQQ_Close"].to_numpy(dtype=float)
    if target.event == "PATH_DOWNSIDE":
        outcome, lead = forward_path_outcomes(
            close, positions, target.horizon_days, target.threshold
        )
    else:
        returns = close[positions + target.horizon_days] / close[positions] - 1.0
        outcome = (returns <= target.threshold).astype(int)
        lead = np.full(len(outcome), float(target.horizon_days))
    sample["Outcome"] = outcome
    sample["LeadTradingDays"] = lead
    sample = sample.loc[sample["ModelSamples"] >= config.minimum_samples]
    sample["Q90"] = expanding_quantile(
        sample["EventProbability"], 0.90, MINIMUM_THRESHOLD_HISTORY
    )
    sample["Q95"] = expanding_quantile(
        sample["EventProbability"], 0.95, MINIMUM_THRESHOLD_HISTORY
    )
    return sample.dropna(subset=["Q90", "Q95"])


def _common_samples(data):
    samples = {target.name: _target_sample(data, target) for target in TARGETS}
    common_index = samples[TARGETS[0].name].index
    for sample in samples.values():
        common_index = common_index.intersection(sample.index)
    return {
        name: sample.loc[common_index].copy()
        for name, sample in samples.items()
    }


def _metric_rows(samples):
    rows = []
    for target_name, sample in samples.items():
        for threshold_name in ("Q90", "Q95"):
            for period, (start, end) in PERIODS.items():
                window = sample.loc[start:end]
                metrics = classification_metrics(
                    window["Outcome"],
                    window["EventProbability"],
                    window[threshold_name],
                )
                precision_low, precision_high = _wilson_interval(
                    metrics["TP"], metrics["TP"] + metrics["FP"]
                )
                years = max(
                    (window.index.max() - window.index.min()).days / 365.25,
                    1.0 / 12.0,
                )
                predicted = window["EventProbability"] >= window[threshold_name]
                true_positive = predicted & window["Outcome"].astype(bool)
                rows.append({
                    "Target": target_name,
                    "WarningThreshold": threshold_name,
                    "Period": period,
                    **metrics,
                    "PrecisionLow95": precision_low,
                    "PrecisionHigh95": precision_high,
                    "FalsePositivesPerYear": metrics["FP"] / years,
                    "MeanLeadTradingDays": window.loc[
                        true_positive, "LeadTradingDays"
                    ].mean(),
                    "MedianLeadTradingDays": window.loc[
                        true_positive, "LeadTradingDays"
                    ].median(),
                })
    return pd.DataFrame(rows)


def _development_selection(metrics):
    development = metrics.loc[
        (metrics["Period"] == "DEVELOPMENT_TO_2017")
        & (metrics["WarningThreshold"] == "Q90")
    ].copy()
    development["PrecisionPass"] = development["Precision"] >= 0.25
    development["RecallPass"] = development["Recall"] >= 0.25
    development["FalsePositivePass"] = development["FalsePositivesPerYear"] <= 1.0
    development["Pass"] = development[[
        "PrecisionPass", "RecallPass", "FalsePositivePass"
    ]].all(axis=1)
    development = development.sort_values(
        ["Pass", "Precision", "Recall", "PR_AUC"],
        ascending=[False, False, False, False],
    )
    development["DevelopmentRank"] = np.arange(1, len(development) + 1)
    development["SelectedForNextStage"] = (
        development["Pass"]
        & (development.groupby("Pass").cumcount() == 0)
    )
    return development[[
        "DevelopmentRank", "Target", "Observations", "EventRate", "Warnings",
        "TP", "FP", "FN", "Precision", "Recall", "BalancedAccuracy",
        "PR_AUC", "FalsePositivesPerYear", "MeanLeadTradingDays",
        "PrecisionLow95", "PrecisionHigh95", "PrecisionPass", "RecallPass",
        "FalsePositivePass", "Pass",
        "SelectedForNextStage",
    ]]


def run_path_tail_target_validation():
    data = _load_feature_data()
    samples = _common_samples(data)
    metrics = _metric_rows(samples)
    selection = _development_selection(metrics)
    metrics.to_csv(RESULT_DIR / "path_tail_target_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "path_tail_target_selection.csv", index=False)
    return {"metrics": metrics, "selection": selection}


if __name__ == "__main__":
    reports = run_path_tail_target_validation()
    print("Development selection (Q90 only)")
    print(reports["selection"].to_string(index=False))
    print("\nValidation and lock results")
    columns = [
        "Target", "WarningThreshold", "Period", "Observations", "EventRate",
        "Warnings", "TP", "FP", "FN", "Precision", "Recall",
        "BalancedAccuracy", "PR_AUC", "FalsePositivesPerYear",
        "MeanLeadTradingDays",
    ]
    future = reports["metrics"].loc[
        reports["metrics"]["Period"].isin(
            ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
        )
    ]
    print(future[columns].to_string(index=False))
