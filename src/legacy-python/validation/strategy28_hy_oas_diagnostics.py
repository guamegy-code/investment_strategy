"""Credit-only warning dates, separate from the Strategy 28 trading overlay."""

import sys
from pathlib import Path

import pandas as pd

MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from downloader import (  # noqa: E402
    _align_macro_to_qqq_sessions, _load_fred_series, _load_hy_oas_observations,
)
from config import DATA_DIR, RESULT_DIR  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
EVENTS = {
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "COVID": ("2020-02-19", "2020-08-31"),
    "2022_BEAR": ("2021-11-19", "2023-01-19"),
    "2025": ("2025-02-19", "2025-04-30"),
}


def build_alerts(qqq_dates, baa, hy, lag=1):
    b = _align_macro_to_qqq_sessions(baa, qqq_dates, lag_sessions=lag)
    h = _align_macro_to_qqq_sessions(hy, qqq_dates, lag_sessions=lag)
    return pd.DataFrame({
        "BAA10Y": b.ge(2.0) & b.diff(20).ge(0.30),
        "HY_OAS": h.ge(4.0) & h.diff(20).ge(0.50),
    }, index=qqq_dates)


def main():
    qqq_dates = pd.DatetimeIndex(
        pd.read_csv(DATA_DIR / "QQQ.csv", usecols=["Date"], parse_dates=["Date"]).Date
    )
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    rows = []
    for lag in (0, 1, 2):
        alerts = build_alerts(qqq_dates, baa, hy, lag)
        for event, (start, end) in EVENTS.items():
            window = alerts.loc[start:end]
            for indicator in alerts.columns:
                hits = window.index[window[indicator]]
                rows.append({
                    "LagSessions": lag, "Event": event, "Indicator": indicator,
                    "FirstAlert": str(hits.min().date()) if len(hits) else "",
                    "AlertSessions": len(hits),
                })
    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(
        RESULT_DIR / "strategy28_hy_oas_credit_alerts.csv", index=False
    )


if __name__ == "__main__":
    main()
