"""Evaluate a four-entry (stage 0~4) extension of strategy 20.

The simulator is intentionally limited to the existing price-only safety-margin
rules.  It keeps one-day next-open execution and the same cost model as the
production backtest.  Any selected candidate must be replayed by the DSL
engine before it is adopted.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from buy_3dip_parameter_search import (  # noqa: E402
    DEVELOPMENT_END,
    Parameters as StageThreeParameters,
    RECENT_START,
    _trade,
    load_prices,
    simulate as simulate_stage_three,
)


@dataclass(frozen=True)
class Parameters:
    drop_b: float
    drop_c: float
    drop_d: float
    drop_e: float
    risk_1: float
    risk_2: float
    risk_3: float


BASELINE = StageThreeParameters(0.10, 0.18, 0.32, 0.87, 0.90, 1.00)


def simulate(data: pd.DataFrame, parameters: Parameters):
    """Return metrics for stage 0~4 with a 70% QQQ starting allocation."""
    values = np.empty(len(data), dtype=float)
    q_open = data["QQQ_Open"].to_numpy(dtype=float)
    q_close = data["QQQ_Close"].to_numpy(dtype=float)
    b_open = data["BIL_Open"].to_numpy(dtype=float)
    b_close = data["BIL_Close"].to_numpy(dtype=float)

    cash = 1.0
    q_shares = 0.0
    b_shares = 0.0
    stage = 0
    peak = 0.0
    pending_stage = None
    transitions = 0
    targets = (0.70, parameters.risk_1, parameters.risk_2, parameters.risk_3, 1.00)

    for index in range(len(data)):
        if pending_stage is not None:
            target_q = targets[pending_stage]
            target_b = 1.0 - target_q
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * target_q / q_open[index] - q_shares
            b_delta = total * target_b / b_open[index] - b_shares
            if q_delta < 0.0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0.0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0.0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0.0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending_stage = None

        old_stage = stage
        price = q_close[index]
        if stage == 4 and peak > 0.0 and price >= peak * (1.0 - parameters.drop_d):
            stage = 3
        elif stage == 3 and peak > 0.0 and price <= peak * (1.0 - parameters.drop_e):
            stage = 4
        elif stage == 3 and peak > 0.0 and price >= peak * (1.0 - parameters.drop_c):
            stage = 2
        elif stage == 2 and peak > 0.0 and price <= peak * (1.0 - parameters.drop_d):
            stage = 3
        elif stage == 2 and peak > 0.0 and price >= peak * (1.0 - parameters.drop_b):
            stage = 1
        elif stage == 1 and peak > 0.0 and price <= peak * (1.0 - parameters.drop_c):
            stage = 2
        elif stage == 1 and peak > 0.0 and price >= peak:
            stage = 0
        elif stage == 0 and peak > 0.0 and price <= peak * (1.0 - parameters.drop_b):
            stage = 1

        changed = stage != old_stage
        if peak == 0.0:
            peak = price
        elif changed and stage == 0:
            peak = price
        elif stage == 0 and price > peak:
            peak = price
        if index == 0 or changed:
            pending_stage = stage
            transitions += int(changed)
        values[index] = cash + q_shares * q_close[index] + b_shares * b_close[index]

    years = (data.index[-1] - data.index[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0
    drawdown = values / np.maximum.accumulate(values) - 1.0
    mdd = float(drawdown.min())
    return {
        "CAGR": cagr,
        "MDD": mdd,
        "Calmar": cagr / abs(mdd),
        "End": values[-1],
        "Transitions": transitions,
    }


def candidates():
    # Fine search around the broad-grid return frontier.  The best coarse
    # candidates consistently retained the first 10% entry and completed the
    # fourth entry near 32%, so the search concentrates resolution there.
    drops_b = (0.10,)
    drops_c = (0.175, 0.180, 0.185, 0.190, 0.195, 0.200)
    drops_d = (0.290, 0.300, 0.310)
    drops_e = (0.320, 0.330, 0.340)
    risks_1 = (0.86, 0.87, 0.88, 0.89, 0.90, 0.91)
    risks_2 = (0.89, 0.90, 0.91, 0.92, 0.93, 0.94)
    risks_3 = (0.97, 0.98, 0.99)
    for values in product(
        drops_b, drops_c, drops_d, drops_e, risks_1, risks_2, risks_3,
    ):
        item = Parameters(*values)
        if (
            item.drop_b < item.drop_c < item.drop_d < item.drop_e
            and item.risk_1 + 0.02 <= item.risk_2
            and item.risk_2 + 0.02 <= item.risk_3
        ):
            yield item


def main():
    full = load_prices()
    development = full.loc[:DEVELOPMENT_END]
    recent = full.loc[RECENT_START:]
    baseline = simulate_stage_three(full, BASELINE)
    baseline_dev = simulate_stage_three(development, BASELINE)
    baseline_recent = simulate_stage_three(recent, BASELINE)
    print("baseline", baseline, flush=True)

    rows = []
    for index, item in enumerate(candidates(), start=1):
        metrics = simulate(full, item)
        rows.append({**item.__dict__, **metrics})
        if index % 1000 == 0:
            print(f"searched={index}", flush=True)

    result = pd.DataFrame(rows).sort_values(
        ["CAGR", "MDD"], ascending=False,
    )
    for index, row in result.head(30).iterrows():
        item = Parameters(*(row[name] for name in Parameters.__dataclass_fields__))
        dev = simulate(development, item)
        recent_metrics = simulate(recent, item)
        result.loc[index, "Dev_CAGR"] = dev["CAGR"]
        result.loc[index, "Dev_MDD"] = dev["MDD"]
        result.loc[index, "Recent_CAGR"] = recent_metrics["CAGR"]
        result.loc[index, "Recent_MDD"] = recent_metrics["MDD"]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)
    print("\nBest CAGR by permitted MDD worsening:", flush=True)
    for allowed_worsening in (0.005, 0.010, 0.015, 0.020):
        eligible = result.loc[result["MDD"] >= baseline["MDD"] - allowed_worsening]
        if eligible.empty:
            continue
        row = eligible.iloc[0]
        print(
            f"MDD worsening cap {allowed_worsening:.1%}: "
            f"CAGR={row['CAGR']:.6%} MDD={row['MDD']:.6%} "
            f"drops={row['drop_b']:.1%}/{row['drop_c']:.1%}/"
            f"{row['drop_d']:.1%}/{row['drop_e']:.1%} "
            f"QQQ={row['risk_1']:.0%}/{row['risk_2']:.0%}/{row['risk_3']:.0%}/100%",
            flush=True,
        )
    print(result.head(30).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
