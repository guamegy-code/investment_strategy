"""Validate state-free absolute and relative momentum allocation."""

from dataclasses import dataclass
from math import exp

import pandas as pd

from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from .continuous_allocation import (
    EXECUTION_DAYS,
    REBALANCE_BAND,
    WINDOWS,
    _allocation_report,
    _annual_report,
    _performance_row,
)
from .extended_data import ASSETS
from strategy import (
    BaseStrategy,
    DownsideTrendOverlayStrategy,
    DynamicRiskAllocationStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


@dataclass(frozen=True)
class MomentumProfile:
    name: str
    mode: str
    minimum_qqq_weight: float = 0.0


PROFILES = (
    MomentumProfile("ABSOLUTE_MOMENTUM_12M", "ABSOLUTE_12M"),
    MomentumProfile("ABSOLUTE_MOMENTUM_COMPOSITE", "ABSOLUTE_COMPOSITE"),
    MomentumProfile(
        "ABSOLUTE_COMPOSITE_CORE_20", "ABSOLUTE_COMPOSITE", 0.20
    ),
    MomentumProfile(
        "ABSOLUTE_COMPOSITE_CORE_35", "ABSOLUTE_COMPOSITE", 0.35
    ),
    MomentumProfile("RANK_WEIGHTED_MOMENTUM", "RANK_WEIGHTED"),
)


class MomentumRankingStrategy(BaseStrategy):
    """Allocate by absolute QQQ momentum or cross-asset momentum ranking."""

    MAX_QQQ_WEIGHT = 0.70
    MAX_GLD_WEIGHT = 0.20
    SOFTMAX_TEMPERATURE = 5.0

    def __init__(self, profile):
        self.profile = profile
        self.target = None
        self.last_signal_month = None
        self.asset_scores = {}

    @staticmethod
    def _valid(*values):
        return all(value is not None and value == value for value in values)

    def _momentum_score(self, asset):
        roc60 = asset.get("ROC60")
        roc120 = asset.get("ROC120")
        roc252 = asset.get("ROC252")
        if not self._valid(roc60, roc120, roc252):
            return 0.0
        if self.profile.mode == "ABSOLUTE_12M":
            return float(roc252)
        return 0.5 * float(roc60) + 0.3 * float(roc120) + 0.2 * float(roc252)

    def _scores(self, market):
        self.asset_scores = {
            ticker: self._momentum_score(market[ticker])
            for ticker in ASSETS
        }
        return self.asset_scores

    def _ranked_safe_weights(self, scores, remaining):
        safe = {"BND": 0.0, "BIL": 0.0, "GLD": 0.0}
        for ticker in sorted(safe, key=scores.get, reverse=True):
            capacity = (
                min(self.MAX_GLD_WEIGHT, remaining)
                if ticker == "GLD"
                else remaining
            )
            safe[ticker] += capacity
            remaining -= capacity
            if remaining <= 1e-12:
                break
        return safe

    @staticmethod
    def _redistribute_excess(weights, ticker, cap):
        if weights[ticker] <= cap:
            return
        excess = weights[ticker] - cap
        weights[ticker] = cap
        recipients = [
            asset for asset in weights
            if asset != ticker and asset not in ("GLD",)
        ]
        recipient_total = sum(weights[asset] for asset in recipients)
        if recipient_total > 0:
            for asset in recipients:
                weights[asset] += excess * weights[asset] / recipient_total
        else:
            weights["BIL"] += excess

    def _rank_weighted_target(self, scores):
        maximum = max(scores.values())
        scaled = {
            ticker: exp((score - maximum) / self.SOFTMAX_TEMPERATURE)
            for ticker, score in scores.items()
        }
        total = sum(scaled.values())
        weights = {
            ticker: value / total for ticker, value in scaled.items()
        }
        self._redistribute_excess(weights, "QQQ", self.MAX_QQQ_WEIGHT)
        self._redistribute_excess(weights, "GLD", self.MAX_GLD_WEIGHT)
        return weights

    def _absolute_target(self, market, scores):
        qqq = market["QQQ"]
        qqq_is_positive = (
            scores["QQQ"] > scores["BIL"]
            and self._valid(qqq.get("Close"), qqq.get("EMA200"))
            and qqq["Close"] > qqq["EMA200"]
        )
        qqq_weight = (
            self.MAX_QQQ_WEIGHT
            if qqq_is_positive
            else self.profile.minimum_qqq_weight
        )
        target = {"QQQ": qqq_weight}
        target.update(
            self._ranked_safe_weights(scores, 1.0 - qqq_weight)
        )
        return target

    def _desired_target(self, market):
        scores = self._scores(market)
        if self.profile.mode == "RANK_WEIGHTED":
            return self._rank_weighted_target(scores)
        return self._absolute_target(market, scores)

    def _signal(self, rebalance, reason):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": EXECUTION_DAYS,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        if self.target is None:
            self.target = self._desired_target(market)
            self.last_signal_month = month
            return self._signal(True, "INITIAL_MOMENTUM_RANK")
        if month == self.last_signal_month:
            return self._signal(False, None)

        self.last_signal_month = month
        desired = self._desired_target(market)
        prices = {ticker: market[ticker]["Close"] for ticker in desired}
        current = portfolio.weights(prices)
        self.target = desired
        rebalance = any(
            abs(current.get(ticker, 0.0) - weight) >= REBALANCE_BAND
            for ticker, weight in desired.items()
        )
        return self._signal(
            rebalance,
            "MONTHLY_MOMENTUM_RANK_5PCT_BAND" if rebalance else None,
        )


def _run_strategy(label, strategy):
    backtest = Backtest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=ASSETS,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "label": label,
        "strategy": strategy,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
    }


