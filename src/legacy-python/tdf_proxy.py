"""Build a reproducible long-history proxy for KODEX TDF2050 Active.

The proxy holds 74.2% global equities and 25.8% broad bonds. The equity sleeve
uses a 55/45 SPY/VXUS split validated on the ETF's available history. Component
observations are delayed one session because a Korean ETF
closing price can only reflect the preceding US market close. This remains a
research index, not a reconstruction of the fund's active historical holdings.
"""

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from config import DATA_DIR
from indicators import Indicator


TDF_PROXY_TICKER = "TDF2050_PROXY"
TDF_PROXY_COMPONENT_WEIGHTS = {
    "SPY": 0.4081,
    "VXUS": 0.3339,
    "BND": 0.2580,
}
TDF_PROXY_OBSERVATION_LAG = 1


def _validate_weights(weights: Mapping[str, float]) -> dict[str, float]:
    normalized = {str(ticker): float(weight) for ticker, weight in weights.items()}
    if not normalized or any(weight <= 0 for weight in normalized.values()):
        raise ValueError("TDF proxy component weights must be positive")
    if not np.isclose(sum(normalized.values()), 1.0):
        raise ValueError("TDF proxy component weights must sum to 100%")
    return normalized


def build_proxy_frame(
    components: Mapping[str, pd.DataFrame],
    weights: Mapping[str, float] = TDF_PROXY_COMPONENT_WEIGHTS,
    initial_value: float = 100.0,
    observation_lag: int = TDF_PROXY_OBSERVATION_LAG,
) -> pd.DataFrame:
    """Return monthly rebalanced synthetic OHLC data for the supplied assets."""
    weights = _validate_weights(weights)
    if set(components) != set(weights):
        raise ValueError("TDF proxy components must match the configured weights")

    required_columns = ("Open", "High", "Low", "Close")
    aligned = {}
    for ticker in weights:
        frame = components[ticker].copy()
        missing = sorted(set(required_columns) - set(frame.columns))
        if missing:
            raise ValueError(f"{ticker} is missing columns: {', '.join(missing)}")
        frame.index = pd.to_datetime(frame.index).tz_localize(None)
        aligned[ticker] = frame.loc[:, required_columns].sort_index()

    if not isinstance(observation_lag, int) or observation_lag < 0:
        raise ValueError("TDF proxy observation lag must be a non-negative integer")
    combined = pd.concat(aligned, axis=1, join="inner").dropna()
    if observation_lag:
        combined = combined.shift(observation_lag).dropna()
    if combined.empty:
        raise ValueError("TDF proxy components have no overlapping observations")

    target = pd.Series(weights, dtype=float)
    current_weights = target.copy()
    previous_component_close = None
    previous_proxy_close = float(initial_value)
    previous_month = None
    rows = []

    for date, observation in combined.iterrows():
        month = date.to_period("M")
        if previous_component_close is None:
            rows.append((date, initial_value, initial_value, initial_value, initial_value, 0.0))
            previous_component_close = pd.Series(
                {ticker: observation[(ticker, "Close")] for ticker in weights}
            )
            previous_month = month
            continue

        if month != previous_month:
            current_weights = target.copy()

        factors = {
            field: pd.Series(
                {
                    ticker: observation[(ticker, field)]
                    / previous_component_close[ticker]
                    for ticker in weights
                }
            )
            for field in required_columns
        }
        proxy_values = {
            field: previous_proxy_close * float((current_weights * factor).sum())
            for field, factor in factors.items()
        }
        close = proxy_values["Close"]
        open_price = proxy_values["Open"]
        high = max(proxy_values["High"], open_price, close)
        low = min(proxy_values["Low"], open_price, close)
        rows.append((date, close, high, low, open_price, 0.0))

        close_contributions = current_weights * factors["Close"]
        current_weights = close_contributions / close_contributions.sum()
        previous_component_close = pd.Series(
            {ticker: observation[(ticker, "Close")] for ticker in weights}
        )
        previous_proxy_close = close
        previous_month = month

    return pd.DataFrame(
        rows,
        columns=("Date", "Close", "High", "Low", "Open", "Volume"),
    ).set_index("Date")


def build_tdf2050_proxy(data_dir=DATA_DIR, output_path=None) -> pd.DataFrame:
    """Load component CSVs, add indicators, and persist the proxy index."""
    data_dir = Path(data_dir)
    components = {
        ticker: pd.read_csv(
            data_dir / f"{ticker}.csv", index_col="Date", parse_dates=True
        )
        for ticker in TDF_PROXY_COMPONENT_WEIGHTS
    }
    frame = Indicator.add_indicators(build_proxy_frame(components))
    output = Path(output_path or data_dir / f"{TDF_PROXY_TICKER}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output)
    return frame


if __name__ == "__main__":
    generated = build_tdf2050_proxy()
    print(
        f"Created {TDF_PROXY_TICKER}: {generated.index.min().date()} ~ "
        f"{generated.index.max().date()} ({len(generated):,} rows)"
    )
