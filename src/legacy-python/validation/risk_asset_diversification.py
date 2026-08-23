"""Test complementary risk assets for a Nasdaq-centered portfolio."""

import numpy as np
import pandas as pd
import yfinance as yf

from backtest import Backtest
from config import EXTENDED_DATA_DIR, PROJECT_ROOT, RESULT_DIR
from indicators import Indicator
from performance import Performance
from strategy import RetirementAllocationStrategy


RISK_ASSET_DATA_DIR = PROJECT_ROOT / "data_risk_assets"
CANDIDATES = ("QQQ", "VTV", "VXUS", "USMV")
BACKTEST_TICKERS = (*CANDIDATES, "BND", "BIL")
TEST_START = "2012-01-03"
WINDOWS = {
    "FULL_COMMON": (TEST_START, None),
    "2015_2016_GROWTH_SCARE": ("2015-07-20", "2016-02-11"),
    "2018_SELL_OFF": ("2018-09-01", "2018-12-31"),
    "COVID_CRASH": ("2020-02-19", "2020-03-23"),
    "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
}

yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))


def download_candidate_data(refresh=False, tickers=CANDIDATES[1:]):
    """Download adjusted ETF histories used only by this validation."""
    RISK_ASSET_DATA_DIR.mkdir(exist_ok=True)
    for ticker in tickers:
        path = RISK_ASSET_DATA_DIR / f"{ticker}.csv"
        if path.exists() and not refresh:
            continue
        frame = yf.download(
            ticker,
            start="2004-01-01",
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if frame.empty:
            raise ValueError(f"No data downloaded for {ticker}")
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = frame.columns.get_level_values(0)
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        frame.index.name = "Date"
        for column in ("Open", "High", "Low"):
            if column not in frame:
                frame[column] = frame["Close"]
        if "Volume" not in frame:
            frame["Volume"] = 0.0
        Indicator.add_indicators(frame).to_csv(path)


class DiversificationBacktest(Backtest):
    """Load new risk candidates beside the existing extended histories."""

    def load_one(self, ticker):
        path = RISK_ASSET_DATA_DIR / f"{ticker}.csv"
        if ticker not in ("QQQ", "BND", "BIL", "GLD", "QLD") and path.exists():
            frame = pd.read_csv(path, index_col="Date", parse_dates=True)
            required = {"EMA200", "ROC60", "ROC120"}
            return (
                frame
                if required.issubset(frame.columns)
                else Indicator.add_indicators(frame)
            )
        return super().load_one(ticker)


class FixedDiversifiedRiskStrategy(RetirementAllocationStrategy):
    """Keep the risk sleeve at QQQ 60%, VTV 20%, and VXUS 20%."""

    MIX = {"QQQ": 0.60, "VTV": 0.20, "VXUS": 0.20}

    def __init__(self):
        super().__init__(signal_asset="QQQ", risk_assets=self.MIX)


class MomentumDiversifiedRiskStrategy(RetirementAllocationStrategy):
    """Rotate the risk sleeve when QQQ weakens on absolute momentum."""

    QQQ_CAP = 0.60

    def __init__(self):
        super().__init__(
            signal_asset="QQQ",
            risk_assets={ticker: 0.25 for ticker in CANDIDATES},
        )
        self.active_mix = {"QQQ": self.QQQ_CAP}
        self.asset_scores = {}
        self.last_mix_month = None

    @staticmethod
    def _score(asset):
        roc60 = asset.get("ROC60")
        roc120 = asset.get("ROC120")
        if not RetirementAllocationStrategy._valid(roc60, roc120):
            return None
        return 0.60 * float(roc60) + 0.40 * float(roc120)

    def _select_mix(self, market):
        bil_score = self._score(market["BIL"])
        self.asset_scores = {
            ticker: self._score(market[ticker]) for ticker in CANDIDATES
        }
        eligible = []
        for ticker in CANDIDATES:
            asset = market[ticker]
            score = self.asset_scores[ticker]
            if not self._valid(
                score, bil_score, asset.get("Close"), asset.get("EMA200")
            ):
                continue
            if score > bil_score and asset["Close"] > asset["EMA200"]:
                eligible.append(ticker)

        alternatives = sorted(
            (ticker for ticker in eligible if ticker != "QQQ"),
            key=self.asset_scores.get,
            reverse=True,
        )
        if "QQQ" in eligible:
            mix = {"QQQ": self.QQQ_CAP}
            if alternatives:
                mix[alternatives[0]] = 1.0 - self.QQQ_CAP
            return mix
        if not alternatives:
            return {}
        selected = alternatives[:2]
        return {ticker: 1.0 / len(selected) for ticker in selected}

    def _target_for_state(self):
        risk_weight = self.STATE_RISK_WEIGHTS[self.state]
        target = {ticker: 0.0 for ticker in CANDIDATES}
        for ticker, share in self.active_mix.items():
            target[ticker] = round(risk_weight * share, 10)
        target.update({self.BOND_ASSET: 0.0, self.CASH_ASSET: 0.0})
        invested_risk = sum(target[ticker] for ticker in CANDIDATES)
        target[self.safe_asset] = round(1.0 - invested_risk, 10)
        return target

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        mix_changed = False
        if month != self.last_mix_month:
            selected = self._select_mix(market)
            mix_changed = selected != self.active_mix
            self.active_mix = selected
            self.last_mix_month = month
        signal = super().evaluate(date, market, portfolio)
        if signal["rebalance"] and mix_changed:
            reason = "MONTHLY_RISK_ASSET_ROTATION"
            signal["reason"] = (
                f"{signal['reason']}|{reason}" if signal.get("reason") else reason
            )
        return signal


def _strategy_set():
    return (
        ("QQQ_ONLY", RetirementAllocationStrategy()),
        ("FIXED_QQQ60_VTV20_VXUS20", FixedDiversifiedRiskStrategy()),
        ("MOMENTUM_QQQ_VTV_VXUS_USMV", MomentumDiversifiedRiskStrategy()),
    )


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


def _max_underwater_days(portfolio):
    underwater = portfolio < portfolio.cummax()
    longest = current = 0
    previous_date = None
    for date, is_underwater in underwater.items():
        if is_underwater:
            if current == 0:
                start = previous_date or date
            current = (date - start).days
            longest = max(longest, current)
        else:
            current = 0
        previous_date = date
    return longest


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


def _activity_report(results):
    rows = []
    for result in results:
        history = result["history"].loc[TEST_START:]
        row = {
            "Strategy": result["label"],
            "Rebalances": len([
                item for item in result["rebalances"]
                if pd.Timestamp(item["Date"]) >= pd.Timestamp(TEST_START)
            ]),
            "Trades": len(result["trades"].loc[
                result["trades"]["Date"] >= TEST_START
            ]),
            "TransactionCosts": history["TransactionCosts"].iloc[-1]
            - history["TransactionCosts"].iloc[0],
        }
        for ticker in CANDIDATES:
            row[f"Avg{ticker}Weight"] = history["Weights"].apply(
                lambda weights, asset=ticker: weights.get(asset, 0.0)
            ).mean()
        rows.append(row)
    return pd.DataFrame(rows)


def _relative_report(windows):
    baseline = windows.loc[windows["Strategy"] == "QQQ_ONLY"].set_index(
        "Window"
    )
    rows = []
    for _, candidate in windows.loc[
        windows["Strategy"] != "QQQ_ONLY"
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


def _asset_behavior_report(market_data):
    """Describe how each candidate behaved, especially on QQQ down days."""
    closes = pd.DataFrame({
        ticker: market_data[f"{ticker}_Close"] for ticker in CANDIDATES
    }).loc[TEST_START:]
    returns = closes.pct_change().dropna()
    qqq = returns["QQQ"]
    qqq_down = qqq < 0.0
    rows = []
    for ticker in CANDIDATES:
        candidate = returns[ticker]
        downside_beta = (
            np.cov(candidate[qqq_down], qqq[qqq_down], ddof=1)[0, 1]
            / np.var(qqq[qqq_down], ddof=1)
        )
        rows.append({
            "Asset": ticker,
            "CorrelationWithQQQ": candidate.corr(qqq),
            "AnnualizedVolatility": candidate.std() * np.sqrt(252),
            "AvgReturnOnQQQDownDays": candidate[qqq_down].mean(),
            "QQQDownsideBeta": downside_beta,
            "PositiveRateOnQQQDownDays": (candidate[qqq_down] > 0.0).mean(),
        })
    return pd.DataFrame(rows)


def run_risk_asset_diversification(refresh_data=False):
    download_candidate_data(refresh=refresh_data)
    results = [_run(label, strategy) for label, strategy in _strategy_set()]
    windows = _window_report(results)
    reports = {
        "risk_asset_summary": windows.loc[
            windows["Window"] == "FULL_COMMON"
        ].drop(columns="Window").reset_index(drop=True),
        "risk_asset_windows": windows,
        "risk_asset_relative": _relative_report(windows),
        "risk_asset_activity": _activity_report(results),
        "risk_asset_behavior": _asset_behavior_report(
            results[0]["market_data"]
        ),
    }
    for name, report in reports.items():
        report.to_csv(RESULT_DIR / f"{name}.csv", index=False)
    return reports


if __name__ == "__main__":
    reports = run_risk_asset_diversification()
    print(reports["risk_asset_summary"].to_string(index=False))
    print("\nCandidate minus QQQ-only baseline")
    print(reports["risk_asset_relative"].to_string(index=False))
    print("\nRealized allocation and activity")
    print(reports["risk_asset_activity"].to_string(index=False))
    print("\nCandidate behavior on QQQ down days")
    print(reports["risk_asset_behavior"].to_string(index=False))
