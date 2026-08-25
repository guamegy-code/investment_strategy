"""Validate a purpose-built, long-history extreme-fear recovery signal."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import pandas as pd

from attribution import RetirementAllocationAttribution
from backtest import Backtest
from config import DATA_DIR, PROJECT_ROOT, RESULT_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from .cnn_official_recovery_gates import SOURCE
from .cnn_fear_greed_reconstruction import causal_percentile


OBSERVATION = "FEAR"
VXN_PATH = PROJECT_ROOT / "data_probability_signals" / "VXN.csv"
WINDOWS = {
    "FULL": (None, None),
    "DEVELOPMENT_PRE2021": (None, "2020-12-31"),
    "RECENT_2021_PRESENT": ("2021-01-01", None),
    "2011_2015": (None, "2015-12-31"),
    "2016_2020": ("2016-01-01", "2020-12-31"),
    "2021_PRESENT": ("2021-01-01", None),
}


@dataclass(frozen=True)
class FearRecoveryProfile:
    name: str
    stress_score: int | None = None
    memory: int | None = None


PROFILES = (
    FearRecoveryProfile("BASELINE"),
    FearRecoveryProfile("FEAR_SCORE2_MEMORY10", 2, 10),
    FearRecoveryProfile("FEAR_SCORE2_MEMORY20", 2, 20),
    FearRecoveryProfile("FEAR_SCORE2_MEMORY30", 2, 30),
    FearRecoveryProfile("FEAR_SCORE3_MEMORY20", 3, 20),
)


def _close(path):
    frame = pd.read_csv(path, index_col="Date", parse_dates=True)
    frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
    return frame["Close"].sort_index()


def build_extreme_fear_signal():
    """Build three causal stress dimensions on their shared trading dates."""
    prices = pd.concat({
        "QQQ": _close(DATA_DIR / "QQQ.csv"),
        "VIX": _close(DATA_DIR / "VIX.csv"),
        "VXN": _close(VXN_PATH),
        "HYG": _close(DATA_DIR / "HYG.csv"),
        "LQD": _close(DATA_DIR / "LQD.csv"),
    }, axis=1, join="inner").sort_index()
    vix_rank = causal_percentile(prices["VIX"])
    vxn_rank = causal_percentile(prices["VXN"])
    credit_raw = prices["HYG"].pct_change(20) - prices["LQD"].pct_change(20)
    credit_weakness_rank = 1.0 - causal_percentile(credit_raw)
    qqq_drawdown120 = prices["QQQ"] / prices["QQQ"].rolling(120).max() - 1.0
    signal = pd.DataFrame(index=prices.index)
    signal["VOL_RANK"] = pd.concat([vix_rank, vxn_rank], axis=1).max(axis=1)
    signal["CREDIT_WEAKNESS_RANK"] = credit_weakness_rank
    signal["QQQ_DRAWDOWN120"] = qqq_drawdown120
    signal["STRESS_SCORE"] = (
        (signal["VOL_RANK"] >= 0.90).astype(int)
        + (signal["CREDIT_WEAKNESS_RANK"] >= 0.90).astype(int)
        + (signal["QQQ_DRAWDOWN120"] <= -0.10).astype(int)
    )
    for memory in (10, 20, 30):
        signal[f"RECENT_SCORE{memory}"] = signal["STRESS_SCORE"].rolling(
            memory, min_periods=1
        ).max()
    signal["Close"] = signal["STRESS_SCORE"]
    return signal.dropna()


def _find_recovery_rule(definition):
    for rule in definition["state"]["market_mode"]["rules"]:
        condition = " ".join(str(rule.get("when", "")).split())
        if rule.get("set") == "RECOVERY" and "state.market_mode == 'BEAR'" in condition:
            return rule
    raise ValueError("BEAR to RECOVERY rule was not found")


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    if OBSERVATION not in observations:
        observations.append(OBSERVATION)
    if profile.stress_score is not None:
        rule = _find_recovery_rule(definition)
        existing = " ".join(str(rule["when"]).split())
        early = (
            "state.market_mode == 'BEAR' "
            "and variables.recovery_score >= 2 "
            f"and FEAR.recent_score{profile.memory} >= {profile.stress_score}"
        )
        rule["when"] = f"({existing}) or ({early})"
    return definition


class ExtremeFearBacktest(Backtest):
    fear_frame = None

    def load_one(self, ticker):
        if ticker == OBSERVATION:
            if self.fear_frame is None:
                self.__class__.fear_frame = build_extreme_fear_signal()
            return self.fear_frame.copy()
        return super().load_one(ticker)


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


def _reports(results):
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


def run_extreme_fear_recovery_validation():
    signal = build_extreme_fear_signal()
    ExtremeFearBacktest.fear_frame = signal
    results = [_run(profile) for profile in PROFILES]
    windows, summary = _reports(results)
    reports = {
        "extreme_fear_recovery_manifest": pd.DataFrame([{
            "StartDate": signal.index.min(),
            "EndDate": signal.index.max(),
            "Observations": len(signal),
            "Dimensions": "volatility,credit,QQQ_drawdown",
        }]),
        "extreme_fear_recovery_summary": summary,
        "extreme_fear_recovery_windows": windows,
        "extreme_fear_recovery_transition_events": _tagged(results, "events"),
        "extreme_fear_recovery_transition_quality": _tagged(results, "quality"),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_extreme_fear_recovery_validation()
    print(output["extreme_fear_recovery_manifest"].to_string(index=False))
    print(output["extreme_fear_recovery_summary"].to_string(index=False))
