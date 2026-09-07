"""Test a conservative valuation overlay for strategy 21.

The overlay uses monthly S&P 500 Shiller CAPE only as a broad-US-equity proxy;
it is *not* a Nasdaq-100 valuation series.  Each monthly value becomes usable
from the following calendar month, avoiding same-month information use.  The
overlay reduces the normal-risk stages first and never blocks dip purchases.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from buy_3dip_parameter_search import _trade, load_prices  # noqa: E402


TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class Profile:
    cape_threshold: float
    stage_0_cut: float
    stage_1_cut: float


def attach_cape(data: pd.DataFrame) -> pd.DataFrame:
    cape = pd.read_csv(MODULE_DIR.parents[1] / "data" / "shiller_cape.csv", parse_dates=["Date"])
    cape = cape.loc[cape["PE10"].gt(0), ["Date", "PE10"]].set_index("Date").sort_index()
    # A monthly observation is known only after that month; make it available
    # on the following calendar month and forward-fill to daily bars.
    cape.index = cape.index.to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    daily = cape.reindex(pd.date_range(data.index.min(), data.index.max(), freq="D")).ffill()
    return data.join(daily.rename(columns={"PE10": "CAPE"}), how="left")


def simulate(data: pd.DataFrame, profile: Profile) -> dict[str, float]:
    q_open, q_close = data["QQQ_Open"].to_numpy(float), data["QQQ_Close"].to_numpy(float)
    b_open, b_close = data["BIL_Open"].to_numpy(float), data["BIL_Close"].to_numpy(float)
    cape = data["CAPE"].to_numpy(float)
    values = np.empty(len(data))
    cash, q_shares, b_shares = 1.0, 0.0, 0.0
    stage, peak, pending, transitions = 0, 0.0, 0, 0
    for index in range(len(data)):
        if pending is not None:
            target = TARGETS[pending]
            if cape[index] >= profile.cape_threshold:
                if pending == 0:
                    target -= profile.stage_0_cut
                elif pending == 1:
                    target -= profile.stage_1_cut
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * target / q_open[index] - q_shares
            b_delta = total * (1 - target) / b_open[index] - b_shares
            if q_delta < 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending = None
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
        if index == 0 or changed:
            pending = stage
            transitions += int(changed)
        values[index] = cash + q_shares * q_close[index] + b_shares * b_close[index]
    years = (data.index[-1] - data.index[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    mdd = float((values / np.maximum.accumulate(values) - 1).min())
    return {"CAGR": cagr, "MDD": mdd, "Calmar": cagr / abs(mdd), "Transitions": transitions}


def main() -> None:
    data = attach_cape(load_prices())
    baseline = simulate(data, Profile(float("inf"), 0.0, 0.0))
    rows = []
    for threshold in (25.0, 30.0, 35.0, 40.0):
        for stage_0_cut, stage_1_cut in ((0.05, 0.00), (0.10, 0.00), (0.10, 0.05), (0.15, 0.05)):
            profile = Profile(threshold, stage_0_cut, stage_1_cut)
            rows.append({**profile.__dict__, **simulate(data, profile)})
    output = pd.DataFrame(rows)
    output["CAGR_vs_21"] = output.CAGR - baseline["CAGR"]
    output["MDD_vs_21"] = output.MDD - baseline["MDD"]
    output["Calmar_vs_21"] = output.Calmar - baseline["Calmar"]
    print("baseline", baseline)
    print(output.sort_values(["Calmar", "CAGR"], ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
