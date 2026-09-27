"""Test a separate short-horizon market-crash overlay with the core 28 strategy."""

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


class CrashMix(FastTierOverlay):
    def __init__(self, mode: str, *, cape_proxy=False):
        super().__init__("REL10_CAP20_INDEPENDENT", cape_proxy=cape_proxy)
        self.mix_mode = mode
        self.market_crash_active = False
        self.market_crash_activations = 0
        self.market_crash_low = None
        self.market_crash_days = 0
        self.market_recovery_days = 0
        self.previous_market_target = None

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        if self.mix_mode == "SECTOR_ONLY":
            return signal
        qqq, spy = market["QQQ"], market["SPY"]
        qqq_close = float(qqq["Close"])
        old_active = self.market_crash_active
        broad_drop = (
            float(qqq["ROC5"]) <= -7.0
            and float(spy["ROC5"]) <= -5.0
            and float(spy["DRAWDOWN120"]) <= -0.08
            and float(qqq["BAA_CHANGE20"]) >= 0.30
        )
        if self.mix_mode == "MARKET20_RELATIVE":
            broad_drop = broad_drop and float(qqq["RELATIVE_ROC20"]) >= 0.0
        if not self.market_crash_active and broad_drop:
            self.market_crash_active = True
            self.market_crash_activations += 1
            self.market_crash_low = qqq_close
            self.market_crash_days = 0
            self.market_recovery_days = 0
        if self.market_crash_active:
            self.market_crash_days += 1
            self.market_crash_low = min(self.market_crash_low, qqq_close)
            recovered = (
                qqq_close >= self.market_crash_low * 1.08
                and float(qqq["ROC5"]) > 0
                and float(spy["ROC5"]) > 0
            )
            self.market_recovery_days = (
                self.market_recovery_days + 1 if recovered else 0
            )
            if self.market_crash_days >= 5 and self.market_recovery_days >= 2:
                self.market_crash_active = False

        cap = 0.20 if self.mix_mode.startswith("MARKET20") else 0.35
        target = dict(signal["target"])
        if self.market_crash_active and target["QQQ"] > cap:
            delta = target["QQQ"] - cap
            target["QQQ"] = cap
            target["BIL"] += delta
        changed = (
            old_active != self.market_crash_active
            and self.previous_market_target is not None
            and target != self.previous_market_target
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
            signal["reason"] = "MARKET_CRASH_CAP"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["market_crash"] = {
            "active": self.market_crash_active,
            "activations": self.market_crash_activations,
            "days": self.market_crash_days,
        }
        self.previous_market_target = deepcopy(target)
        return signal


def run(data_dir, dates, mode, *, proxy=False, cost_multiplier=1):
    strategy = CrashMix(mode, cape_proxy=proxy)
    history, trades, rebalances = Backtest(
        strategy, data_dir=data_dir, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost_multiplier,
        slippage=SLIPPAGE * cost_multiplier,
    ).run_all()
    summary = summarize(history, trades, rebalances, strategy)
    summary["SectorDeep"] = strategy.deep_activations
    summary["MarketCrashes"] = strategy.market_crash_activations
    return summary, history


def main():
    periods = (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    )
    modes = ("SECTOR_ONLY", "MARKET35", "MARKET20", "MARKET20_RELATIVE")
    summary_rows, event_rows, transitions = [], [], []
    for period, dates, proxy in periods:
        with TemporaryDirectory(prefix="crash-mix-", dir=ROOT / "tmp") as temp:
            data_dir = Path(temp)
            build_data(data_dir, dotcom=proxy)
            for mode in modes:
                row, history = run(data_dir, dates, mode, proxy=proxy)
                summary_rows.append({"Period": period, "Mode": mode, **row})
                spans = (
                    {k: v for k, v in EVENTS.items() if v[0] >= CURRENT[0]}
                    if period == "CURRENT" else
                    {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                    {"GFC": ("2007-10-31", "2009-03-09")}
                )
                for name, span in spans.items():
                    event_rows.append({
                        "Period": period, "Mode": mode, "Event": name,
                        **event_result(history, *span),
                    })
                previous = None
                for date, context in history.NotificationContext.items():
                    state = context.get("market_crash", {"active": False})
                    active = state["active"]
                    if active != previous:
                        transitions.append({
                            "Period": period, "Mode": mode,
                            "Date": str(date.date()), "MarketActive": active,
                            "QQQTarget": context["target_weights"]["QQQ"],
                        })
                    previous = active
                print(summary_rows[-1], flush=True)
            if period == "CURRENT":
                for mode in modes:
                    row, _ = run(data_dir, dates, mode, proxy=proxy, cost_multiplier=3)
                    summary_rows.append({"Period": "CURRENT_COST3", "Mode": mode, **row})

    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        RESULT_DIR / "strategy28_crash_overlay_mix_summary.csv", index=False
    )
    pd.DataFrame(event_rows).to_csv(
        RESULT_DIR / "strategy28_crash_overlay_mix_events.csv", index=False
    )
    pd.DataFrame(transitions).to_csv(
        RESULT_DIR / "strategy28_crash_overlay_mix_transitions.csv", index=False
    )


if __name__ == "__main__":
    main()
