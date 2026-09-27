"""Test a temporary systemic-risk bridge around the first credit warning."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest
from config import COMMISSION, SLIPPAGE
from strategy28_failed_dip_regime import (
    CURRENT, DOTCOM, GFC, EVENTS, ROOT, RESULT_DIR,
    build_data, event_result, summarize,
)
from strategy28_dotcom_fast_tier import FastTierOverlay


class SystemicBridge(FastTierOverlay):
    def __init__(self, cap, *, cape_proxy=False):
        super().__init__("REL10_CAP20_INDEPENDENT", cape_proxy=cape_proxy)
        self.bridge_cap = cap
        self.bridge_active = False
        self.bridge_activations = 0
        self.previous_first_active = False
        self.previous_bridge_target = None

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        if self.bridge_cap is None:
            return signal
        spy = market["SPY"]
        old_active = self.bridge_active
        entered_first = self.active and not self.previous_first_active
        self.previous_first_active = self.active
        if (
            entered_first
            and float(spy["DRAWDOWN120"]) <= -0.12
            and float(spy["ROC20"]) <= -5.0
        ):
            self.bridge_active = True
            self.bridge_activations += 1
        original_target = float(
            self.base.notification_context["target_weights"]["QQQ"]
        )
        if self.bridge_active and (original_target <= self.bridge_cap or not self.active):
            self.bridge_active = False
        target = dict(signal["target"])
        if self.bridge_active and target["QQQ"] > self.bridge_cap:
            delta = target["QQQ"] - self.bridge_cap
            target["QQQ"] = self.bridge_cap
            target["BIL"] += delta
        changed = (
            old_active != self.bridge_active
            and self.previous_bridge_target is not None
            and target != self.previous_bridge_target
        )
        if target != signal["target"]:
            current = self._weights(portfolio, market)
            deviation = max(abs(current[t] - target[t]) for t in target)
            qqq_under = current["QQQ"] < target["QQQ"] - 0.04
            signal["rebalance"] = changed or deviation >= 0.075 or qqq_under
        else:
            signal["rebalance"] = signal["rebalance"] or changed
        signal["target"] = target
        if changed:
            signal["reason"] = "SYSTEMIC_BRIDGE_CAP"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["systemic_bridge"] = {
            "active": self.bridge_active, "activations": self.bridge_activations
        }
        self.previous_bridge_target = deepcopy(target)
        return signal


def run(data_dir, dates, cap, *, proxy=False):
    strategy = SystemicBridge(cap, cape_proxy=proxy)
    history, trades, rebalances = Backtest(
        strategy, data_dir=data_dir, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    result = summarize(history, trades, rebalances, strategy)
    result["BridgeActivations"] = strategy.bridge_activations
    result["DeepActivations"] = strategy.deep_activations
    return result, history


def main():
    summaries, events, transitions = [], [], []
    periods = (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    )
    for period, dates, proxy in periods:
        with TemporaryDirectory(prefix="systemic-bridge-", dir=ROOT / "tmp") as temp:
            data_dir = Path(temp)
            build_data(data_dir, dotcom=proxy)
            for label, cap in (("NONE", None), ("CAP35", 0.35), ("CAP20", 0.20)):
                row, history = run(data_dir, dates, cap, proxy=proxy)
                summaries.append({"Period": period, "Mode": label, **row})
                spans = (
                    {k: v for k, v in EVENTS.items() if v[0] >= CURRENT[0]}
                    if period == "CURRENT" else
                    {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                    {"GFC": ("2007-10-31", "2009-03-09")}
                )
                for event, span in spans.items():
                    events.append({
                        "Period": period, "Mode": label, "Event": event,
                        **event_result(history, *span),
                    })
                previous = None
                for date, context in history.NotificationContext.items():
                    state = context.get("systemic_bridge", {"active": False})
                    active = state["active"]
                    if active != previous:
                        transitions.append({
                            "Period": period, "Mode": label,
                            "Date": str(date.date()), "Active": active,
                            "QQQTarget": context["target_weights"]["QQQ"],
                        })
                    previous = active
                print(summaries[-1], flush=True)
    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summaries).to_csv(
        RESULT_DIR / "strategy28_systemic_bridge_summary.csv", index=False
    )
    pd.DataFrame(events).to_csv(
        RESULT_DIR / "strategy28_systemic_bridge_events.csv", index=False
    )
    pd.DataFrame(transitions).to_csv(
        RESULT_DIR / "strategy28_systemic_bridge_transitions.csv", index=False
    )


if __name__ == "__main__":
    main()
