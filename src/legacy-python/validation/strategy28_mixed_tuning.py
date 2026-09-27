"""Research-only tuning of the 21.182% strategy-28 composite overlay.

Compare credit observation lag, defense top-ups, partial recovery, and the
first-tier cap. No strategy YAML or production data is modified.
"""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_dotcom_fast_tier import FastTierOverlay
from strategy28_failed_dip_regime import (
    CURRENT, DOTCOM, EVENTS, GFC, ROOT, RESULT_DIR,
    build_data, event_result, summarize,
)
from backtest import Backtest
from config import COMMISSION, SLIPPAGE


class MixedTuning(FastTierOverlay):
    def __init__(self, *, cape_proxy=False, no_topup=False,
                 partial_release=False, first_cap=.65, topup_floor=0.0):
        super().__init__("REL10_CAP20_INDEPENDENT", cape_proxy=cape_proxy)
        self.no_topup = no_topup
        self.partial_release = partial_release
        self.first_cap = first_cap
        self.topup_floor = topup_floor
        self.partial_active = False
        self.partial_days = 0
        self.partial_activations = 0
        self.no_topup_suppressions = 0
        self.previous_research_target = None

    def evaluate(self, date, market, portfolio):
        prior_first = self.active
        prior_deep = self.deep_active
        prior_partial = self.partial_active
        signal = super().evaluate(date, market, portfolio)
        q, spy = market["QQQ"], market["SPY"]
        if not self.no_topup and not self.partial_release and self.first_cap == .65:
            self.notification_context = deepcopy(self.notification_context)
            self.notification_context["mixed_tuning"] = {
                "first_active": self.active, "deep_active": self.deep_active,
                "partial_active": False,
                "relative_roc20": float(q["RELATIVE_ROC20"]),
                "baa_spread": float(q["BAA_SPREAD"]),
            }
            return signal
        if not self.deep_active:
            self.partial_active = False
            self.partial_days = 0
        elif self.partial_release:
            rel20 = float(q["RELATIVE_ROC20"])
            change20 = float(q["BAA_CHANGE20"])
            if self.partial_active and (rel20 <= -5 or change20 >= .30):
                self.partial_active = False
                self.partial_days = 0
            if not self.partial_active:
                recovering = (
                    float(q["Close"]) > float(q["EMA55"])
                    and rel20 > 0
                    and change20 <= 0
                    and float(spy["Close"]) > float(spy["EMA55"])
                )
                self.partial_days = self.partial_days + 1 if recovering else 0
                if self.partial_days >= 5:
                    self.partial_active = True
                    self.partial_activations += 1
        base_target = dict(self.base.notification_context["target_weights"])
        cap = .35 if self.deep_active and self.partial_active else (
            .20 if self.deep_active else self.first_cap if self.active else 1.0
        )
        target = dict(base_target)
        if target["QQQ"] > cap:
            target["BIL"] += target["QQQ"] - cap
            target["QQQ"] = cap
        current = self._weights(portfolio, market)
        suppressed = self.no_topup and self.deep_active and prior_deep and target["QQQ"] > current["QQQ"]
        if suppressed:
            reduced_target = max(current["QQQ"], min(target["QQQ"], self.topup_floor))
            target["BIL"] += target["QQQ"] - reduced_target
            target["QQQ"] = reduced_target
            self.no_topup_suppressions += 1
        changed = (
            prior_first != self.active or prior_deep != self.deep_active
            or prior_partial != self.partial_active
        )
        if target != base_target:
            deviation = max(abs(current[t] - target[t]) for t in target)
            signal["rebalance"] = (
                changed or deviation >= .075
                or current["QQQ"] < target["QQQ"] - .04
                or (suppressed and self.topup_floor > 0
                    and current["QQQ"] < target["QQQ"] - .01)
            )
        elif target != signal["target"]:
            signal["rebalance"] = signal["rebalance"] or changed
        signal["target"] = target
        if changed:
            signal["reason"] = "MIXED_TUNING_TRANSITION"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["mixed_tuning"] = {
            "first_active": self.active, "deep_active": self.deep_active,
            "partial_active": self.partial_active,
            "relative_roc20": float(q["RELATIVE_ROC20"]),
            "baa_spread": float(q["BAA_SPREAD"]),
        }
        self.previous_research_target = deepcopy(target)
        return signal


def apply_credit_lag(directory: Path, sessions: int):
    if not sessions:
        return
    path = directory / "QQQ.csv"
    q = pd.read_csv(path, index_col="Date", parse_dates=True)
    spread = q["BAA_SPREAD"].shift(sessions)
    q["BAA_SPREAD"] = spread
    q["BAA_CHANGE20"] = spread.diff(20)
    q["BAA_DROP60"] = spread.rolling(60).max() - spread
    q.to_csv(path, index_label="Date")


