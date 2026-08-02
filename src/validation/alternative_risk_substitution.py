"""Compare non-USMV risk assets as capped defensive substitutes."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from performance import Performance
from strategy import PensionRiskAllocationStrategy
from .risk_asset_diversification import (
    DiversificationBacktest,
    TEST_START,
    WINDOWS,
    _max_underwater_days,
    download_candidate_data,
)


ALTERNATIVES = ("VTV", "VIG", "SPLV", "VXUS")
BACKTEST_TICKERS = ("QQQ", "BND", "BIL", *ALTERNATIVES)
REPLACEMENT_MODES = {
    "BND": frozenset(("BND",)),
    "BIL": frozenset(("BIL",)),
    "BND_OR_BIL": frozenset(("BND", "BIL")),
}


@dataclass(frozen=True)
class AlternativeProfile:
    name: str
    candidate: str
    replaced_assets: frozenset


PROFILES = tuple(
    AlternativeProfile(
        f"{candidate}_INSTEAD_OF_{mode}", candidate, replaced_assets
    )
    for candidate in ALTERNATIVES
    for mode, replaced_assets in REPLACEMENT_MODES.items()
)


class AlternativeRiskSubstitutionStrategy(PensionRiskAllocationStrategy):
    """Use one equity candidate without exceeding the 70% risk-asset cap."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile

    @property
    def required_tickers(self):
        return (*super().required_tickers, self.profile.candidate)

    def _target_for_state(self):
        target = super()._target_for_state()
        candidate = self.profile.candidate
        target[candidate] = 0.0
        qqq_weight = self.STATE_RISK_WEIGHTS[self.state]
        if (
            qqq_weight < self.MAX_RISK_WEIGHT
            and self.safe_asset in self.profile.replaced_assets
        ):
            risk_capacity = self.MAX_RISK_WEIGHT - qqq_weight
            candidate_weight = min(target[self.safe_asset], risk_capacity)
            target[candidate] = round(candidate_weight, 10)
            target[self.safe_asset] = round(
                target[self.safe_asset] - candidate_weight, 10
            )
        if target["QQQ"] + target[candidate] > self.MAX_RISK_WEIGHT:
            raise ValueError("combined risk-asset weight exceeds 70%")
        return target


def _strategy_set():
    strategies = [("QQQ_BASELINE", PensionRiskAllocationStrategy())]
    strategies.extend(
        (profile.name, AlternativeRiskSubstitutionStrategy(profile))
        for profile in PROFILES
    )
    return strategies


def _run(label, strategy):
    backtest = DiversificationBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=BACKTEST_TICKERS,
    )
    history, trades, rebalances = backtest.run_all()
    return {
        "label": label,
        "history": history,
        "trades": trades,
        "rebalances": rebalances,
        "market_data": backtest.data,
    }


def _performance_row(result, window, start, end):
    history = result["history"].loc[start:end]
    if len(history) < 2:
        return None
    metrics = Performance(history).summary()
    return {
        "Strategy": result["label"],
        "Window": window,
        "StartDate": history.index.min(),
        "EndDate": history.index.max(),
        "TotalReturn": history["Portfolio"].iloc[-1]
        / history["Portfolio"].iloc[0] - 1.0,
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Volatility": metrics["Volatility"],
        "Sharpe": metrics["Sharpe"],
        "Sortino": metrics["Sortino"],
        "Calmar": metrics["Calmar"],
        "MaxUnderwaterDays": _max_underwater_days(history["Portfolio"]),
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
        windows["Strategy"] == "QQQ_BASELINE"
    ].set_index("Window")
    rows = []
    for _, candidate in windows.loc[
        windows["Strategy"] != "QQQ_BASELINE"
    ].iterrows():
        window = candidate["Window"]
        rows.append({
            "Strategy": candidate["Strategy"],
            "Window": window,
            "CAGRGap": candidate["CAGR"] - baseline.at[window, "CAGR"],
            "MDDImprovement": candidate["MDD"] - baseline.at[window, "MDD"],
            "SharpeGap": candidate["Sharpe"] - baseline.at[window, "Sharpe"],
            "UnderwaterDaysImprovement": baseline.at[
                window, "MaxUnderwaterDays"
            ] - candidate["MaxUnderwaterDays"],
        })
    return pd.DataFrame(rows)


