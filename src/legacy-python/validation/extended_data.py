"""Build a proxy-extended data set for pre-ETF stress testing.

The regular data directory remains untouched.  Before each ETF's inception,
BND is represented by VBMFX, GLD by gold futures, and BIL by a synthetic
13-week Treasury-bill total-return index derived from ^IRX.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

from config import EXTENDED_DATA_DIR, EXTENDED_START_DATE, PROJECT_ROOT
from indicators import Indicator


ASSETS = ("QQQ", "BND", "GLD", "BIL")
PROXIES = {
    "BND": "VBMFX",
    "GLD": "GC=F",
    "BIL": "^IRX",
}

# Keep yfinance's SQLite cookie/time-zone cache inside the writable workspace.
yf.set_tz_cache_location(str(PROJECT_ROOT / ".yf-cache"))


def _download(ticker, start=EXTENDED_START_DATE):
    frame = yf.download(
        ticker,
        start=start,
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
    return frame[["Close", "High", "Low", "Open", "Volume"]].dropna(
        subset=["Close"]
    )


def _cash_total_return(yields):
    """Convert annualized percentage T-bill yields into a daily price index."""
    annual_yield = yields["Close"].clip(lower=0) / 100.0
    daily_return = (1.0 + annual_yield.shift(1).fillna(annual_yield.iloc[0])) ** (
        1.0 / 252.0
    ) - 1.0
    close = 100.0 * (1.0 + daily_return).cumprod()
    frame = pd.DataFrame(index=yields.index)
    for column in ("Close", "High", "Low", "Open"):
        frame[column] = close
    frame["Volume"] = 0.0
    return frame


def _stitch(actual, proxy):
    """Use proxy before actual inception and preserve continuity at the seam."""
    common = actual.index.intersection(proxy.index)
    if common.empty:
        raise ValueError("Actual and proxy histories do not overlap")
    seam = common.min()
    ratio = actual.at[seam, "Close"] / proxy.at[seam, "Close"]
    scaled = proxy.copy()
    for column in ("Close", "High", "Low", "Open"):
        scaled[column] *= ratio
    before = scaled.loc[scaled.index < seam]
    combined = pd.concat([before, actual.loc[actual.index >= seam]])
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    return combined, seam


def _align_to_calendar(frame, calendar):
    aligned = frame.reindex(calendar)
    previous_close = aligned["Close"].ffill()
    for column in ("Open", "High", "Low", "Close"):
        aligned[column] = aligned[column].fillna(previous_close)
    aligned["Volume"] = aligned["Volume"].fillna(0.0)
    return aligned.dropna(subset=["Close"])


def _proxy_quality(asset, actual, proxy):
    joined = pd.concat(
        [actual["Close"].pct_change(), proxy["Close"].pct_change()],
        axis=1,
        keys=["Actual", "Proxy"],
        join="inner",
    ).dropna()
    if joined.empty:
        return {"Asset": asset, "OverlapDays": 0}
    difference = joined["Proxy"] - joined["Actual"]
    return {
        "Asset": asset,
        "Proxy": PROXIES[asset],
        "OverlapStart": joined.index.min(),
        "OverlapEnd": joined.index.max(),
        "OverlapDays": len(joined),
        "DailyReturnCorrelation": joined.corr().iloc[0, 1],
        "AnnualizedTrackingError": difference.std() * np.sqrt(252),
        "AnnualizedReturnGap": difference.mean() * 252,
    }


def build_extended_data(output_dir=EXTENDED_DATA_DIR):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    actual = {ticker: _download(ticker) for ticker in ASSETS}
    raw_proxy = {
        "BND": _download("VBMFX"),
        "GLD": _download("GC=F"),
        "BIL": _cash_total_return(_download("^IRX")),
    }

    combined = {"QQQ": actual["QQQ"]}
    seams = {"QQQ": actual["QQQ"].index.min()}
    quality = []
    for ticker in ("BND", "GLD", "BIL"):
        combined[ticker], seams[ticker] = _stitch(
            actual[ticker], raw_proxy[ticker]
        )
        quality.append(_proxy_quality(ticker, actual[ticker], raw_proxy[ticker]))

    # Calculate indicators over each asset's full available history before the
    # backtest's inner join.  In particular, QQQ gets a 1999-2000 warm-up even
    # though investable history begins with gold futures in August 2000.
    calendar = actual["QQQ"].loc[EXTENDED_START_DATE:].index
    starts = {}
    for ticker, frame in combined.items():
        ready = _align_to_calendar(frame, calendar)
        starts[ticker] = ready.index.min()
        ready = Indicator.add_indicators(ready)
        ready.to_csv(output_dir / f"{ticker}.csv")

    manifest = pd.DataFrame([
        {
            "Asset": ticker,
            "PreInceptionSource": PROXIES.get(ticker, ticker),
            "ActualSource": ticker,
            "ActualStart": seams[ticker],
            "ExtendedStart": starts[ticker],
            "ExtendedEnd": calendar.max(),
        }
        for ticker in ASSETS
    ])
    manifest.to_csv(output_dir / "manifest.csv", index=False)
    pd.DataFrame(quality).to_csv(output_dir / "proxy_quality.csv", index=False)
    return manifest, pd.DataFrame(quality)


if __name__ == "__main__":
    built_manifest, built_quality = build_extended_data()
    print(built_manifest.to_string(index=False))
    print("\nProxy overlap diagnostics")
    print(built_quality.to_string(index=False))
