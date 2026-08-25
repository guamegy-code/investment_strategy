"""Test fear-conditioned QQQ sizing inside RECOVERY without changing states."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from attribution import RetirementAllocationAttribution
from config import RESULT_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from .extreme_fear_recovery import (
    OBSERVATION,
    SOURCE,
    ExtremeFearBacktest,
    build_extreme_fear_signal,
)


WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
    "2011_2015": (None, "2015-12-31"),
    "2016_2020": ("2016-01-01", "2020-12-31"),
    "2021_PRESENT": ("2021-01-01", None),
}


@dataclass(frozen=True)
class RecoveryTierProfile:
    name: str
    stressed_weight: float | None = None
    stress_score: int = 2
    memory: int = 20


PROFILES = (
    RecoveryTierProfile("BASELINE"),
    RecoveryTierProfile("RECOVERY25_SCORE2_MEMORY10", 0.25, 2, 10),
    RecoveryTierProfile("RECOVERY25_SCORE2_MEMORY20", 0.25, 2, 20),
    RecoveryTierProfile("RECOVERY25_SCORE2_MEMORY30", 0.25, 2, 30),
    RecoveryTierProfile("RECOVERY35_SCORE2_MEMORY20", 0.35, 2, 20),
    RecoveryTierProfile("RECOVERY40_SCORE2_MEMORY20", 0.40, 2, 20),
    RecoveryTierProfile("RECOVERY25_SCORE3_MEMORY10", 0.25, 3, 10),
    RecoveryTierProfile("RECOVERY25_SCORE3_MEMORY20", 0.25, 3, 20),
    RecoveryTierProfile("RECOVERY25_SCORE3_MEMORY30", 0.25, 3, 30),
    RecoveryTierProfile("RECOVERY35_SCORE3_MEMORY20", 0.35, 3, 20),
    RecoveryTierProfile("RECOVERY40_SCORE3_MEMORY20", 0.40, 3, 20),
)


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    if OBSERVATION not in observations:
        observations.append(OBSERVATION)
    if profile.stressed_weight is None:
        return definition

    risk_rules = definition["state"]["risk_weight"]["rules"]
    recovery_index = next(
        index for index, rule in enumerate(risk_rules)
        if rule.get("when") == "state.market_mode == 'RECOVERY'"
    )
    risk_rules.insert(recovery_index, {
        "when": (
            "state.market_mode == 'RECOVERY' "
            f"and FEAR.recent_score{profile.memory} >= {profile.stress_score}"
        ),
        "set": f"{profile.stressed_weight * 100:g}%",
    })

    monthly_index = next(
        index for index, rule in enumerate(definition["rebalance"])
        if rule.get("check") == "monthly"
    )
    definition["rebalance"].insert(monthly_index, {
        "when": (
            "state.market_mode == 'RECOVERY' "
            "and changed(state.risk_weight) "
            "and target_deviation() >= 5%"
        ),
        "days": "state.execution_days",
    })
    return definition


def _run(profile):
    strategy = DeclarativeStrategy(candidate_definition(profile))
    backtest = ExtremeFearBacktest(strategy, tickers=strategy.required_tickers)
    history, trades, rebalances = backtest.run_all()
    attribution = RetirementAllocationAttribution(history, backtest.data, rebalances)
    return {
        "profile": profile,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "events": attribution.transition_events(),
        "quality": attribution.transition_quality(),
    }


def _performance_reports(results):
    rows = []
    for result in results:
        for window, (start, end) in WINDOWS.items():
            sample = result["history"].loc[start:end]
            if len(sample) < 2:
                continue
            metrics = Performance(sample).summary()
            rows.append({
                "Profile": result["profile"].name,
                "Window": window,
                "StartDate": sample.index.min(),
                "EndDate": sample.index.max(),
                "Observations": len(sample),
                "CAGR": metrics["CAGR"],
                "MDD": metrics["MDD"],
                "Sharpe": metrics["Sharpe"],
                "Calmar": metrics["Calmar"],
                "TransactionCosts": (
                    sample["TransactionCosts"].iloc[-1]
                    - sample["TransactionCosts"].iloc[0]
                ),
            })
    windows = pd.DataFrame(rows)
    indexed = windows.set_index(["Profile", "Window"])
    summary_rows = []
    for profile in PROFILES:
        row = {"Profile": profile.name}
        for window in ("FULL", "DEVELOPMENT_PRE2021", "RECENT_2021_PRESENT"):
            current = indexed.loc[(profile.name, window)]
            baseline = indexed.loc[("BASELINE", window)]
            prefix = "Full" if window == "FULL" else ("Development" if window.startswith("DEVELOPMENT") else "Recent")
            row[f"{prefix}CAGRGap"] = current["CAGR"] - baseline["CAGR"]
            row[f"{prefix}MDDImprovement"] = current["MDD"] - baseline["MDD"]
            row[f"{prefix}CalmarGap"] = current["Calmar"] - baseline["Calmar"]
        row["Rebalances"] = len(results[PROFILES.index(profile)]["rebalances"])
        row["Trades"] = len(results[PROFILES.index(profile)]["trades"])
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    summary["DirectionalPass"] = (
        (summary["DevelopmentCAGRGap"] >= 0)
        & (summary["RecentCAGRGap"] >= 0)
    )
    summary["DrawdownPass"] = (
        (summary["DevelopmentMDDImprovement"] >= -0.005)
        & (summary["RecentMDDImprovement"] >= -0.005)
    )
    summary["OverallPass"] = (
        (summary["Profile"] != "BASELINE")
        & summary["DirectionalPass"]
        & summary["DrawdownPass"]
        & (summary["FullCAGRGap"] > 0)
        & (summary["FullCalmarGap"] >= 0)
    )
    return windows, summary


def _tagged(results, key):
    frames = []
    for result in results:
        frame = result[key].copy()
        frame.insert(0, "Profile", result["profile"].name)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def run_recovery_risk_tier_validation():
    signal = build_extreme_fear_signal()
    ExtremeFearBacktest.fear_frame = signal
    results = [_run(profile) for profile in PROFILES]
    windows, summary = _performance_reports(results)
    reports = {
        "recovery_risk_tier_summary": summary,
        "recovery_risk_tier_windows": windows,
        "recovery_risk_tier_transition_events": _tagged(results, "events"),
        "recovery_risk_tier_transition_quality": _tagged(results, "quality"),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_recovery_risk_tier_validation()
    print(output["recovery_risk_tier_summary"].to_string(index=False))
