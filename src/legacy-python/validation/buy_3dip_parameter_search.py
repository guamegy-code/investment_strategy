"""Search robust thresholds and cash deployment weights for buy-3dip-bil.

The search simulator mirrors the two-asset, one-day execution path in
``Backtest``/``Portfolio``.  Finalists must still be verified with the
production DSL engine before promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_DIR.parents[1]
sys.path.insert(0, str(MODULE_DIR))

from config import COMMISSION, SLIPPAGE  # noqa: E402


START_DATE = "2012-01-03"
DEVELOPMENT_END = "2020-12-31"
RECENT_START = "2021-01-01"


@dataclass(frozen=True)
class Parameters:
    drop_b: float
    drop_c: float
    drop_d: float
    risk_1: float
    risk_2: float
    risk_3: float


def load_prices(start_date: str = START_DATE, end_date: str | None = None):
    columns = ["Open", "Close"]
    frames = []
    for ticker in ("QQQ", "BIL"):
        frame = pd.read_csv(
            PROJECT_ROOT / "data" / f"{ticker}.csv",
            index_col="Date",
            parse_dates=True,
            usecols=["Date", *columns],
        )[columns].add_prefix(f"{ticker}_")
        frames.append(frame)
    data = frames[0].join(frames[1], how="inner").sort_index()
    data = data.loc[data.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        data = data.loc[data.index <= pd.Timestamp(end_date)]
    return data


def _trade(cash, shares, price, share_delta):
    if abs(share_delta) < 1e-8:
        return cash, shares
    direction = 1.0 if share_delta > 0.0 else -1.0
    execution_price = price * (1.0 + direction * SLIPPAGE)
    if share_delta > 0.0:
        affordable = max(cash, 0.0) / (execution_price * (1.0 + COMMISSION))
        share_delta = min(share_delta, affordable)
        if share_delta < 1e-8:
            return cash, shares
    notional = share_delta * execution_price
    fee = abs(notional) * COMMISSION
    return cash - notional - fee, shares + share_delta


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
            target_q = (0.70, parameters.risk_1, parameters.risk_2, parameters.risk_3)[pending_stage]
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
        if stage == 3 and peak > 0.0 and price >= peak * (1.0 - parameters.drop_c):
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
    return {
        "CAGR": cagr,
        "MDD": float(drawdown.min()),
        "Calmar": cagr / abs(float(drawdown.min())),
        "End": values[-1],
        "Transitions": transitions,
    }


def candidates():
    # Focused grid around the only broad-grid Pareto improvement:
    # 10% / 20% / 32% with 88% / 91% / 100% QQQ.
    drops_b = (0.085, 0.090, 0.095, 0.100, 0.105, 0.110, 0.115)
    drops_c = (0.180, 0.190, 0.200, 0.210, 0.220)
    drops_d = (0.290, 0.300, 0.310, 0.320, 0.330, 0.340, 0.350)
    risk_1 = (0.85, 0.86, 0.87, 0.88, 0.89, 0.90)
    risk_2 = (0.90, 0.91, 0.92, 0.93, 0.94, 0.95)
    risk_3 = (0.98, 0.99, 1.00)
    for values in product(drops_b, drops_c, drops_d, risk_1, risk_2, risk_3):
        item = Parameters(*values)
        if (
            item.drop_b < item.drop_c < item.drop_d
            and item.risk_1 + 0.03 <= item.risk_2
            and item.risk_2 + 0.03 <= item.risk_3
        ):
            yield item


def main():
    full = load_prices()
    development = full.loc[:DEVELOPMENT_END]
    recent = full.loc[RECENT_START:]
    baseline = Parameters(0.10, 0.20, 0.30, 0.85, 0.94, 1.00)
    baseline_metrics = simulate(full, baseline)
    print("baseline", baseline, baseline_metrics, flush=True)
    print("baseline development", simulate(development, baseline), flush=True)
    print("baseline recent", simulate(recent, baseline), flush=True)

    rows = []
    for index, item in enumerate(candidates(), start=1):
        metrics = simulate(full, item)
        if metrics["CAGR"] >= baseline_metrics["CAGR"] and metrics["MDD"] >= baseline_metrics["MDD"]:
            rows.append({**item.__dict__, **metrics})
        if index % 1000 == 0:
            print(f"searched={index} pareto_improvements={len(rows)}", flush=True)

    full_ranked = sorted(
        rows,
        key=lambda row: (row["Calmar"], row["CAGR"], row["MDD"]),
        reverse=True,
    )
    for row in full_ranked:
        item = Parameters(*(row[name] for name in Parameters.__dataclass_fields__))
        dev = simulate(development, item)
        rec = simulate(recent, item)
        row.update({f"Dev_{key}": value for key, value in dev.items()})
        row.update({f"Recent_{key}": value for key, value in rec.items()})

    baseline_dev = simulate(development, baseline)
    baseline_recent = simulate(recent, baseline)
    ranked = [
        row for row in full_ranked
        if row["Dev_CAGR"] >= baseline_dev["CAGR"]
        and row["Dev_MDD"] >= baseline_dev["MDD"]
        and row["Recent_CAGR"] >= baseline_recent["CAGR"]
        and row["Recent_MDD"] >= baseline_recent["MDD"]
    ]

    output = pd.DataFrame(ranked).sort_values(
        ["Calmar", "CAGR", "MDD"], ascending=False
    )
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)
    print(output.head(20).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
