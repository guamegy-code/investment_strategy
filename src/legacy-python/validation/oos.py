"""Freeze the selected strategy and track genuinely unseen future results."""

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from config import (
    COMMISSION,
    EXTENDED_DATA_DIR,
    PROJECT_ROOT,
    RESULT_DIR,
    SLIPPAGE,
)
from .extended_data import ASSETS, build_extended_data
from performance import Performance
from runner import Runner
from strategy import (
    AllocationState,
    RetirementAllocationLegacyStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


LOCK_PATH = PROJECT_ROOT / "validation" / "oos_strategy_lock.json"
RETROSPECTIVE_PERIODS = {
    "DOTCOM_AND_RECOVERY": ("2000-08-30", "2006-12-29"),
    "GFC_AND_RECOVERY": ("2007-01-01", "2012-12-31"),
    "LONG_EXPANSION": ("2013-01-01", "2019-12-31"),
    "PANDEMIC_AND_INFLATION": ("2020-01-01", "2022-12-30"),
    "RECENT_ALREADY_SEEN": ("2023-01-01", "2026-07-31"),
}


def load_lock():
    with LOCK_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_parameters():
    strategy = RetirementAllocationLegacyStrategy()
    state_weights = {
        state.value: [
            strategy.STATE_RISK_WEIGHTS[state],
            round(1.0 - strategy.STATE_RISK_WEIGHTS[state], 10),
        ]
        for state in AllocationState
    }
    return {
        "safe_momentum_period": strategy.SAFE_MOMENTUM_PERIOD,
        "safe_switch_buffer_pct_points": strategy.SAFE_SWITCH_BUFFER,
        "bull_reentry_rule": "ABOVE_EMA55_AND_ROC60_POSITIVE",
        "structural_drawdown": strategy.STRUCTURAL_DRAWDOWN,
        "bear_confirmation_days": strategy.BEAR_CONFIRMATION_DAYS,
        "caution_enter_score": strategy.CAUTION_ENTER_SCORE,
        "caution_confirmation_days": strategy.CAUTION_CONFIRMATION_DAYS,
        "bear_recovery_score": strategy.BEAR_RECOVERY_SCORE,
        "recovery_confirmation_days": strategy.RECOVERY_CONFIRMATION_DAYS,
        "state_weights_risk_safe": state_weights,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
        "execution": "signal_close_then_next_session_open",
        "extra_signal_delay_days": 0,
    }


def verify_lock():
    lock = load_lock()
    errors = []
    if current_parameters() != lock["parameters"]:
        errors.append("strategy parameters differ from the locked values")
    for filename, expected in lock["code_sha256"].items():
        actual = _file_sha256(PROJECT_ROOT / "src" / filename)
        if actual != expected:
            errors.append(f"{filename} hash differs from the locked version")
    if errors:
        raise RuntimeError("OOS lock verification failed: " + "; ".join(errors))
    return lock


def _run_locked_pair():
    runner = Runner(data_dir=EXTENDED_DATA_DIR, tickers=ASSETS)
    runner.add_strategy(RetirementAllocationLegacyStrategy())
    runner.add_strategy(STATIC_70_BND10_BIL10_GLD10())
    return runner.run()


def _period_metrics(history, start, end):
    sample = history.loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    costs = sample.get("TransactionCosts")
    metrics["TransactionCosts"] = (
        costs.iloc[-1] - costs.iloc[0] if costs is not None else 0.0
    )
    metrics["TotalReturn"] = (
        sample["Portfolio"].iloc[-1] / sample["Portfolio"].iloc[0] - 1.0
    )
    metrics["StartDate"] = sample.index.min()
    metrics["EndDate"] = sample.index.max()
    metrics["Observations"] = len(sample)
    return metrics


def retrospective_validation(results):
    rows = []
    for result in results:
        name = result["strategy"].__class__.__name__
        for period, (start, end) in RETROSPECTIVE_PERIODS.items():
            metrics = _period_metrics(result["history"], start, end)
            if metrics is None:
                continue
            rows.append({
                "EvidenceType": "RETROSPECTIVE_NOT_OOS",
                "Period": period,
                "Strategy": name,
                **metrics,
            })
    frame = pd.DataFrame(rows)
    frame.to_csv(RESULT_DIR / "final_validation_retrospective.csv", index=False)
    return frame


def annual_retrospective_validation(results):
    annual = {}
    for result in results:
        name = result["strategy"].__class__.__name__
        returns = result["history"]["Portfolio"].pct_change().dropna()
        annual[name] = (1.0 + returns).groupby(returns.index.year).prod() - 1.0
    dynamic_name = "RetirementAllocationLegacyStrategy"
    benchmark_name = "STATIC_70_BND10_BIL10_GLD10"
    frame = pd.concat([
        annual[dynamic_name].rename("DynamicReturn"),
        annual[benchmark_name].rename("BenchmarkReturn"),
    ], axis=1).dropna()
    frame["Gap"] = frame["DynamicReturn"] - frame["BenchmarkReturn"]
    frame["DynamicWon"] = frame["Gap"] > 0
    frame.insert(0, "EvidenceType", "RETROSPECTIVE_NOT_OOS")
    frame.index.name = "Year"
    frame.to_csv(RESULT_DIR / "final_validation_annual.csv")
    return frame


def _future_with_anchor(history, cutoff):
    future = history.loc[history.index > cutoff]
    if future.empty:
        return pd.DataFrame(), future
    prior = history.loc[history.index <= cutoff]
    if prior.empty:
        raise ValueError("No pre-OOS anchor observation is available")
    anchored = pd.concat([prior.iloc[-1:], future])
    return anchored, future


def update_oos(refresh_data=False):
    lock = verify_lock()
    if refresh_data:
        build_extended_data()
    results = _run_locked_pair()
    retrospective = retrospective_validation(results)
    annual = annual_retrospective_validation(results)
    cutoff = pd.Timestamp(lock["historical_data_cutoff"])

    status_rows = []
    daily_frames = []
    for result in results:
        name = result["strategy"].__class__.__name__
        anchored, future = _future_with_anchor(result["history"], cutoff)
        if future.empty:
            status_rows.append({
                "Strategy": name,
                "Status": "AWAITING_UNSEEN_DATA",
                "LockedCutoff": cutoff,
                "OOSStart": lock["oos_start"],
                "LatestAvailableDate": result["history"].index.max(),
                "OOSObservations": 0,
            })
            continue

        metrics = Performance(anchored).summary()
        normalized = future.copy()
        anchor_value = anchored["Portfolio"].iloc[0]
        normalized["OOSPortfolio"] = normalized["Portfolio"] / anchor_value
        normalized["Strategy"] = name
        daily_frames.append(normalized.reset_index())
        status_rows.append({
            "Strategy": name,
            "Status": "OOS_TRACKING_ACTIVE",
            "LockedCutoff": cutoff,
            "OOSStart": lock["oos_start"],
            "LatestAvailableDate": future.index.max(),
            "OOSObservations": len(future),
            "OOSTotalReturn": normalized["OOSPortfolio"].iloc[-1] - 1.0,
            "OOSCAGR": metrics["CAGR"],
            "OOSMDD": metrics["MDD"],
            "OOSSharpe": metrics["Sharpe"],
        })

    status = pd.DataFrame(status_rows)
    status.to_csv(RESULT_DIR / "oos_status.csv", index=False)
    if daily_frames:
        pd.concat(daily_frames, ignore_index=True).to_csv(
            RESULT_DIR / "oos_daily.csv", index=False
        )
    return status, retrospective, annual


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh-data", action="store_true",
        help="Download current data before updating the forward OOS ledger.",
    )
    arguments = parser.parse_args()
    current_status, retrospective_report, annual_report = update_oos(
        refresh_data=arguments.refresh_data
    )
    print(current_status.to_string(index=False))
    print("\nRetrospective annual comparison")
    print({
        "Years": len(annual_report),
        "WinRate": annual_report["DynamicWon"].mean(),
        "AverageAnnualGap": annual_report["Gap"].mean(),
    })
