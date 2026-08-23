"""Test USMV in place of BND/BIL while QQQ is below its 70% target."""

from dataclasses import dataclass

import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from performance import Performance
from strategy import RetirementAllocationStrategy
from .risk_asset_diversification import (
    DiversificationBacktest,
    TEST_START,
    WINDOWS,
    _max_underwater_days,
    download_candidate_data,
)


BACKTEST_TICKERS = ("QQQ", "BND", "BIL", "USMV")


@dataclass(frozen=True)
class SubstitutionProfile:
    name: str
    replaced_assets: frozenset


PROFILES = (
    SubstitutionProfile("USMV_INSTEAD_OF_BND", frozenset(("BND",))),
    SubstitutionProfile("USMV_INSTEAD_OF_BIL", frozenset(("BIL",))),
    SubstitutionProfile(
        "USMV_INSTEAD_OF_BND_OR_BIL", frozenset(("BND", "BIL"))
    ),
)


class USMVDefensiveSubstitutionStrategy(RetirementAllocationStrategy):
    """Replace selected safe assets with USMV only below 70% QQQ."""

    USMV_ASSET = "USMV"

    def __init__(self, profile):
        super().__init__()
        self.profile = profile

    @property
    def required_tickers(self):
        return (*super().required_tickers, self.USMV_ASSET)

    def _target_for_state(self):
        target = super()._target_for_state()
        target[self.USMV_ASSET] = 0.0
        qqq_weight = self.STATE_RISK_WEIGHTS[self.state]
        if (
            qqq_weight < self.MAX_RISK_WEIGHT
            and self.safe_asset in self.profile.replaced_assets
        ):
            available_risk_capacity = self.MAX_RISK_WEIGHT - qqq_weight
            usmv_weight = min(
                target[self.safe_asset], available_risk_capacity
            )
            target[self.USMV_ASSET] = round(usmv_weight, 10)
            target[self.safe_asset] = round(
                target[self.safe_asset] - usmv_weight, 10
            )
        if target["QQQ"] + target[self.USMV_ASSET] > self.MAX_RISK_WEIGHT:
            raise ValueError("combined QQQ and USMV weight exceeds 70%")
        return target


def _strategy_set():
    strategies = [("QQQ_BASELINE", RetirementAllocationStrategy())]
    strategies.extend(
        (profile.name, USMVDefensiveSubstitutionStrategy(profile))
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
    for result in results:
        history = result["history"].loc[TEST_START:]
        usmv = history["Weights"].apply(lambda weights: weights.get("USMV", 0.0))
        below_70 = history["StrategyState"].isin(("BEAR", "RECOVERY"))
        rows.append({
            "Strategy": result["label"],
            "AvgUSMVWeight": usmv.mean(),
            "AvgUSMVWeightWhenQQQBelow70": (
                usmv[below_70].mean() if below_70.any() else 0.0
            ),
            "USMVExposureDayRate": (usmv > 0.001).mean(),
            "Rebalances": sum(
                pd.Timestamp(item["Date"]) >= pd.Timestamp(TEST_START)
                for item in result["rebalances"]
            ),
            "Trades": len(result["trades"].loc[
                result["trades"]["Date"] >= TEST_START
            ]),
            "TransactionCosts": history["TransactionCosts"].iloc[-1]
            - history["TransactionCosts"].iloc[0],
        })
    return pd.DataFrame(rows)


def run_usmv_defensive_substitution(refresh_data=False):
    download_candidate_data(refresh=refresh_data, tickers=("USMV",))
    results = [_run(label, strategy) for label, strategy in _strategy_set()]
    windows = _window_report(results)
    reports = {
        "usmv_substitution_summary": windows.loc[
            windows["Window"] == "FULL_COMMON"
        ].drop(columns="Window").reset_index(drop=True),
        "usmv_substitution_windows": windows,
        "usmv_substitution_relative": _relative_report(windows),
        "usmv_substitution_activity": _activity_report(results),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    reports = run_usmv_defensive_substitution()
    print(reports["usmv_substitution_summary"].to_string(index=False))
    print("\nCandidate minus baseline")
    print(reports["usmv_substitution_relative"].to_string(index=False))
    print("\nUSMV exposure and activity")
    print(reports["usmv_substitution_activity"].to_string(index=False))
