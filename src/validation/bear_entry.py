"""Retrospective validation of alternative BEAR-entry rules.

The production strategy remains untouched and OOS-locked.  These candidates
exist only to test whether an earlier accelerating-downtrend rule improves the
timing of BEAR entries without adding too many false alarms.
"""

from dataclasses import dataclass

import pandas as pd

from attribution import DynamicAllocationAttribution
from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from .extended_data import ASSETS
from performance import Performance
from strategy import DynamicRiskAllocationStrategy


@dataclass(frozen=True)
class BearEntryProfile:
    name: str
    confirmation_days: int
    drawdown_threshold: float
    roc20_threshold: float
    exhaustion_filter: bool = False
    use_baseline_rule: bool = False
    require_falling_ema200: bool = False
    minimum_bear_days: int = 0
    strict_recovery: bool = False
    staged_bear_qqq_weight: float | None = None


PROFILES = (
    BearEntryProfile(
        "BASELINE", 10, -0.08, 0.0, use_baseline_rule=True
    ),
    BearEntryProfile(
        "BASELINE_FAST_CONFIRMATION",
        5,
        -0.08,
        0.0,
        use_baseline_rule=True,
    ),
    BearEntryProfile(
        "BASELINE_MIN_HOLD_20",
        10,
        -0.08,
        0.0,
        use_baseline_rule=True,
        minimum_bear_days=20,
    ),
    BearEntryProfile(
        "BASELINE_STRICT_RECOVERY",
        10,
        -0.08,
        0.0,
        use_baseline_rule=True,
        strict_recovery=True,
    ),
    BearEntryProfile(
        "EARLY_LONG_TREND",
        5,
        -0.08,
        0.0,
        require_falling_ema200=True,
    ),
    BearEntryProfile("EARLY_TREND", 5, -0.06, 0.0),
    BearEntryProfile("EARLY_ACCELERATION", 3, -0.06, -5.0),
    BearEntryProfile(
        "EARLY_ACCELERATION_MIN_HOLD_20",
        3,
        -0.06,
        -5.0,
        minimum_bear_days=20,
    ),
    BearEntryProfile(
        "EARLY_ACCELERATION_STRICT_RECOVERY",
        3,
        -0.06,
        -5.0,
        strict_recovery=True,
    ),
    BearEntryProfile(
        "EARLY_ACCELERATION_STAGED",
        3,
        -0.06,
        -5.0,
        staged_bear_qqq_weight=0.35,
    ),
    BearEntryProfile(
        "EARLY_ACCELERATION_STAGED_STRICT_RECOVERY",
        3,
        -0.06,
        -5.0,
        strict_recovery=True,
        staged_bear_qqq_weight=0.35,
    ),
    BearEntryProfile(
        "EARLY_ACCELERATION_EXHAUSTION_FILTER",
        3,
        -0.06,
        -5.0,
        exhaustion_filter=True,
    ),
)

WINDOWS = {
    "FULL_EXTENDED": ("2000-08-30", None),
    "DOTCOM_UNWIND": ("2000-08-30", "2002-10-09"),
    "PRE_GFC_EXPANSION": ("2002-10-10", "2007-10-08"),
    "GLOBAL_FINANCIAL_CRISIS": ("2007-10-09", "2009-03-09"),
    "POST_GFC_TO_2017": ("2009-03-10", "2017-12-29"),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
    "POST_2010": ("2010-01-01", None),
}


