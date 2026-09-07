"""Research a price/rate/dividend composite overlay for strategy 21.

Signals are calculated from information available at each date and sampled at
month-end for use from the next month.  The script deliberately keeps stage 2
and 3 allocations unchanged so valuation never blocks deep-dip purchases.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yfinance as yf


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))
CACHE = MODULE_DIR.parents[1] / "tmp" / "buy_3dip_composite_scores.csv"

from buy_3dip_parameter_search import _trade, load_prices  # noqa: E402


BASE_TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class Profile:
    score: str
    mild_entry: float
    high_entry: float
    mild_stage0_cut: float
    mild_stage1_cut: float
    high_stage0_cut: float
    high_stage1_cut: float


def _history(ticker: str, *, auto_adjust: bool) -> pd.DataFrame:
    frame = yf.Ticker(ticker).history(
        start="1999-01-01", auto_adjust=auto_adjust, actions=True
    )
    if frame.empty:
        raise RuntimeError(f"{ticker}: no Yahoo history")
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame.sort_index()


def _rolling_percentile(series: pd.Series) -> pd.Series:
    return series.rolling(1260, min_periods=504).apply(
        lambda values: float(np.count_nonzero(values <= values[-1]) / len(values)),
        raw=True,
    )


def load_scores() -> pd.DataFrame:
    if CACHE.is_file():
        cached = pd.read_csv(CACHE, index_col="Date", parse_dates=True)
        print(f"loaded cached scores rows={len(cached)}", flush=True)
        return cached
    qqq_raw = _history("QQQ", auto_adjust=False)
    print(f"loaded QQQ rows={len(qqq_raw)}", flush=True)
    qqq = qqq_raw["Adj Close"].rename("QQQ")
    spy = _history("SPY", auto_adjust=True)["Close"].rename("SPY")
    print(f"loaded SPY rows={len(spy)}", flush=True)
    bil = _history("BIL", auto_adjust=True)["Close"].rename("BIL")
    print(f"loaded BIL rows={len(bil)}", flush=True)
    data = pd.concat([qqq, spy, bil], axis=1, sort=True).ffill()

    relative = np.log(data.QQQ / data.SPY)
    relative_premium = relative - relative.rolling(756, min_periods=504).median()
    absolute_stretch = data.QQQ / data.QQQ.rolling(756, min_periods=504).median() - 1
    cash_yield = (data.BIL / data.BIL.shift(63)) ** 4 - 1
    dividend_yield = (
        qqq_raw["Dividends"].rolling("365D").sum() / qqq_raw["Close"]
    ).reindex(data.index).ffill()

    components = pd.DataFrame(
        {
            "relative": _rolling_percentile(relative_premium),
            "stretch": _rolling_percentile(absolute_stretch),
            "rate": _rolling_percentile(cash_yield),
            "dividend": 1 - _rolling_percentile(dividend_yield),
        }
    )
    weights = {
        "all4": {"relative": .25, "stretch": .25, "rate": .35, "dividend": .15},
        "three": {"relative": .30, "stretch": .30, "rate": .40},
        "no_relative": {"stretch": .50, "rate": .35, "dividend": .15},
        "no_stretch": {"relative": .50, "rate": .35, "dividend": .15},
        "no_rate": {"relative": .425, "stretch": .425, "dividend": .15},
        "price_only": {"relative": .50, "stretch": .50},
    }
    scores = pd.DataFrame(index=components.index)
    for name, parts in weights.items():
        scores[name] = sum(components[field] * weight for field, weight in parts.items()) * 100

    # A month-end value is usable from the next calendar month.  Daily rows in
    # that month all carry the same prior-month score.
    monthly = scores.resample("ME").last()
    monthly.index = monthly.index + pd.offsets.MonthBegin(1)
    daily = monthly.reindex(pd.date_range(monthly.index.min(), monthly.index.max(), freq="D")).ffill()
    daily.index.name = "Date"
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(CACHE)
    print(f"calculated scores rows={len(daily)}", flush=True)
    return daily


def install_local_strategy_score(scores: pd.DataFrame) -> None:
    """Persist the selected four-factor score for strategy 22's DSL replay."""
    path = MODULE_DIR.parents[1] / "data" / "QQQ.csv"
    qqq = pd.read_csv(path, index_col="Date", parse_dates=True)
    qqq["VALUATION_SCORE"] = scores["all4"].reindex(qqq.index)
    qqq.to_csv(path)


def _overlay_level(score: float, old: int, profile: Profile) -> int:
    if not np.isfinite(score):
        return old
    mild_exit = profile.mild_entry - 5
    high_exit = profile.high_entry - 10
    if old == 2:
        if score < mild_exit:
            return 0
        if score < high_exit:
            return 1
        return 2
    if old == 1:
        if score >= profile.high_entry:
            return 2
        if score < mild_exit:
            return 0
        return 1
    if score >= profile.high_entry:
        return 2
    if score >= profile.mild_entry:
        return 1
    return 0


def _target(stage: int, level: int, profile: Profile) -> float:
    target = BASE_TARGETS[stage]
    if stage >= 2 or level == 0:
        return target
    if level == 1:
        return target - (profile.mild_stage0_cut if stage == 0 else profile.mild_stage1_cut)
    return target - (profile.high_stage0_cut if stage == 0 else profile.high_stage1_cut)


