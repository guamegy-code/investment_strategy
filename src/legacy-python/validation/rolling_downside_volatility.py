"""Rolling HAR-style downside-volatility forecast with gentle pension allocation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import RESULT_DIR
from strategy import BaseStrategy, STATIC_RETIREMENT_7030
from .continuous_risk_forecast import (
    ContinuousForecastStrategy,
    EXECUTION_DAYS,
    HORIZON_DAYS,
    PROFILES,
    PreparedContinuousRiskBacktest,
    _performance_rows,
    _run,
    forward_continuous_risk_targets,
    walk_forward_continuous_risk,
)
from .path_tail_targets import PERIODS, _load_feature_data


MODEL_NAME = "ROLLING_HAR_DOWNSIDE_VOL_50_70"
PREVIOUS_MODEL_NAME = "EXPANDING_COMBINED_RISK_30_70"
MINIMUM_SAMPLES = 60
MAXIMUM_TRAINING_SAMPLES = 120
RIDGE_PENALTY = 5.0
LOG_EPSILON = 1e-4
MINIMUM_RISK_WEIGHT = 0.50
MAXIMUM_RISK_WEIGHT = 0.70
STRESS_DEADBAND = 1.10
FULL_DEFENSE_STRESS = 1.50
REBALANCE_BAND = 0.10
MAXIMUM_MONTHLY_RECOVERY = 0.05


def build_har_downside_features(close):
    """Build lag-safe 1/5/21/63-day downside-volatility features."""
    close = pd.Series(close, copy=False, dtype=float)
    returns = close.pct_change()
    downside_square = returns.clip(upper=0.0).pow(2)
    features = pd.DataFrame(index=close.index)
    for window in (1, 5, 21, 63):
        volatility = np.sqrt(downside_square.rolling(window).mean() * 252.0)
        features[f"LOG_DOWNSIDE_VOL_{window}"] = np.log(volatility + LOG_EPSILON)
    return features


def fit_log_ridge_risk(train_x, train_y, predict_x, ridge_penalty=RIDGE_PENALTY):
    """Fit ridge regression in log-risk space and return level forecast/base."""
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((train_x - mean) / scale, -5.0, 5.0)
    point = np.clip((predict_x - mean) / scale, -5.0, 5.0)
    design = np.column_stack((np.ones(len(standardized)), standardized))
    prediction_design = np.concatenate(([1.0], point))
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[0, 0] = 0.0
    log_target = np.log(np.maximum(train_y, 0.0) + LOG_EPSILON)
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ log_target)
    prediction = np.exp(prediction_design @ beta) - LOG_EPSILON
    return float(max(0.0, prediction)), float(np.mean(train_y))


def walk_forward_rolling_downside_volatility(data):
    """Forecast monthly risk from at most the latest ten years of observations."""
    frame = data.copy()
    har = build_har_downside_features(frame["QQQ_Close"])
    features = har.to_numpy(dtype=float)
    decision_mask = ~frame.index.to_period("M").duplicated()
    decisions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    complete = decisions + HORIZON_DAYS < len(frame)
    targets = np.full(len(decisions), np.nan)
    targets[complete], _ = forward_continuous_risk_targets(close, decisions[complete])
    predicted = pd.Series(np.nan, index=frame.index, dtype=float)
    base = pd.Series(np.nan, index=frame.index, dtype=float)
    stress = pd.Series(np.nan, index=frame.index, dtype=float)
    samples = pd.Series(0, index=frame.index, dtype=int)

    for decision_number, position in enumerate(decisions):
        eligible_number = np.arange(decision_number)[
            decisions[:decision_number] + HORIZON_DAYS <= position
        ]
        if len(eligible_number):
            eligible_number = eligible_number[
                np.isfinite(features[decisions[eligible_number]]).all(axis=1)
            ][-MAXIMUM_TRAINING_SAMPLES:]
        current_valid = np.isfinite(features[position]).all()
        if len(eligible_number) < MINIMUM_SAMPLES or not current_valid:
            current_prediction = current_base = np.nan
            current_stress = 1.0
        else:
            current_prediction, current_base = fit_log_ridge_risk(
                features[decisions[eligible_number]],
                targets[eligible_number],
                features[position],
            )
            reliability = min(1.0, len(eligible_number) / MAXIMUM_TRAINING_SAMPLES)
            current_prediction = current_base + reliability * (
                current_prediction - current_base
            )
            current_stress = float(np.clip(
                current_prediction / max(current_base, LOG_EPSILON), 0.5, 3.0
            ))
        predicted.iloc[position] = current_prediction
        base.iloc[position] = current_base
        stress.iloc[position] = current_stress
        samples.iloc[position] = len(eligible_number)

    return pd.DataFrame({
        "PredictedDownsideVolatility": predicted.ffill(),
        "BaseDownsideVolatility": base.ffill(),
        "ForecastStressRatio": stress.ffill(),
        "RiskModelSamples": samples.where(decision_mask).ffill().fillna(0).astype(int),
    }, index=frame.index)


def rolling_risk_weight(stress_ratio):
    reduction = np.clip(
        (float(stress_ratio) - STRESS_DEADBAND)
        / (FULL_DEFENSE_STRESS - STRESS_DEADBAND),
        0.0,
        1.0,
    )
    return float(
        MAXIMUM_RISK_WEIGHT
        - (MAXIMUM_RISK_WEIGHT - MINIMUM_RISK_WEIGHT) * reduction
    )


def limit_monthly_recovery(previous_weight, desired_weight):
    """Cut risk immediately but restore no more than five percentage points."""
    if desired_weight <= previous_weight:
        return float(desired_weight)
    return float(min(desired_weight, previous_weight + MAXIMUM_MONTHLY_RECOVERY))


class RollingDownsideVolatilityStrategy(BaseStrategy):
    def __init__(self):
        self.target = None
        self.last_signal_month = None
        self.stress_ratio = 1.0

    @property
    def required_tickers(self):
        return ("QQQ", "BND", "BIL")

    def _desired_target(self, market):
        stress = market["QQQ"].get("ForecastStressRatio")
        if stress is None or not np.isfinite(stress):
            stress = 1.0
        self.stress_ratio = float(stress)
        risk_weight = rolling_risk_weight(self.stress_ratio)
        if self.target is not None:
            risk_weight = limit_monthly_recovery(self.target["QQQ"], risk_weight)
        safe_weight = 1.0 - risk_weight
        return {"QQQ": risk_weight, "BND": safe_weight / 2.0, "BIL": safe_weight / 2.0}

    def _signal(self, rebalance, reason):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": EXECUTION_DAYS,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        if self.target is None:
            self.target = self._desired_target(market)
            self.last_signal_month = month
            return self._signal(True, "INITIAL_ROLLING_DOWNSIDE_VOL_TARGET")
        if month == self.last_signal_month:
            return self._signal(False, None)
        self.last_signal_month = month
        desired = self._desired_target(market)
        prices = {ticker: market[ticker]["Close"] for ticker in desired}
        current = portfolio.weights(prices)
        self.target = desired
        rebalance = any(
            abs(current.get(ticker, 0.0) - weight) >= REBALANCE_BAND
            for ticker, weight in desired.items()
        )
        reason = (
            f"MONTHLY_ROLLING_DOWNSIDE_VOL(stress={self.stress_ratio:.3f})"
            if rebalance else None
        )
        return self._signal(rebalance, reason)


def _development_gate(metrics):
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"]
    baseline = development.loc[
        development["Strategy"] == "STATIC_RETIREMENT_7030"
    ].iloc[0]
    candidate = development.loc[development["Strategy"] == MODEL_NAME].iloc[0]
    report = pd.DataFrame([{
        "Strategy": MODEL_NAME,
        "CAGRGapVsStatic": candidate["CAGR"] - baseline["CAGR"],
        "MDDImprovementVsStatic": candidate["MDD"] - baseline["MDD"],
        "SharpeGapVsStatic": candidate["Sharpe"] - baseline["Sharpe"],
        "TransactionCostGapVsStatic": (
            candidate["TransactionCosts"] - baseline["TransactionCosts"]
        ),
    }])
    report["CAGRPass"] = report["CAGRGapVsStatic"] >= -0.005
    report["MDDPass"] = report["MDDImprovementVsStatic"] > 0.0
    report["SharpePass"] = report["SharpeGapVsStatic"] > 0.0
    report["Pass"] = report[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    return report


def _later_gate(metrics, development):
    rows = []
    for period in ("VALIDATION_2018_2022", "LOCK_2023_PRESENT"):
        window = metrics.loc[metrics["Period"] == period]
        baseline = window.loc[window["Strategy"] == "STATIC_RETIREMENT_7030"].iloc[0]
        candidate = window.loc[window["Strategy"] == MODEL_NAME].iloc[0]
        rows.append({
            "Period": period,
            "CAGRGapVsStatic": candidate["CAGR"] - baseline["CAGR"],
            "MDDImprovementVsStatic": candidate["MDD"] - baseline["MDD"],
            "SharpeGapVsStatic": candidate["Sharpe"] - baseline["Sharpe"],
            "TransactionCostGapVsStatic": (
                candidate["TransactionCosts"] - baseline["TransactionCosts"]
            ),
        })
    report = pd.DataFrame(rows)
    report["DevelopmentPass"] = bool(development["Pass"].iloc[0])
    report["CAGRPass"] = report["CAGRGapVsStatic"] >= -0.005
    report["MDDPass"] = report["MDDImprovementVsStatic"] > 0.0
    report["SharpePass"] = report["SharpeGapVsStatic"] > 0.0
    report["PeriodPass"] = report[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    report["Promote"] = report["DevelopmentPass"] & report["PeriodPass"].all()
    return report


def _diagnostics(prepared, active_start):
    decisions = prepared.loc[~prepared.index.to_period("M").duplicated()].copy()
    positions = prepared.index.get_indexer(decisions.index)
    complete = positions + HORIZON_DAYS < len(prepared)
    decisions = decisions.iloc[np.flatnonzero(complete)].copy()
    positions = positions[complete]
    actual, _ = forward_continuous_risk_targets(
        prepared["QQQ_Close"].to_numpy(dtype=float), positions
    )
    decisions["Actual"] = actual
    decisions = decisions.loc[decisions.index >= active_start]
    rows = []
    for period, (start, end) in PERIODS.items():
        window = decisions.loc[start:end].dropna(subset=[
            "Actual", "PredictedDownsideVolatility", "BaseDownsideVolatility"
        ])
        model_error = window["PredictedDownsideVolatility"] - window["Actual"]
        base_error = window["BaseDownsideVolatility"] - window["Actual"]
        rows.append({
            "Period": period,
            "Observations": len(window),
            "MAE": model_error.abs().mean(),
            "BaseMAE": base_error.abs().mean(),
            "RMSESkillVsBase": (
                1.0 - np.mean(model_error ** 2) / np.mean(base_error ** 2)
            ),
            "Correlation": window["PredictedDownsideVolatility"].corr(window["Actual"]),
        })
    return pd.DataFrame(rows)


def run_rolling_downside_volatility_validation():
    data = _load_feature_data()
    old_forecast = walk_forward_continuous_risk(data)
    new_forecast = walk_forward_rolling_downside_volatility(data)
    old_prepared = data.join(old_forecast)
    new_prepared = data.join(new_forecast)
    active_start = max(
        old_prepared.index[old_prepared["RiskModelSamples"] >= 60][0],
        new_prepared.index[new_prepared["RiskModelSamples"] >= MINIMUM_SAMPLES][0],
    )
    results = [
        _run("STATIC_RETIREMENT_7030", STATIC_RETIREMENT_7030(), new_prepared),
        _run(
            PREVIOUS_MODEL_NAME,
            ContinuousForecastStrategy(PROFILES[0]),
            old_prepared,
        ),
        _run(MODEL_NAME, RollingDownsideVolatilityStrategy(), new_prepared),
    ]
    metrics = _performance_rows(results, active_start)
    development = _development_gate(metrics)
    later = _later_gate(metrics, development)
    diagnostics = _diagnostics(new_prepared, active_start)
    metrics.to_csv(RESULT_DIR / "rolling_downside_volatility_metrics.csv", index=False)
    development.to_csv(RESULT_DIR / "rolling_downside_volatility_development.csv", index=False)
    later.to_csv(RESULT_DIR / "rolling_downside_volatility_later.csv", index=False)
    diagnostics.to_csv(RESULT_DIR / "rolling_downside_volatility_diagnostics.csv", index=False)
    return {
        "metrics": metrics,
        "development": development,
        "later": later,
        "diagnostics": diagnostics,
    }


if __name__ == "__main__":
    reports = run_rolling_downside_volatility_validation()
    columns = [
        "Strategy", "Period", "CAGR", "MDD", "Sharpe", "Calmar",
        "AverageRiskWeight", "MinimumRiskWeight", "MaximumRiskWeight",
        "TotalRebalances", "TransactionCosts",
    ]
    print("Development comparison")
    print(reports["metrics"].loc[
        reports["metrics"]["Period"] == "DEVELOPMENT_TO_2017", columns
    ].to_string(index=False))
    print("\nDevelopment gate")
    print(reports["development"].to_string(index=False))
    print("\nDevelopment forecast diagnostics")
    print(reports["diagnostics"].loc[
        reports["diagnostics"]["Period"] == "DEVELOPMENT_TO_2017"
    ].to_string(index=False))
    if reports["development"]["Pass"].any():
        print("\nFixed candidate later comparison")
        print(reports["metrics"].loc[
            reports["metrics"]["Period"].isin(
                ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
            ) & reports["metrics"]["Strategy"].isin(
                ("STATIC_RETIREMENT_7030", MODEL_NAME)
            ), columns
        ].to_string(index=False))
        print("\nPromotion gate")
        print(reports["later"].to_string(index=False))
    else:
        print("\nCandidate failed development; later periods were not used for selection.")
