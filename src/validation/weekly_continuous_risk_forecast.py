"""Compare monthly and weekly continuous-risk forecasts at fixed parameters."""

from __future__ import annotations

import pandas as pd

from config import RESULT_DIR
from strategy import STATIC_RETIREMENT_7030
from .continuous_risk_forecast import (
    ContinuousForecastStrategy,
    MINIMUM_MODEL_SAMPLES,
    PROFILES,
    PreparedContinuousRiskBacktest,
    _performance_rows,
    _run,
    walk_forward_continuous_risk,
)
from .path_tail_targets import _load_feature_data


MONTHLY_MODEL = "CONTINUOUS_RISK_MONTHLY_STRESS_150"
WEEKLY_MODEL = "CONTINUOUS_RISK_WEEKLY_STRESS_150"
WEEKLY_MINIMUM_SAMPLES = 260
WEEKLY_RELIABILITY_SAMPLES = 520


def _active_start(prepared, minimum_samples):
    active = prepared.index[prepared["RiskModelSamples"] >= minimum_samples]
    if active.empty:
        raise RuntimeError("continuous risk model never reached its minimum sample count")
    return active[0]


def _development_gate(metrics):
    development = metrics.loc[metrics["Period"] == "DEVELOPMENT_TO_2017"]
    monthly = development.loc[development["Strategy"] == MONTHLY_MODEL].iloc[0]
    weekly = development.loc[development["Strategy"] == WEEKLY_MODEL].iloc[0]
    report = pd.DataFrame([{
        "Strategy": WEEKLY_MODEL,
        "CAGRGapVsMonthly": weekly["CAGR"] - monthly["CAGR"],
        "MDDImprovementVsMonthly": weekly["MDD"] - monthly["MDD"],
        "SharpeGapVsMonthly": weekly["Sharpe"] - monthly["Sharpe"],
        "TransactionCostGapVsMonthly": (
            weekly["TransactionCosts"] - monthly["TransactionCosts"]
        ),
    }])
    report["CAGRPass"] = report["CAGRGapVsMonthly"] >= -0.005
    report["MDDPass"] = report["MDDImprovementVsMonthly"] > 0.0
    report["SharpePass"] = report["SharpeGapVsMonthly"] > 0.0
    report["Pass"] = report[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    return report


def _later_comparison(metrics, development_gate):
    rows = []
    for period in ("VALIDATION_2018_2022", "LOCK_2023_PRESENT"):
        window = metrics.loc[metrics["Period"] == period]
        monthly = window.loc[window["Strategy"] == MONTHLY_MODEL].iloc[0]
        weekly = window.loc[window["Strategy"] == WEEKLY_MODEL].iloc[0]
        rows.append({
            "Period": period,
            "CAGRGapVsMonthly": weekly["CAGR"] - monthly["CAGR"],
            "MDDImprovementVsMonthly": weekly["MDD"] - monthly["MDD"],
            "SharpeGapVsMonthly": weekly["Sharpe"] - monthly["Sharpe"],
            "TransactionCostGapVsMonthly": (
                weekly["TransactionCosts"] - monthly["TransactionCosts"]
            ),
        })
    report = pd.DataFrame(rows)
    report["DevelopmentPass"] = bool(development_gate["Pass"].iloc[0])
    report["CAGRPass"] = report["CAGRGapVsMonthly"] >= -0.005
    report["MDDPass"] = report["MDDImprovementVsMonthly"] > 0.0
    report["SharpePass"] = report["SharpeGapVsMonthly"] > 0.0
    report["PeriodPass"] = report[["CAGRPass", "MDDPass", "SharpePass"]].all(axis=1)
    report["Promote"] = report["DevelopmentPass"] & report["PeriodPass"].all()
    return report


def run_weekly_continuous_risk_validation():
    data = _load_feature_data()
    monthly_forecast = walk_forward_continuous_risk(data)
    weekly_forecast = walk_forward_continuous_risk(
        data,
        decision_period="W-FRI",
        minimum_samples=WEEKLY_MINIMUM_SAMPLES,
        reliability_samples=WEEKLY_RELIABILITY_SAMPLES,
    )
    monthly_prepared = data.join(monthly_forecast)
    weekly_prepared = data.join(weekly_forecast)
    active_start = max(
        _active_start(monthly_prepared, MINIMUM_MODEL_SAMPLES),
        _active_start(weekly_prepared, WEEKLY_MINIMUM_SAMPLES),
    )
    profile = PROFILES[0]
    results = [
        _run("STATIC_RETIREMENT_7030", STATIC_RETIREMENT_7030(), monthly_prepared),
        _run(
            MONTHLY_MODEL,
            ContinuousForecastStrategy(profile, "M"),
            monthly_prepared,
        ),
        _run(
            WEEKLY_MODEL,
            ContinuousForecastStrategy(profile, "W-FRI"),
            weekly_prepared,
        ),
    ]
    metrics = _performance_rows(results, active_start)
    development = _development_gate(metrics)
    later = _later_comparison(metrics, development)
    metrics.to_csv(RESULT_DIR / "weekly_continuous_risk_metrics.csv", index=False)
    development.to_csv(
        RESULT_DIR / "weekly_continuous_risk_development.csv", index=False
    )
    later.to_csv(RESULT_DIR / "weekly_continuous_risk_later.csv", index=False)
    return {"metrics": metrics, "development": development, "later": later}


if __name__ == "__main__":
    reports = run_weekly_continuous_risk_validation()
    columns = [
        "Strategy", "Period", "CAGR", "MDD", "Sharpe", "AverageRiskWeight",
        "MinimumRiskWeight", "MaximumRiskWeight", "TotalRebalances", "TransactionCosts",
    ]
    print("Development frequency comparison")
    print(reports["metrics"].loc[
        reports["metrics"]["Period"] == "DEVELOPMENT_TO_2017", columns
    ].to_string(index=False))
    print("\nDevelopment gate")
    print(reports["development"].to_string(index=False))
    if reports["development"]["Pass"].any():
        print("\nFixed-frequency later comparison")
        print(reports["metrics"].loc[
            reports["metrics"]["Period"].isin(
                ("VALIDATION_2018_2022", "LOCK_2023_PRESENT")
            ), columns
        ].to_string(index=False))
        print("\nPromotion gate")
        print(reports["later"].to_string(index=False))
    else:
        print("\nWeekly frequency did not pass development; later periods were not used for selection.")
