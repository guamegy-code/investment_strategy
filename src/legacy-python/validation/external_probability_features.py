"""Validate VIX term-structure and credit-risk probability features."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config import EXTENDED_DATA_DIR, PROJECT_ROOT, RESULT_DIR
from indicators import Indicator
from performance import Performance
from strategy import STATIC_RETIREMENT_7030
from .enhanced_probability_features import build_enhanced_features
from .multi_horizon_probability import TAIL_CONFIG, TAIL_RETURN_THRESHOLD
from .multi_market import MultiMarketBacktest, MULTI_MARKET_TICKERS
from .probabilistic_allocation import FEATURE_COLUMNS, _summary, walk_forward_event_probabilities
from .tail_risk_overlay import PROFILES as TAIL_PROFILES, TailRiskOverlayStrategy


EXTERNAL_DATA_DIR = PROJECT_ROOT / "data_probability_signals"
EXTERNAL_SYMBOLS = {
    "VIX": "^VIX",
    "VIX3M": "^VIX3M",
    "HYG": "HYG",
    "LQD": "LQD",
}
EXTERNAL_TICKERS = tuple(EXTERNAL_SYMBOLS)
ALL_TICKERS = MULTI_MARKET_TICKERS + EXTERNAL_TICKERS

LOCAL_COLUMNS = FEATURE_COLUMNS + ("QQQ_VOL_ACCELERATION",)
VIX_COLUMNS = (
    "VIX_LOG_LEVEL",
    "VIX_CHANGE5",
    "VIX_CHANGE20",
    "VIX_TERM_STRUCTURE",
)
CREDIT_COLUMNS = (
    "HYG_LQD_REL20",
    "HYG_LQD_REL60",
    "HYG_VOL20",
    "HYG_DRAWDOWN60",
)


@dataclass(frozen=True)
class ExternalFeatureProfile:
    name: str
    columns: tuple[str, ...]


PROFILES = (
    ExternalFeatureProfile("LOCAL_VOL_ACCEL", LOCAL_COLUMNS),
    ExternalFeatureProfile("LOCAL_PLUS_VIX", LOCAL_COLUMNS + VIX_COLUMNS),
    ExternalFeatureProfile("LOCAL_PLUS_CREDIT", LOCAL_COLUMNS + CREDIT_COLUMNS),
    ExternalFeatureProfile(
        "LOCAL_PLUS_VIX_CREDIT",
        LOCAL_COLUMNS + VIX_COLUMNS + CREDIT_COLUMNS,
    ),
)

EXTERNAL_ABLATION_PROFILES = tuple(
    ExternalFeatureProfile(f"LOCAL_PLUS_{column}", LOCAL_COLUMNS + (column,))
    for column in VIX_COLUMNS + CREDIT_COLUMNS
)


def download_external_signals(refresh=False, output_dir=EXTERNAL_DATA_DIR):
    """Download adjusted signal histories into the workspace cache."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)
    yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))
    rows = []
    for label, symbol in EXTERNAL_SYMBOLS.items():
        path = output_dir / f"{label}.csv"
        if path.exists() and not refresh:
            frame = pd.read_csv(path, index_col="Date", parse_dates=True)
        else:
            frame = yf.download(
                symbol,
                start="1999-01-01",
                auto_adjust=True,
                progress=False,
                multi_level_index=False,
            )
            if frame.empty:
                raise ValueError(f"No external signal data for {symbol}")
            if isinstance(frame.columns, pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)
            frame.index = pd.to_datetime(frame.index).tz_localize(None)
            frame.index.name = "Date"
            for column in ("Open", "High", "Low"):
                if column not in frame:
                    frame[column] = frame["Close"]
            if "Volume" not in frame:
                frame["Volume"] = 0.0
            frame = Indicator.add_indicators(frame)
            frame.to_csv(path)
        rows.append({
            "Label": label,
            "Symbol": symbol,
            "StartDate": frame.index.min(),
            "EndDate": frame.index.max(),
            "Observations": len(frame),
        })
    manifest = pd.DataFrame(rows)
    manifest.to_csv(output_dir / "manifest.csv", index=False)
    return manifest


def build_external_features(data):
    frame = build_enhanced_features(data)
    frame["VIX_LOG_LEVEL"] = np.log(frame["VIX_Close"].clip(lower=0.01))
    frame["VIX_CHANGE5"] = frame["VIX_Close"].pct_change(5)
    frame["VIX_CHANGE20"] = frame["VIX_Close"].pct_change(20)
    frame["VIX_TERM_STRUCTURE"] = (
        frame["VIX_Close"] / frame["VIX3M_Close"] - 1.0
    )

    credit_ratio = frame["HYG_Close"] / frame["LQD_Close"]
    frame["HYG_LQD_REL20"] = credit_ratio.pct_change(20) * 100.0
    frame["HYG_LQD_REL60"] = credit_ratio.pct_change(60) * 100.0
    hyg_returns = frame["HYG_Close"].pct_change()
    frame["HYG_VOL20"] = hyg_returns.rolling(20).std() * np.sqrt(252)
    frame["HYG_DRAWDOWN60"] = (
        frame["HYG_Close"] / frame["HYG_Close"].rolling(60).max() - 1.0
    )
    return frame


