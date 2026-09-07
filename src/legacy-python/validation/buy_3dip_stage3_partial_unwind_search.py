"""Test a two-step recovery unwind from strategy 21's stage 3 allocation.

The base strategy changes QQQ from 100% to 90% when price recovers to -17.5%
from its stage-zero reference.  Candidates first reduce to an intermediate QQQ
weight, then complete the reduction to 90% at a higher recovery price.
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

from buy_3dip_delayed_recovery_allocation_search import (  # noqa: E402
    ENTRY_B,
    ENTRY_C,
    ENTRY_D,
    RECOVERY_1_GAIN,
    RECOVERY_2_DRAWDOWN,
    RECOVERY_3_DRAWDOWN,
)
from buy_3dip_parameter_search import (  # noqa: E402
    DEVELOPMENT_END,
    RECENT_START,
    _trade,
    load_prices,
)


TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class Parameters:
    partial_weight: float
    first_recovery_drawdown: float
    final_recovery_drawdown: float


BASELINE = Parameters(0.90, RECOVERY_3_DRAWDOWN, RECOVERY_3_DRAWDOWN)


def simulate(data: pd.DataFrame, parameters: Parameters):
    """Simulate one intermediate stage-3 recovery allocation."""
    values = np.empty(len(data), dtype=float)
    q_open = data["QQQ_Open"].to_numpy(dtype=float)
    q_close = data["QQQ_Close"].to_numpy(dtype=float)
    b_open = data["BIL_Open"].to_numpy(dtype=float)
    b_close = data["BIL_Close"].to_numpy(dtype=float)

    cash = 1.0
    q_shares = 0.0
    b_shares = 0.0
    stage = 0
    partial = False
    peak = 0.0
    pending_target = None
    transitions = 0

    for index in range(len(data)):
        if pending_target is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            target_q = pending_target
            q_delta = total * target_q / q_open[index] - q_shares
            b_delta = total * (1.0 - target_q) / b_open[index] - b_shares
            if q_delta < 0.0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0.0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0.0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0.0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending_target = None

        old_stage, old_partial = stage, partial
        price = q_close[index]
        if partial:
            if price <= peak * (1.0 - ENTRY_D):
                stage, partial = 3, False
            elif price >= peak * (1.0 - parameters.final_recovery_drawdown):
                stage, partial = 2, False
        elif stage == 3 and price >= peak * (1.0 - parameters.first_recovery_drawdown):
            if parameters.partial_weight <= TARGETS[2]:
                stage = 2
            else:
                partial = True
        elif stage == 2 and price <= peak * (1.0 - ENTRY_D):
            stage = 3
        elif stage == 2 and price >= peak * (1.0 - RECOVERY_2_DRAWDOWN):
            stage = 1
        elif stage == 1 and price <= peak * (1.0 - ENTRY_C):
            stage = 2
        elif stage == 1 and price >= peak * (1.0 + RECOVERY_1_GAIN):
            stage = 0
        elif stage == 0 and peak > 0.0 and price <= peak * (1.0 - ENTRY_B):
            stage = 1

        changed = stage != old_stage or partial != old_partial
        if peak == 0.0:
            peak = price
        elif changed and stage == 0:
            peak = price
        elif stage == 0 and price > peak:
            peak = price
        if index == 0 or changed:
            pending_target = parameters.partial_weight if partial else TARGETS[stage]
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
    for values in product(
        (0.92, 0.94, 0.96, 0.98),
        (0.165, 0.170, 0.175, 0.180),
        (0.100, 0.120, 0.140, 0.150, 0.160),
    ):
        item = Parameters(*values)
        if item.final_recovery_drawdown < item.first_recovery_drawdown:
            yield item


def main():
    full = load_prices()
    development = full.loc[:DEVELOPMENT_END]
    recent = full.loc[RECENT_START:]
    baseline = simulate(full, BASELINE)
    print("baseline", baseline, flush=True)
    rows = []
    for parameters in candidates():
        rows.append({**parameters.__dict__, **simulate(full, parameters)})
    output = pd.DataFrame(rows)
    output["CAGR_vs_baseline"] = output["CAGR"] - baseline["CAGR"]
    output["MDD_vs_baseline"] = output["MDD"] - baseline["MDD"]
    output["Calmar_vs_baseline"] = output["Calmar"] - baseline["Calmar"]
    top = output.sort_values(["CAGR", "MDD"], ascending=False).head(30).copy()
    for index, row in top.iterrows():
        item = Parameters(*(row[name] for name in Parameters.__dataclass_fields__))
        dev = simulate(development, item)
        rec = simulate(recent, item)
        top.loc[index, "Dev_CAGR"] = dev["CAGR"]
        top.loc[index, "Dev_MDD"] = dev["MDD"]
        top.loc[index, "Recent_CAGR"] = rec["CAGR"]
        top.loc[index, "Recent_MDD"] = rec["MDD"]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 280)
    print("\nTop CAGR candidates", flush=True)
    print(top.to_string(index=False), flush=True)
    print("\nCandidates improving CAGR and MDD", flush=True)
    print(
        output.loc[
            (output["CAGR"] > baseline["CAGR"])
            & (output["MDD"] > baseline["MDD"])
        ].sort_values(["Calmar", "CAGR"], ascending=False).to_string(index=False),
        flush=True,
    )


if __name__ == "__main__":
    main()
