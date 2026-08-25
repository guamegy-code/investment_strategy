"""Test official CNN Fear & Greed only on BEAR -> RECOVERY.

Candidates preserve the production recovery path and add an earlier path only
when QQQ independently supplies at least two recovery observations.  All
profiles load the same post-2021 CNN calendar so their results are comparable.
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
from .cnn_fear_greed_reconstruction import (
    OFFICIAL_PATH,
    OFFICIAL_VERSION,
    fetch_official,
)
from .point_in_time_signals import load_point_in_time_signal


SOURCE = (
    PROJECT_ROOT
    / "strategies"
    / "14_profit_band_tdf2050_gate_spy_tdf100_no_bnd.yaml"
)
OBSERVATION = "CNNFG"
# Five official observations are needed by the most data-hungry candidate.
# Use the same ready date for every profile so performance gaps are paired.
START_DATE = "2021-02-08"
WINDOWS = {
    "FULL_OFFICIAL": (START_DATE, None),
    "2021": (START_DATE, "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
    "2025_PRESENT": ("2025-01-01", None),
}


@dataclass(frozen=True)
class CnnRecoveryProfile:
    name: str
    family: str = "BASELINE"
    threshold: int | None = None
    early_condition: str | None = None


def _profile(family, threshold):
    prefix = f"CNN_{family}_{threshold}_SCORE2"
    if family == "LEVEL":
        condition = f"CNNFG.close <= {threshold}"
    elif family == "TURN":
        condition = (
            f"CNNFG.min10 <= {threshold} and CNNFG.delta5 >= 5"
        )
    elif family == "EXIT":
        condition = (
            f"CNNFG.min10 <= {threshold} and CNNFG.close > {threshold}"
        )
    else:
        raise ValueError(f"Unknown CNN profile family: {family}")
    return CnnRecoveryProfile(prefix, family, threshold, condition)


PROFILES = (
    CnnRecoveryProfile("BASELINE"),
    *(_profile(family, threshold) for family in ("LEVEL", "TURN", "EXIT")
      for threshold in (15, 20, 25)),
)


def add_cnn_features(signal):
    """Convert the point-in-time signal to a DSL observation frame."""
    values = signal.set_index("AvailableDate")["Value"].sort_index()
    values = values[~values.index.duplicated(keep="last")]
    frame = pd.DataFrame(index=values.index)
    frame["Close"] = values
    frame["MIN10"] = values.rolling(10, min_periods=1).min()
    frame["DELTA5"] = values - values.shift(5)
    return frame


def _find_recovery_rule(definition):
    for rule in definition["state"]["market_mode"]["rules"]:
        condition = " ".join(str(rule.get("when", "")).split())
        if (
            rule.get("set") == "RECOVERY"
            and "state.market_mode == 'BEAR'" in condition
        ):
            return rule
    raise ValueError("BEAR to RECOVERY rule was not found")


def candidate_definition(profile, source=SOURCE):
    definition = deepcopy(load_strategy_definition(source))
    observations = definition["assets"].setdefault("observations", [])
    if OBSERVATION not in observations:
        observations.append(OBSERVATION)
    if profile.early_condition:
        rule = _find_recovery_rule(definition)
        existing = " ".join(str(rule["when"]).split())
        early = (
            "state.market_mode == 'BEAR' "
            "and variables.recovery_score >= 2 "
            f"and ({profile.early_condition})"
        )
        rule["when"] = f"({existing}) or ({early})"
    return definition


class CnnOfficialBacktest(Backtest):
    cnn_frame = None

    def load_one(self, ticker):
        if ticker == OBSERVATION:
            if self.cnn_frame is None:
                signal = load_point_in_time_signal(
                    OFFICIAL_PATH,
                    minimum=0,
                    maximum=100,
                    expected_methodology_version=OFFICIAL_VERSION,
                )
                self.__class__.cnn_frame = add_cnn_features(signal)
            return self.cnn_frame.copy()
        return super().load_one(ticker)


def _run_profile(profile):
    definition = candidate_definition(profile)
    strategy = DeclarativeStrategy(definition)
    backtest = CnnOfficialBacktest(
        strategy,
        tickers=strategy.required_tickers,
        start_date=START_DATE,
    )
    history, trades, rebalances = backtest.run_all()
    attribution = RetirementAllocationAttribution(
        history, backtest.data, rebalances
    )
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
                "Family": result["profile"].family,
                "Threshold": result["profile"].threshold,
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
    full = windows.loc[windows["Window"] == "FULL_OFFICIAL"].copy()
    reference = full.loc[full["Profile"] == "BASELINE"].iloc[0]
    full["CAGRGap"] = full["CAGR"] - reference["CAGR"]
    full["MDDImprovement"] = full["MDD"] - reference["MDD"]
    full["SharpeGap"] = full["Sharpe"] - reference["Sharpe"]
    full["CalmarGap"] = full["Calmar"] - reference["Calmar"]
    full["ChangedBaseline"] = (
        full[["CAGRGap", "MDDImprovement", "SharpeGap", "CalmarGap"]]
        .abs().max(axis=1) > 1e-12
    )
    full["OverallPass"] = (
        full["ChangedBaseline"]
        & (full["CAGRGap"] > 0)
        & (full["MDDImprovement"] >= 0)
        & (full["CalmarGap"] >= 0)
    )
    return windows, full


def _tagged(results, key):
    frames = []
    for result in results:
        frame = result[key].copy()
        frame.insert(0, "Profile", result["profile"].name)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def fear_episodes(signal, max_gap_days=10):
    """Collapse official <=20 observations into independent stress episodes."""
    series = signal.set_index("ObservationDate")["Value"].sort_index()
    extreme = series[series <= 20]
    if extreme.empty:
        return pd.DataFrame(columns=["Episode", "StartDate", "EndDate", "Min"])
    groups = extreme.index.to_series().diff().dt.days.gt(max_gap_days).cumsum()
    rows = []
    for episode, observations in extreme.groupby(groups):
        rows.append({
            "Episode": int(episode) + 1,
            "StartDate": observations.index.min(),
            "EndDate": observations.index.max(),
            "Min": observations.min(),
            "ExtremeDays": len(observations),
        })
    return pd.DataFrame(rows)


def _episode_transition_report(results, episodes):
    rows = []
    for _, episode in episodes.iterrows():
        search_end = episode["EndDate"] + pd.Timedelta(days=40)
        for result in results:
            events = result["events"]
            recovery = events.loc[
                (events["Transition"] == "BEAR->RECOVERY")
                & (pd.to_datetime(events["Date"]) >= episode["StartDate"])
                & (pd.to_datetime(events["Date"]) <= search_end)
            ]
            transition_date = (
                pd.to_datetime(recovery.iloc[0]["Date"])
                if not recovery.empty else pd.NaT
            )
            rows.append({
                **episode.to_dict(),
                "Profile": result["profile"].name,
                "RecoveryDate": transition_date,
                "DaysFromEpisodeEnd": (
                    (transition_date - episode["EndDate"]).days
                    if pd.notna(transition_date) else pd.NA
                ),
            })
    return pd.DataFrame(rows)


def run_cnn_official_recovery_validation(refresh_official=False):
    if refresh_official or not OFFICIAL_PATH.exists():
        signal = fetch_official()
    else:
        signal = load_point_in_time_signal(
            OFFICIAL_PATH,
            minimum=0,
            maximum=100,
            expected_methodology_version=OFFICIAL_VERSION,
        )
    CnnOfficialBacktest.cnn_frame = add_cnn_features(signal)
    results = [_run_profile(profile) for profile in PROFILES]
    windows, summary = _performance_reports(results)
    events = _tagged(results, "events")
    quality = _tagged(results, "quality")
    episodes = fear_episodes(signal)
    episode_transitions = _episode_transition_report(results, episodes)
    reports = {
        "cnn_official_recovery_summary": summary,
        "cnn_official_recovery_windows": windows,
        "cnn_official_recovery_transition_events": events,
        "cnn_official_recovery_transition_quality": quality,
        "cnn_official_fear_episodes": episodes,
        "cnn_official_episode_transitions": episode_transitions,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    output = run_cnn_official_recovery_validation()
    print(output["cnn_official_recovery_summary"].to_string(index=False))
    print("\nFear episodes")
    print(output["cnn_official_fear_episodes"].to_string(index=False))
