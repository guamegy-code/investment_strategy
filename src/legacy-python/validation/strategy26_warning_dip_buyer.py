"""Research a strategy-28 style WARNING dip buyer for strategy 26.

The production strategy 26 holds QQQ 70% and a TDF2050 proxy 30% in its
normal allocation.  This experiment creates a temporary BIL buffer only while
valuation is in WARNING, then restores the normal 70/30 allocation after QQQ
falls 10% from its tracked peak.  Existing BEAR, structural-bear, DEFENSE, and
RECOVERY allocations retain priority.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from backtest import Backtest
from config import COMMISSION, RESULT_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
START_DATE = "2012-01-03"
END_DATE = "2026-07-31"
STRATEGY26 = ROOT / "strategies/26_band_7030_tdf_valuation_defense.yaml"
STRATEGY28 = ROOT / "strategies/28_qqq_valuation_warning_dip_buyer.yaml"
PERIODS = {
    "FULL": (START_DATE, END_DATE),
    "DEVELOPMENT": (START_DATE, "2020-12-31"),
    "RECENT": ("2021-01-01", END_DATE),
}
EVENT_WINDOWS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "2020_CRASH_RECOVERY": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2022-01-03", "2022-12-30"),
    "2025_2026": ("2025-01-02", END_DATE),
}
WARNING_ALLOCATIONS = {
    # Reduce only QQQ while retaining the full retirement/TDF sleeve.
    "Q60_T30_B10": (0.60, 0.30, 0.10),
    "Q50_T30_B20": (0.50, 0.30, 0.20),
    "Q40_T30_B30": (0.40, 0.30, 0.30),
    # Reduce QQQ and TDF proportionally to create the BIL buffer.
    "Q63_T27_B10": (0.63, 0.27, 0.10),
    "Q56_T24_B20": (0.56, 0.24, 0.20),
    "Q49_T21_B30": (0.49, 0.21, 0.30),
    # Preserve QQQ and temporarily replace part or all of the TDF sleeve.
    "Q70_T20_B10": (0.70, 0.20, 0.10),
    "Q70_T10_B20": (0.70, 0.10, 0.20),
    "Q70_T00_B30": (0.70, 0.00, 0.30),
}
BALANCED_FINALIST = "Q50_T30_B20"


def _metrics(history: pd.DataFrame, start: str, end: str) -> dict[str, float]:
    selected = history.loc[start:end]
    performance = Performance(selected)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Volatility": performance.volatility(),
        "Sharpe": performance.sharpe_ratio(),
        "Calmar": performance.calmar_ratio(),
        "TotalReturn": selected["Portfolio"].iloc[-1]
        / selected["Portfolio"].iloc[0]
        - 1,
    }


def _variant(
    label: str,
    warning_weights: tuple[float, float, float],
    *,
    drop_b: float = -0.10,
    execution_days: int = 1,
    redeploy_at_dip: bool = True,
) -> dict[str, Any]:
    definition = load_strategy_definition(STRATEGY26)
    dip_source = load_strategy_definition(STRATEGY28)
    definition["strategy"] = {
        **definition["strategy"],
        "id": f"strategy26-warning-dip-{label.lower()}",
        "name": f"Strategy 26 WARNING dip buyer {label}",
    }
    for name in (
        "drop_b",
        "drop_c",
        "drop_d",
        "recovery_stage_1",
        "recovery_stage_2",
        "recovery_stage_3",
    ):
        definition["parameters"][name] = dip_source["parameters"][name]
    definition["parameters"]["drop_b"] = drop_b
    definition["state"] = {
        "stage": deepcopy(dip_source["state"]["stage"]),
        "peak_price": deepcopy(dip_source["state"]["peak_price"]),
        **definition["state"],
    }

    normal = {"QQQ": "70%", "TDF2050_PROXY": "30%", "BIL": "0%"}
    qqq, tdf, bil = warning_weights
    warning = {
        "QQQ": f"{qqq:.12g}",
        "TDF2050_PROXY": f"{tdf:.12g}",
        "BIL": f"{bil:.12g}",
    }
    priority_targets = deepcopy(definition["target"][:-1])
    dip_targets = [
        {"when": f"state.stage == {stage}", "weights": deepcopy(normal)}
        for stage in (3, 2, 1)
    ] if redeploy_at_dip else []
    definition["target"] = [
        *priority_targets,
        *dip_targets,
        {"when": "state.defense_mode == 'WARNING'", "weights": warning},
        {"weights": normal},
    ]
    definition["execution"]["days"] = execution_days
    return definition


def _run(
    definition: Mapping[str, Any],
    *,
    label: str,
    cost_multiple: float = 1.0,
) -> dict[str, Any]:
    strategy = DeclarativeStrategy(deepcopy(definition))
    history, trades, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
        start_date=START_DATE,
        end_date=END_DATE,
    ).run_all()
    dip_signals = 0
    dip_redeployments = []
    warning_entries = []
    for date, context in history.get("NotificationContext", []).items():
        if not isinstance(context, dict):
            continue
        changes = context.get("state_changes") or []
        is_dip = any(
            change.get("name") == "stage"
            and change.get("previous") == 0
            and change.get("current") == 1
            for change in changes
        )
        dip_signals += int(is_dip)
        if any(
            change.get("name") == "defense_mode"
            and change.get("previous") == "NORMAL"
            and change.get("current") == "WARNING"
            for change in changes
        ):
            warning_entries.append(str(pd.Timestamp(date).date()))
        previous = context.get("previous_target_weights") or {}
        target = context.get("target_weights") or {}
        if (
            is_dip
            and context.get("target_changed")
            and float(previous.get("BIL", 0.0)) > 1e-12
            and abs(float(target.get("QQQ", 0.0)) - 0.70) < 1e-12
            and abs(float(target.get("TDF2050_PROXY", 0.0)) - 0.30) < 1e-12
        ):
            dip_redeployments.append(str(pd.Timestamp(date).date()))
    return {
        "Label": label,
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "DipSignals": dip_signals,
        "DipRedeployments": len(dip_redeployments),
        "DipRedeploymentDates": ";".join(dip_redeployments),
        "WarningEntries": len(warning_entries),
        "WarningEntryDates": ";".join(warning_entries),
        "TransactionCosts": float(history["TransactionCosts"].iloc[-1]),
        "history": history,
    }


def run_search() -> pd.DataFrame:
    baseline_definition = load_strategy_definition(STRATEGY26)
    runs = {
        "BASELINE_26": _run(baseline_definition, label="BASELINE_26"),
        **{
            label: _run(_variant(label, weights), label=label)
            for label, weights in WARNING_ALLOCATIONS.items()
        },
    }
    rows = []
    for label, result in runs.items():
        for period, (start, end) in PERIODS.items():
            rows.append({
                "Label": label,
                "Period": period,
                "Trades": result["Trades"],
                "Rebalances": result["Rebalances"],
                "DipSignals": result["DipSignals"],
                "DipRedeployments": result["DipRedeployments"],
                "DipRedeploymentDates": result["DipRedeploymentDates"],
                "WarningEntries": result["WarningEntries"],
                "WarningEntryDates": result["WarningEntryDates"],
                "TransactionCosts": result["TransactionCosts"],
                **_metrics(result["history"], start, end),
            })
    report = pd.DataFrame(rows)
    baseline = report.loc[
        report["Label"] == "BASELINE_26",
        ["Period", "CAGR", "MDD", "Sharpe", "Calmar"],
    ].rename(columns={
        "CAGR": "BaselineCAGR",
        "MDD": "BaselineMDD",
        "Sharpe": "BaselineSharpe",
        "Calmar": "BaselineCalmar",
    })
    report = report.merge(baseline, on="Period", how="left")
    report["CAGRGap"] = report["CAGR"] - report["BaselineCAGR"]
    report["MDDImprovement"] = report["MDD"] - report["BaselineMDD"]
    report["SharpeGap"] = report["Sharpe"] - report["BaselineSharpe"]
    report["CalmarGap"] = report["Calmar"] - report["BaselineCalmar"]
    report.to_csv(RESULT_DIR / "strategy26_warning_dip_search.csv", index=False)
    return report


def _rolling_report(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for years in (3, 5):
        for start_year in range(2012, 2026 - years + 1):
            start = f"{start_year}-01-01"
            end = f"{start_year + years - 1}-12-31"
            base_metrics = _metrics(baseline, start, end)
            candidate_metrics = _metrics(candidate, start, end)
            rows.append({
                "Years": years,
                "Window": f"{start_year}_{start_year + years - 1}",
                **candidate_metrics,
                "CAGRGap": candidate_metrics["CAGR"] - base_metrics["CAGR"],
                "MDDImprovement": candidate_metrics["MDD"] - base_metrics["MDD"],
                "SharpeGap": candidate_metrics["Sharpe"] - base_metrics["Sharpe"],
            })
    rolling = pd.DataFrame(rows)
    summary = (
        rolling.groupby("Years", as_index=False)
        .agg(
            Windows=("Window", "count"),
            CAGRWins=("CAGRGap", lambda values: int((values > 1e-12).sum())),
            CAGRLosses=("CAGRGap", lambda values: int((values < -1e-12).sum())),
            AverageCAGRGap=("CAGRGap", "mean"),
            WorstCAGRGap=("CAGRGap", "min"),
            MDDWins=("MDDImprovement", lambda values: int((values > 1e-12).sum())),
            MDDLosses=("MDDImprovement", lambda values: int((values < -1e-12).sum())),
            AverageMDDImprovement=("MDDImprovement", "mean"),
            SharpeWins=("SharpeGap", lambda values: int((values > 1e-12).sum())),
            AverageSharpeGap=("SharpeGap", "mean"),
        )
    )
    return rolling, summary


def run_robustness():
    baseline_definition = load_strategy_definition(STRATEGY26)
    finalist_weights = WARNING_ALLOCATIONS[BALANCED_FINALIST]
    candidate_definition = _variant(BALANCED_FINALIST, finalist_weights)
    baseline = _run(baseline_definition, label="BASELINE_26")
    candidate = _run(candidate_definition, label=BALANCED_FINALIST)

    rolling, rolling_summary = _rolling_report(
        baseline["history"], candidate["history"]
    )

    sensitivity_rows = []
    for label in ("Q50_T30_B20", "Q40_T30_B30", "Q49_T21_B30", "Q70_T00_B30"):
        for threshold in (-0.08, -0.10, -0.12):
            result = _run(
                _variant(label, WARNING_ALLOCATIONS[label], drop_b=threshold),
                label=f"{label}_DROP_{threshold:.0%}",
            )
            sensitivity_rows.append({
                "WarningAllocation": label,
                "FirstDipThreshold": threshold,
                "Trades": result["Trades"],
                "Rebalances": result["Rebalances"],
                "DipRedeployments": result["DipRedeployments"],
                **_metrics(result["history"], START_DATE, END_DATE),
                "RecentCAGR": _metrics(result["history"], *PERIODS["RECENT"])["CAGR"],
                "RecentMDD": _metrics(result["history"], *PERIODS["RECENT"])["MDD"],
            })
    sensitivity = pd.DataFrame(sensitivity_rows)

    warning_only = _run(
        _variant(
            f"{BALANCED_FINALIST}_WARNING_ONLY",
            finalist_weights,
            redeploy_at_dip=False,
        ),
        label=f"{BALANCED_FINALIST}_WARNING_ONLY",
    )
    policy_rows = []
    for result in (baseline, warning_only, candidate):
        policy_rows.append({
            "Policy": result["Label"],
            "Trades": result["Trades"],
            "Rebalances": result["Rebalances"],
            "DipRedeployments": result["DipRedeployments"],
            "DipRedeploymentDates": result["DipRedeploymentDates"],
            **_metrics(result["history"], START_DATE, END_DATE),
            "RecentCAGR": _metrics(result["history"], *PERIODS["RECENT"])["CAGR"],
            "RecentMDD": _metrics(result["history"], *PERIODS["RECENT"])["MDD"],
        })
    policies = pd.DataFrame(policy_rows)

    stress_rows = []
    for cost_multiple in (1.0, 3.0, 5.0):
        for label, definition in (
            ("BASELINE_26", baseline_definition),
            (BALANCED_FINALIST, candidate_definition),
        ):
            result = (
                baseline if cost_multiple == 1.0 and label == "BASELINE_26"
                else candidate if cost_multiple == 1.0
                else _run(definition, label=label, cost_multiple=cost_multiple)
            )
            stress_rows.append({
                "Stress": "COST",
                "Level": cost_multiple,
                "Policy": label,
                **_metrics(result["history"], START_DATE, END_DATE),
            })
    for execution_days in (1, 2, 3):
        for label, definition in (
            ("BASELINE_26", deepcopy(baseline_definition)),
            (
                BALANCED_FINALIST,
                _variant(
                    BALANCED_FINALIST,
                    finalist_weights,
                    execution_days=execution_days,
                ),
            ),
        ):
            definition["execution"]["days"] = execution_days
            result = _run(definition, label=label)
            stress_rows.append({
                "Stress": "EXECUTION_DAYS",
                "Level": execution_days,
                "Policy": label,
                **_metrics(result["history"], START_DATE, END_DATE),
            })
    stress = pd.DataFrame(stress_rows)

    event_rows = []
    for event, (start, end) in EVENT_WINDOWS.items():
        for result in (baseline, candidate):
            event_rows.append({
                "Event": event,
                "Policy": result["Label"],
                **_metrics(result["history"], start, end),
            })
    events = pd.DataFrame(event_rows)

    reports = {
        "strategy26_warning_dip_rolling.csv": rolling,
        "strategy26_warning_dip_rolling_summary.csv": rolling_summary,
        "strategy26_warning_dip_sensitivity.csv": sensitivity,
        "strategy26_warning_dip_policies.csv": policies,
        "strategy26_warning_dip_stress.csv": stress,
        "strategy26_warning_dip_events.csv": events,
    }
    for filename, report in reports.items():
        report.to_csv(RESULT_DIR / filename, index=False)
    return rolling_summary, sensitivity, policies, stress, events


if __name__ == "__main__":
    output = run_search()
    columns = [
        "Period",
        "Label",
        "CAGR",
        "CAGRGap",
        "MDD",
        "MDDImprovement",
        "Sharpe",
        "SharpeGap",
        "Calmar",
        "Trades",
        "Rebalances",
        "DipSignals",
    ]
    print(output[columns].to_string(index=False))
    rolling_summary, sensitivity, policies, stress, events = run_robustness()
    print("\nRolling summary")
    print(rolling_summary.to_string(index=False))
    print("\nSensitivity")
    print(sensitivity.to_string(index=False))
    print("\nPolicy isolation")
    print(policies.to_string(index=False))
    print("\nCost and execution stress")
    print(stress.to_string(index=False))
    print("\nEvent windows")
    print(events.to_string(index=False))
