"""Research-only short/medium credit spread changes and unusual moves.

Produces event timing summaries, not orders or a modified Strategy 30.
All signals use a one-QQQ-session lag by default.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import DATA_DIR, RESULT_DIR  # noqa: E402
from downloader import (  # noqa: E402
    _align_macro_to_qqq_sessions, _load_fred_series, _load_hy_oas_observations,
)

ROOT = Path(__file__).resolve().parents[3]
EVENTS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "COVID": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2021-11-19", "2023-01-19"),
    "2025": ("2025-02-19", "2025-08-31"),
}
QUIET_WINDOWS = {
    "2017": ("2017-01-01", "2017-12-31"),
    "2019_H2": ("2019-05-01", "2019-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
}


def credit_features(observations: pd.Series, qqq_dates, *, lag: int = 1) -> pd.DataFrame:
    """Changes are percentage-point differences; z baselines use past values only."""
    level = _align_macro_to_qqq_sessions(observations, qqq_dates, lag_sessions=lag)
    frame = pd.DataFrame({"level": level}, index=qqq_dates)
    for days in (5, 20):
        change = level.diff(days)
        prior = change.shift(1).rolling(252, min_periods=200)
        deviation = prior.std()
        frame[f"diff{days}"] = change
        frame[f"diff{days}_z"] = ((change - prior.mean()) / deviation).where(
            deviation.gt(0)
        )
    return frame


def first_and_count(signal: pd.Series, start: str, end: str) -> tuple[str, int]:
    matched = signal.loc[start:end]
    dates = matched.index[matched.fillna(False)]
    return (str(dates.min().date()) if len(dates) else "", len(dates))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lag", type=int, default=1)
    args = parser.parse_args()
    dates = pd.DatetimeIndex(
        pd.read_csv(DATA_DIR / "QQQ.csv", usecols=["Date"], parse_dates=["Date"]).Date
    )
    sources = {
        "BAA10Y": _load_fred_series(ROOT / "tmp/BAA10Y.csv"),
        "HY_OAS": _load_hy_oas_observations(),
    }
    rows = []
    for indicator, observations in sources.items():
        features = credit_features(observations, dates, lag=args.lag)
        # Fixed descriptive cutoffs. The z-scores compare today's change with
        # the preceding 252 sessions and do not use future observations.
        signals = {
            "widen_5d_z2": features["diff5_z"].ge(2),
            "widen_20d_z2": features["diff20_z"].ge(2),
            "narrow_5d_zminus2": features["diff5_z"].le(-2),
            "narrow_20d_zminus2": features["diff20_z"].le(-2),
        }
        if indicator == "BAA10Y":
            signals["strategy30_entry_credit_only"] = (
                features.level.ge(2.0) & features.diff20.ge(0.30)
            )
        else:
            signals["prior_hy_entry_credit_only"] = (
                features.level.ge(4.0) & features.diff20.ge(0.50)
            )
        for category, windows in (("event", EVENTS), ("quiet_reference", QUIET_WINDOWS)):
            for name, (start, end) in windows.items():
                for signal_name, signal in signals.items():
                    first, count = first_and_count(signal, start, end)
                    rows.append({
                        "Indicator": indicator, "WindowType": category,
                        "Window": name, "Signal": signal_name,
                        "LagSessions": args.lag, "FirstDate": first,
                        "SignalSessions": count,
                    })
    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(
        RESULT_DIR / f"strategy30_credit_change_lag{args.lag}.csv", index=False
    )


if __name__ == "__main__":
    main()