def run(directory, dates, proxy, *, cost_multiple=1, **kwargs):
    strategy = MixedTuning(cape_proxy=proxy, **kwargs)
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
    ).run_all()
    return strategy, history, summarize(history, trades, rebalances, strategy)


def main():
    summaries, events, transitions = [], [], []
    variants = (
        ("BASE_L0", 0, {}),
        ("BASE_L1", 1, {}),
        ("BASE_L2", 2, {}),
        ("NO_TOPUP_L1", 1, {"no_topup": True}),
        ("NO_TOPUP_L2", 2, {"no_topup": True}),
        ("PARTIAL_L1", 1, {"partial_release": True}),
        ("CAP60_L1", 1, {"first_cap": .60}),
        ("CAP70_L1", 1, {"first_cap": .70}),
        ("CAP60_NO_TOPUP_L1", 1, {"first_cap": .60, "no_topup": True}),
    )
    for period, dates, proxy in (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    ):
        for name, lag, kwargs in variants:
            with TemporaryDirectory(prefix="mixed-tuning-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                build_data(directory, dotcom=proxy)
                apply_credit_lag(directory, lag)
                strategy, history, metrics = run(directory, dates, proxy, **kwargs)
                summaries.append({"Period": period, "Variant": name, **metrics,
                                  "DeepAlerts": strategy.deep_activations,
                                  "PartialAlerts": strategy.partial_activations,
                                  "NoTopupDays": strategy.no_topup_suppressions})
                spans = (
                    {k: v for k, v in EVENTS.items() if k != "DOTCOM"}
                    if period == "CURRENT" else
                    {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                    {"GFC": ("2007-10-31", "2009-03-09")}
                )
                for event, span in spans.items():
                    events.append({"Period": period, "Variant": name,
                                   "Event": event, **event_result(history, *span)})
                previous = None
                for date, ctx in history.NotificationContext.items():
                    state = ctx["mixed_tuning"]
                    flags = (state["first_active"], state["deep_active"], state["partial_active"])
                    if flags != previous:
                        transitions.append({
                            "Period": period, "Variant": name,
                            "Date": str(date.date()),
                            "First": flags[0], "Deep": flags[1], "Partial": flags[2],
                            "RelativeROC20": state["relative_roc20"],
                            "BAASpread": state["baa_spread"],
                            "QQQTarget": ctx["target_weights"]["QQQ"],
                        })
                    previous = flags
                print(summaries[-1], flush=True)
        if period == "DOTCOM_PROXY":
            with TemporaryDirectory(prefix="mixed-tuning-stress-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                build_data(directory, dotcom=True)
                apply_credit_lag(directory, 1)
                for name, kwargs in (
                    ("BASE_L1_COST3", {"cost_multiple": 3}),
                    ("NO_TOPUP_L1_COST3", {"cost_multiple": 3, "no_topup": True}),
                ):
                    strategy, _, metrics = run(directory, dates, proxy, **kwargs)
                    summaries.append({"Period": period, "Variant": name, **metrics,
                                      "DeepAlerts": strategy.deep_activations,
                                      "PartialAlerts": strategy.partial_activations,
                                      "NoTopupDays": strategy.no_topup_suppressions})
                    print(summaries[-1], flush=True)
                bil_path = directory / "BIL.csv"
                bil = pd.read_csv(bil_path)
                for field in ("Open", "High", "Low", "Close"):
                    bil[field] = 100.0
                bil.to_csv(bil_path, index=False)
                for name, kwargs in (
                    ("BASE_L1_ZERO_CASH", {}),
                    ("NO_TOPUP_L1_ZERO_CASH", {"no_topup": True}),
                ):
                    strategy, _, metrics = run(directory, dates, proxy, **kwargs)
                    summaries.append({"Period": period, "Variant": name, **metrics,
                                      "DeepAlerts": strategy.deep_activations,
                                      "PartialAlerts": strategy.partial_activations,
                                      "NoTopupDays": strategy.no_topup_suppressions})
                    print(summaries[-1], flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULT_DIR / "strategy28_mixed_tuning_summary.csv", index=False)
    pd.DataFrame(events).to_csv(RESULT_DIR / "strategy28_mixed_tuning_events.csv", index=False)
    pd.DataFrame(transitions).to_csv(RESULT_DIR / "strategy28_mixed_tuning_transitions.csv", index=False)


if __name__ == "__main__":
    main()
