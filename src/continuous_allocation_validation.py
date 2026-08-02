"""Validate state-free continuous QQQ allocation profiles."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest import Backtest
from config import EXTENDED_DATA_DIR, RESULT_DIR
from extended_data import ASSETS
from performance import Performance
from strategy import (
    BaseStrategy,
    DynamicRiskAllocationStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


@dataclass(frozen=True)
class ContinuousProfile:
    name: str
    minimum_qqq_weight: float
    mapping: str = "SYMMETRIC"


PROFILES = (
    ContinuousProfile("CONTINUOUS_0_70", 0.00),
    ContinuousProfile("CORE_20_TACTICAL_50", 0.20),
    ContinuousProfile("CORE_35_TACTICAL_35", 0.35),
    ContinuousProfile("DOWNSIDE_OVERLAY_20_70", 0.20, "DOWNSIDE_ONLY"),
    ContinuousProfile("DOWNSIDE_OVERLAY_35_70", 0.35, "DOWNSIDE_ONLY"),
)

MAX_QQQ_WEIGHT = 0.70
MAX_GLD_WEIGHT = 0.20
TARGET_QQQ_VOLATILITY = 0.25
REBALANCE_BAND = 0.05
EXECUTION_DAYS = 3

WINDOWS = {
    "FULL_EXTENDED": ("2000-08-30", None),
    "DOTCOM_UNWIND": ("2000-08-30", "2002-10-09"),
    "GLOBAL_FINANCIAL_CRISIS": ("2007-10-09", "2009-03-09"),
    "POST_GFC_TO_2017": ("2009-03-10", "2017-12-29"),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
    "POST_2010": ("2010-01-01", None),
}


class ContinuousFeatureBacktest(Backtest):
    """Expose long-horizon features without changing the production engine."""

    def get_market(self, row):
        market = super().get_market(row)
        for ticker in self.tickers:
            market[ticker].update({
                "ROC120": row.get(f"{ticker}_ROC120"),
                "ROC252": row.get(f"{ticker}_ROC252"),
                "VOL60": row.get(f"{ticker}_VOL60"),
            })
        return market


class ContinuousRiskAllocationStrategy(BaseStrategy):
    """Map trend strength and volatility directly to portfolio weights."""

    def __init__(self, profile):
        self.profile = profile
        self.target = None
        self.last_signal_month = None
        self.risk_strength = None
        self.trend_score = None
        self.volatility_multiplier = None

    @staticmethod
    def _valid(*values):
        return all(value is not None and value == value for value in values)

    @staticmethod
    def _clip_signal(value, scale):
        return float(np.clip(value / scale, -1.0, 1.0))

    def _qqq_weight(self, qqq):
        close = qqq.get("Close")
        ema200 = qqq.get("EMA200")
        roc60 = qqq.get("ROC60")
        roc120 = qqq.get("ROC120")
        roc252 = qqq.get("ROC252")
        volatility = qqq.get("VOL60")
        if not self._valid(close, ema200, roc60, roc120, roc252, volatility):
            self.trend_score = 0.0
            self.risk_strength = 0.5
            self.volatility_multiplier = 1.0
        else:
            signals = (
                self._clip_signal(roc60, 15.0),
                self._clip_signal(roc120, 25.0),
                self._clip_signal(roc252, 40.0),
                self._clip_signal(close / ema200 - 1.0, 0.15),
            )
            self.trend_score = float(np.mean(signals))
            self.risk_strength = (self.trend_score + 1.0) / 2.0
            self.volatility_multiplier = float(np.clip(
                TARGET_QQQ_VOLATILITY / max(float(volatility), 0.01),
                0.25,
                1.0,
            ))

        tactical_capacity = MAX_QQQ_WEIGHT - self.profile.minimum_qqq_weight
        if self.profile.mapping == "DOWNSIDE_ONLY":
            volatility_stress = 1.0 / self.volatility_multiplier
            reduction = float(np.clip(
                max(0.0, -self.trend_score) * volatility_stress,
                0.0,
                1.0,
            ))
            weight = MAX_QQQ_WEIGHT - tactical_capacity * reduction
        else:
            weight = self.profile.minimum_qqq_weight + (
                tactical_capacity
                * self.risk_strength
                * self.volatility_multiplier
            )
        return float(np.clip(
            weight, self.profile.minimum_qqq_weight, MAX_QQQ_WEIGHT
        ))

    def _safe_weights(self, market, remaining):
        scores = {}
        for ticker in ("BND", "BIL", "GLD"):
            roc60 = market[ticker].get("ROC60")
            roc120 = market[ticker].get("ROC120")
            volatility = market[ticker].get("VOL60")
            if not self._valid(roc60, roc120, volatility):
                scores[ticker] = 0.0
                continue
            momentum = (float(roc60) + float(roc120)) / 200.0
            scores[ticker] = momentum - 0.25 * float(volatility)

        logits = np.array([scores[ticker] for ticker in ("BND", "BIL", "GLD")])
        logits = (logits - logits.max()) / 0.05
        proportions = np.exp(logits)
        proportions /= proportions.sum()
        safe = {
            ticker: remaining * proportion
            for ticker, proportion in zip(
                ("BND", "BIL", "GLD"), proportions
            )
        }

        gld_cap = min(MAX_GLD_WEIGHT, remaining)
        if safe["GLD"] > gld_cap:
            excess = safe["GLD"] - gld_cap
            safe["GLD"] = gld_cap
            non_gold = safe["BND"] + safe["BIL"]
            if non_gold > 0:
                safe["BND"] += excess * safe["BND"] / non_gold
                safe["BIL"] += excess * safe["BIL"] / non_gold
            else:
                safe["BND"] += excess / 2.0
                safe["BIL"] += excess / 2.0
        return safe

    def _desired_target(self, market):
        qqq_weight = self._qqq_weight(market["QQQ"])
        target = {"QQQ": qqq_weight}
        target.update(self._safe_weights(market, 1.0 - qqq_weight))
        return target

    @staticmethod
    def _outside_band(current, target):
        return any(
            abs(current.get(ticker, 0.0) - weight) >= REBALANCE_BAND
            for ticker, weight in target.items()
        )

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
            return self._signal(True, "INITIAL_CONTINUOUS_TARGET")
        if month == self.last_signal_month:
            return self._signal(False, None)

        self.last_signal_month = month
        desired = self._desired_target(market)
        prices = {ticker: market[ticker]["Close"] for ticker in desired}
        current = portfolio.weights(prices)
        self.target = desired
        rebalance = self._outside_band(current, desired)
        return self._signal(
            rebalance,
            "MONTHLY_CONTINUOUS_5PCT_BAND" if rebalance else None,
        )


def _run_strategy(label, strategy):
    backtest = ContinuousFeatureBacktest(
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


def _performance_row(result, window, start, end):
    sample = result["history"].loc[start:end]
    if len(sample) < 2:
        return None
    metrics = Performance(sample).summary()
    costs = sample["TransactionCosts"]
    return {
        "Strategy": result["label"],
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
        "TransactionCosts": costs.iloc[-1] - costs.iloc[0],
    }


def _window_report(results):
    rows = []
    for result in results:
        for window, (start, end) in WINDOWS.items():
            row = _performance_row(result, window, start, end)
            if row is not None:
                rows.append(row)
    return pd.DataFrame(rows)


def _allocation_report(results):
    rows = []
    for result in results:
        history = result["history"]
        weights = history["Weights"]
        qqq = weights.apply(lambda value: value.get("QQQ", 0.0))
        gld = weights.apply(lambda value: value.get("GLD", 0.0))
        invested_qqq = qqq.iloc[EXECUTION_DAYS:]
        invested_gld = gld.iloc[EXECUTION_DAYS:]
        rows.append({
            "Strategy": result["label"],
            "AvgQQQWeight": invested_qqq.mean(),
            "MinQQQWeight": invested_qqq.min(),
            "MaxQQQWeight": invested_qqq.max(),
            "AvgGLDWeight": invested_gld.mean(),
            "MaxGLDWeight": invested_gld.max(),
            "Rebalances": len(result["rebalances"]),
            "Trades": len(result["trades"]),
            "TransactionCosts": history["TransactionCosts"].iloc[-1],
        })
    return pd.DataFrame(rows)


def _annual_report(results):
    rows = []
    for result in results:
        returns = result["history"]["Portfolio"].pct_change().dropna()
        annual = (1.0 + returns).groupby(returns.index.year).prod() - 1.0
        for year, value in annual.items():
            rows.append({
                "Year": year,
                "Strategy": result["label"],
                "Return": value,
            })
    return pd.DataFrame(rows)


def _relative_report(windows):
    dynamic = windows.loc[
        windows["Strategy"] == "DYNAMIC_BASELINE"
    ].set_index("Window")
    static = windows.loc[
        windows["Strategy"] == "STATIC_70_10_10_10"
    ].set_index("Window")
    rows = []
    for _, candidate in windows.loc[
        windows["Strategy"].str.startswith(
            ("CONTINUOUS_", "CORE_", "DOWNSIDE_")
        )
    ].iterrows():
        window = candidate["Window"]
        rows.append({
            "Strategy": candidate["Strategy"],
            "Window": window,
            "CAGRGapVsDynamic": candidate["CAGR"] - dynamic.at[window, "CAGR"],
            "MDDImprovementVsDynamic": (
                candidate["MDD"] - dynamic.at[window, "MDD"]
            ),
            "SharpeGapVsDynamic": (
                candidate["Sharpe"] - dynamic.at[window, "Sharpe"]
            ),
            "CAGRGapVsStatic": candidate["CAGR"] - static.at[window, "CAGR"],
            "MDDImprovementVsStatic": (
                candidate["MDD"] - static.at[window, "MDD"]
            ),
        })
    return pd.DataFrame(rows)


def run_continuous_allocation_validation():
    results = [
        _run_strategy("DYNAMIC_BASELINE", DynamicRiskAllocationStrategy()),
        _run_strategy("STATIC_70_10_10_10", STATIC_70_BND10_BIL10_GLD10()),
    ]
    results.extend(
        _run_strategy(
            profile.name, ContinuousRiskAllocationStrategy(profile)
        )
        for profile in PROFILES
    )

    windows = _window_report(results)
    full_summary = windows.loc[
        windows["Window"] == "FULL_EXTENDED"
    ].drop(columns="Window").reset_index(drop=True)
    reports = {
        "continuous_allocation_summary": full_summary,
        "continuous_allocation_windows": windows,
        "continuous_allocation_relative": _relative_report(windows),
        "continuous_allocation_annual": _annual_report(results),
        "continuous_allocation_weights": _allocation_report(results),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_continuous_allocation_validation()
    print(validation_reports["continuous_allocation_summary"].to_string(index=False))
    print("\nRealized allocation")
    print(validation_reports["continuous_allocation_weights"].to_string(index=False))
    print("\nRelative results")
    relative = validation_reports["continuous_allocation_relative"]
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
