"""Optimize stage allocations for strategy 21 with fixed entry/recovery rules."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

from backtest import Backtest  # noqa: E402
from buy_3dip_parameter_search import (  # noqa: E402
    DEVELOPMENT_END,
    RECENT_START,
    _trade,
    load_prices,
)
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402


ENTRY_B = 0.10
ENTRY_C = 0.20
ENTRY_D = 0.325
RECOVERY_1_GAIN = 0.075
RECOVERY_2_DRAWDOWN = 0.085
RECOVERY_3_DRAWDOWN = 0.175


@dataclass(frozen=True)
class Allocations:
    stage_1: float
    stage_2: float
    stage_3: float


BASELINE = Allocations(0.87, 0.90, 1.00)


def simulate(data: pd.DataFrame, allocations: Allocations):
    """Mirror strategy 21's one-day next-open execution path and costs."""
    values = np.empty(len(data), dtype=float)
    q_open = data["QQQ_Open"].to_numpy(dtype=float)
    q_close = data["QQQ_Close"].to_numpy(dtype=float)
    b_open = data["BIL_Open"].to_numpy(dtype=float)
    b_close = data["BIL_Close"].to_numpy(dtype=float)
    targets = (0.70, allocations.stage_1, allocations.stage_2, allocations.stage_3)

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
            target_q = targets[pending_stage]
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
            pending_stage = None

        old_stage = stage
        price = q_close[index]
        if stage == 3 and price >= peak * (1.0 - RECOVERY_3_DRAWDOWN):
            stage = 2
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
    for values in product(
        np.arange(0.82, 0.921, 0.01),
        np.arange(0.86, 0.971, 0.01),
        np.arange(0.96, 1.001, 0.01),
    ):
        candidate = Allocations(*(round(value, 4) for value in values))
        if (
            candidate.stage_1 + 0.02 <= candidate.stage_2
            and candidate.stage_2 + 0.02 <= candidate.stage_3
        ):
            yield candidate


def verify_with_dsl(allocations: Allocations):
    root = MODULE_DIR.parents[1]
    definition = deepcopy(
        load_strategy_definition(root / "strategies" / "21_buy_3dip_buyer_delayed_recovery.yaml")
    )
    definition["target"][0]["weights"] = {"QQQ": allocations.stage_3, "BIL": 1.0 - allocations.stage_3}
    definition["target"][1]["weights"] = {"QQQ": allocations.stage_2, "BIL": 1.0 - allocations.stage_2}
    definition["target"][2]["weights"] = {"QQQ": allocations.stage_1, "BIL": 1.0 - allocations.stage_1}
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
    baseline = simulate(full, BASELINE)
    baseline_dev = simulate(development, BASELINE)
    baseline_recent = simulate(recent, BASELINE)
    print("baseline", baseline, flush=True)

    rows = []
    for allocations in candidates():
        rows.append({**allocations.__dict__, **simulate(full, allocations)})
    output = pd.DataFrame(rows)
    output["CAGR_vs_baseline"] = output["CAGR"] - baseline["CAGR"]
    output["MDD_vs_baseline"] = output["MDD"] - baseline["MDD"]
    output["Calmar_vs_baseline"] = output["Calmar"] - baseline["Calmar"]
    top = output.sort_values(["CAGR", "MDD"], ascending=False).head(30).copy()
    for index, row in top.iterrows():
        allocations = Allocations(*(row[name] for name in Allocations.__dataclass_fields__))
        dev = simulate(development, allocations)
        rec = simulate(recent, allocations)
        top.loc[index, "Dev_CAGR"] = dev["CAGR"]
        top.loc[index, "Dev_MDD"] = dev["MDD"]
        top.loc[index, "Recent_CAGR"] = rec["CAGR"]
        top.loc[index, "Recent_MDD"] = rec["MDD"]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 280)
    print("\nTop CAGR candidates", flush=True)
    print(top.to_string(index=False), flush=True)
    print("\nCandidates improving CAGR and MDD", flush=True)
    improvements = output.loc[
        (output["CAGR"] > baseline["CAGR"])
        & (output["MDD"] > baseline["MDD"])
    ].sort_values(["Calmar", "CAGR"], ascending=False)
    print(improvements.head(30).to_string(index=False), flush=True)
    if not improvements.empty:
        best = improvements.iloc[0]
        allocations = Allocations(*(best[name] for name in Allocations.__dataclass_fields__))
        print("\nDSL verification", verify_with_dsl(allocations), flush=True)
    print("\nBest CAGR by permitted MDD worsening", flush=True)
    for allowed_worsening in (0.0025, 0.005, 0.010, 0.015, 0.020):
        eligible = output.loc[output["MDD"] >= baseline["MDD"] - allowed_worsening]
        if eligible.empty:
            continue
        row = eligible.sort_values(["CAGR", "MDD"], ascending=False).iloc[0]
        print(
            f"MDD cap {allowed_worsening:.2%}: CAGR={row['CAGR']:.6%} "
            f"MDD={row['MDD']:.6%} QQQ={row['stage_1']:.0%}/"
            f"{row['stage_2']:.0%}/{row['stage_3']:.0%}",
            flush=True,
        )
    print("\nbaseline development", baseline_dev, flush=True)
    print("baseline recent", baseline_recent, flush=True)


if __name__ == "__main__":
    main()