def _activity_report(results):
    rows = []
    for result in results[1:]:
        history = result["history"].loc[TEST_START:]
        candidate = result["label"].split("_INSTEAD_OF_")[0]
        weight = history["Weights"].apply(
            lambda weights: weights.get(candidate, 0.0)
        )
        below_70 = history["StrategyState"].isin(("BEAR", "RECOVERY"))
        rows.append({
            "Strategy": result["label"],
            "Candidate": candidate,
            "AvgCandidateWeight": weight.mean(),
            "AvgCandidateWeightWhenQQQBelow70": weight[below_70].mean(),
            "ExposureDayRate": (weight > 0.001).mean(),
            "Rebalances": sum(
                pd.Timestamp(item["Date"]) >= pd.Timestamp(TEST_START)
                for item in result["rebalances"]
            ),
            "Trades": len(result["trades"].loc[
                result["trades"]["Date"] >= TEST_START
            ]),
        })
    return pd.DataFrame(rows)


def _asset_behavior_report(market_data):
    assets = ("QQQ", *ALTERNATIVES)
    closes = pd.DataFrame({
        ticker: market_data[f"{ticker}_Close"] for ticker in assets
    }).loc[TEST_START:]
    returns = closes.pct_change().dropna()
    qqq = returns["QQQ"]
    qqq_down = qqq < 0.0
    rows = []
    for ticker in assets:
        candidate = returns[ticker]
        rows.append({
            "Asset": ticker,
            "CorrelationWithQQQ": candidate.corr(qqq),
            "AnnualizedVolatility": candidate.std() * np.sqrt(252),
            "AvgReturnOnQQQDownDays": candidate[qqq_down].mean(),
            "QQQDownsideBeta": (
                np.cov(candidate[qqq_down], qqq[qqq_down], ddof=1)[0, 1]
                / np.var(qqq[qqq_down], ddof=1)
            ),
            "PositiveRateOnQQQDownDays": (candidate[qqq_down] > 0.0).mean(),
        })
    return pd.DataFrame(rows)


def _annual_reports(results):
    rows = []
    for result in results:
        returns = result["history"].loc[TEST_START:, "Portfolio"].pct_change().dropna()
        annual = (1.0 + returns).groupby(returns.index.year).prod() - 1.0
        for year, value in annual.items():
            rows.append({
                "Year": year,
                "Strategy": result["label"],
                "Return": value,
            })
    annual = pd.DataFrame(rows)
    pivot = annual.pivot(index="Year", columns="Strategy", values="Return")
    baseline = pivot["QQQ_BASELINE"]
    score_rows = []
    for strategy in pivot.columns.drop("QQQ_BASELINE"):
        excess = (pivot[strategy] - baseline).dropna()
        score_rows.append({
            "Strategy": strategy,
            "Years": len(excess),
            "AnnualWinRate": (excess > 0.0).mean(),
            "AvgAnnualExcessReturn": excess.mean(),
            "MedianAnnualExcessReturn": excess.median(),
            "WorstAnnualUnderperformance": excess.min(),
            "BestAnnualOutperformance": excess.max(),
        })
    scorecard = pd.DataFrame(score_rows).sort_values(
        ["AnnualWinRate", "AvgAnnualExcessReturn"], ascending=False
    )
    return annual, scorecard


def run_alternative_risk_substitution(refresh_data=False):
    download_candidate_data(refresh=refresh_data, tickers=ALTERNATIVES)
    results = [_run(label, strategy) for label, strategy in _strategy_set()]
    windows = _window_report(results)
    annual, annual_scorecard = _annual_reports(results)
    summary = windows.loc[windows["Window"] == "FULL_COMMON"].drop(
        columns="Window"
    ).sort_values("Sharpe", ascending=False).reset_index(drop=True)
    reports = {
        "alternative_substitution_summary": summary,
        "alternative_substitution_windows": windows,
        "alternative_substitution_relative": _relative_report(windows),
        "alternative_substitution_activity": _activity_report(results),
        "alternative_substitution_behavior": _asset_behavior_report(
            results[0]["market_data"]
        ),
        "alternative_substitution_annual": annual,
        "alternative_substitution_annual_scorecard": annual_scorecard,
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    reports = run_alternative_risk_substitution()
    print(reports["alternative_substitution_summary"].to_string(index=False))
    print("\nCandidate behavior on QQQ down days")
    print(reports["alternative_substitution_behavior"].to_string(index=False))
