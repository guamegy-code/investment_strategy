"""Validate QQQ downside overlays confirmed by SPY and IWM trends."""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

from backtest import Backtest
from config import EXTENDED_DATA_DIR, PROJECT_ROOT, RESULT_DIR
from .continuous_allocation import (
    WINDOWS,
    _allocation_report,
    _annual_report,
    _performance_row,
)
from .extended_data import ASSETS
from indicators import Indicator
from strategy import (
    DownsideTrendOverlayStrategy,
    DynamicRiskAllocationStrategy,
    STATIC_70_BND10_BIL10_GLD10,
)


CONFIRMATION_ASSETS = ("SPY", "IWM")
MULTI_MARKET_DATA_DIR = PROJECT_ROOT / "data_multimarket"
MULTI_MARKET_TICKERS = ASSETS + CONFIRMATION_ASSETS


@dataclass(frozen=True)
class MultiMarketProfile:
    name: str
    confirmation: str


PROFILES = (
    MultiMarketProfile("MULTI_MARKET_2_OF_3", "TWO_OF_THREE"),
    MultiMarketProfile("MULTI_MARKET_ALL_3", "ALL_THREE"),
    MultiMarketProfile("MULTI_MARKET_GRADED", "GRADED"),
)


def download_confirmation_assets(refresh=False):
    MULTI_MARKET_DATA_DIR.mkdir(exist_ok=True)
    for ticker in CONFIRMATION_ASSETS:
        path = MULTI_MARKET_DATA_DIR / f"{ticker}.csv"
        if path.exists() and not refresh:
            continue
        frame = yf.download(
            ticker,
            start="1999-03-10",
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if frame.empty:
            raise ValueError(f"No confirmation-market data for {ticker}")
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "Date"
        for column in ("Open", "High", "Low"):
            if column not in frame:
                frame[column] = frame["Close"]
        Indicator.add_indicators(frame).to_csv(path)


class MultiMarketBacktest(Backtest):
    """Load confirmation ETFs separately from proxy-extended portfolio data."""

    def load_one(self, ticker):
        if ticker not in CONFIRMATION_ASSETS:
            return super().load_one(ticker)
        path = MULTI_MARKET_DATA_DIR / f"{ticker}.csv"
        frame = pd.read_csv(path, index_col="Date", parse_dates=True)
        required = {"ROC60", "ROC120", "ROC252", "EMA200", "VOL60"}
        return (
            frame
            if required.issubset(frame.columns)
            else Indicator.add_indicators(frame)
        )


class MultiMarketDownsideStrategy(DownsideTrendOverlayStrategy):
    """Reduce QQQ only when broad equity trends confirm its weakness."""

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.market_scores = {}

    def _market_score(self, asset):
        close = asset.get("Close")
        ema200 = asset.get("EMA200")
        roc60 = asset.get("ROC60")
        roc120 = asset.get("ROC120")
        roc252 = asset.get("ROC252")
        if not self._valid(close, ema200, roc60, roc120, roc252):
            return 0.0
        signals = (
            self._scaled_signal(roc60, 15.0),
            self._scaled_signal(roc120, 25.0),
            self._scaled_signal(roc252, 40.0),
            self._scaled_signal(close / ema200 - 1.0, 0.15),
        )
        return sum(signals) / len(signals)

    def _confirmed_qqq_weight(self, market):
        self.market_scores = {
            ticker: self._market_score(market[ticker])
            for ticker in ("QQQ", "SPY", "IWM")
        }
        qqq_score = self.market_scores["QQQ"]
        negative_markets = sum(
            score < 0 for score in self.market_scores.values()
        )
        if qqq_score >= 0:
            confirmation_fraction = 0.0
        elif self.profile.confirmation == "ALL_THREE":
            confirmation_fraction = 1.0 if negative_markets == 3 else 0.0
        elif self.profile.confirmation == "TWO_OF_THREE":
            confirmation_fraction = 1.0 if negative_markets >= 2 else 0.0
        else:
            confirmation_fraction = negative_markets / 3.0

        volatility = market["QQQ"].get("VOL60")
        if not self._valid(volatility):
            self.volatility_multiplier = 1.0
        else:
            self.volatility_multiplier = self._clip(
                self.TARGET_QQQ_VOLATILITY / max(float(volatility), 0.01),
                0.25,
                1.0,
            )
        self.trend_score = qqq_score
        volatility_stress = 1.0 / self.volatility_multiplier
        reduction = self._clip(
            max(0.0, -qqq_score)
            * volatility_stress
            * confirmation_fraction,
            0.0,
            1.0,
        )
        tactical_range = self.MAX_QQQ_WEIGHT - self.MIN_QQQ_WEIGHT
        return self.MAX_QQQ_WEIGHT - tactical_range * reduction

    def _desired_target(self, market):
        qqq_weight = self._confirmed_qqq_weight(market)
        target = {"QQQ": qqq_weight}
        target.update(self._safe_weights(market, 1.0 - qqq_weight))
        return target


def _run_strategy(label, strategy):
    backtest = MultiMarketBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=MULTI_MARKET_TICKERS,
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
            ("QQQ_ONLY_", "MULTI_MARKET_")
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


def run_multi_market_validation(refresh_data=False):
    download_confirmation_assets(refresh=refresh_data)
    results = [
        _run_strategy("DYNAMIC_BASELINE", DynamicRiskAllocationStrategy()),
        _run_strategy("STATIC_70_10_10_10", STATIC_70_BND10_BIL10_GLD10()),
        _run_strategy("QQQ_ONLY_DOWNSIDE", DownsideTrendOverlayStrategy()),
    ]
    results.extend(
        _run_strategy(profile.name, MultiMarketDownsideStrategy(profile))
        for profile in PROFILES
    )
    windows = _window_report(results)
    reports = {
        "multi_market_summary": windows.loc[
            windows["Window"] == "FULL_EXTENDED"
        ].drop(columns="Window").reset_index(drop=True),
        "multi_market_windows": windows,
        "multi_market_relative": _relative_report(windows),
        "multi_market_annual": _annual_report(results),
        "multi_market_weights": _allocation_report(results),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    validation_reports = run_multi_market_validation()
    print(validation_reports["multi_market_summary"].to_string(index=False))
    print("\nRealized allocation")
    print(validation_reports["multi_market_weights"].to_string(index=False))
    print("\nRelative results")
    relative = validation_reports["multi_market_relative"]
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
