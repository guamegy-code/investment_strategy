"""Ablate orthogonal feature groups for causal probability forecasts."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR
from strategy import STATIC_RETIREMENT_7030
from .multi_horizon_probability import (
    TAIL_CONFIG,
    TAIL_RETURN_THRESHOLD,
)
from .multi_market import MultiMarketBacktest, MULTI_MARKET_TICKERS
from .probabilistic_allocation import (
    FEATURE_COLUMNS,
    _summary,
    _window_summary,
    walk_forward_event_probabilities,
)
from .tail_risk_overlay import (
    PROFILES as TAIL_PROFILES,
    TailRiskOverlayStrategy,
)


SIGNAL_TICKERS = MULTI_MARKET_TICKERS

VOLATILITY_FEATURES = (
    "QQQ_VOL20_LOCAL",
    "QQQ_VOL_ACCELERATION",
    "QQQ_DOWNSIDE_VOL20",
    "QQQ_ATR20_PCT",
)
BREADTH_FEATURES = (
    "SPY_ROC20",
    "IWM_ROC20",
    "IWM_SPY_REL20",
    "SPY_QQQ_REL60",
    "BROAD_MARKET_CONFIRMATION",
)
CROSS_ASSET_FEATURES = (
    "BND_ROC20",
    "BIL_ROC20",
    "GLD_ROC20",
    "BND_BIL_REL20",
    "DEFENSIVE_RELATIVE_MOMENTUM",
)
REVERSAL_FEATURES = (
    "QQQ_REBOUND20",
    "QQQ_DRAWDOWN_SQUARED",
    "QQQ_ROC5_DRAWDOWN",
    "QQQ_OVERSOLD_DEPTH",
    "QQQ_VOLUME_Z20",
)


@dataclass(frozen=True)
class FeatureProfile:
    name: str
    columns: tuple[str, ...]


FEATURE_PROFILES = (
    FeatureProfile("BASE", FEATURE_COLUMNS),
    FeatureProfile("BASE_VOLATILITY", FEATURE_COLUMNS + VOLATILITY_FEATURES),
    FeatureProfile(
        "BASE_VOLATILITY_BREADTH",
        FEATURE_COLUMNS + VOLATILITY_FEATURES + BREADTH_FEATURES,
    ),
    FeatureProfile(
        "BASE_VOLATILITY_BREADTH_CROSS",
        FEATURE_COLUMNS
        + VOLATILITY_FEATURES
        + BREADTH_FEATURES
        + CROSS_ASSET_FEATURES,
    ),
    FeatureProfile(
        "ALL_WITH_REVERSAL_NONLINEAR",
        FEATURE_COLUMNS
        + VOLATILITY_FEATURES
        + BREADTH_FEATURES
        + CROSS_ASSET_FEATURES
        + REVERSAL_FEATURES,
    ),
)

VOLATILITY_ABLATION_PROFILES = tuple(
    FeatureProfile(f"BASE_PLUS_{column}", FEATURE_COLUMNS + (column,))
    for column in VOLATILITY_FEATURES
)
VALIDATION_PROFILES = (
    FEATURE_PROFILES[0],
    *VOLATILITY_ABLATION_PROFILES,
    *FEATURE_PROFILES[1:],
)

DIAGNOSTIC_WINDOWS = {
    "EARLY_MODEL_2005_2009": ("2005-01-01", "2009-12-31"),
    "EXPANSION_2010_2017": ("2010-01-01", "2017-12-31"),
    "RECENT_2018_PRESENT": ("2018-01-01", None),
}


def build_enhanced_features(data):
    """Create lag-safe features from prices available at the decision close."""
    frame = data.copy()
    qqq_close = frame["QQQ_Close"]
    qqq_returns = qqq_close.pct_change()
    vol20 = qqq_returns.rolling(20).std() * np.sqrt(252)
    frame["QQQ_VOL20_LOCAL"] = vol20
    frame["QQQ_VOL_ACCELERATION"] = vol20 / frame["QQQ_VOL60"] - 1.0
    frame["QQQ_DOWNSIDE_VOL20"] = (
        qqq_returns.clip(upper=0.0).pow(2).rolling(20).mean().pow(0.5)
        * np.sqrt(252)
    )
    true_range = pd.concat(
        (
            frame["QQQ_High"] - frame["QQQ_Low"],
            (frame["QQQ_High"] - qqq_close.shift(1)).abs(),
            (frame["QQQ_Low"] - qqq_close.shift(1)).abs(),
        ),
        axis=1,
    ).max(axis=1)
    frame["QQQ_ATR20_PCT"] = true_range.rolling(20).mean() / qqq_close

    frame["IWM_SPY_REL20"] = (
        (frame["IWM_Close"] / frame["SPY_Close"]).pct_change(20) * 100.0
    )
    frame["SPY_QQQ_REL60"] = (
        (frame["SPY_Close"] / qqq_close).pct_change(60) * 100.0
    )
    frame["BROAD_MARKET_CONFIRMATION"] = (
        (frame["SPY_Close"] > frame["SPY_EMA200"]).astype(float)
        + (frame["IWM_Close"] > frame["IWM_EMA200"]).astype(float)
    ) / 2.0

    frame["BND_BIL_REL20"] = (
        (frame["BND_Close"] / frame["BIL_Close"]).pct_change(20) * 100.0
    )
    frame["DEFENSIVE_RELATIVE_MOMENTUM"] = (
        frame[["BND_ROC20", "BIL_ROC20", "GLD_ROC20"]].max(axis=1)
        - frame["QQQ_ROC20"]
    )

    drawdown = frame["QQQ_DRAWDOWN120"]
    frame["QQQ_REBOUND20"] = qqq_close / qqq_close.rolling(20).min() - 1.0
    frame["QQQ_DRAWDOWN_SQUARED"] = drawdown.pow(2)
    frame["QQQ_ROC5_DRAWDOWN"] = frame["QQQ_ROC5"] * drawdown
    frame["QQQ_OVERSOLD_DEPTH"] = (
        (30.0 - frame["QQQ_RSI14"]).clip(lower=0.0) / 30.0
    )
    log_volume = np.log1p(frame["QQQ_Volume"].clip(lower=0.0))
    frame["QQQ_VOLUME_Z20"] = (
        (log_volume - log_volume.rolling(20).mean())
        / log_volume.rolling(20).std().replace(0.0, np.nan)
    )
    return frame


class EnhancedProbabilityBacktest(MultiMarketBacktest):
    """Load broad markets and attach a selected feature-set tail forecast."""

    def __init__(self, *args, feature_profile, **kwargs):
        self.feature_profile = feature_profile
        super().__init__(*args, **kwargs)

    def load_data(self):
        data = build_enhanced_features(super().load_data())
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


def _event_diagnostics(data, profile, event, threshold=0.0):
    forecasts = walk_forward_event_probabilities(
        data,
        TAIL_CONFIG,
        event=event,
        threshold=threshold,
        feature_columns=profile.columns,
    )
    monthly_mask = ~data.index.to_period("M").duplicated()
    monthly = forecasts.loc[monthly_mask].copy()
    close = data["QQQ_Close"]
    monthly["ForwardReturn"] = (
        close.shift(-TAIL_CONFIG.horizon_days).reindex(monthly.index)
        / close.reindex(monthly.index)
        - 1.0
    )
    if event == "UP":
        monthly["Outcome"] = (monthly["ForwardReturn"] > threshold).astype(float)
    else:
        monthly["Outcome"] = (monthly["ForwardReturn"] <= threshold).astype(float)
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
        "Event": "UP_21D" if event == "UP" else "LOSS_BELOW_5PCT_21D",
        "Features": len(profile.columns),
        "Forecasts": len(active),
        "MeanForecast": active["EventProbability"].mean(),
        "ActualEventRate": active["Outcome"].mean(),
        "BrierScore": brier,
        "BaseBrierScore": base_brier,
        "BrierSkillVsBase": 1.0 - brier / base_brier,
    }


def _tail_diagnostic_windows(data, profile):
    forecasts = walk_forward_event_probabilities(
        data,
        TAIL_CONFIG,
        event="DOWNSIDE",
        threshold=TAIL_RETURN_THRESHOLD,
        feature_columns=profile.columns,
    )
    monthly_mask = ~data.index.to_period("M").duplicated()
    monthly = forecasts.loc[monthly_mask].copy()
    close = data["QQQ_Close"]
    monthly["ForwardReturn"] = (
        close.shift(-TAIL_CONFIG.horizon_days).reindex(monthly.index)
        / close.reindex(monthly.index)
        - 1.0
    )
    monthly["Outcome"] = (
        monthly["ForwardReturn"] <= TAIL_RETURN_THRESHOLD
    ).astype(float)
    monthly.loc[
        monthly.index > data.index[-TAIL_CONFIG.horizon_days - 1], "Outcome"
    ] = np.nan
    active = monthly.loc[
        (monthly["ModelSamples"] >= TAIL_CONFIG.minimum_samples)
        & monthly["Outcome"].notna()
    ]
    rows = []
    for window, (start, end) in DIAGNOSTIC_WINDOWS.items():
        sample = active.loc[start:end]
        brier = ((sample["EventProbability"] - sample["Outcome"]) ** 2).mean()
        base_brier = (
            (sample["BaseEventProbability"] - sample["Outcome"]) ** 2
        ).mean()
        rows.append({
            "FeatureProfile": profile.name,
            "Window": window,
            "Forecasts": len(sample),
            "ActualEventRate": sample["Outcome"].mean(),
            "BrierScore": brier,
            "BaseBrierScore": base_brier,
            "BrierSkillVsBase": 1.0 - brier / base_brier,
        })
    return rows


def _run_static():
    backtest = MultiMarketBacktest(
        STATIC_RETIREMENT_7030(),
        data_dir=EXTENDED_DATA_DIR,
        tickers=SIGNAL_TICKERS,
    )
    history, trades, rebalances = backtest.run_all()
    return "STATIC_RETIREMENT_7030", history, trades, rebalances, backtest.data


def _run_profile(profile):
    # The 50% floor isolates forecast quality while limiting the cost of a
    # false positive; allocation aggressiveness is tested separately.
    strategy = TailRiskOverlayStrategy(TAIL_PROFILES[0])
    backtest = EnhancedProbabilityBacktest(
        strategy,
        data_dir=EXTENDED_DATA_DIR,
        tickers=SIGNAL_TICKERS,
        feature_profile=profile,
    )
    history, trades, rebalances = backtest.run_all()
    return (
        f"{profile.name}_TAIL_FLOOR_50",
        history,
        trades,
        rebalances,
        backtest.data,
    )


def run_enhanced_probability_validation():
    results = [_run_static()]
    results.extend(_run_profile(profile) for profile in VALIDATION_PROFILES)
    summary = pd.DataFrame([
        _summary(label, history, rebalances)
        for label, history, _, rebalances, _ in results
    ])
    windows = _window_summary(results)

    diagnostic_rows = []
    for result, profile in zip(results[1:], VALIDATION_PROFILES):
        data = result[4]
        diagnostic_rows.append(_event_diagnostics(data, profile, "UP"))
        diagnostic_rows.append(_event_diagnostics(
            data,
            profile,
            "DOWNSIDE",
            threshold=TAIL_RETURN_THRESHOLD,
        ))
    diagnostics = pd.DataFrame(diagnostic_rows)
    diagnostic_window_rows = []
    selected_names = {"BASE", "BASE_PLUS_QQQ_VOL_ACCELERATION"}
    for result, profile in zip(results[1:], VALIDATION_PROFILES):
        if profile.name in selected_names:
            diagnostic_window_rows.extend(
                _tail_diagnostic_windows(result[4], profile)
            )
    diagnostic_windows = pd.DataFrame(diagnostic_window_rows)

    summary.to_csv(
        RESULT_DIR / "enhanced_probability_summary.csv", index=False
    )
    windows.to_csv(
        RESULT_DIR / "enhanced_probability_windows.csv", index=False
    )
    diagnostics.to_csv(
        RESULT_DIR / "enhanced_probability_diagnostics.csv", index=False
    )
    diagnostic_windows.to_csv(
        RESULT_DIR / "enhanced_probability_diagnostic_windows.csv", index=False
    )
    return {
        "summary": summary,
        "windows": windows,
        "diagnostics": diagnostics,
        "diagnostic_windows": diagnostic_windows,
    }


if __name__ == "__main__":
    reports = run_enhanced_probability_validation()
    print(reports["summary"].to_string(index=False))
    print("\nWindows")
    print(reports["windows"].to_string(index=False))
    print("\nProbability diagnostics")
    print(reports["diagnostics"].to_string(index=False))
    print("\nSelected tail diagnostics by period")
    print(reports["diagnostic_windows"].to_string(index=False))