class ExternalProbabilityBacktest(MultiMarketBacktest):
    def __init__(self, *args, feature_profile, **kwargs):
        self.feature_profile = feature_profile
        super().__init__(*args, **kwargs)

    def load_one(self, ticker):
        if ticker not in EXTERNAL_TICKERS:
            return super().load_one(ticker)
        path = EXTERNAL_DATA_DIR / f"{ticker}.csv"
        frame = pd.read_csv(path, index_col="Date", parse_dates=True)
        required = {"ROC20", "ROC60", "EMA200", "VOL60"}
        return frame if required.issubset(frame.columns) else Indicator.add_indicators(frame)

    def load_data(self):
        data = build_external_features(super().load_data())
        forecast = walk_forward_event_probabilities(
            data,
            TAIL_CONFIG,
            event="DOWNSIDE",
            threshold=TAIL_RETURN_THRESHOLD,
            feature_columns=self.feature_profile.columns,
        ).rename(columns={
            "EventProbability": "ProbabilityLoss21",
            "BaseEventProbability": "BaseLossProbability21",
            "ModelSamples": "TailModelSamples",
        })
        return data.join(forecast)

    def get_market(self, row):
        market = super().get_market(row)
        market["QQQ"].update({
            "ProbabilityLoss21": row.get("ProbabilityLoss21"),
            "BaseLossProbability21": row.get("BaseLossProbability21"),
            "TailModelSamples": row.get("TailModelSamples"),
        })
        return market


def _run_static():
    backtest = ExternalProbabilityBacktest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=ALL_TICKERS,
        feature_profile=PROFILES[0],
    )
    history, trades, rebalances = backtest.run_all()
    return "STATIC_COMMON_PERIOD", history, trades, rebalances, backtest.data


def _run_profile(profile):
    strategy = TailRiskOverlayStrategy(TAIL_PROFILES[0])
    backtest = ExternalProbabilityBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=ALL_TICKERS,
        feature_profile=profile,
    )
    history, trades, rebalances = backtest.run_all()
    return profile.name, history, trades, rebalances, backtest.data


def _active_start(data):
    active = data.index[data["TailModelSamples"] >= TAIL_CONFIG.minimum_samples]
    return active.min()


def _performance_windows(results):
    active_start = max(_active_start(result[4]) for result in results[1:])
    windows = {
        "MODEL_ACTIVE_COMMON": (active_start, None),
        "PRE_2018": (active_start, "2017-12-31"),
        "RECENT_2018_PRESENT": ("2018-01-01", None),
        "COVID_CRASH": ("2020-02-19", "2020-03-23"),
        "2022_RATE_SHOCK": ("2022-01-03", "2022-12-30"),
    }
    rows = []
    for label, history, _, _, _ in results:
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
                "Sharpe": metrics["Sharpe"],
                "Calmar": metrics["Calmar"],
            })
    return pd.DataFrame(rows)


def _probability_diagnostics(data, profile):
    forecasts = walk_forward_event_probabilities(
        data,
        TAIL_CONFIG,
        event="DOWNSIDE",
        threshold=TAIL_RETURN_THRESHOLD,
        feature_columns=profile.columns,
    )
    monthly = data.loc[
        ~data.index.to_period("M").duplicated(),
        ["QQQ_Close"],
    ].copy()
    monthly = monthly.join(forecasts.loc[monthly.index])
    future = data["QQQ_Close"].shift(-TAIL_CONFIG.horizon_days)
    monthly["Outcome"] = (
        future.reindex(monthly.index) / monthly["QQQ_Close"] - 1.0
        <= TAIL_RETURN_THRESHOLD
    ).astype(float)
    monthly.loc[
        monthly.index > data.index[-TAIL_CONFIG.horizon_days - 1], "Outcome"
    ] = np.nan
    active = monthly.loc[
        (monthly["ModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & monthly["Outcome"].notna()
    ]
    brier = ((active["EventProbability"] - active["Outcome"]) ** 2).mean()
    base_brier = (
        (active["BaseEventProbability"] - active["Outcome"]) ** 2
    ).mean()
    return {
        "FeatureProfile": profile.name,
        "Features": len(profile.columns),
        "Forecasts": len(active),
        "StartDate": active.index.min(),
        "ActualEventRate": active["Outcome"].mean(),
        "BrierScore": brier,
        "BaseBrierScore": base_brier,
        "BrierSkillVsBase": 1.0 - brier / base_brier,
    }


def run_external_probability_validation(refresh_data=False):
    manifest = download_external_signals(refresh=refresh_data)
    results = [_run_static()]
    results.extend(_run_profile(profile) for profile in PROFILES)
    summary = pd.DataFrame([
        _summary(label, history, rebalances)
        for label, history, _, rebalances, _ in results
    ])
    windows = _performance_windows(results)
    diagnostic_rows = [
        _probability_diagnostics(result[4], profile)
        for result, profile in zip(results[1:], PROFILES)
    ]
    common_feature_data = results[1][4]
    diagnostic_rows.extend(
        _probability_diagnostics(common_feature_data, profile)
        for profile in EXTERNAL_ABLATION_PROFILES
    )
    diagnostics = pd.DataFrame(diagnostic_rows)
    manifest.to_csv(RESULT_DIR / "external_signal_manifest.csv", index=False)
    summary.to_csv(RESULT_DIR / "external_probability_summary.csv", index=False)
    windows.to_csv(RESULT_DIR / "external_probability_windows.csv", index=False)
    diagnostics.to_csv(
        RESULT_DIR / "external_probability_diagnostics.csv", index=False
    )
    return {
        "manifest": manifest,
        "summary": summary,
        "windows": windows,
        "diagnostics": diagnostics,
    }


if __name__ == "__main__":
    reports = run_external_probability_validation()
    print(reports["manifest"].to_string(index=False))
    print("\nSummary")
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nProbability diagnostics")
    print(reports["diagnostics"].to_string(index=False))
