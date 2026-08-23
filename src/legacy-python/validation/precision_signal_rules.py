"""Evaluate duplicate suppression and exploratory late-warning guards."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from .precision_thresholds import (
    PERIODS,
    _load_forecast_sample,
    _wilson_interval,
    build_causal_thresholds,
)
from .probability_accuracy import classification_metrics


SELECTED_THRESHOLDS = ("EXPANDING_Q90", "EXPANDING_Q95")


def suppress_next_decision(raw_warning):
    """Suppress a warning at the immediately following monthly decision."""
    raw_warning = pd.Series(raw_warning, copy=False).astype(bool)
    accepted = pd.Series(False, index=raw_warning.index)
    cooldown = 0
    for position, warning in enumerate(raw_warning):
        if cooldown:
            cooldown -= 1
            continue
        if warning:
            accepted.iloc[position] = True
            cooldown = 1
    return accepted


def _late_guard(sample, name):
    deep_rebound = (
        (sample["QQQ_DRAWDOWN120"] <= -0.15)
        & (sample["QQQ_REBOUND20"] >= 0.075)
    )
    extreme_rebound = sample["QQQ_REBOUND20"] >= 0.15
    if name == "NONE":
        return pd.Series(False, index=sample.index)
    if name == "DEEP_REBOUND":
        return deep_rebound
    if name == "EXTREME_REBOUND":
        return extreme_rebound
    if name == "COMBINED_REBOUND":
        return deep_rebound | extreme_rebound
    raise ValueError(f"unknown late guard: {name}")


def _rule_predictions(sample, thresholds):
    rows = {}
    for threshold_name in SELECTED_THRESHOLDS:
        raw = (
            sample["ProbabilityLoss21"]
            >= thresholds[threshold_name]
        )
        rows[(threshold_name, "RAW")] = raw
        deduplicated = suppress_next_decision(raw)
        rows[(threshold_name, "DEDUPLICATED")] = deduplicated
        for guard_name in (
            "DEEP_REBOUND", "EXTREME_REBOUND", "COMBINED_REBOUND"
        ):
            guard = _late_guard(sample, guard_name)
            rows[(threshold_name, f"DEDUP_{guard_name}")] = (
                deduplicated & ~guard
            )
    return rows


def _metrics(sample, predictions):
    rows = []
    for (threshold, rule), predicted in predictions.items():
        for period, (start, end) in PERIODS.items():
            window = sample.loc[start:end]
            current_prediction = predicted.loc[window.index]
            metrics = classification_metrics(
                window["Outcome"], current_prediction.astype(float), 0.5
            )
            precision_low, precision_high = _wilson_interval(
                metrics["TP"], metrics["TP"] + metrics["FP"]
            )
            years = max(
                (window.index.max() - window.index.min()).days / 365.25,
                1.0 / 12.0,
            )
            rows.append({
                "Threshold": threshold,
                "SignalRule": rule,
                "Period": period,
                "Warnings": metrics["Warnings"],
                "TP": metrics["TP"],
                "FP": metrics["FP"],
                "FN": metrics["FN"],
                "Precision": metrics["Precision"],
                "PrecisionLow95": precision_low,
                "PrecisionHigh95": precision_high,
                "Recall": metrics["Recall"],
                "BalancedAccuracy": metrics["BalancedAccuracy"],
                "FalsePositivesPerYear": metrics["FP"] / years,
            })
    return pd.DataFrame(rows)


def _events(sample, predictions):
    rows = []
    for (threshold, rule), predicted in predictions.items():
        for date in predicted.index[predicted]:
            event = sample.loc[date]
            rows.append({
                "Threshold": threshold,
                "SignalRule": rule,
                "SignalDate": date,
                "ProbabilityLoss21": event["ProbabilityLoss21"],
                "ForwardReturn21D": event["ForwardReturn21D"],
                "TailLossOccurred": bool(event["Outcome"]),
                "Drawdown120": event["QQQ_DRAWDOWN120"],
                "Rebound20": event["QQQ_REBOUND20"],
            })
    return pd.DataFrame(rows)


def run_precision_signal_rule_validation():
    full_sample = _load_forecast_sample()
    thresholds = build_causal_thresholds(full_sample)
    sample = full_sample.loc[thresholds.index]
    predictions = _rule_predictions(sample, thresholds)
    metrics = _metrics(sample, predictions)
    events = _events(sample, predictions)
    metrics.to_csv(RESULT_DIR / "precision_signal_rule_metrics.csv", index=False)
    events.to_csv(RESULT_DIR / "precision_signal_rule_events.csv", index=False)
    return {"metrics": metrics, "events": events}


if __name__ == "__main__":
    reports = run_precision_signal_rule_validation()
    common = reports["metrics"].loc[reports["metrics"]["Period"] == "COMMON"]
    print(common.to_string(index=False))
    print("\nPeriod stability for raw and deduplicated rules")
    stable = reports["metrics"].loc[
        reports["metrics"]["SignalRule"].isin(("RAW", "DEDUPLICATED"))
    ]
    print(stable.to_string(index=False))
