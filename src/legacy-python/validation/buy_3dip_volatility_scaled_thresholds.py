"""Test volatility-scaled entry and recovery bands for strategy 21."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import numpy as np


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from buy_3dip_parameter_search import _trade, load_prices  # noqa: E402


TARGETS = (0.70, 0.87, 0.90, 1.00)
BASE = (0.10, 0.20, 0.325, 0.075, 0.085, 0.175)


@dataclass(frozen=True)
class Profile:
    strength: float
    floor: float
    ceiling: float


def simulate(data, profile: Profile):
    q_open, q_close = data["QQQ_Open"].to_numpy(float), data["QQQ_Close"].to_numpy(float)
    b_open, b_close = data["BIL_Open"].to_numpy(float), data["BIL_Close"].to_numpy(float)
    vol = data["QQQ_VOL60"].to_numpy(float)
    reference_vol = float(np.nanmedian(vol))
    values = np.empty(len(data))
    cash, q_shares, b_shares = 1.0, 0.0, 0.0
    stage, peak, pending, transitions = 0, 0.0, 0, 0
    for index in range(len(data)):
        if pending is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            target = TARGETS[pending]
            q_delta = total * target / q_open[index] - q_shares
            b_delta = total * (1 - target) / b_open[index] - b_shares
            if q_delta < 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0: cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0: cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending = None
        ratio = 1.0 if not np.isfinite(vol[index]) else (vol[index] / reference_vol) ** profile.strength
        scale = float(np.clip(ratio, profile.floor, profile.ceiling))
        b, c, d, g1, r2, r3 = (value * scale for value in BASE)
        old_stage, price = stage, q_close[index]
        if stage == 3 and price >= peak * (1 - r3): stage = 2
        elif stage == 2 and price <= peak * (1 - d): stage = 3
        elif stage == 2 and price >= peak * (1 - r2): stage = 1
        elif stage == 1 and price <= peak * (1 - c): stage = 2
        elif stage == 1 and price >= peak * (1 + g1): stage = 0
        elif stage == 0 and peak > 0 and price <= peak * (1 - b): stage = 1
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


def main():
    data = load_prices()
    # load_prices deliberately selects OHLC only; attach point-in-time VOL60.
    import pandas as pd
    qqq = pd.read_csv(MODULE_DIR.parents[1] / "data" / "QQQ.csv", index_col="Date", parse_dates=True, usecols=["Date", "VOL60"])
    data = data.join(qqq.add_prefix("QQQ_"), how="left")
    baseline = simulate(data, Profile(0.0, 1.0, 1.0))
    rows = []
    for strength in (0.25, 0.50, 0.75, 1.00):
        for floor, ceiling in ((0.90, 1.10), (0.80, 1.20), (0.75, 1.25)):
            profile = Profile(strength, floor, ceiling)
            rows.append({**profile.__dict__, **simulate(data, profile)})
    import pandas as pd
    output = pd.DataFrame(rows)
    output["CAGR_vs_21"] = output.CAGR - baseline["CAGR"]
    output["MDD_vs_21"] = output.MDD - baseline["MDD"]
    output["Calmar_vs_21"] = output.Calmar - baseline["Calmar"]
    print("baseline", baseline)
    print(output.sort_values(["Calmar", "CAGR"], ascending=False).to_string(index=False))


if __name__ == "__main__":
    main()
