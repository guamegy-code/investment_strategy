"""Describe fixed downside signal hypotheses; this is not a portfolio backtest.

Read locally cached public daily data. Cboe observations receive one US session
of delay; ETF price/volume signals are observed at the close. No thresholds are
fitted, no orders are simulated, and scenario labels never enter the signals.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from config import RESEARCH_DATA_DIR
from result_paths import research_result_path


DATA_DIRECTORY = RESEARCH_DATA_DIR / "downside_signals"
SIGNALS = ("short_vol_stress", "vol_of_vol_stress", "internal_weakness", "selling_volume")
WINDOWS = {
    "2015_correction": ("2015-08-01", "2015-10-30"),
    "2018_q4": ("2018-10-01", "2018-12-31"),
    "2020_crash_rebound": ("2020-02-01", "2020-06-30"),
    "2020_september": ("2020-09-01", "2020-10-30"),
    "2022_bear": ("2022-01-01", "2022-12-30"),
    "2023_rally": ("2023-01-01", "2023-12-29"),
    "2025_correction": ("2025-02-01", "2025-05-30"),
}


def load_inputs(directory=DATA_DIRECTORY):
    frames, inventory = {}, []
    for symbol in ("VIX", "VIX9D", "VIX3M", "VVIX", "SKEW", "QQQ", "SPY", "RSP", "IWM", "SMH"):
        path = Path(directory) / f"{symbol}.csv"
        frame = pd.read_csv(path)
        date_column = "DATE" if "DATE" in frame else "Date"
        dates = pd.to_datetime(frame.pop(date_column), errors="raise")
        frame.index = pd.DatetimeIndex(dates, name="Date")
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f"Unsorted or duplicated dates: {symbol}")
        frame = frame.rename(columns={"CLOSE": "Close", symbol: "Close"})
        frames[symbol] = frame
        inventory.append({
            "Symbol": symbol, "Rows": len(frame),
            "Start": str(frame.index.min().date()), "End": str(frame.index.max().date()),
            "SHA256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "CachePath": str(path.relative_to(RESEARCH_DATA_DIR)),
        })
    return frames, inventory


def build_features(frames, *, vol_lag=1):
    if vol_lag < 1:
        raise ValueError("Volatility observations require at least one session of lag")
    calendar = frames["QQQ"].index
    # Reindex before shifting: one row means one QQQ trading session.
    price = {s: frames[s].Close.reindex(calendar) for s in ("QQQ", "SPY", "RSP", "IWM")}
    vol = {s: frames[s].Close.reindex(calendar).shift(vol_lag) for s in ("VIX", "VIX9D", "VIX3M", "VVIX")}
    qqq = price["QQQ"]
    frame = pd.DataFrame(index=calendar)
    frame["qqq_return5"] = qqq.pct_change(5, fill_method=None)
    frame["qqq_ema20"] = qqq.ewm(span=20, adjust=False, min_periods=20).mean()
    frame["qqq_ema55"] = qqq.ewm(span=55, adjust=False, min_periods=55).mean()
    frame["vix9d_vix"] = vol["VIX9D"] / vol["VIX"]
    frame["vix_vix3m"] = vol["VIX"] / vol["VIX3M"]
    frame["vvix_change5"] = vol["VVIX"].pct_change(5, fill_method=None)
    frame["vvix_prior_q80"] = vol["VVIX"].rolling(252, min_periods=252).quantile(.8).shift(1)
    frame["rsp_spy_return20"] = (price["RSP"] / price["SPY"]).pct_change(20, fill_method=None)
    frame["iwm_spy_return20"] = (price["IWM"] / price["SPY"]).pct_change(20, fill_method=None)
    volume = frames["QQQ"].Volume.reindex(calendar)
    negative = qqq.diff().lt(0).where(qqq.diff().notna())
    frame["down_volume_share5"] = (volume * negative).rolling(5).sum() / volume.rolling(5).sum()
    frame["eligible"] = frame.notna().all(axis=1) & vol["VVIX"].notna()
    # These are pre-specified screening hypotheses, not selected strategy rules.
    frame["short_vol_stress"] = (frame.vix9d_vix > 1.05) & (frame.vix_vix3m > 1) & (frame.qqq_return5 < -.03)
    frame["vol_of_vol_stress"] = (vol["VVIX"] > frame.vvix_prior_q80) & (frame.vvix_change5 > .10) & (qqq < frame.qqq_ema20)
    frame["internal_weakness"] = (frame.rsp_spy_return20 < -.02) & (frame.iwm_spy_return20 < -.03) & (qqq < frame.qqq_ema55)
    frame["selling_volume"] = (frame.down_volume_share5 > .65) & (frame.qqq_return5 < -.03)
    return frame


def summarize(frame, start, end):
    sample = frame.loc[start:end]
    rows = []
    for signal in SIGNALS:
        active = sample[signal] & sample.eligible
        # Count starts inside this window, including an episode crossing its start.
        starts = active & ~active.shift(1, fill_value=False)
        rows.append({
            "Signal": signal, "EligibleDays": int(sample.eligible.sum()),
            "ActiveDays": int(active.sum()), "Episodes": int(starts.sum()),
            "FirstActive": str(active[active].index[0].date()) if active.any() else "",
        })
    return rows


def main():
    frames, inventory = load_inputs()
    features = build_features(frames)
    # Recomputing a prefix must not depend on prices appended afterward.
    for cutoff in ("2018-12-31", "2020-03-31", "2022-06-30"):
        prefix = build_features({key: value.loc[:cutoff] for key, value in frames.items()})
        pd.testing.assert_frame_equal(prefix, features.loc[:cutoff])
    rows = []
    for name, (start, end) in WINDOWS.items():
        rows.extend({"Window": name, **row} for row in summarize(features, start, end))
    research_result_path("downside_source_inventory.json").write_text(
        json.dumps(inventory, indent=2), encoding="utf-8"
    )
    features.loc["2012-01-01":].to_csv(research_result_path("downside_signal_screen.csv"))
    report = pd.DataFrame(rows)
    report.to_csv(research_result_path("downside_signal_scenarios.csv"), index=False)
    print(report.to_string(index=False))
    print("Prefix invariance verified at three cutoffs. No portfolio performance was calculated.")


if __name__ == "__main__":
    main()
