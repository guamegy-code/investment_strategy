"""Backtest only precision-qualified causal warning rules."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from performance import Performance
from strategy import BaseStrategy, STATIC_RETIREMENT_7030
from .enhanced_probability_features import EnhancedProbabilityBacktest, SIGNAL_TICKERS
from .precision_thresholds import (
    MINIMUM_THRESHOLD_HISTORY,
    PROFILE,
    expanding_quantile,
)


@dataclass(frozen=True)
class PrecisionAllocationProfile:
    name: str
    staged: bool
    late_guard: bool = False


PROFILES = (
    PrecisionAllocationProfile("Q90_FLAT_60", staged=False),
    PrecisionAllocationProfile("Q90_Q95_STAGED", staged=True),
    PrecisionAllocationProfile(
        "Q90_Q95_STAGED_EXPLORATORY_GUARD", staged=True, late_guard=True
    ),
)


class PrecisionThresholdBacktest(EnhancedProbabilityBacktest):
    """Attach causal exit, warning, and severe-warning percentiles."""

    def load_data(self):
        data = super().load_data()
        decision_mask = ~data.index.to_period("M").duplicated()
        active = data.loc[
            decision_mask & (data["TailModelSamples"] >= 60),
            "ProbabilityLoss21",
        ]
        percentile = pd.DataFrame(index=data.index)
        for quantile in (0.75, 0.90, 0.95):
            monthly = expanding_quantile(
                active, quantile, MINIMUM_THRESHOLD_HISTORY
            )
            percentile.loc[monthly.index, f"ProbabilityQ{int(quantile * 100)}"] = monthly
        percentile = percentile.ffill()
        return data.join(percentile)

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"].update({
            "ProbabilityQ75": row.get("ProbabilityQ75"),
            "ProbabilityQ90": row.get("ProbabilityQ90"),
            "ProbabilityQ95": row.get("ProbabilityQ95"),
            "DRAWDOWN120": row.get("QQQ_DRAWDOWN120"),
            "REBOUND20": row.get("QQQ_REBOUND20"),
        })
        return market


class PrecisionWarningAllocationStrategy(BaseStrategy):
    """Use Q90/Q95 entries, Q75 hysteresis, and gradual recovery."""

    def __init__(self, profile):
        self.profile = profile
        self.target = None
        self.risk_target = 0.70
        self.last_signal_month = None
        self.decision_log = []

    @property
    def required_tickers(self):
        return ("QQQ", "BND", "BIL")

    @staticmethod
    def _valid(*values):
        return all(value is not None and np.isfinite(value) for value in values)

    @staticmethod
    def _is_late_signal(qqq):
        drawdown = qqq.get("DRAWDOWN120")
        rebound = qqq.get("REBOUND20")
        if not PrecisionWarningAllocationStrategy._valid(drawdown, rebound):
            return False
        return (
            (drawdown <= -0.15 and rebound >= 0.075)
            or rebound >= 0.15
        )

    def _next_risk_target(self, qqq):
        probability = qqq.get("ProbabilityLoss21")
        q75 = qqq.get("ProbabilityQ75")
        q90 = qqq.get("ProbabilityQ90")
        q95 = qqq.get("ProbabilityQ95")
        if not self._valid(probability, q75, q90, q95):
            return 0.70, "THRESHOLD_WARMUP"

        late_signal = self.profile.late_guard and self._is_late_signal(qqq)
        if self.risk_target >= 0.70:
            if probability >= q95 and not late_signal:
                return (0.50 if self.profile.staged else 0.60), "Q95_ENTRY"
            if probability >= q90 and not late_signal:
                return 0.60, "Q90_ENTRY"
            return 0.70, "NORMAL"

        if self.profile.staged and probability >= q95 and not late_signal:
            return 0.50, "Q95_ESCALATION"
        if probability < q75:
            return min(0.70, self.risk_target + 0.10), "Q75_RECOVERY_STEP"
        return self.risk_target, "DEFENSE_HOLD"

    @staticmethod
    def _target_for_risk(risk_weight):
        safe_weight = 1.0 - risk_weight
        return {
            "QQQ": risk_weight,
            "BND": safe_weight / 2.0,
            "BIL": safe_weight / 2.0,
        }

    def _signal(self, rebalance, reason):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": 3,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        if self.target is not None and month == self.last_signal_month:
            return self._signal(False, None)

        self.last_signal_month = month
        previous_risk = self.risk_target
        self.risk_target, reason = self._next_risk_target(market["QQQ"])
        self.target = self._target_for_risk(self.risk_target)
        self.decision_log.append({
            "Date": date,
            "Reason": reason,
            "PreviousRiskTarget": previous_risk,
            "RiskTarget": self.risk_target,
            "ProbabilityLoss21": market["QQQ"].get("ProbabilityLoss21"),
            "ProbabilityQ75": market["QQQ"].get("ProbabilityQ75"),
            "ProbabilityQ90": market["QQQ"].get("ProbabilityQ90"),
            "ProbabilityQ95": market["QQQ"].get("ProbabilityQ95"),
        })
        if previous_risk != self.risk_target or len(self.decision_log) == 1:
            return self._signal(True, reason)

        prices = {ticker: market[ticker]["Close"] for ticker in self.target}
        current = portfolio.weights(prices)
        outside_band = any(
            abs(current.get(ticker, 0.0) - weight) >= 0.05
            for ticker, weight in self.target.items()
        )
        return self._signal(outside_band, "MONTHLY_5PCT_BAND" if outside_band else None)


def _run(label, strategy):
    backtest = PrecisionThresholdBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=SIGNAL_TICKERS,
        feature_profile=PROFILE,
    )
    history, trades, rebalances = backtest.run_all()
    return label, strategy, history, trades, rebalances, backtest.data


def _active_start(results):
    starts = []
    for result in results:
        data = result[5]
        active = data.index[data["ProbabilityQ90"].notna()]
        starts.append(active.min())
    return max(starts)


def _reports(results):
    active_start = _active_start(results)
    windows = {
        "MODEL_ACTIVE": (active_start, None),
        "PRE_2018": (active_start, "2017-12-31"),
        "RECENT_2018_PRESENT": ("2018-01-01", None),
        "COVID_CRASH": ("2020-02-19", "2020-03-23"),
        "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
    }
    rows = []
    for label, _, history, _, _, _ in results:
        for window, (start, end) in windows.items():
            sample = history.loc[start:end]
            if len(sample) < 2:
                continue
            metrics = Performance(sample).summary()
            rows.append({
                "Strategy": label,
                "Window": window,
                "StartDate": sample.index.min(),
                "EndDate": sample.index.max(),
                "CAGR": metrics["CAGR"],
                "MDD": metrics["MDD"],
                "Volatility": metrics["Volatility"],
                "Sharpe": metrics["Sharpe"],
                "Sortino": metrics["Sortino"],
                "Calmar": metrics["Calmar"],
            })
    windows_report = pd.DataFrame(rows)
    summary = windows_report.loc[
        windows_report["Window"] == "MODEL_ACTIVE"
    ].drop(columns="Window").reset_index(drop=True)
    decisions = pd.concat(
        (
            pd.DataFrame(strategy.decision_log).assign(Strategy=label)
            for label, strategy, _, _, _, _ in results
            if hasattr(strategy, "decision_log")
        ),
        ignore_index=True,
    )
    return summary, windows_report, decisions


def run_precision_portfolio_validation():
    results = [_run("STATIC_7030", STATIC_RETIREMENT_7030())]
    results.extend(
        _run(profile.name, PrecisionWarningAllocationStrategy(profile))
        for profile in PROFILES
    )
    summary, windows, decisions = _reports(results)
    summary.to_csv(RESULT_DIR / "precision_portfolio_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "precision_portfolio_windows.csv", index=False)
    decisions.to_csv(RESULT_DIR / "precision_portfolio_decisions.csv", index=False)
    return {"summary": summary, "windows": windows, "decisions": decisions}


if __name__ == "__main__":
    reports = run_precision_portfolio_validation()
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nAllocation changes")
    changes = reports["decisions"].loc[
        reports["decisions"]["PreviousRiskTarget"]
        != reports["decisions"]["RiskTarget"]
    ]
    print(changes.to_string(index=False))
