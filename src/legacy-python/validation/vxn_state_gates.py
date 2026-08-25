"""Validate state-specific VXN gates without changing the production strategy.

Each candidate changes exactly one transition rule.  Combinations are deferred
until an individual gate improves transition quality out of sample.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from attribution import RetirementAllocationAttribution
from backtest import Backtest
from config import PROJECT_ROOT, RESULT_DIR
from indicators import Indicator
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


SOURCE = (
    PROJECT_ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)
SIGNAL_DIR = PROJECT_ROOT / "data_probability_signals"
VXN_PATH = SIGNAL_DIR / "VXN.csv"
VXN_SYMBOL = "^VXN"

WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_2012_2020": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
}


@dataclass(frozen=True)
class VxnGateProfile:
    name: str
    target_transition: str | None = None
    entry_condition: str | None = None
    bear_recovery_condition: str | None = None
    bull_reentry_condition: str | None = None


PROFILES = (
    VxnGateProfile("BASELINE"),
    VxnGateProfile(
        "VXN_ENTRY_RANK70",
        target_transition="BULL->CAUTION",
        entry_condition="VXN.pct_rank252 >= 0.70",
    ),
    VxnGateProfile(
        "VXN_ENTRY_ROC5_GE0",
        target_transition="BULL->CAUTION",
        entry_condition="VXN.roc5 >= 0",
    ),
    VxnGateProfile(
        "VXN_ENTRY_ROC5_GE5",
        target_transition="BULL->CAUTION",
        entry_condition="VXN.roc5 >= 5",
    ),
    VxnGateProfile(
        "VXN_ENTRY_ROC5_GE10",
        target_transition="BULL->CAUTION",
        entry_condition="VXN.roc5 >= 10",
    ),
    VxnGateProfile(
        "VXN_BEAR_RECOVERY_FALLING",
        target_transition="BEAR->RECOVERY",
        bear_recovery_condition="VXN.roc5 < 0",
    ),
    VxnGateProfile(
        "VXN_BEAR_RECOVERY_BELOW_EMA20",
        target_transition="BEAR->RECOVERY",
        bear_recovery_condition="VXN.close < VXN.ema20",
    ),
    VxnGateProfile(
        "VXN_RECOVERY_BULL_BELOW_EMA20",
        target_transition="RECOVERY->BULL",
        bull_reentry_condition="VXN.close < VXN.ema20",
    ),
)


def add_vxn_features(frame):
    """Add lag-safe VXN features used by the declarative candidates."""
    result = Indicator.add_indicators(frame.copy())
    close = result["Close"]
    result["ROC5_LAG1"] = result["ROC5"].shift(1)
    result["PCT_RANK252"] = close.rolling(252).apply(
        lambda values: float(np.mean(values <= values[-1])),
        raw=True,
    )
    return result


def download_vxn(refresh=False, output_path=VXN_PATH):
    """Download VXN under a DSL-safe alias and cache it in the workspace."""
    output_path = Path(output_path)
    required = {"PCT_RANK252", "ROC5", "ROC5_LAG1", "EMA20"}
    if output_path.exists() and not refresh:
        frame = pd.read_csv(output_path, index_col="Date", parse_dates=True)
        if required.issubset(frame.columns):
            return frame
        frame = add_vxn_features(frame)
    else:
        yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))
        frame = yf.download(
            VXN_SYMBOL,
            start="2001-01-01",
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if frame.empty:
            raise ValueError(f"No VXN data downloaded for {VXN_SYMBOL}")
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "Date"
        for column in ("Open", "High", "Low"):
            if column not in frame:
                frame[column] = frame["Close"]
        if "Volume" not in frame:
            frame["Volume"] = 0.0
        frame = add_vxn_features(frame)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path)
    return frame


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


def _and_condition(rule, condition):
    existing = " ".join(str(rule["when"]).split())
    rule["when"] = f"({existing}) and ({condition})"


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    if "VXN" not in observations:
        observations.append("VXN")
    if profile == PROFILES[0]:
        return definition
    if profile.entry_condition:
        _and_condition(
            _find_rule(definition, "BULL", "CAUTION"),
            profile.entry_condition,
        )
    if profile.bear_recovery_condition:
        _and_condition(
            _find_rule(definition, "BEAR", "RECOVERY"),
            profile.bear_recovery_condition,
        )
    if profile.bull_reentry_condition:
        _and_condition(
            _find_rule(definition, "RECOVERY", "BULL"),
            profile.bull_reentry_condition,
        )
    return definition


class VxnStateBacktest(Backtest):
    def load_one(self, ticker):
        if ticker != "VXN":
            return super().load_one(ticker)
        frame = pd.read_csv(VXN_PATH, index_col="Date", parse_dates=True)
        required = {"PCT_RANK252", "ROC5", "ROC5_LAG1", "EMA20"}
        if not required.issubset(frame.columns):
            frame = add_vxn_features(frame)
        return frame


def _run_profile(profile):
    definition = candidate_definition(profile)
    strategy = DeclarativeStrategy(definition)
    backtest = VxnStateBacktest(
        strategy,
        tickers=strategy.required_tickers,
    )
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
        "market_data": backtest.data,
        "transition_events": attribution.transition_events(),
        "transition_quality": attribution.transition_quality(),
        "state_quality": attribution.state_market_quality(),
    }


def _performance_row(profile, history, window, start, end):
    sample = history.loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    costs = sample["TransactionCosts"]
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
        "TransactionCosts": costs.iloc[-1] - costs.iloc[0],
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
            "TargetTransition": result["profile"].target_transition,
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
    baseline_summary = summary.set_index("Profile").loc["BASELINE"]
    baseline_quality = quality.loc[quality["Profile"] == "BASELINE"].set_index(
        "Transition"
    )
    rows = []
    for profile in PROFILES[1:]:
        candidate = quality.loc[
            (quality["Profile"] == profile.name)
            & (quality["Transition"] == profile.target_transition)
        ]
        if candidate.empty or profile.target_transition not in baseline_quality.index:
            continue
        current = candidate.iloc[0]
        reference = baseline_quality.loc[profile.target_transition]
        performance = summary.set_index("Profile").loc[profile.name]
        row = {
            "Profile": profile.name,
            "Transition": profile.target_transition,
            "Count": current["Count"],
            "BaselineCount": reference["Count"],
            "CAGRGap": performance["CAGR"] - baseline_summary["CAGR"],
            "MDDImprovement": performance["MDD"] - baseline_summary["MDD"],
            "RecentCAGRGap": (
                performance["RecentCAGR"] - baseline_summary["RecentCAGR"]
            ),
        }
        for metric in (
            "DirectionalSuccessRate20D",
            "DirectionalFalseAlarmRate20D",
            "RecoveryRelapseRate20D",
            "RecoveryRelapseRate60D",
            "AvgQQQForwardReturn20D",
            "AvgQQQForwardMaxDrawdown20D",
        ):
            row[metric] = current.get(metric)
            row[f"Baseline{metric}"] = reference.get(metric)
            row[f"Delta{metric}"] = current.get(metric) - reference.get(metric)
        rows.append(row)
    return pd.DataFrame(rows)


def run_vxn_state_gate_validation(refresh_data=False):
    vxn = download_vxn(refresh=refresh_data)
    results = [_run_profile(profile) for profile in PROFILES]
    windows = _window_report(results)
    summary = _summary_report(results, windows)
    events = _tagged_report(results, "transition_events")
    quality = _tagged_report(results, "transition_quality")
    state_quality = _tagged_report(results, "state_quality")
    comparison = _transition_comparison(summary, quality)
    manifest = pd.DataFrame([{
        "Label": "VXN",
        "Symbol": VXN_SYMBOL,
        "StartDate": vxn.index.min(),
        "EndDate": vxn.index.max(),
        "Observations": len(vxn),
        "Features": "PCT_RANK252,ROC5,EMA20",
    }])
    reports = {
        "vxn_signal_manifest": manifest,
        "vxn_state_gate_summary": summary,
        "vxn_state_gate_windows": windows,
        "vxn_state_gate_transition_events": events,
        "vxn_state_gate_transition_quality": quality,
        "vxn_state_gate_state_quality": state_quality,
        "vxn_state_gate_transition_comparison": comparison,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_vxn_state_gate_validation()
    print(validation_reports["vxn_signal_manifest"].to_string(index=False))
    print("\nSummary")
    print(validation_reports["vxn_state_gate_summary"].to_string(index=False))
    print("\nTarget-transition comparison")
    print(
        validation_reports["vxn_state_gate_transition_comparison"].to_string(
            index=False
        )
    )
