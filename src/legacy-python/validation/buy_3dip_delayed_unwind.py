"""Test delayed one-stage recovery unwinds for strategy 20.

Entries and allocation targets remain unchanged.  Recovery transitions happen
one stage at a time, but at higher prices than the matching entry thresholds:
stage 3 -> 2 above -18%, stage 2 -> 1 above -10%, and stage 1 -> 0 above the
previous peak.  Returning to stage 0 resets its reference to that higher price.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys
from copy import deepcopy

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from buy_3dip_parameter_search import (  # noqa: E402
    DEVELOPMENT_END,
    RECENT_START,
    Parameters,
    _trade,
    load_prices,
    simulate as simulate_standard,
)
from backtest import Backtest  # noqa: E402
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402


BASELINE = Parameters(0.10, 0.18, 0.32, 0.87, 0.90, 1.00)
TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class RecoveryPolicy:
    stage_1_gain: float
    stage_2_drawdown: float
    stage_3_drawdown: float


def simulate(data: pd.DataFrame, policy: RecoveryPolicy):
    """Use production-equivalent next-open execution and trading costs."""
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

    for index in range(len(data)):
        if pending_stage is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * TARGETS[pending_stage] / q_open[index] - q_shares
            b_delta = total * (1.0 - TARGETS[pending_stage]) / b_open[index] - b_shares
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
        if stage == 3 and price >= peak * (1.0 - policy.stage_3_drawdown):
            stage = 2
        elif stage == 2 and price <= peak * (1.0 - BASELINE.drop_d):
            stage = 3
        elif stage == 2 and price >= peak * (1.0 - policy.stage_2_drawdown):
            stage = 1
        elif stage == 1 and price <= peak * (1.0 - BASELINE.drop_c):
            stage = 2
        elif stage == 1 and price >= peak * (1.0 + policy.stage_1_gain):
            stage = 0
        elif stage == 0 and peak > 0.0 and price <= peak * (1.0 - BASELINE.drop_b):
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


def verify_with_dsl(policy: RecoveryPolicy):
    """Replay the selected candidate through the production DSL engine."""
    root = MODULE_DIR.parents[1]
    definition = deepcopy(
        load_strategy_definition(root / "strategies" / "20_buy_3dip_buyer_optimized.yaml")
    )
    definition["parameters"].update(
        {
            "recovery_stage_1": policy.stage_1_gain,
            "recovery_stage_2": -policy.stage_2_drawdown,
            "recovery_stage_3": -policy.stage_3_drawdown,
        }
    )
    rules = definition["state"]["stage"]["rules"]
    rules[0]["when"] = (
        "state.stage == 3 and state.peak_price > 0 and "
        "QQQ.close >= state.peak_price * (1 + parameters.recovery_stage_3)"
    )
    rules[2]["when"] = (
        "state.stage == 2 and state.peak_price > 0 and "
        "QQQ.close >= state.peak_price * (1 + parameters.recovery_stage_2)"
    )
    rules[4]["when"] = (
        "state.stage == 1 and state.peak_price > 0 and "
        "QQQ.close >= state.peak_price * (1 + parameters.recovery_stage_1)"
    )
    strategy = DeclarativeStrategy(definition)
    history, _, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers, start_date="2012-01-03"
    ).run_all()
    performance = Performance(history)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
    }


def main():
    full = load_prices()
    development = full.loc[:DEVELOPMENT_END]
    recent = full.loc[RECENT_START:]
    baseline = simulate_standard(full, BASELINE)
    baseline_dev = simulate_standard(development, BASELINE)
    baseline_recent = simulate_standard(recent, BASELINE)
    rows = []
    for values in product(
        (0.005, 0.010, 0.020, 0.030, 0.050, 0.075),
        (0.025, 0.050, 0.075, 0.080, 0.085, 0.090, 0.095),
        (0.120, 0.140, 0.150, 0.160, 0.170, 0.175),
    ):
        policy = RecoveryPolicy(*values)
        full_metrics = simulate(full, policy)
        dev_metrics = simulate(development, policy)
        recent_metrics = simulate(recent, policy)
        rows.append({
            **policy.__dict__,
            **full_metrics,
            **{f"Dev_{key}": value for key, value in dev_metrics.items()},
            **{f"Recent_{key}": value for key, value in recent_metrics.items()},
        })
    output = pd.DataFrame(rows)
    output["CAGR_vs_standard"] = output["CAGR"] - baseline["CAGR"]
    output["MDD_vs_standard"] = output["MDD"] - baseline["MDD"]
    output["Calmar_vs_standard"] = output["Calmar"] - baseline["Calmar"]
    print("standard", baseline, flush=True)
    print("standard development", baseline_dev, flush=True)
    print("standard recent", baseline_recent, flush=True)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 280)
    print("\nTop CAGR candidates", flush=True)
    print(output.sort_values(["CAGR", "MDD"], ascending=False).head(15).to_string(index=False), flush=True)
    print("\nCandidates improving CAGR and MDD", flush=True)
    print(
        output.loc[
            (output["CAGR"] > baseline["CAGR"])
            & (output["MDD"] > baseline["MDD"])
        ].sort_values(["Calmar", "CAGR"], ascending=False).to_string(index=False),
        flush=True,
    )
    print("\nBest CAGR by permitted MDD worsening", flush=True)
    for allowed_worsening in (0.0025, 0.005, 0.010, 0.015, 0.020):
        eligible = output.loc[output["MDD"] >= baseline["MDD"] - allowed_worsening]
        if eligible.empty:
            continue
        row = eligible.sort_values(["CAGR", "MDD"], ascending=False).iloc[0]
        print(
            f"MDD cap {allowed_worsening:.2%}: CAGR={row['CAGR']:.6%} "
            f"MDD={row['MDD']:.6%} stage1=+{row['stage_1_gain']:.1%} "
            f"stage2=-{row['stage_2_drawdown']:.1%} "
            f"stage3=-{row['stage_3_drawdown']:.1%}",
            flush=True,
        )
    best = output.sort_values(["CAGR", "MDD"], ascending=False).iloc[0]
    policy = RecoveryPolicy(
        best["stage_1_gain"], best["stage_2_drawdown"], best["stage_3_drawdown"]
    )
    print("\nDSL verification", verify_with_dsl(policy), flush=True)


if __name__ == "__main__":
    main()
