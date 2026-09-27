"""Explore failure boundaries of the proposed strategy-30 composite.

Deterministic hypothetical paths are imposed on a common 2024 starting point.
They are conditional scenarios, not forecasts or probability estimates.
"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from strategy28_failed_dip_regime import ROOT, RESULT_DIR, STRATEGY, build_data
from strategy28_mixed_tuning import MixedTuning, apply_credit_lag
from backtest import Backtest
from config import COMMISSION, SLIPPAGE
from indicators import Indicator
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


EVENT_START = pd.Timestamp("2024-01-02")
EVENT_END = pd.Timestamp("2024-12-31")
BACKTEST_START = "2012-01-03"
SCENARIOS = {
    # factor is relative to the historical price, with 1.0 = unchanged.
    "ORIGINAL_2024": {
        "qqq": [(0, 1), (250, 1)], "spy": [(0, 1), (250, 1)],
        "credit": None,
    },
    "FAST_SHARED_SILENT": {
        "qqq": [(0, 1), (10, .60), (80, .55), (220, .80)],
        "spy": [(0, 1), (10, .65), (80, .60), (220, .82)],
        "credit": [(0, 0), (250, 0)],
    },
    "FAST_GROWTH_LATE_CREDIT": {
        "qqq": [(0, 1), (12, .60), (120, .50), (240, .85)],
        "spy": [(0, 1), (12, .80), (120, .75), (240, .93)],
        "credit": [(0, 0), (15, 0), (40, 1.0), (120, 1.0), (240, .3)],
    },
    "SLOW_GROWTH_CREDIT": {
        "qqq": [(0, 1), (60, .70), (160, .45), (240, .70)],
        "spy": [(0, 1), (60, .93), (160, .82), (240, .90)],
        "credit": [(0, 0), (25, .9), (160, .9), (240, .2)],
    },
    "REBOUND_RE_DROP": {
        "qqq": [(0, 1), (20, .70), (50, .90), (95, .50), (220, .80)],
        "spy": [(0, 1), (20, .90), (50, .98), (95, .75), (220, .92)],
        "credit": [(0, 0), (20, .9), (50, .4), (95, 1.2), (220, .3)],
    },
    "FAST_V_RECOVERY": {
        "qqq": [(0, 1), (15, .65), (70, 1), (220, 1)],
        "spy": [(0, 1), (15, .80), (70, 1), (220, 1)],
        "credit": [(0, 0), (15, 1.0), (70, 0), (220, 0)],
    },
}


def path(index, knots):
    return np.interp(index, [i for i, _ in knots], [v for _, v in knots])


def inject(directory: Path, scenario: dict):
    qpath, spath = directory / "QQQ.csv", directory / "SPY.csv"
    q = pd.read_csv(qpath, index_col="Date", parse_dates=True)
    spy = pd.read_csv(spath, index_col="Date", parse_dates=True)
    dates = q.index[(q.index >= EVENT_START) & (q.index <= EVENT_END)]
    if len(dates) != 252:
        raise ValueError(f"Expected 252 common 2024 QQQ sessions, got {len(dates)}")
    for frame, knots in ((q, scenario["qqq"]), (spy, scenario["spy"])):
        factor = path(np.arange(len(dates)), knots)
        for col in ("Open", "High", "Low", "Close"):
            frame.loc[dates, col] *= factor
        # Recompute every price-derived feature on the modified OHLC path.
        Indicator.add_indicators(frame)
    relative = q["Close"] / spy["Close"]
    q["RELATIVE_ROC20"] = relative.pct_change(20) * 100
    q["RELATIVE_ROC20_MIN20"] = q["RELATIVE_ROC20"].rolling(20).min()
    q["RELATIVE_ROC60"] = relative.pct_change(60) * 100
    # Keep the valuation series exogenous: price shocks do not fabricate
    # a new CAPE/valuation observation. This limits scenario interpretation.
    if scenario["credit"] is not None:
        before = q.index[q.index < EVENT_START][-1]
        anchor = float(q.loc[before, "BAA_SPREAD"])
        # The stress increment becomes observable one trading day after the
        # hypothetical economic observation. Historical credit was lagged by
        # apply_credit_lag before this function was called.
        stress_increment = pd.Series(
            path(np.arange(len(dates)), scenario["credit"]), index=dates
        ).shift(1).fillna(0)
        q.loc[dates, "BAA_SPREAD"] = anchor + stress_increment
    q["BAA_CHANGE20"] = q["BAA_SPREAD"].diff(20)
    q["BAA_DROP60"] = q["BAA_SPREAD"].rolling(60).max() - q["BAA_SPREAD"]
    q.to_csv(qpath, index_label="Date")
    spy.to_csv(spath, index_label="Date")


def run(directory: Path, variant: str):
    if variant == "STRATEGY_28":
        strategy = DeclarativeStrategy(load_strategy_definition(STRATEGY))
    else:
        strategy = MixedTuning(
            no_topup=variant in ("NO_TOPUP", "FLOOR10"),
            topup_floor=.10 if variant == "FLOOR10" else 0,
        )
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=BACKTEST_START, end_date=EVENT_END,
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    value = history.loc[EVENT_START:EVENT_END, "Portfolio"]
    peak = value.cummax()
    trough = value.idxmin()
    drawdown = value.div(peak).sub(1)
    first, deep = None, None
    alerts = []
    if variant != "STRATEGY_28":
        prev_first = prev_deep = False
        for date, context in history.loc[EVENT_START:EVENT_END, "NotificationContext"].items():
            state = context["mixed_tuning"]
            if state["first_active"] and not prev_first:
                alerts.append({"date": date, "tier": "FIRST", "target": context["target_weights"]["QQQ"]})
                if first is None:
                    first = date
            if state["deep_active"] and not prev_deep:
                alerts.append({"date": date, "tier": "DEEP", "target": context["target_weights"]["QQQ"]})
                if deep is None:
                    deep = date
            prev_first, prev_deep = state["first_active"], state["deep_active"]
    first_loss = None if first is None else float(value.loc[first] / value.iloc[0] - 1)
    deep_loss = None if deep is None else float(value.loc[deep] / value.iloc[0] - 1)
    return {
        "EventReturn": float(value.iloc[-1] / value.iloc[0] - 1),
        "EventMDD": float(drawdown.min()),
        "MDDDate": str(drawdown.idxmin().date()),
        "TroughDate": str(trough.date()),
        "FirstAlert": None if first is None else str(first.date()),
        "LossAtFirstAlert": first_loss,
        "DeepAlert": None if deep is None else str(deep.date()),
        "LossAtDeepAlert": deep_loss,
        "TradesWholeBacktest": len(trades),
        "RebalancesWholeBacktest": len(rebalances),
    }, alerts


def main():
    summaries, alerts = [], []
    for name, scenario in SCENARIOS.items():
        with TemporaryDirectory(prefix="strategy30-stress-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=False)
            apply_credit_lag(directory, 1)
            inject(directory, scenario)
            for variant in ("STRATEGY_28", "MIXED_21_182", "NO_TOPUP"):
                result, changes = run(directory, variant)
                row = {"Scenario": name, "Variant": variant, **result}
                summaries.append(row)
                for event in changes:
                    alerts.append({"Scenario": name, "Variant": variant,
                                   "Date": str(event["date"].date()),
                                   "Tier": event["tier"], "QQQTarget": event["target"]})
                print(row, flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULT_DIR / "strategy30_unknown_crash_stress_summary.csv", index=False)
    pd.DataFrame(alerts).to_csv(RESULT_DIR / "strategy30_unknown_crash_stress_alerts.csv", index=False)


if __name__ == "__main__":
    main()
