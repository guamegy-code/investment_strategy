"""Validate HYG/LQD credit proxies only on the BULL-to-CAUTION path.

The baseline also loads HYG and LQD so every profile has the same calendar.
No credit condition is allowed to change BEAR or recovery transitions here.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from attribution import RetirementAllocationAttribution
from backtest import Backtest
from config import PROJECT_ROOT, RESULT_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


SOURCE = (
    PROJECT_ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)

WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_2012_2020": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
}

QQQ_ENTRY_CORE = (
    "state.market_mode == 'BULL' and QQQ.close < QQQ.ema20 and "
    "variables.risk_off_score >= 3"
)
SPY_STRESS = "SPY.close < SPY.ema20 and SPY.roc5 <= -1"


def credit_stress(gap):
    return (
        "HYG.close < HYG.ema20 and "
        f"HYG.roc20 < LQD.roc20 - {gap:g}"
    )


@dataclass(frozen=True)
class CreditEntryProfile:
    name: str
    mode: str = "baseline"
    gap: float | None = None
    target_transition: str = "BULL->CAUTION"


PROFILES = (
    CreditEntryProfile("BASELINE_ALIGNED"),
    CreditEntryProfile("CREDIT_OR_REL20_GAP0", mode="or", gap=0),
    CreditEntryProfile("CREDIT_OR_REL20_GAP1", mode="or", gap=1),
    CreditEntryProfile("CREDIT_OR_REL20_GAP2", mode="or", gap=2),
    CreditEntryProfile("CREDIT_CONFIRM_REL20_GAP1", mode="and", gap=1),
)


def _rules(definition):
    return definition["state"]["market_mode"]["rules"]


def _find_rule(definition, source_state, target_state):
    for rule in _rules(definition):
        condition = str(rule.get("when", ""))
        if (
            rule.get("set") == target_state
            and f"state.market_mode == '{source_state}'" in condition
        ):
            return rule
    raise ValueError(f"{source_state} to {target_state} rule was not found")


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    for ticker in ("HYG", "LQD"):
        if ticker not in observations:
            observations.append(ticker)

    if profile.mode == "baseline":
        return definition

    condition = credit_stress(profile.gap)
    entry = _find_rule(definition, "BULL", "CAUTION")
    if profile.mode == "or":
        entry["when"] = (
            f"{QQQ_ENTRY_CORE} and "
            f"(({SPY_STRESS}) or ({condition}))"
        )
    elif profile.mode == "and":
        existing = " ".join(str(entry["when"]).split())
        entry["when"] = f"({existing}) and ({condition})"
    else:
        raise ValueError(f"Unknown credit-entry mode: {profile.mode}")
    return definition


def _run_profile(profile):
    strategy = DeclarativeStrategy(candidate_definition(profile))
    backtest = Backtest(strategy, tickers=strategy.required_tickers)
    history, trades, rebalances = backtest.run_all()
    attribution = RetirementAllocationAttribution(
        history,
        backtest.data,
        rebalances,
    )
    return {
        "profile": profile,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "transition_events": attribution.transition_events(),
        "transition_quality": attribution.transition_quality(),
    }


def _performance_row(profile, history, window, start, end):
    sample = history.loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    return {
        "Profile": profile.name,
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
    }


def _window_report(results):
    rows = []
    for result in results:
        for window, (start, end) in WINDOWS.items():
            row = _performance_row(
                result["profile"], result["history"], window, start, end
            )
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def _tagged_report(results, name):
    frames = []
    for result in results:
        frame = result[name].copy()
        frame.insert(0, "Profile", result["profile"].name)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _summary_report(results, windows):
    full = windows.loc[windows["Window"] == "FULL"].set_index("Profile")
    recent = windows.loc[
        windows["Window"] == "RECENT_2021_PRESENT"
    ].set_index("Profile")
    rows = []
    for result in results:
        name = result["profile"].name
        rows.append({
            "Profile": name,
            "Mode": result["profile"].mode,
            "RelativeGap": result["profile"].gap,
            "CAGR": full.at[name, "CAGR"],
            "MDD": full.at[name, "MDD"],
            "Sharpe": full.at[name, "Sharpe"],
            "Calmar": full.at[name, "Calmar"],
            "RecentCAGR": recent.at[name, "CAGR"],
            "RecentMDD": recent.at[name, "MDD"],
            "Rebalances": len(result["rebalances"]),
            "Trades": len(result["trades"]),
            "TransactionCosts": full.at[name, "TransactionCosts"],
        })
    return pd.DataFrame(rows)


def _transition_comparison(summary, quality):
    transition = "BULL->CAUTION"
    baseline_summary = summary.set_index("Profile").loc["BASELINE_ALIGNED"]
    baseline_quality = quality.loc[
        (quality["Profile"] == "BASELINE_ALIGNED")
        & (quality["Transition"] == transition)
    ].iloc[0]
    rows = []
    for profile in PROFILES[1:]:
        candidate = quality.loc[
            (quality["Profile"] == profile.name)
            & (quality["Transition"] == transition)
        ]
        if candidate.empty:
            continue
        current = candidate.iloc[0]
        performance = summary.set_index("Profile").loc[profile.name]
        row = {
            "Profile": profile.name,
            "Mode": profile.mode,
            "RelativeGap": profile.gap,
            "Count": current["Count"],
            "BaselineCount": baseline_quality["Count"],
            "CAGRGap": performance["CAGR"] - baseline_summary["CAGR"],
            "MDDImprovement": performance["MDD"] - baseline_summary["MDD"],
            "RecentCAGRGap": (
                performance["RecentCAGR"] - baseline_summary["RecentCAGR"]
            ),
        }
        for metric in (
            "DirectionalSuccessRate20D",
            "DirectionalFalseAlarmRate20D",
            "AvgQQQForwardReturn20D",
            "AvgQQQForwardMaxDrawdown20D",
        ):
            row[metric] = current.get(metric)
            row[f"Baseline{metric}"] = baseline_quality.get(metric)
            row[f"Delta{metric}"] = (
                current.get(metric) - baseline_quality.get(metric)
            )
        rows.append(row)
    return pd.DataFrame(rows)


def run_credit_entry_validation():
    results = [_run_profile(profile) for profile in PROFILES]
    windows = _window_report(results)
    summary = _summary_report(results, windows)
    events = _tagged_report(results, "transition_events")
    quality = _tagged_report(results, "transition_quality")
    comparison = _transition_comparison(summary, quality)
    reports = {
        "credit_entry_gate_summary": summary,
        "credit_entry_gate_windows": windows,
        "credit_entry_gate_transition_events": events,
        "credit_entry_gate_transition_quality": quality,
        "credit_entry_gate_transition_comparison": comparison,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_credit_entry_validation()
    print(validation_reports["credit_entry_gate_summary"].to_string(index=False))
    print("\nTarget-transition comparison")
    print(
        validation_reports[
            "credit_entry_gate_transition_comparison"
        ].to_string(index=False)
    )
