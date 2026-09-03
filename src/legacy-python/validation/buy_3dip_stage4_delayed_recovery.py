"""Test selected delayed-recovery rules for the conservative stage 0~4 candidate.

The stage-4 candidate uses 10%/17.5%/31%/32% entries and QQQ targets
70%/88%/90%/99%/100%.  Each selected deep stage holds its QQQ allocation
until the following recovery threshold, then skips one recovery state.
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
    RECENT_START,
    _trade,
    load_prices,
)


DROPS = (0.10, 0.175, 0.31, 0.32)
TARGETS = (0.70, 0.88, 0.90, 0.99, 1.00)


@dataclass(frozen=True)
class RecoveryPolicy:
    delay_stage_2: bool
    delay_stage_3: bool
    delay_stage_4: bool

    @property
    def name(self):
        delayed = [
            str(stage)
            for stage, enabled in (
                (2, self.delay_stage_2),
                (3, self.delay_stage_3),
                (4, self.delay_stage_4),
            )
            if enabled
        ]
        return "standard" if not delayed else "delay_stage_" + "_".join(delayed)


POLICIES = tuple(RecoveryPolicy(*flags) for flags in product((False, True), repeat=3))


def simulate(data: pd.DataFrame, policy: RecoveryPolicy):
    """Use the same next-open, cost-inclusive execution model as production."""
    values = np.empty(len(data), dtype=float)
    q_open = data["QQQ_Open"].to_numpy(dtype=float)
    q_close = data["QQQ_Close"].to_numpy(dtype=float)
    b_open = data["BIL_Open"].to_numpy(dtype=float)
    b_close = data["BIL_Close"].to_numpy(dtype=float)
    b, c, d, e = DROPS

    cash = 1.0
    q_shares = 0.0
    b_shares = 0.0
    stage = 0
    peak = 0.0
    pending_stage = None
    transitions = 0

    for index in range(len(data)):
        if pending_stage is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            target_q = TARGETS[pending_stage]
            target_b = 1.0 - target_q
            q_delta = total * target_q / q_open[index] - q_shares
            b_delta = total * target_b / b_open[index] - b_shares
            for price, delta, asset in (
                (q_open[index], q_delta, "q"),
                (b_open[index], b_delta, "b"),
            ):
                if delta < 0.0:
                    cash, shares = _trade(
                        cash, q_shares if asset == "q" else b_shares, price, delta
                    )
                    if asset == "q":
                        q_shares = shares
                    else:
                        b_shares = shares
            for price, delta, asset in (
                (q_open[index], q_delta, "q"),
                (b_open[index], b_delta, "b"),
            ):
                if delta > 0.0:
                    cash, shares = _trade(
                        cash, q_shares if asset == "q" else b_shares, price, delta
                    )
                    if asset == "q":
                        q_shares = shares
                    else:
                        b_shares = shares
            pending_stage = None

        old_stage = stage
        price = q_close[index]
        if stage == 4:
            if policy.delay_stage_4 and price >= peak * (1.0 - c):
                stage = 2
            elif not policy.delay_stage_4 and price >= peak * (1.0 - d):
                stage = 3
        elif stage == 3:
            if price <= peak * (1.0 - e):
                stage = 4
            elif policy.delay_stage_3 and price >= peak * (1.0 - b):
                stage = 1
            elif not policy.delay_stage_3 and price >= peak * (1.0 - c):
                stage = 2
        elif stage == 2:
            if price <= peak * (1.0 - d):
                stage = 3
            elif policy.delay_stage_2 and price >= peak:
                stage = 0
            elif not policy.delay_stage_2 and price >= peak * (1.0 - b):
                stage = 1
        elif stage == 1:
            if price <= peak * (1.0 - c):
                stage = 2
            elif price >= peak:
                stage = 0
        elif stage == 0 and peak > 0.0 and price <= peak * (1.0 - b):
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


def main():
    full = load_prices()
    periods = {
        "Full": full,
        "Development": full.loc[:DEVELOPMENT_END],
        "Recent": full.loc[RECENT_START:],
    }
    rows = []
    for policy in POLICIES:
        row = {"Policy": policy.name}
        for label, data in periods.items():
            row.update({f"{label}_{key}": value for key, value in simulate(data, policy).items()})
        rows.append(row)
    output = pd.DataFrame(rows)
    baseline = output.loc[output["Policy"] == "standard"].iloc[0]
    for metric in ("CAGR", "MDD", "Calmar"):
        output[f"Full_{metric}_vs_standard"] = output[f"Full_{metric}"] - baseline[f"Full_{metric}"]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 260)
    print(output.sort_values("Full_CAGR", ascending=False).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
