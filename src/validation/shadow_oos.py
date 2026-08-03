"""Forward OOS tracking for the selected strict-RECOVERY shadow strategy."""

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
from .oos import _future_with_anchor, verify_lock as verify_production_lock
from performance import Performance
from runner import Runner
from strategy import AllocationState, RetirementAllocationStrategy


LOCK_PATH = PROJECT_ROOT / "validation" / "shadow_strict_recovery_lock.json"
BASELINE_NAME = "PRODUCTION_BASELINE"
SHADOW_NAME = "STRICT_RECOVERY_SHADOW"


class StrictRecoveryShadowStrategy(RetirementAllocationStrategy):
    """Production strategy with a stricter BEAR-to-RECOVERY transition only."""

    RECOVERY_ENTRY_SCORE = 4

    def _desired_state(self, qqq):
        desired = super()._desired_state(qqq)
        if (
            self.state != AllocationState.BEAR
            or desired != AllocationState.RECOVERY
        ):
            return desired

        close = qqq.get("Close")
        ema20 = qqq.get("EMA20")
        roc20 = qqq.get("ROC20")
        confirmed = (
            self.recovery_score >= self.RECOVERY_ENTRY_SCORE
            and self._valid(close, ema20, roc20)
            and close > ema20
            and roc20 > 0
        )
        return desired if confirmed else self.state


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_shadow_lock():
    with LOCK_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def current_shadow_parameters():
    strategy = StrictRecoveryShadowStrategy()
    state_weights = {}
    for state in AllocationState:
        risk_weight = strategy.STATE_RISK_WEIGHTS[state]
        state_weights[state.value] = [
            risk_weight,
            round(1.0 - risk_weight, 10),
        ]
    return {
        "base_strategy": "RetirementAllocationStrategy",
        "bear_entry_rule": "UNCHANGED_FROM_PRODUCTION",
        "recovery_entry_score": strategy.RECOVERY_ENTRY_SCORE,
        "recovery_close_above_ema20": True,
        "recovery_roc20_positive": True,
        "recovery_confirmation_days": strategy.RECOVERY_CONFIRMATION_DAYS,
        "state_weights_risk_safe": state_weights,
        "commission": COMMISSION,
        "slippage": SLIPPAGE,
        "execution": "signal_close_then_next_session_open",
        "extra_signal_delay_days": 0,
    }


def verify_shadow_lock():
    verify_production_lock()
    lock = load_shadow_lock()
    errors = []
    if current_shadow_parameters() != lock["parameters"]:
        errors.append("shadow strategy parameters differ from locked values")
    for filename, expected in lock["code_sha256"].items():
        actual = _file_sha256(PROJECT_ROOT / "src" / filename)
        if actual != expected:
            errors.append(f"{filename} hash differs from locked shadow version")
    if errors:
        raise RuntimeError("Shadow OOS lock verification failed: " + "; ".join(errors))
    return lock


def _run_locked_pair():
    runner = Runner(data_dir=EXTENDED_DATA_DIR, tickers=ASSETS)
    runner.add_strategy(RetirementAllocationStrategy())
    runner.add_strategy(StrictRecoveryShadowStrategy())
    results = runner.run()
    return {
        BASELINE_NAME: results[0],
        SHADOW_NAME: results[1],
    }


def _empty_daily_frame():
    return pd.DataFrame(columns=[
        "Date",
        "Strategy",
        "OOSPortfolio",
        "StrategyState",
        "RiskOffScore",
        "RecoveryScore",
        "TransactionCosts",
        "Weights",
    ])


def _comparison_frame(daily):
    if daily.empty:
        return pd.DataFrame(columns=[
            "Date",
            "BaselineOOSPortfolio",
            "ShadowOOSPortfolio",
            "ShadowGap",
        ])
    values = daily.pivot(
        index="Date", columns="Strategy", values="OOSPortfolio"
    )
    if BASELINE_NAME not in values or SHADOW_NAME not in values:
        return pd.DataFrame()
    comparison = pd.DataFrame({
        "BaselineOOSPortfolio": values[BASELINE_NAME],
        "ShadowOOSPortfolio": values[SHADOW_NAME],
    })
    comparison["ShadowGap"] = (
        comparison["ShadowOOSPortfolio"]
        - comparison["BaselineOOSPortfolio"]
    )
    return comparison.reset_index()


def update_shadow_oos(refresh_data=False):
    lock = verify_shadow_lock()
    if refresh_data:
        build_extended_data()
    results = _run_locked_pair()
    cutoff = pd.Timestamp(lock["historical_data_cutoff"])

    status_rows = []
    daily_frames = []
    for name, result in results.items():
        history = result["history"]
        anchored, future = _future_with_anchor(history, cutoff)
        status = {
            "Strategy": name,
            "LockedCutoff": cutoff,
            "OOSStart": lock["oos_start"],
            "LatestAvailableDate": history.index.max(),
            "OOSObservations": len(future),
        }
        if future.empty:
            status["Status"] = "AWAITING_UNSEEN_DATA"
            status_rows.append(status)
            continue

        metrics = Performance(anchored).summary()
        normalized = future.copy()
        normalized["OOSPortfolio"] = (
            normalized["Portfolio"] / anchored["Portfolio"].iloc[0]
        )
        normalized["Strategy"] = name
        normalized.index.name = "Date"
        available_columns = [
            "Strategy",
            "OOSPortfolio",
            "StrategyState",
            "RiskOffScore",
            "RecoveryScore",
            "TransactionCosts",
            "Weights",
        ]
        daily_frames.append(
            normalized[
                [column for column in available_columns if column in normalized]
            ].reset_index()
        )
        status.update({
            "Status": "OOS_TRACKING_ACTIVE",
            "OOSTotalReturn": normalized["OOSPortfolio"].iloc[-1] - 1.0,
            "OOSCAGR": metrics["CAGR"],
            "OOSMDD": metrics["MDD"],
            "OOSSharpe": metrics["Sharpe"],
        })
        status_rows.append(status)

    status = pd.DataFrame(status_rows)
    daily = (
        pd.concat(daily_frames, ignore_index=True)
        if daily_frames
        else _empty_daily_frame()
    )
    comparison = _comparison_frame(daily)
    status.to_csv(RESULT_DIR / "shadow_strict_recovery_status.csv", index=False)
    daily.to_csv(RESULT_DIR / "shadow_strict_recovery_daily.csv", index=False)
    comparison.to_csv(
        RESULT_DIR / "shadow_strict_recovery_comparison.csv", index=False
    )
    return status, daily, comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Refresh proxy-extended prices before updating the shadow ledger.",
    )
    arguments = parser.parse_args()
    current_status, _, current_comparison = update_shadow_oos(
        refresh_data=arguments.refresh_data
    )
    print(current_status.to_string(index=False))
    if not current_comparison.empty:
        print("\nLatest paired comparison")
        print(current_comparison.tail().to_string(index=False))
