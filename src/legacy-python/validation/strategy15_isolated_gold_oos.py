"""Track the locked strategy-15 isolated GLD shadow pair on unseen data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from config import RESULT_DIR
from performance import Performance
from validation.strategy15_isolated_gold_overlay import (
    PROFILES,
    STRESS_SCENARIOS,
    _run_scenario,
)


ROOT = Path(__file__).resolve().parents[3]
LOCK_PATH = ROOT / "validation" / "strategy15_isolated_gold_oos_lock.json"


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def verify_lock(lock: dict | None = None) -> dict:
    lock = lock or load_lock()
    errors = []
    expected_profiles = {
        "CONDITIONAL_GLD_5": {
            "mode": "conditional",
            "maximum_gld_weight": 0.05,
            "confirmation_days": 5,
            "minimum_hold_days": 20,
        },
        "STATIC_GLD_5": {
            "mode": "static",
            "maximum_gld_weight": 0.05,
            "confirmation_days": 5,
            "minimum_hold_days": 20,
        },
    }
    params = lock.get("candidate_parameters", {})
    actual = {
        "mode": params.get("mode"),
        "maximum_gld_weight": params.get("maximum_gld_weight"),
        "confirmation_days": params.get("confirmation_days"),
        "minimum_hold_days": params.get("minimum_hold_days"),
    }
    if actual != expected_profiles.get(lock.get("selected_candidate")):
        errors.append("locked candidate parameters are not a supported frozen profile")
    profile_names = {profile.name for profile in PROFILES}
    for name in (lock.get("baseline"), lock.get("selected_candidate"), lock.get("negative_control")):
        if name not in profile_names:
            errors.append(f"unknown locked profile: {name}")
    for filename, expected in lock.get("code_sha256", {}).items():
        path = ROOT / filename
        if not path.exists():
            errors.append(f"missing locked file: {filename}")
        elif _sha256(path) != str(expected).upper():
            errors.append(f"{filename} hash differs from locked version")
    if errors:
        raise RuntimeError("isolated GLD OOS lock verification failed: " + "; ".join(errors))
    return lock


def _future_with_anchor(history: pd.DataFrame, cutoff: pd.Timestamp):
    future = history.loc[history.index > cutoff]
    if future.empty:
        return pd.DataFrame(), future
    prior = history.loc[history.index <= cutoff]
    if prior.empty:
        raise ValueError("no pre-OOS anchor observation is available")
    return pd.concat([prior.iloc[-1:], future]), future


def update_isolated_gold_oos() -> pd.DataFrame:
    lock = verify_lock()
    results = _run_scenario(STRESS_SCENARIOS[0])
    wanted = {
        lock["baseline"],
        lock["selected_candidate"],
        lock["negative_control"],
    }
    cutoff = pd.Timestamp(lock["historical_data_cutoff"])
    status_rows = []
    daily_frames = []
    for result in results:
        name = result["profile"].name
        if name not in wanted:
            continue
        anchored, future = _future_with_anchor(result["history"], cutoff)
        row = {
            "Strategy": name,
            "Status": "AWAITING_UNSEEN_DATA" if future.empty else "OOS_TRACKING_ACTIVE",
            "LockedCutoff": cutoff,
            "OOSStart": lock["oos_start"],
            "LatestAvailableDate": result["history"].index.max(),
            "OOSObservations": len(future),
        }
        if not future.empty:
            metrics = Performance(anchored).summary()
            anchor = float(anchored["Portfolio"].iloc[0])
            normalized = future.copy()
            normalized["OOSPortfolio"] = normalized["Portfolio"] / anchor
            normalized["Strategy"] = name
            daily_frames.append(normalized.reset_index())
            row.update({
                "OOSTotalReturn": float(normalized["OOSPortfolio"].iloc[-1] - 1.0),
                "OOSCAGR": float(metrics["CAGR"]),
                "OOSMDD": float(metrics["MDD"]),
                "OOSSharpe": float(metrics["Sharpe"]),
            })
        status_rows.append(row)
    status = pd.DataFrame(status_rows)
    status.to_csv(RESULT_DIR / "strategy15_isolated_gold_oos_status.csv", index=False)
    if daily_frames:
        pd.concat(daily_frames, ignore_index=True).to_csv(
            RESULT_DIR / "strategy15_isolated_gold_oos_daily.csv", index=False
        )
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    lock = verify_lock()
    if args.verify_only:
        print("LOCK_OK", lock["oos_start"])
    else:
        print(update_isolated_gold_oos().to_string(index=False))
