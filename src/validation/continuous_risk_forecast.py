"""Continuous allocation from walk-forward forecasts of future downside risk."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest import Backtest
from config import RESULT_DIR
from performance import Performance
from strategy import BaseStrategy, STATIC_RETIREMENT_7030
from .path_tail_targets import PERIODS, _load_feature_data
from .precision_thresholds import PROFILE


HORIZON_DAYS = 21
MINIMUM_MODEL_SAMPLES = 60
MAXIMUM_RISK_WEIGHT = 0.70
MINIMUM_RISK_WEIGHT = 0.30
STRESS_DEADBAND = 1.10
REBALANCE_BAND = 0.05
EXECUTION_DAYS = 3


@dataclass(frozen=True)
class ContinuousRiskProfile:
    name: str
    full_defense_stress: float


PROFILES = (
    ContinuousRiskProfile("CONTINUOUS_RISK_STRESS_150", 1.50),
    ContinuousRiskProfile("CONTINUOUS_RISK_STRESS_175", 1.75),
    ContinuousRiskProfile("CONTINUOUS_RISK_STRESS_200", 2.00),
)


def forward_continuous_risk_targets(close, positions, horizon_days=HORIZON_DAYS):
    """Return annualized downside volatility and maximum origin-relative loss."""
    close = np.asarray(close, dtype=float)
    downside_volatility = np.empty(len(positions), dtype=float)
    maximum_loss = np.empty(len(positions), dtype=float)
    for row, position in enumerate(positions):
        prices = close[position:position + horizon_days + 1]
        returns = prices[1:] / prices[:-1] - 1.0
        downside_volatility[row] = np.sqrt(
            np.mean(np.minimum(returns, 0.0) ** 2) * 252.0
        )
        maximum_loss[row] = max(0.0, 1.0 - prices[1:].min() / prices[0])
    return downside_volatility, maximum_loss


def fit_ridge_risk(train_x, train_y, predict_x, ridge_penalty=5.0):
    """Fit a standardized ridge regression with an unpenalized intercept."""
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    standardized = np.clip((train_x - mean) / scale, -5.0, 5.0)
    point = np.clip((predict_x - mean) / scale, -5.0, 5.0)
    design = np.column_stack((np.ones(len(standardized)), standardized))
    prediction_design = np.concatenate(([1.0], point))
    penalty = np.eye(design.shape[1]) * ridge_penalty
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ train_y)
    return float(max(0.0, prediction_design @ beta)), float(np.mean(train_y))


def walk_forward_continuous_risk(
    data,
    feature_columns=PROFILE.columns,
    decision_period="M",
    minimum_samples=MINIMUM_MODEL_SAMPLES,
    reliability_samples=120,
):
    """Produce causal forecasts after complete 21-day targets resolve."""
    frame = data.copy()
    frame["QQQ_EMA200_DISTANCE"] = frame["QQQ_Close"] / frame["QQQ_EMA200"] - 1.0
    decision_mask = ~frame.index.to_period(decision_period).duplicated()
    decisions = np.flatnonzero(decision_mask)
    close = frame["QQQ_Close"].to_numpy(dtype=float)
    features = frame.loc[:, feature_columns].to_numpy(dtype=float)
    columns = {
        name: pd.Series(np.nan, index=frame.index, dtype=float)
        for name in (
            "PredictedDownsideVolatility", "BaseDownsideVolatility",
            "PredictedMaximumLoss", "BaseMaximumLoss", "ForecastStressRatio",
        )
    }
    samples = pd.Series(0, index=frame.index, dtype=int)
    complete = decisions + HORIZON_DAYS < len(frame)
    target_volatility = np.full(len(decisions), np.nan)
    target_loss = np.full(len(decisions), np.nan)
    target_volatility[complete], target_loss[complete] = (
        forward_continuous_risk_targets(close, decisions[complete])
    )

    for decision_number, position in enumerate(decisions):
        eligible_number = np.arange(decision_number)[
            decisions[:decision_number] + HORIZON_DAYS <= position
        ]
        if len(eligible_number):
            eligible_number = eligible_number[
                np.isfinite(features[decisions[eligible_number]]).all(axis=1)
            ]
        eligible = decisions[eligible_number]
        current_valid = np.isfinite(features[position]).all()
        if len(eligible) < minimum_samples or not current_valid:
            predicted_volatility = base_volatility = np.nan
            predicted_loss = base_loss = np.nan
            stress_ratio = 1.0
        else:
            volatility = target_volatility[eligible_number]
            loss = target_loss[eligible_number]
            predicted_volatility, base_volatility = fit_ridge_risk(
                features[eligible], volatility, features[position]
            )
            predicted_loss, base_loss = fit_ridge_risk(
                features[eligible], loss, features[position]
            )
            reliability = min(1.0, len(eligible) / float(reliability_samples))
            predicted_volatility = base_volatility + reliability * (
                predicted_volatility - base_volatility
            )
            predicted_loss = base_loss + reliability * (predicted_loss - base_loss)
            volatility_ratio = predicted_volatility / max(base_volatility, 1e-4)
            loss_ratio = predicted_loss / max(base_loss, 1e-4)
            stress_ratio = float(np.clip(
                (volatility_ratio + loss_ratio) / 2.0, 0.5, 3.0
            ))
        values = (
            predicted_volatility, base_volatility, predicted_loss, base_loss, stress_ratio
        )
        for series, value in zip(columns.values(), values):
            series.iloc[position] = value
        samples.iloc[position] = len(eligible)

    output = pd.DataFrame({name: series.ffill() for name, series in columns.items()})
    output["RiskModelSamples"] = (
        samples.where(decision_mask).ffill().fillna(0).astype(int)
    )
    return output


def continuous_risk_weight(stress_ratio, full_defense_stress):
    if full_defense_stress <= STRESS_DEADBAND:
        raise ValueError("full_defense_stress must exceed the stress deadband")
    reduction = np.clip(
        (float(stress_ratio) - STRESS_DEADBAND)
        / (full_defense_stress - STRESS_DEADBAND),
        0.0,
        1.0,
    )
    capacity = MAXIMUM_RISK_WEIGHT - MINIMUM_RISK_WEIGHT
    return float(MAXIMUM_RISK_WEIGHT - capacity * reduction)


class ContinuousForecastStrategy(BaseStrategy):
    def __init__(self, profile, decision_period="M"):
        self.profile = profile
        self.decision_period = decision_period
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
        risk_weight = continuous_risk_weight(
            self.stress_ratio, self.profile.full_defense_stress
        )
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
        month = date.to_period(self.decision_period)
        if self.target is None:
            self.target = self._desired_target(market)
            self.last_signal_month = month
            return self._signal(True, "INITIAL_CONTINUOUS_RISK_TARGET")
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
            f"MONTHLY_CONTINUOUS_RISK(stress={self.stress_ratio:.3f})"
            if rebalance else None
        )
        return self._signal(rebalance, reason)


class PreparedContinuousRiskBacktest(Backtest):
    def __init__(self, strategy, prepared_data):
        self.prepared_data = prepared_data
        super().__init__(strategy, tickers=("QQQ", "BND", "BIL"))

    def load_data(self):
        return self.prepared_data.copy()

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"]["ForecastStressRatio"] = row.get("ForecastStressRatio")
        return market


def _run(label, strategy, prepared_data):
    backtest = PreparedContinuousRiskBacktest(strategy, prepared_data)
    history, trades, rebalances = backtest.run_all()
    return {
        "Strategy": label,
        "History": history,
        "Trades": trades,
        "Rebalances": rebalances,
    }


def _performance_rows(results, active_start):
    rows = []
    for result in results:
        for period, (start, end) in PERIODS.items():
            period_start = active_start if start is None else max(active_start, pd.Timestamp(start))
            sample = result["History"].loc[period_start:end]
            if len(sample) < 2:
                continue
            performance = Performance(sample).summary()
            qqq_weight = sample["Weights"].apply(lambda value: value.get("QQQ", 0.0))
            rows.append({
                "Strategy": result["Strategy"],
                "Period": period,
                "StartDate": sample.index.min(),
                "EndDate": sample.index.max(),
                "CAGR": performance["CAGR"],
                "MDD": performance["MDD"],
                "Volatility": performance["Volatility"],
                "Sharpe": performance["Sharpe"],
                "Calmar": performance["Calmar"],
                "AverageRiskWeight": qqq_weight.mean(),
                "MinimumRiskWeight": qqq_weight.min(),
                "MaximumRiskWeight": qqq_weight.max(),
                "TotalRebalances": len(result["Rebalances"]),
                "TransactionCosts": (
                    sample["TransactionCosts"].iloc[-1]
                    - sample["TransactionCosts"].iloc[0]
                ),
            })
    return pd.DataFrame(rows)


def _development_selection(metrics):
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"].copy()
    baseline = development.loc[development["Strategy"] == "STATIC_RETIREMENT_7030"].iloc[0]
    candidates = development.loc[
        development["Strategy"] != "STATIC_RETIREMENT_7030"
    ].copy()
    candidates["CAGRGap"] = candidates["CAGR"] - baseline["CAGR"]
    candidates["MDDImprovement"] = candidates["MDD"] - baseline["MDD"]
    candidates["SharpeGap"] = candidates["Sharpe"] - baseline["Sharpe"]
    candidates["CAGRPass"] = candidates["CAGRGap"] >= -0.01
    candidates["MDDPass"] = candidates["MDDImprovement"] > 0.0
    candidates["SharpePass"] = candidates["SharpeGap"] > 0.0
    candidates["Pass"] = candidates[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    candidates["Selected"] = False
    passed = candidates.loc[candidates["Pass"]].sort_values(
        ["Calmar", "Sharpe", "CAGR"], ascending=False
    )
    if not passed.empty:
        candidates.loc[passed.index[0], "Selected"] = True
    return candidates.sort_values(
        ["Selected", "Pass", "Calmar"], ascending=[False, False, False]
    )


def _forecast_diagnostics(data, active_start):
    decisions = data.loc[~data.index.to_period("M").duplicated()].copy()
    positions = data.index.get_indexer(decisions.index)
    complete = positions + HORIZON_DAYS < len(data)
    decisions = decisions.iloc[np.flatnonzero(complete)].copy()
    positions = positions[complete]
    actual_volatility, actual_loss = forward_continuous_risk_targets(
        data["QQQ_Close"].to_numpy(dtype=float), positions
    )
    decisions["ActualDownsideVolatility"] = actual_volatility
    decisions["ActualMaximumLoss"] = actual_loss
    decisions = decisions.loc[decisions.index >= active_start]
    rows = []
    for period, (start, end) in PERIODS.items():
        window = decisions.loc[start:end]
        for target, prediction, base in (
            ("DOWNSIDE_VOLATILITY", "PredictedDownsideVolatility", "BaseDownsideVolatility"),
            ("MAXIMUM_PATH_LOSS", "PredictedMaximumLoss", "BaseMaximumLoss"),
        ):
            actual_column = (
                "ActualDownsideVolatility" if target == "DOWNSIDE_VOLATILITY"
                else "ActualMaximumLoss"
            )
            valid = window[[actual_column, prediction, base]].dropna()
            model_error = valid[prediction] - valid[actual_column]
            base_error = valid[base] - valid[actual_column]
            rows.append({
                "Target": target,
                "Period": period,
                "Observations": len(valid),
                "MAE": model_error.abs().mean(),
                "BaseMAE": base_error.abs().mean(),
                "RMSE": np.sqrt(np.mean(model_error ** 2)),
                "BaseRMSE": np.sqrt(np.mean(base_error ** 2)),
                "RMSESkillVsBase": 1.0 - np.mean(model_error ** 2) / np.mean(base_error ** 2),
                "Correlation": valid[prediction].corr(valid[actual_column]),
            })
    return pd.DataFrame(rows)


def _promotion_report(metrics, selection):
    selected = selection.loc[selection["Selected"], "Strategy"]
    if selected.empty:
        return pd.DataFrame()
    selected_name = selected.iloc[0]
    rows = []
    for period in ("VALIDATION_2018_2022", "LOCK_2023_PRESENT"):
        baseline = metrics.loc[
            (metrics["Strategy"] == "STATIC_RETIREMENT_7030")
            & (metrics["Period"] == period)
        ].iloc[0]
        candidate = metrics.loc[
            (metrics["Strategy"] == selected_name) & (metrics["Period"] == period)
        ].iloc[0]
        cagr_gap = candidate["CAGR"] - baseline["CAGR"]
        mdd_improvement = candidate["MDD"] - baseline["MDD"]
        sharpe_gap = candidate["Sharpe"] - baseline["Sharpe"]
        rows.append({
            "Strategy": selected_name,
            "Period": period,
            "CAGRGap": cagr_gap,
            "MDDImprovement": mdd_improvement,
            "SharpeGap": sharpe_gap,
            "CAGRPass": cagr_gap >= -0.01,
            "MDDPass": mdd_improvement > 0.0,
            "SharpePass": sharpe_gap > 0.0,
        })
    report = pd.DataFrame(rows)
    report["PeriodPass"] = report[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    report["Promote"] = report["PeriodPass"].all()
    return report


def run_continuous_risk_forecast_validation():
    data = _load_feature_data()
    forecast = walk_forward_continuous_risk(data)
    prepared = data.join(forecast)
    active = prepared.index[prepared["RiskModelSamples"] >= MINIMUM_MODEL_SAMPLES]
    if active.empty:
        raise RuntimeError("continuous risk model never reached its minimum sample count")
    active_start = active[0]
    results = [_run("STATIC_RETIREMENT_7030", STATIC_RETIREMENT_7030(), prepared)]
    results.extend(
        _run(profile.name, ContinuousForecastStrategy(profile), prepared)
        for profile in PROFILES
    )
    metrics = _performance_rows(results, active_start)
    selection = _development_selection(metrics)
    diagnostics = _forecast_diagnostics(prepared, active_start)
    promotion = _promotion_report(metrics, selection)
    metrics.to_csv(RESULT_DIR / "continuous_risk_forecast_metrics.csv", index=False)
    selection.to_csv(RESULT_DIR / "continuous_risk_forecast_selection.csv", index=False)
    diagnostics.to_csv(RESULT_DIR / "continuous_risk_forecast_diagnostics.csv", index=False)
    promotion.to_csv(RESULT_DIR / "continuous_risk_forecast_promotion.csv", index=False)
    return {
        "metrics": metrics,
        "selection": selection,
        "diagnostics": diagnostics,
        "promotion": promotion,
    }


if __name__ == "__main__":
    reports = run_continuous_risk_forecast_validation()
    print("Development allocation selection")
    columns = [
        "Strategy", "CAGR", "MDD", "Sharpe", "Calmar", "AverageRiskWeight",
        "CAGRGap", "MDDImprovement", "SharpeGap", "CAGRPass", "MDDPass",
        "SharpePass", "Pass", "Selected",
    ]
    print(reports["selection"][columns].to_string(index=False))
    print("\nDevelopment forecast accuracy")
    print(reports["diagnostics"].loc[
        reports["diagnostics"]["Period"] == "DEVELOPMENT_TO_2017"
    ].to_string(index=False))
    if reports["selection"]["Selected"].any():
        selected = reports["selection"].loc[reports["selection"]["Selected"], "Strategy"].iloc[0]
        print("\nFixed candidate vs static")
        print(reports["metrics"].loc[
            reports["metrics"]["Strategy"].isin(("STATIC_RETIREMENT_7030", selected))
        ].to_string(index=False))
        print("\nPromotion gate")
        print(reports["promotion"].to_string(index=False))
    else:
        print("\nNo development allocation candidate passed.")
