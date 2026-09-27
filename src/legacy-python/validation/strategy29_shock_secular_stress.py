"""Causal stress checks for strategy 29 and a long-bear guard candidate.

The 1999-2011 section tests QQQ signals only: the TDF proxy and valuation
series needed for a faithful 29 backtest do not extend to the dot-com peak.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, EXTENDED_DATA_DIR
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
START, END = "2012-01-03", "2026-07-31"
FILES = {
    "15": "15_band_7030_tdf_state_bil.yaml",
    "26": "26_band_7030_tdf_valuation_defense.yaml",
    "29": "29_band_7030_tdf_valuation_warning_dip_buyer.yaml",
}
WINDOWS = {
    "2020": ("2020-02-19", "2020-08-31"),
    "2021-2023": ("2021-11-19", "2023-01-19"),
    "2025": ("2025-02-19", "2025-04-08"),
}


def guarded_definition() -> dict:
    """One prespecified persistent-bear test; core 29 priorities stay first."""
    definition = deepcopy(load_strategy_definition(ROOT / "strategies" / FILES["29"]))
    definition["strategy"]["id"] += "-long-bear-research"
    definition["state"]["long_bear_guard"] = {
        "initial": "OFF",
        "rules": [
            {
                "when": (
                    "state.long_bear_guard == 'OFF' and "
                    "QQQ.close < QQQ.ema200 and QQQ.ema200_slope20 < 0 "
                    "and QQQ.drawdown120 <= -0.10"
                ),
                "set": "ON",
                "confirm": 3,
            },
            {
                "when": (
                    "state.long_bear_guard == 'ON' and "
                    "QQQ.close > QQQ.ema55 and QQQ.roc20 > 0"
                ),
                "set": "OFF",
                "confirm": 3,
            },
        ],
    }
    # BEAR, structural bear, DEFENSE, and RECOVERY remain above this rule.
    definition["target"].insert(
        4,
        {
            "when": "state.long_bear_guard == 'ON'",
            "weights": {"QQQ": "50%", "TDF2050_PROXY": "30%", "BIL": "20%"},
        },
    )
    return definition


def run(definition: dict):
    strategy = DeclarativeStrategy(deepcopy(definition))
    history, trades, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        start_date=START,
        end_date=END,
    ).run_all()
    return history, trades, rebalances


def summary(history: pd.DataFrame, trades: list, rebalances: list) -> dict:
    value = history["Portfolio"]
    drawdown = value / value.cummax() - 1
    trough = drawdown.idxmin()
    peak = value.loc[:trough].idxmax()
    perf = Performance(history)
    result = {
        "CAGR": perf.cagr(),
        "MDD": drawdown.min(),
        "Peak": str(peak.date()),
        "Trough": str(trough.date()),
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "2023Peak": str(value.loc[:"2023-01-19"].idxmax().date()),
        "2023TroughDD": float(drawdown.loc["2023-01-19"]),
    }
    for label, (start, end) in WINDOWS.items():
        period = value.loc[start:end]
        result[f"{label}Return"] = float(period.iloc[-1] / period.iloc[0] - 1)
        result[f"{label}MDD"] = float((period / period.cummax() - 1).min())
    return result


def extended_qqq_events() -> dict:
    qqq = pd.read_csv(EXTENDED_DATA_DIR / "QQQ.csv", parse_dates=["Date"])
    qqq = qqq.set_index("Date").sort_index()
    close = qqq["Close"]
    shock = (close / close.rolling(20).max() - 1 <= -0.05) & (qqq["ROC5"] <= -4)
    secular = (
        (close < qqq["EMA200"])
        & (qqq["EMA200_SLOPE20"] < 0)
        & (qqq["DRAWDOWN120"] <= -0.10)
    )
    events = {}
    for label, start, end in (
        ("DOTCOM", "2000-03-27", "2002-10-07"),
        ("GFC", "2007-10-31", "2009-03-09"),
        ("COVID", "2020-02-19", "2020-03-23"),
        ("2022", "2021-11-19", "2023-01-19"),
        ("2025", "2025-02-19", "2025-04-08"),
    ):
        episode = qqq.loc[start:end]
        peak_date = close.loc[start:end].idxmax()
        first_shock = shock.loc[start:end]
        first_secular = secular.loc[start:end]
        events[label] = {
            "shock": str(first_shock[first_shock].index[0].date()) if first_shock.any() else None,
            "secular": str(first_secular[first_secular].index[0].date()) if first_secular.any() else None,
            "trough": str(episode["Close"].idxmin().date()),
            "peak_to_trough": float(episode["Close"].min() / close.loc[peak_date] - 1),
        }
        if first_secular.any():
            date = first_secular[first_secular].index[0]
            events[label]["secular_peak_dd"] = float(close.loc[date] / close.loc[peak_date] - 1)
    return events


def main():
    for label, filename in FILES.items():
        h, t, r = run(load_strategy_definition(ROOT / "strategies" / filename))
        print(label, summary(h, t, r))
    h, t, r = run(guarded_definition())
    print("29_LONG_BEAR_GUARD", summary(h, t, r))
    print("QQQ_EVENTS", extended_qqq_events())
    print("DATE_RANGE", pd.read_csv(DATA_DIR / "TDF2050_PROXY.csv", usecols=["Date"])["Date"].agg(["min", "max"]).to_dict())


if __name__ == "__main__":
    main()
