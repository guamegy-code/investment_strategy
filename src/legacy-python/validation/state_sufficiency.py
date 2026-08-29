"""Test whether the production market state contains enough allocation information.

This is deliberately an information test, not another allocation optimisation.
At non-overlapping 20-trading-day decision dates it compares a model using
only the four production states with the same model augmented by pre-declared
continuous trend, momentum, volatility, and drawdown measurements.  Every
forecast is expanding-window and uses only targets already resolved at its
decision date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest import Backtest
from config import RESULT_DIR
from strategy_dsl import load_strategy_definition
from validation.probability_accuracy import classification_metrics
from validation.state_conditioned_continuous_overlay import (
    QQQ,
    SOURCE,
    RecordingDeclarativeStrategy,
)


HORIZON_DAYS = 20
DECISION_STRIDE_DAYS = 20
MINIMUM_MODEL_SAMPLES = 60
RIDGE_PENALTY = 5.0
PATH_LOSS_THRESHOLD = 0.05
TOP_FRACTION = 0.20

STATE_COLUMNS = (
    "State_CAUTION",
    "State_BEAR",
    "State_RECOVERY",
)
CONTINUOUS_COLUMNS = (
    "QQQ_EMA55_DISTANCE",
    "QQQ_EMA200_DISTANCE",
    "QQQ_ROC20",
    "QQQ_ROC60",
    "QQQ_ROC120",
    "QQQ_VOL60",
    "QQQ_DRAWDOWN120",
)
MODEL_FEATURES = {
    "STATE_ONLY": STATE_COLUMNS,
    "STATE_PLUS_CONTINUOUS": STATE_COLUMNS + CONTINUOUS_COLUMNS,
}
PERIODS = {
    "FULL_OOS": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
}


def _standardized_design(train_x, predict_x):
    """Return standardized design matrices with an unpenalized intercept."""

    train_x = np.asarray(train_x, dtype=float)
    predict_x = np.asarray(predict_x, dtype=float)
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((train_x - mean) / scale, -5.0, 5.0)
    point = np.clip((predict_x - mean) / scale, -5.0, 5.0)
    return (
        np.column_stack((np.ones(len(standardized)), standardized)),
        np.concatenate(([1.0], point)),
    )


def fit_ridge(train_x, train_y, predict_x, ridge_penalty=RIDGE_PENALTY):
    """Fit a small causal ridge model; unlike risk-only helpers, keep sign."""

    design, point = _standardized_design(train_x, predict_x)
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ train_y)
    return float(point @ beta)


def _sigmoid(values):
    values = np.clip(values, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-values))


def fit_logistic_probability(
    train_x,
    train_y,
    predict_x,
    ridge_penalty=RIDGE_PENALTY,
):
    """Fit a regularized tail-event probability model using prior samples only."""

    train_y = np.asarray(train_y, dtype=float)
    base_probability = float((train_y.sum() + 1.0) / (len(train_y) + 2.0))
    if np.unique(train_y).size < 2:
        return base_probability
    design, point = _standardized_design(train_x, predict_x)
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[0, 0] = 0.0
    beta = np.zeros(design.shape[1])
    beta[0] = np.log(base_probability / (1.0 - base_probability))
    for _ in range(50):
        fitted = _sigmoid(design @ beta)
        weights = np.maximum(fitted * (1.0 - fitted), 1e-6)
        gradient = design.T @ (train_y - fitted) - penalty @ beta
        hessian = (design.T * weights) @ design + penalty
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break
    return float(np.clip(_sigmoid(point @ beta), 0.01, 0.99))


def _state_features(states):
    """Encode BULL as the reference state and reject unexpected state values."""

    states = pd.Series(states, copy=False).astype(str)
    valid = {"BULL", "CAUTION", "BEAR", "RECOVERY"}
    unknown = sorted(set(states) - valid)
    if unknown:
        raise ValueError(f"Unexpected production states: {', '.join(unknown)}")
    return pd.DataFrame({
        "State_CAUTION": (states == "CAUTION").astype(float),
        "State_BEAR": (states == "BEAR").astype(float),
        "State_RECOVERY": (states == "RECOVERY").astype(float),
    }, index=states.index)


def _continuous_features(data):
    """Return the frozen seven continuous inputs tested for information value."""

    close = data["QQQ_Close"]
    return pd.DataFrame({
        "QQQ_EMA55_DISTANCE": close / data["QQQ_EMA55"] - 1.0,
        "QQQ_EMA200_DISTANCE": close / data["QQQ_EMA200"] - 1.0,
        "QQQ_ROC20": data["QQQ_ROC20"],
        "QQQ_ROC60": data["QQQ_ROC60"],
        "QQQ_ROC120": data["QQQ_ROC120"],
        "QQQ_VOL60": data["QQQ_VOL60"],
        "QQQ_DRAWDOWN120": data["QQQ_DRAWDOWN120"],
    }, index=data.index)


def _load_state_data():
    """Run an independent production shadow book and capture its daily state."""

    definition = load_strategy_definition(SOURCE)
    schedule = {}

    def record(date, point):
        schedule[date] = point

    strategy = RecordingDeclarativeStrategy(definition, record)
    backtest = Backtest(strategy, tickers=strategy.required_tickers)
    backtest.run_all()
    return backtest.data.copy(), schedule


def build_nonoverlap_sample(data, schedule, horizon_days=HORIZON_DAYS,
                            stride_days=DECISION_STRIDE_DAYS):
    """Create 20-day non-overlapping, fully observed targets and inputs."""

    if horizon_days <= 0 or stride_days < horizon_days:
        raise ValueError("Decision stride must be at least the target horizon")
    dates = pd.DatetimeIndex(data.index)
    missing = dates.difference(pd.DatetimeIndex(schedule))
    if len(missing):
        raise ValueError("The production state schedule does not cover market data")
    positions = np.arange(0, len(data) - horizon_days, stride_days, dtype=int)
    decision_dates = dates[positions]
    close = data["QQQ_Close"].to_numpy(dtype=float)
    state = pd.Series(
        [schedule[date]["state"] for date in decision_dates],
        index=decision_dates,
        name="State",
    )
    features = _state_features(state).join(
        _continuous_features(data).reindex(decision_dates)
    )
    max_loss = np.array([
        max(0.0, 1.0 - close[position + 1:position + horizon_days + 1].min()
            / close[position])
        for position in positions
    ])
    sample = pd.DataFrame(index=decision_dates)
    sample.index.name = "DecisionDate"
    sample["TargetEndDate"] = dates[positions + horizon_days]
    sample["DecisionPosition"] = positions
    sample["State"] = state
    sample["ForwardReturn20D"] = close[positions + horizon_days] / close[positions] - 1.0
    sample["MaximumLoss20D"] = max_loss
    sample["TailLoss5Pct"] = (max_loss >= PATH_LOSS_THRESHOLD).astype(float)
    return sample.join(features)


def walk_forward_state_sufficiency(sample, minimum_samples=MINIMUM_MODEL_SAMPLES):
    """Forecast each target with matched state-only and augmented models."""

    output = sample.copy()
    for model in MODEL_FEATURES:
        output[f"{model}_PredictedReturn20D"] = np.nan
        output[f"{model}_PredictedMaximumLoss20D"] = np.nan
        output[f"{model}_TailProbability5Pct"] = np.nan
    output["ModelSamples"] = 0
    all_columns = list(MODEL_FEATURES["STATE_PLUS_CONTINUOUS"])
    valid = np.isfinite(output[all_columns].to_numpy(dtype=float)).all(axis=1)

    for position in range(len(output)):
        # The stride equals the horizon, so all previous windows have ended.
        eligible = np.flatnonzero(valid[:position])
        current_valid = bool(valid[position])
        if len(eligible) < minimum_samples or not current_valid:
            continue
        output.iloc[position, output.columns.get_loc("ModelSamples")] = len(eligible)
        for model, columns in MODEL_FEATURES.items():
            train_x = output.iloc[eligible].loc[:, columns].to_numpy(dtype=float)
            predict_x = output.iloc[position].loc[list(columns)].to_numpy(dtype=float)
            predicted_return = fit_ridge(
                train_x,
                output.iloc[eligible]["ForwardReturn20D"].to_numpy(dtype=float),
                predict_x,
            )
            predicted_loss = max(0.0, fit_ridge(
                train_x,
                output.iloc[eligible]["MaximumLoss20D"].to_numpy(dtype=float),
                predict_x,
            ))
            tail_probability = fit_logistic_probability(
                train_x,
                output.iloc[eligible]["TailLoss5Pct"].to_numpy(dtype=float),
                predict_x,
            )
            output.iloc[position, output.columns.get_loc(
                f"{model}_PredictedReturn20D"
            )] = predicted_return
            output.iloc[position, output.columns.get_loc(
                f"{model}_PredictedMaximumLoss20D"
            )] = predicted_loss
            output.iloc[position, output.columns.get_loc(
                f"{model}_TailProbability5Pct"
            )] = tail_probability
    return output.loc[output["ModelSamples"] >= minimum_samples].copy()


def _correlation(actual, predicted):
    if len(actual) < 2 or np.std(actual) < 1e-12 or np.std(predicted) < 1e-12:
        return np.nan
    return float(np.corrcoef(actual, predicted)[0, 1])


def _top_fraction_loss_lift(actual_loss, score, fraction=TOP_FRACTION):
    actual_loss = np.asarray(actual_loss, dtype=float)
    score = np.asarray(score, dtype=float)
    if len(actual_loss) == 0 or actual_loss.mean() <= 0.0:
        return np.nan
    count = max(1, int(np.ceil(len(actual_loss) * fraction)))
    selected = actual_loss[np.argsort(-score, kind="stable")[:count]]
    return float(selected.mean() / actual_loss.mean())


def model_metrics(forecasts):
    """Report common-date OOS accuracy for return, path loss, and tail event."""

    rows = []
    for model in MODEL_FEATURES:
        for period, (start, end) in PERIODS.items():
            window = forecasts.loc[start:end]
            if window.empty:
                continue
            actual_return = window["ForwardReturn20D"].to_numpy(dtype=float)
            actual_loss = window["MaximumLoss20D"].to_numpy(dtype=float)
            tail = window["TailLoss5Pct"].to_numpy(dtype=int)
            return_prediction = window[
                f"{model}_PredictedReturn20D"
            ].to_numpy(dtype=float)
            loss_prediction = window[
                f"{model}_PredictedMaximumLoss20D"
            ].to_numpy(dtype=float)
            tail_probability = window[
                f"{model}_TailProbability5Pct"
            ].to_numpy(dtype=float)
            tail_metrics = classification_metrics(tail, tail_probability, 0.50)
            rows.append({
                "Model": model,
                "Period": period,
                "StartDate": window.index.min(),
                "EndDate": window.index.max(),
                "Observations": len(window),
                "TailEvents": int(tail.sum()),
                "ReturnMAE": float(np.mean(np.abs(actual_return - return_prediction))),
                "ReturnRMSE": float(np.sqrt(np.mean((actual_return - return_prediction) ** 2))),
                "ReturnCorrelation": _correlation(actual_return, return_prediction),
                "MaxLossMAE": float(np.mean(np.abs(actual_loss - loss_prediction))),
                "MaxLossRMSE": float(np.sqrt(np.mean((actual_loss - loss_prediction) ** 2))),
                "MaxLossCorrelation": _correlation(actual_loss, loss_prediction),
                "MaxLossTop20PctLift": _top_fraction_loss_lift(
                    actual_loss, loss_prediction
                ),
                "TailBrierScore": tail_metrics["BrierScore"],
                "TailLogLoss": tail_metrics["LogLoss"],
                "TailROCAUC": tail_metrics["ROC_AUC"],
                "TailTop20PctLift": tail_metrics["Top20PctLift"],
            })
    return pd.DataFrame(rows)


def incremental_metrics(metrics):
    """Express the added continuous-input value; positive always favors it."""

    keys = ["Period", "StartDate", "EndDate", "Observations", "TailEvents"]
    state = metrics.loc[metrics["Model"] == "STATE_ONLY"].set_index(keys)
    augmented = metrics.loc[
        metrics["Model"] == "STATE_PLUS_CONTINUOUS"
    ].set_index(keys)
    common = state.index.intersection(augmented.index)
    rows = []
    for index in common:
        baseline = state.loc[index]
        candidate = augmented.loc[index]
        row = dict(zip(keys, index))
        row.update({
            "ReturnMAEImprovement": baseline["ReturnMAE"] - candidate["ReturnMAE"],
            "ReturnRMSEImprovement": baseline["ReturnRMSE"] - candidate["ReturnRMSE"],
            "ReturnCorrelationGain": candidate["ReturnCorrelation"] - baseline["ReturnCorrelation"],
            "MaxLossMAEImprovement": baseline["MaxLossMAE"] - candidate["MaxLossMAE"],
            "MaxLossRMSEImprovement": baseline["MaxLossRMSE"] - candidate["MaxLossRMSE"],
            "MaxLossCorrelationGain": candidate["MaxLossCorrelation"] - baseline["MaxLossCorrelation"],
            "MaxLossTop20PctLiftGain": (
                candidate["MaxLossTop20PctLift"] - baseline["MaxLossTop20PctLift"]
            ),
            "TailBrierImprovement": baseline["TailBrierScore"] - candidate["TailBrierScore"],
            "TailLogLossImprovement": baseline["TailLogLoss"] - candidate["TailLogLoss"],
            "TailROCAUCGain": candidate["TailROCAUC"] - baseline["TailROCAUC"],
            "TailTop20PctLiftGain": (
                candidate["TailTop20PctLift"] - baseline["TailTop20PctLift"]
            ),
        })
        rows.append(row)
    return pd.DataFrame(rows)


def information_gate(incremental):
    """Require tail fit and tail ranking gains in both non-overlapping eras."""

    rows = []
    for period in ("DEVELOPMENT_PRE2021", "RECENT_2021_PRESENT"):
        point = incremental.loc[incremental["Period"] == period]
        if point.empty:
            continue
        point = point.iloc[0]
        tail_fit = (
            point["MaxLossRMSEImprovement"] > 0.0
            and point["TailBrierImprovement"] > 0.0
        )
        tail_ranking = (
            point["MaxLossTop20PctLiftGain"] > 0.0
            or point["TailTop20PctLiftGain"] > 0.0
        )
        rows.append({
            "Period": period,
            "TailFitPass": tail_fit,
            "TailRankingPass": tail_ranking,
            "IncrementalInformationPass": tail_fit and tail_ranking,
        })
    report = pd.DataFrame(rows)
    required = {"DEVELOPMENT_PRE2021", "RECENT_2021_PRESENT"}
    passes = set(report.loc[report["IncrementalInformationPass"], "Period"])
    confirmed = required.issubset(passes)
    decision = pd.DataFrame([{
        "Test": "STATE_SUFFICIENCY_20D",
        "HorizonTradingDays": HORIZON_DAYS,
        "DecisionStrideTradingDays": DECISION_STRIDE_DAYS,
        "MinimumModelSamples": MINIMUM_MODEL_SAMPLES,
        "ContinuousInformationConfirmed": confirmed,
        "Decision": (
            "CONTINUOUS_INFORMATION_CONFIRMED"
            if confirmed else "NO_ROBUST_INCREMENTAL_INFORMATION"
        ),
    }])
    return report, decision


def run_state_sufficiency_validation():
    """Run and persist the pre-allocation state-sufficiency diagnostic."""

    data, schedule = _load_state_data()
    sample = build_nonoverlap_sample(data, schedule)
    forecasts = walk_forward_state_sufficiency(sample)
    metrics = model_metrics(forecasts)
    incremental = incremental_metrics(metrics)
    gate, decision = information_gate(incremental)
    state_mix = forecasts.groupby("State", as_index=False).agg(
        Decisions=("State", "size"),
        MeanForwardReturn20D=("ForwardReturn20D", "mean"),
        MeanMaximumLoss20D=("MaximumLoss20D", "mean"),
        TailLossRate5Pct=("TailLoss5Pct", "mean"),
    )
    for name, report in {
        "state_sufficiency_sample": sample,
        "state_sufficiency_forecasts": forecasts,
        "state_sufficiency_metrics": metrics,
        "state_sufficiency_incremental": incremental,
        "state_sufficiency_gate": gate,
        "state_sufficiency_decision": decision,
        "state_sufficiency_state_mix": state_mix,
    }.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=True if name in {
            "state_sufficiency_sample", "state_sufficiency_forecasts"
        } else False)
    return {
        "sample": sample,
        "forecasts": forecasts,
        "metrics": metrics,
        "incremental": incremental,
        "gate": gate,
        "decision": decision,
        "state_mix": state_mix,
    }


if __name__ == "__main__":
    reports = run_state_sufficiency_validation()
    print("State sufficiency gate")
    print(reports["gate"].to_string(index=False))
    print(reports["decision"].to_string(index=False))
    print("\nIncremental OOS metrics (positive favors state + continuous)")
    print(reports["incremental"].to_string(index=False))