class BearEntryCandidateStrategy(DynamicRiskAllocationStrategy):
    """Parameterized experimental strategy used only by this validation."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.BEAR_CONFIRMATION_DAYS = profile.confirmation_days
        self._bear_state_days = 0
        self._baseline_bear_active = False

    def _is_structural_bear(self, qqq):
        baseline_bear = DynamicRiskAllocationStrategy._is_structural_bear(
            self, qqq
        )
        if baseline_bear:
            self._baseline_bear_active = True
        elif self.state is None or self.state.value != "BEAR":
            self._baseline_bear_active = False
        if self.profile.use_baseline_rule:
            return baseline_bear

        close = qqq.get("Close")
        ema20 = qqq.get("EMA20")
        ema55 = qqq.get("EMA55")
        ema200 = qqq.get("EMA200")
        roc20 = qqq.get("ROC20")
        roc60 = qqq.get("ROC60")
        slope5 = qqq.get("EMA20_SLOPE5")
        slope200 = qqq.get("EMA200_SLOPE20")
        drawdown120 = qqq.get("DRAWDOWN120")
        rsi14 = qqq.get("RSI14")
        if not self._valid(
            close,
            ema20,
            ema55,
            ema200,
            roc20,
            roc60,
            slope5,
            drawdown120,
        ):
            return False

        early_downtrend = (
            self.risk_off_score >= self.BEAR_ENTRY_SCORE
            and close < ema20 < ema55
            and close < ema200
            and roc20 <= self.profile.roc20_threshold
            and roc60 < 0
            and slope5 < 0
            and drawdown120 <= self.profile.drawdown_threshold
            and (
                not self.profile.require_falling_ema200
                or (self._valid(slope200) and slope200 < 0)
            )
        )
        if not early_downtrend:
            return False

        # A new BEAR entry after an 18% decline and RSI <= 30 often occurs near
        # capitulation.  Earlier signals remain unaffected because the filter
        # is checked only while the strategy is not already in BEAR.
        exhausted = (
            self.profile.exhaustion_filter
            and self._valid(rsi14)
            and drawdown120 <= -0.18
            and rsi14 <= 30
        )
        return not exhausted

    def _desired_state(self, qqq):
        desired = super()._desired_state(qqq)
        if self.state is not None and self.state.value == "BEAR":
            if desired.value != "RECOVERY":
                return desired
            if self._bear_state_days < self.profile.minimum_bear_days:
                return self.state
            if self.profile.strict_recovery:
                close = qqq.get("Close")
                ema20 = qqq.get("EMA20")
                roc20 = qqq.get("ROC20")
                strict_recovery = (
                    self.recovery_score >= 4
                    and self._valid(close, ema20, roc20)
                    and close > ema20
                    and roc20 > 0
                )
                if not strict_recovery:
                    return self.state
        return desired

    def _target_for_state(self):
        staged_weight = self.profile.staged_bear_qqq_weight
        if (
            self.state is not None
            and self.state.value == "BEAR"
            and staged_weight is not None
            and not self._baseline_bear_active
        ):
            gold_weight = self.STATE_WEIGHTS[self.state][1]
            target = {
                "QQQ": staged_weight,
                "BND": 0.0,
                "BIL": 0.0,
                "GLD": gold_weight,
            }
            target[self.safe_asset] = 1.0 - staged_weight - gold_weight
            return target
        return super()._target_for_state()

    def evaluate(self, date, market, portfolio):
        previous_state = self.state
        if self.state is not None and self.state.value == "BEAR":
            self._bear_state_days += 1
        else:
            self._bear_state_days = 0

        signal = super().evaluate(date, market, portfolio)
        if (
            (previous_state is None or previous_state.value != "BEAR")
            and self.state is not None
            and self.state.value == "BEAR"
        ):
            self._bear_state_days = 1
        elif (
            previous_state is not None
            and previous_state.value == "BEAR"
            and self.state.value != "BEAR"
        ):
            self._bear_state_days = 0
            self._baseline_bear_active = False
        return signal


def _run_profile(profile):
    strategy = BearEntryCandidateStrategy(profile)
    backtest = Backtest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=ASSETS,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "profile": profile.name,
        "strategy": strategy,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "market_data": backtest.data,
    }


def _performance_row(profile, history, window, start, end):
    sample = history.loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    costs = sample.get("TransactionCosts")
    return {
        "Profile": profile,
        "Window": window,
        "StartDate": sample.index.min(),
        "EndDate": sample.index.max(),
        "Observations": len(sample),
        "TotalReturn": sample["Portfolio"].iloc[-1]
        / sample["Portfolio"].iloc[0]
        - 1.0,
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Volatility": metrics["Volatility"],
        "Sharpe": metrics["Sharpe"],
        "Calmar": metrics["Calmar"],
        "TransactionCosts": (
            costs.iloc[-1] - costs.iloc[0] if costs is not None else 0.0
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


def _transition_reports(results):
    event_frames = []
    quality_frames = []
    for result in results:
        attribution = DynamicAllocationAttribution(
            result["history"],
            result["market_data"],
            result["rebalances"],
        )
        events = attribution.transition_events()
        events.insert(0, "Profile", result["profile"])
        event_frames.append(events)
        quality = attribution.transition_quality()
        quality.insert(0, "Profile", result["profile"])
        quality_frames.append(quality)
    return (
        pd.concat(event_frames, ignore_index=True),
        pd.concat(quality_frames, ignore_index=True),
    )


def _candidate_summary(windows, events):
    full = windows.loc[windows["Window"] == "FULL_EXTENDED"].set_index(
        "Profile"
    )
    rows = []
    for profile in full.index:
        bear_entries = events.loc[
            (events["Profile"] == profile) & (events["NewState"] == "BEAR")
        ]
        rows.append({
            "Profile": profile,
            "CAGR": full.at[profile, "CAGR"],
            "MDD": full.at[profile, "MDD"],
            "Sharpe": full.at[profile, "Sharpe"],
            "TransactionCosts": full.at[profile, "TransactionCosts"],
            "BearEntries": len(bear_entries),
            "AvgBearStateDurationDays": bear_entries[
                "StateDurationDays"
            ].mean(),
            "ShortLivedBearRate5D": bear_entries[
                "ShortLivedState5D"
            ].mean(),
            "BearDirectionalSuccessRate20D": bear_entries[
                "DirectionalSuccess20D"
            ].mean(),
            "BearReboundRate20D": bear_entries["BearRebound20D"].mean(),
            "BearNoMeaningfulDownsideRate20D": bear_entries[
                "NoMeaningfulDownside20D"
            ].mean(),
            "AvgDaysFromPrior120DHigh": bear_entries[
                "DaysFromPrior120DHigh"
            ].mean(),
            "AvgDrawdownAtBearEntry": bear_entries[
                "DrawdownFromPrior120DHigh"
            ].mean(),
            "AvgForwardMaxDrawdown20D": bear_entries[
                "QQQForwardMaxDrawdown20D"
            ].mean(),
        })
    return pd.DataFrame(rows)


def _relative_to_baseline(windows):
    baseline = windows.loc[windows["Profile"] == "BASELINE"].set_index(
        "Window"
    )
    rows = []
    for _, candidate in windows.iterrows():
        reference = baseline.loc[candidate["Window"]]
        rows.append({
            "Profile": candidate["Profile"],
            "Window": candidate["Window"],
            "CAGRGapVsBaseline": candidate["CAGR"] - reference["CAGR"],
            "MDDImprovementVsBaseline": candidate["MDD"] - reference["MDD"],
            "SharpeGapVsBaseline": candidate["Sharpe"] - reference["Sharpe"],
            "TotalReturnGapVsBaseline": (
                candidate["TotalReturn"] - reference["TotalReturn"]
            ),
        })
    return pd.DataFrame(rows)


def run_bear_entry_validation():
    results = [_run_profile(profile) for profile in PROFILES]
    windows = _window_report(results)
    events, transition_quality = _transition_reports(results)
    summary = _candidate_summary(windows, events)
    relative = _relative_to_baseline(windows)

    reports = {
        "bear_entry_candidate_summary": summary,
        "bear_entry_candidate_windows": windows,
        "bear_entry_candidate_relative": relative,
        "bear_entry_candidate_events": events,
        "bear_entry_candidate_transition_quality": transition_quality,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_bear_entry_validation()
    print(validation_reports["bear_entry_candidate_summary"].to_string(index=False))
    relative = validation_reports["bear_entry_candidate_relative"]
    print("\nCrisis-window gaps versus baseline")
    print(
        relative.loc[
            relative["Window"].isin(
                [
                    "DOTCOM_UNWIND",
                    "GLOBAL_FINANCIAL_CRISIS",
                    "COVID_CRASH",
                    "2022_RATE_SHOCK",
                    "POST_2010",
                ]
            )
        ].to_string(index=False)
    )
