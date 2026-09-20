"""Quick event study for strategy 26's drawdown redeployment threshold.

Each independent QQQ drawdown episode begins at a new closing high and ends
when that high is recovered.  For every episode, record the first close that
crosses each threshold from -6% through -16%, then measure forward returns and
the next 60 trading days' maximum adverse move.  This does not reproduce the
valuation WARNING filter; it tests whether -10% is a sensible generic point at
which to restore a temporarily reduced QQQ allocation.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import EXTENDED_DATA_DIR, RESULT_DIR


THRESHOLDS = tuple(-value / 100 for value in range(6, 17))


def run_event_study() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prices = pd.read_csv(
        EXTENDED_DATA_DIR / "QQQ.csv",
        parse_dates=["Date"],
        usecols=["Date", "Close"],
    ).dropna().sort_values("Date").reset_index(drop=True)
    close = prices["Close"].astype(float)
    peak = float(close.iloc[0])
    peak_date = prices.loc[0, "Date"]
    crossed: set[float] = set()
    episode = 0
    rows = []

    for index in range(1, len(prices)):
        value = float(close.iloc[index])
        date = prices.loc[index, "Date"]
        if value >= peak:
            if crossed:
                episode += 1
            peak = value
            peak_date = date
            crossed.clear()
            continue
        drawdown = value / peak - 1
        for threshold in THRESHOLDS:
            if threshold in crossed or drawdown > threshold:
                continue
            crossed.add(threshold)
            row = {
                "Episode": episode,
                "PeakDate": peak_date.date().isoformat(),
                "SignalDate": date.date().isoformat(),
                "Threshold": threshold,
                "SignalDrawdown": drawdown,
            }
            for horizon in (20, 60):
                end = min(index + horizon, len(prices) - 1)
                future = close.iloc[index + 1 : end + 1]
                row[f"Return{horizon}"] = (
                    float(close.iloc[end]) / value - 1 if end > index else float("nan")
                )
                row[f"MaxAdverse{horizon}"] = (
                    float((future / value - 1).min()) if len(future) else float("nan")
                )
            rows.append(row)

    events = pd.DataFrame(rows)
    summary = (
        events.groupby("Threshold", as_index=False)
        .agg(
            Events=("Episode", "count"),
            MeanReturn20=("Return20", "mean"),
            MedianReturn20=("Return20", "median"),
            WinRate20=("Return20", lambda values: float((values > 0).mean())),
            MeanReturn60=("Return60", "mean"),
            MedianReturn60=("Return60", "median"),
            WinRate60=("Return60", lambda values: float((values > 0).mean())),
            MedianMaxAdverse60=("MaxAdverse60", "median"),
            WorstMaxAdverse60=("MaxAdverse60", "min"),
        )
        .sort_values("Threshold", ascending=False)
    )
    events["Sample"] = events["SignalDate"].map(
        lambda value: "1999_2011" if value < "2012-01-01" else "2012_2026"
    )
    split = (
        events.loc[events["Threshold"].isin((-0.08, -0.09, -0.10, -0.11, -0.12))]
        .groupby(["Sample", "Threshold"], as_index=False)
        .agg(
            Events=("Episode", "count"),
            MedianReturn20=("Return20", "median"),
            WinRate20=("Return20", lambda values: float((values > 0).mean())),
            MedianReturn60=("Return60", "median"),
            WinRate60=("Return60", lambda values: float((values > 0).mean())),
            MedianMaxAdverse60=("MaxAdverse60", "median"),
        )
        .sort_values(["Sample", "Threshold"], ascending=[True, False])
    )
    events.to_csv(RESULT_DIR / "strategy26_dip_threshold_events.csv", index=False)
    summary.to_csv(RESULT_DIR / "strategy26_dip_threshold_summary.csv", index=False)
    split.to_csv(RESULT_DIR / "strategy26_dip_threshold_split.csv", index=False)
    return events, summary, split


if __name__ == "__main__":
    _, output, split = run_event_study()
    print(output.to_string(index=False))
    print("\nSplit samples")
    print(split.to_string(index=False))