def simulate(data: pd.DataFrame, profile: Profile | None) -> dict[str, float]:
    q_open, q_close = data["QQQ_Open"].to_numpy(float), data["QQQ_Close"].to_numpy(float)
    b_open, b_close = data["BIL_Open"].to_numpy(float), data["BIL_Close"].to_numpy(float)
    score = data[profile.score].to_numpy(float) if profile else np.full(len(data), np.nan)
    values = np.empty(len(data))
    cash, q_shares, b_shares = 1.0, 0.0, 0.0
    stage, level, peak, pending, transitions = 0, 0, 0.0, True, 0
    prior_month = None
    for index, date in enumerate(data.index):
        if pending:
            target = _target(stage, level, profile) if profile else BASE_TARGETS[stage]
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * target / q_open[index] - q_shares
            b_delta = total * (1 - target) / b_open[index] - b_shares
            if q_delta < 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending = False

        month = date.to_period("M")
        if profile and month != prior_month:
            new_level = _overlay_level(score[index], level, profile)
            if new_level != level:
                level = new_level
                pending = True
                transitions += 1
            prior_month = month

        old_stage, price = stage, q_close[index]
        if stage == 3 and price >= peak * (1 - 0.175): stage = 2
        elif stage == 2 and price <= peak * (1 - 0.325): stage = 3
        elif stage == 2 and price >= peak * (1 - 0.085): stage = 1
        elif stage == 1 and price <= peak * (1 - 0.20): stage = 2
        elif stage == 1 and price >= peak * 1.075: stage = 0
        elif stage == 0 and peak > 0 and price <= peak * 0.90: stage = 1
        changed = stage != old_stage
        if peak == 0: peak = price
        elif changed and stage == 0: peak = price
        elif stage == 0 and price > peak: peak = price
        if changed:
            pending = True
            transitions += 1
        values[index] = cash + q_shares * q_close[index] + b_shares * b_close[index]
    years = (data.index[-1] - data.index[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    mdd = float((values / np.maximum.accumulate(values) - 1).min())
    return {"CAGR": cagr, "MDD": mdd, "Calmar": cagr / abs(mdd), "Transitions": transitions}


def profiles(score_filter: str | None = None):
    for score in ("all4", "three", "no_relative", "no_stretch", "no_rate", "price_only"):
        if score_filter and score != score_filter:
            continue
        for mild_entry in (60.0, 65.0, 70.0):
            for high_entry in (75.0, 80.0, 85.0):
                if high_entry <= mild_entry:
                    continue
                for mild_cuts in ((.05, .03), (.10, .03)):
                    for high_cuts in ((.10, .05), (.15, .05), (.15, .10)):
                        if high_cuts[0] < mild_cuts[0] or high_cuts[1] < mild_cuts[1]:
                            continue
                        yield Profile(score, mild_entry, high_entry, *mild_cuts, *high_cuts)


def main() -> None:
    score_filter = sys.argv[1] if len(sys.argv) > 1 else None
    scores = load_scores()
    install_local_strategy_score(scores)
    data = load_prices().join(scores, how="left")
    baseline = simulate(data, None)
    development = data.loc[:"2020-12-31"]
    recent = data.loc["2021-01-01":]
    baseline_dev, baseline_recent = simulate(development, None), simulate(recent, None)
    rows = []
    for index, profile in enumerate(profiles(score_filter), start=1):
        full = simulate(data, profile)
        dev = simulate(development, profile)
        rec = simulate(recent, profile)
        rows.append({
            **profile.__dict__, **full,
            "Dev_CAGR": dev["CAGR"], "Dev_MDD": dev["MDD"],
            "Recent_CAGR": rec["CAGR"], "Recent_MDD": rec["MDD"],
        })
        if index % 50 == 0:
            print(f"searched={index}", flush=True)
    output = pd.DataFrame(rows)
    eligible = output.loc[
        (output.CAGR > baseline["CAGR"])
        & (output.MDD > baseline["MDD"])
        & (output.Dev_CAGR >= baseline_dev["CAGR"] - .001)
        & (output.Dev_MDD >= baseline_dev["MDD"] - .0025)
        & (output.Recent_CAGR > baseline_recent["CAGR"])
        & (output.Recent_MDD > baseline_recent["MDD"])
    ].copy()
    print("baseline", baseline)
    print("development", baseline_dev)
    print("recent", baseline_recent)
    print(f"searched={len(output)} eligible={len(eligible)}")
    columns = [*Profile.__dataclass_fields__, "CAGR", "MDD", "Calmar", "Transitions", "Dev_CAGR", "Dev_MDD", "Recent_CAGR", "Recent_MDD"]
    print("\nTop eligible")
    print(eligible.sort_values(["Calmar", "CAGR"], ascending=False)[columns].head(30).to_string(index=False))
    print("\nBest per score")
    if not eligible.empty:
        best = eligible.sort_values(["Calmar", "CAGR"], ascending=False).groupby("score", sort=False).head(1)
        print(best[columns].to_string(index=False))


if __name__ == "__main__":
    main()
