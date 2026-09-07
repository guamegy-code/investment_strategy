"""Test a two-step recovery unwind from strategy 21's stage 1 allocation."""

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
    ENTRY_B, ENTRY_C, ENTRY_D, RECOVERY_1_GAIN, RECOVERY_2_DRAWDOWN,
    RECOVERY_3_DRAWDOWN,
)
from buy_3dip_parameter_search import (  # noqa: E402
    _trade, load_prices,
)


TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class Parameters:
    partial_weight: float
    first_recovery_gain: float
    final_recovery_gain: float


BASELINE = Parameters(0.70, RECOVERY_1_GAIN, RECOVERY_1_GAIN)


def simulate(data: pd.DataFrame, parameters: Parameters):
    values = np.empty(len(data), dtype=float)
    q_open = data["QQQ_Open"].to_numpy(dtype=float)
    q_close = data["QQQ_Close"].to_numpy(dtype=float)
    b_open = data["BIL_Open"].to_numpy(dtype=float)
    b_close = data["BIL_Close"].to_numpy(dtype=float)
    cash = 1.0
    q_shares = b_shares = 0.0
    stage = 0
    partial = False
    peak = 0.0
    pending_target = None
    transitions = 0

    for index in range(len(data)):
        if pending_target is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * pending_target / q_open[index] - q_shares
            b_delta = total * (1.0 - pending_target) / b_open[index] - b_shares
            if q_delta < 0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            pending_target = None

        old_stage, old_partial = stage, partial
        price = q_close[index]
        if partial:
            if price <= peak * (1.0 - ENTRY_C):
                stage, partial = 2, False
            elif price <= peak:
                stage, partial = 1, False
            elif price >= peak * (1.0 + parameters.final_recovery_gain):
                stage, partial = 0, False
        elif stage == 3 and price >= peak * (1.0 - RECOVERY_3_DRAWDOWN):
            stage = 2
        elif stage == 2 and price <= peak * (1.0 - ENTRY_D):
            stage = 3
        elif stage == 2 and price >= peak * (1.0 - RECOVERY_2_DRAWDOWN):
            stage = 1
        elif stage == 1 and price <= peak * (1.0 - ENTRY_C):
            stage = 2
        elif stage == 1 and price >= peak * (1.0 + parameters.first_recovery_gain):
            if parameters.partial_weight <= TARGETS[0]:
                stage = 0
            else:
                partial = True
        elif stage == 0 and peak > 0 and price <= peak * (1.0 - ENTRY_B):
            stage = 1

        changed = stage != old_stage or partial != old_partial
        if peak == 0:
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
    return {"CAGR": cagr, "MDD": mdd, "Calmar": cagr / abs(mdd), "End": values[-1], "Transitions": transitions}


def main():
    full = load_prices()
    baseline = simulate(full, BASELINE)
    rows = []
    for values in product((0.75, 0.78, 0.80, 0.83, 0.85), (0.05, 0.075, 0.10), (0.10, 0.125, 0.15, 0.20)):
        item = Parameters(*values)
        if item.final_recovery_gain > item.first_recovery_gain:
            rows.append({**item.__dict__, **simulate(full, item)})
    output = pd.DataFrame(rows)
    for metric in ("CAGR", "MDD", "Calmar"):
        output[f"{metric}_vs_baseline"] = output[metric] - baseline[metric]
    pd.set_option("display.max_columns", None)
    print("baseline", baseline, flush=True)
    print(output.sort_values(["CAGR", "MDD"], ascending=False).head(30).to_string(index=False), flush=True)
    print("\nCandidates improving CAGR and MDD", flush=True)
    print(output.loc[(output.CAGR > baseline["CAGR"]) & (output.MDD > baseline["MDD"])].sort_values(["Calmar", "CAGR"], ascending=False).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