def _window_report(results):
    rows = []
    for result in results:
        for window, (start, end) in WINDOWS.items():
            row = _performance_row(result, window, start, end)
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def _relative_report(windows):
    baseline = windows.loc[
        windows["Strategy"] == "DYNAMIC_BASELINE"
    ].set_index("Window")
    rows = []
    candidates = windows.loc[
        windows["Strategy"].str.startswith(
            ("ABSOLUTE_", "RANK_WEIGHTED_")
        )
    ]
    for _, candidate in candidates.iterrows():
        window = candidate["Window"]
        rows.append({
            "Strategy": candidate["Strategy"],
            "Window": window,
            "CAGRGapVsDynamic": (
                candidate["CAGR"] - baseline.at[window, "CAGR"]
            ),
            "MDDImprovementVsDynamic": (
                candidate["MDD"] - baseline.at[window, "MDD"]
            ),
            "SharpeGapVsDynamic": (
                candidate["Sharpe"] - baseline.at[window, "Sharpe"]
            ),
        })
    return pd.DataFrame(rows)


def run_momentum_ranking_validation():
    results = [
        _run_strategy("DYNAMIC_BASELINE", DynamicRiskAllocationStrategy()),
        _run_strategy("STATIC_70_10_10_10", STATIC_70_BND10_BIL10_GLD10()),
        _run_strategy("QQQ_ONLY_DOWNSIDE", DownsideTrendOverlayStrategy()),
    ]
    results.extend(
        _run_strategy(profile.name, MomentumRankingStrategy(profile))
        for profile in PROFILES
    )
    windows = _window_report(results)
    reports = {
        "momentum_ranking_summary": windows.loc[
            windows["Window"] == "FULL_EXTENDED"
        ].drop(columns="Window").reset_index(drop=True),
        "momentum_ranking_windows": windows,
        "momentum_ranking_relative": _relative_report(windows),
        "momentum_ranking_annual": _annual_report(results),
        "momentum_ranking_weights": _allocation_report(results),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_momentum_ranking_validation()
    print(validation_reports["momentum_ranking_summary"].to_string(index=False))
    print("\nRealized allocation")
    print(validation_reports["momentum_ranking_weights"].to_string(index=False))
    print("\nRelative results")
    relative = validation_reports["momentum_ranking_relative"]
    print(
        relative.loc[
            relative["Window"].isin(
                [
                    "FULL_EXTENDED",
                    "DOTCOM_UNWIND",
                    "GLOBAL_FINANCIAL_CRISIS",
                    "COVID_CRASH",
                    "2022_RATE_SHOCK",
                    "POST_2010",
                ]
            )
        ].to_string(index=False)
    )
