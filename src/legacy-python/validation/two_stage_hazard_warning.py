"""Apply predeclared trend and volatility confirmations to Q85 hazard alerts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .constrained_hazard_threshold import _hazard_sample, build_candidate_thresholds
from .path_tail_targets import PERIODS, _load_feature_data
from .precision_thresholds import _wilson_interval
from .probability_accuracy import classification_metrics
from .weekly_path_tail import (
    COOLDOWN_DECISIONS,
    _episode_metrics,
    suppress_overlapping_warnings,
)


SELECTED_THRESHOLD = "Q85"
MINIMUM_WARNINGS = 5
FILTERS = ("NONE", "TREND_WEAK", "VOL_ACCELERATING", "TREND_AND_VOL")


def confirmation_masks(sample):
    """Return fixed, contemporaneously observable second-stage conditions."""
    trend_weak = (sample["QQQ_ROC20"] < 0.0) | (
        sample["QQQ_Close"] < sample["QQQ_EMA200"]
    )
    volatility_accelerating = sample["QQQ_VOL_ACCELERATION"] > 0.0
    return {
        "NONE": pd.Series(True, index=sample.index),
        "TREND_WEAK": trend_weak,
        "VOL_ACCELERATING": volatility_accelerating,
        "TREND_AND_VOL": trend_weak & volatility_accelerating,
    }


def build_two_stage_predictions(sample, threshold):
    """Confirm raw Q85 candidates before applying overlap suppression."""
    raw_candidate = sample["EventProbability"] >= threshold
    masks = confirmation_masks(sample)
    return {
        name: suppress_overlapping_warnings(
            raw_candidate & masks[name], COOLDOWN_DECISIONS
        )
        for name in FILTERS
    }


def select_development_filter(metrics):
    """Prefer precision and fewer false alarms without reducing episode recall."""
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"].copy()
    baseline = development.loc[development["Filter"] == "NONE"].iloc[0]
    development["PrecisionImproved"] = development["Precision"] > baseline["Precision"]
    development["FalsePositiveReduced"] = (
        development["FalsePositivesPerYear"] < baseline["FalsePositivesPerYear"]
    )
    development["EpisodeNonInferior"] = (
        development["EpisodeRecall"] >= baseline["EpisodeRecall"]
    )
    development["SamplePass"] = development["Warnings"] >= MINIMUM_WARNINGS
    development["Candidate"] = (
        (development["Filter"] != "NONE")
        & development["PrecisionImproved"]
        & development["FalsePositiveReduced"]
        & development["EpisodeNonInferior"]
    )
    development["SelectedForEvaluation"] = False
    candidates = development.loc[development["Candidate"]].sort_values(
        ["Precision", "FalsePositivesPerYear", "EpisodeRecall", "Warnings"],
        ascending=[False, True, False, False],
    )
    if not candidates.empty:
        development.loc[candidates.index[0], "SelectedForEvaluation"] = True
    development["DevelopmentPass"] = (
        development["SelectedForEvaluation"] & development["SamplePass"]
    )
    return development.sort_values(
        ["SelectedForEvaluation", "Candidate", "Precision", "FalsePositivesPerYear"],
        ascending=[False, False, False, True],
    )


def _prepare_sample(data):
    sample = _hazard_sample(data)
    feature_columns = [
        "QQQ_Close", "QQQ_EMA200", "QQQ_ROC20", "QQQ_VOL_ACCELERATION"
    ]
    return sample.join(data.loc[:, feature_columns], how="left")


def _metric_rows(sample, predictions):
    rows = []
    for filter_name, warning in predictions.items():
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
                "Filter": filter_name,
                "Period": period,
                "Observations": len(window),
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


def _promotion_report(metrics, selection):
    selected = selection.loc[selection["SelectedForEvaluation"], "Filter"]
    if selected.empty:
        return pd.DataFrame()
    selected_name = selected.iloc[0]
    rows = []
    for period in ("VALIDATION_2018_2022", "LOCK_2023_PRESENT"):
        baseline = metrics.loc[
            (metrics["Filter"] == "NONE") & (metrics["Period"] == period)
        ].iloc[0]
        candidate = metrics.loc[
            (metrics["Filter"] == selected_name) & (metrics["Period"] == period)
        ].iloc[0]
        rows.append({
            "Filter": selected_name,
            "Period": period,
            "PrecisionVsBase": candidate["Precision"] - baseline["Precision"],
            "FalsePositivesVsBase": candidate["FP"] - baseline["FP"],
            "EpisodeRecallVsBase": candidate["EpisodeRecall"] - baseline["EpisodeRecall"],
            "PrecisionNonInferior": candidate["Precision"] >= baseline["Precision"],
            "FalsePositiveReduced": candidate["FP"] < baseline["FP"],
            "EpisodeNonInferior": candidate["EpisodeRecall"] >= baseline["EpisodeRecall"],
        })
    report = pd.DataFrame(rows)
    development_pass = bool(selection["DevelopmentPass"].any())
    report["DevelopmentPass"] = development_pass
    report["PeriodPass"] = (
        report["PrecisionNonInferior"]
        & report["FalsePositiveReduced"]
        & report["EpisodeNonInferior"]
    )
    report["Promote"] = development_pass & report["PeriodPass"].all()
    return report


def run_two_stage_hazard_validation():
    data = _load_feature_data()
    sample = _prepare_sample(data)
    thresholds = build_candidate_thresholds(
        sample["EventProbability"], (0.85,)
    ).dropna()
    sample = sample.loc[thresholds.index]
    predictions = build_two_stage_predictions(sample, thresholds[SELECTED_THRESHOLD])
    metrics = _metric_rows(sample, predictions)
    selection = select_development_filter(metrics)
    promotion = _promotion_report(metrics, selection)
    metrics.to_csv(RESULT_DIR / "two_stage_hazard_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "two_stage_hazard_selection.csv", index=False)
    promotion.to_csv(RESULT_DIR / "two_stage_hazard_promotion.csv", index=False)
    return {"metrics": metrics, "selection": selection, "promotion": promotion}


if __name__ == "__main__":
    reports = run_two_stage_hazard_validation()
    selection_columns = [
        "Filter", "Warnings", "TP", "FP", "Precision", "EpisodeRecall",
        "FalsePositivesPerYear", "PrecisionImproved", "FalsePositiveReduced",
        "EpisodeNonInferior", "SamplePass", "SelectedForEvaluation", "DevelopmentPass",
    ]
    print("Development-only filter selection")
    print(reports["selection"][selection_columns].to_string(index=False))
    selected = reports["selection"].loc[
        reports["selection"]["SelectedForEvaluation"], "Filter"
    ]
    if len(selected):
        print("\nFixed filter evaluation")
        fixed = reports["metrics"].loc[
            reports["metrics"]["Filter"].isin(("NONE", selected.iloc[0]))
        ]
        columns = [
            "Filter", "Period", "Warnings", "TP", "FP", "Precision",
            "EpisodeRecall", "FalsePositivesPerYear", "MeanLeadTradingDays",
        ]
        print(fixed[columns].to_string(index=False))
        print("\nPromotion gate")
        print(reports["promotion"].to_string(index=False))
    else:
        print("\nNo development candidate improved both precision and false positives.")
