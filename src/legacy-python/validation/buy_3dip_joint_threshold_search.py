"""Jointly optimize Buy 3 Dip entry and delayed-recovery thresholds.

All candidates preserve the 70%/87%/90%/100% QQQ stage targets and one-stage
unwinds.  They vary the three drawdown entries and the three higher-price
recovery exits while retaining a non-overlapping hysteresis band per stage.
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
    _trade,
    load_prices,
)
from backtest import Backtest  # noqa: E402
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402


TARGETS = (0.70, 0.87, 0.90, 1.00)


@dataclass(frozen=True)
class Parameters:
    entry_b: float
    entry_c: float
    entry_d: float
    recovery_1_gain: float
    recovery_2_drawdown: float
    recovery_3_drawdown: float


BASELINE = Parameters(0.10, 0.18, 0.32, 0.075, 0.08, 0.175)


def simulate(data: pd.DataFrame, parameters: Parameters):
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
        if stage == 3 and price >= peak * (1.0 - parameters.recovery_3_drawdown):
            stage = 2
        elif stage == 2 and price <= peak * (1.0 - parameters.entry_d):
            stage = 3
        elif stage == 2 and price >= peak * (1.0 - parameters.recovery_2_drawdown):
            stage = 1
        elif stage == 1 and price <= peak * (1.0 - parameters.entry_c):
            stage = 2
        elif stage == 1 and price >= peak * (1.0 + parameters.recovery_1_gain):
            stage = 0
        elif stage == 0 and peak > 0.0 and price <= peak * (1.0 - parameters.entry_b):
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


def verify_with_dsl(parameters: Parameters):
    """Replay a selected joint candidate using the production DSL engine."""
    root = MODULE_DIR.parents[1]
    definition = deepcopy(
        load_strategy_definition(root / "strategies" / "20_buy_3dip_buyer_optimized.yaml")
    )
    definition["parameters"].update(
        {
            "drop_b": -parameters.entry_b,
            "drop_c": -parameters.entry_c,
            "drop_d": -parameters.entry_d,
            "recovery_stage_1": parameters.recovery_1_gain,
            "recovery_stage_2": -parameters.recovery_2_drawdown,
            "recovery_stage_3": -parameters.recovery_3_drawdown,
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


def candidates():
    # Fine grid around the best coarse region: 10% / 20% / 32% entries.
    entries_b = (0.100, 0.1025, 0.105)
    entries_c = (0.1975, 0.200, 0.2025)
    entries_d = (0.315, 0.320, 0.325)
    recovery_1_gains = (0.0725, 0.0750, 0.0775)
    recovery_2_gaps = (0.0125, 0.0150, 0.0175)
    recovery_3_gaps = (0.0225, 0.0250, 0.0275)
    for entry_b, entry_c, entry_d, gain, gap_2, gap_3 in product(
        entries_b,
        entries_c,
        entries_d,
        recovery_1_gains,
        recovery_2_gaps,
        recovery_3_gaps,
    ):
        if not entry_b < entry_c < entry_d:
            continue
        recovery_2 = entry_b - gap_2
        recovery_3 = entry_c - gap_3
        if recovery_2 <= 0.0 or recovery_3 <= entry_b:
            continue
        yield Parameters(
            entry_b, entry_c, entry_d, gain, recovery_2, recovery_3
        )


def main():
    full = load_prices()
    development = full.loc[:DEVELOPMENT_END]
    recent = full.loc[RECENT_START:]
    baseline = simulate(full, BASELINE)
    print("baseline", BASELINE, baseline, flush=True)
    rows = []
    for index, parameters in enumerate(candidates(), start=1):
        rows.append({**parameters.__dict__, **simulate(full, parameters)})
        if index % 1000 == 0:
            print(f"searched={index}", flush=True)
    output = pd.DataFrame(rows)
    output["CAGR_vs_baseline"] = output["CAGR"] - baseline["CAGR"]
    output["MDD_vs_baseline"] = output["MDD"] - baseline["MDD"]
    output["Calmar_vs_baseline"] = output["Calmar"] - baseline["Calmar"]
    top = output.sort_values(["CAGR", "MDD"], ascending=False).head(30).copy()
    for index, row in top.iterrows():
        parameters = Parameters(*(row[name] for name in Parameters.__dataclass_fields__))
        dev = simulate(development, parameters)
        rec = simulate(recent, parameters)
        top.loc[index, "Dev_CAGR"] = dev["CAGR"]
        top.loc[index, "Dev_MDD"] = dev["MDD"]
        top.loc[index, "Recent_CAGR"] = rec["CAGR"]
        top.loc[index, "Recent_MDD"] = rec["MDD"]
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 300)
    print("\nTop CAGR candidates", flush=True)
    print(top.to_string(index=False), flush=True)
    print("\nBest CAGR by permitted MDD worsening", flush=True)
    for allowed_worsening in (0.0, 0.0025, 0.005, 0.010):
        eligible = output.loc[output["MDD"] >= baseline["MDD"] - allowed_worsening]
        if eligible.empty:
            continue
        row = eligible.sort_values(["CAGR", "MDD"], ascending=False).iloc[0]
        print(
            f"MDD cap {allowed_worsening:.2%}: CAGR={row['CAGR']:.6%} "
            f"MDD={row['MDD']:.6%} entries={row['entry_b']:.1%}/"
            f"{row['entry_c']:.1%}/{row['entry_d']:.1%} recoveries=+"
            f"{row['recovery_1_gain']:.1%}/-{row['recovery_2_drawdown']:.1%}/"
            f"-{row['recovery_3_drawdown']:.1%}",
            flush=True,
        )
    best = output.sort_values(["CAGR", "MDD"], ascending=False).iloc[0]
    parameters = Parameters(*(best[name] for name in Parameters.__dataclass_fields__))
    print("\nDSL verification", verify_with_dsl(parameters), flush=True)


if __name__ == "__main__":
    main()
