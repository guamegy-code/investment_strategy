"""Research a faster second defense tier after the credit/relative warning."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_failed_dip_regime import (
    CURRENT, DOTCOM, GFC, EVENTS, ROOT, RESULT_DIR,
    FailedDipOverlay, build_data, event_result, summarize,
)
from backtest import Backtest
from config import COMMISSION, SLIPPAGE


class FastTierOverlay(FailedDipOverlay):
    def __init__(self, mode: str, *, cape_proxy: bool = False):
        self.independent_deep = mode == "REL10_CAP20_INDEPENDENT"
        super().__init__(
            -5.0, long_release=True, credit_gate=True,
            cape_proxy=cape_proxy,
            release_mode=("trend_spy" if self.independent_deep else "cycle_peak_spy"),
        )
        self.mode = mode
        self.deep_active = False
        self.deep_activations = 0
        self.deep_release_days = 0
        self.deep_credit_peak = None
        self.previous_final_target = None

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        if self.mode == "BASE_COMPOSITE":
            return signal
        qqq = market["QQQ"]
        old_deep = self.deep_active
        rel20 = float(qqq["RELATIVE_ROC20"])
        if not self.active and not self.independent_deep:
            self.deep_active = False
        elif self.active and not self.deep_active:
            if self.mode.startswith("FIXED"):
                deep = True
            elif self.mode.startswith("REL7_5"):
                deep = rel20 <= -7.5
            elif self.mode.startswith("REL8"):
                deep = rel20 <= -8.0
            elif self.mode.startswith("REL10"):
                deep = rel20 <= -10.0
            elif self.mode.startswith("REL12"):
                deep = rel20 <= -12.0
            elif self.mode == "FAST_TREND35":
                deep = (
                    float(qqq["Close"]) < float(qqq["EMA20"])
                    and float(qqq["EMA20"]) < float(qqq["EMA55"])
                    and float(qqq["ROC20"]) < 0
                )
            else:
                raise ValueError(self.mode)
            if deep:
                self.deep_active = True
                self.deep_activations += 1
                self.deep_release_days = 0
                self.deep_credit_peak = float(qqq["BAA_SPREAD"])
        if self.independent_deep and self.deep_active:
            spread = float(qqq["BAA_SPREAD"])
            self.deep_credit_peak = max(self.deep_credit_peak, spread)
            spy = market["SPY"]
            recovered = (
                float(qqq["Close"]) > float(qqq["EMA200"])
                and float(qqq["EMA55"]) > float(qqq["EMA200"])
                and float(qqq["RELATIVE_ROC60"]) > 0
                and float(spy["Close"]) > float(spy["EMA200"])
                and (spread < 2.0 or self.deep_credit_peak - spread >= 0.50)
            )
            self.deep_release_days = self.deep_release_days + 1 if recovered else 0
            if self.deep_release_days >= 10:
                self.deep_active = False
                self.deep_release_days = 0
        cap = (
            0.20 if self.mode.endswith("CAP20") or self.independent_deep or self.mode == "FIXED20" else
            0.50 if self.mode == "FIXED50" else 0.35
        )
        target = dict(signal["target"])
        if self.deep_active and target["QQQ"] > cap:
            delta = target["QQQ"] - cap
            target["QQQ"] = cap
            target["BIL"] += delta
        changed = (
            old_deep != self.deep_active
            and self.previous_final_target is not None
            and target != self.previous_final_target
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
            signal["reason"] = "FAST_SECOND_TIER"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["fast_tier"] = {
            "active": self.deep_active,
            "activations": self.deep_activations,
            "relative_roc20": rel20,
        }
        self.previous_final_target = deepcopy(target)
        return signal


def run(data_dir, dates, mode, *, proxy=False, cost_multiplier=1.0):
    strategy = FastTierOverlay(mode, cape_proxy=proxy)
    history, trades, rebalances = Backtest(
        strategy, data_dir=data_dir, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost_multiplier,
        slippage=SLIPPAGE * cost_multiplier,
    ).run_all()
    row = summarize(history, trades, rebalances, strategy)
    row["DeepActivations"] = strategy.deep_activations
    return row, history


def main():
    modes = (
        "BASE_COMPOSITE", "FIXED50", "FIXED35", "FIXED20",
        "REL7_5_CAP35",
        "REL8_CAP20", "REL10_CAP35", "REL10_CAP20",
        "REL12_CAP20", "REL10_CAP20_INDEPENDENT", "FAST_TREND35",
    )
    periods = (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    )
    summary_rows, events, transitions = [], [], []
    for period, dates, proxy in periods:
        with TemporaryDirectory(prefix="fast-tier-", dir=ROOT / "tmp") as temp:
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
                    events.append({
                        "Period": period, "Mode": mode, "Event": name,
                        **event_result(history, *span),
                    })
                previous = None
                for date, context in history.NotificationContext.items():
                    state = context.get("fast_tier", {"active": False})
                    active = state["active"]
                    if active != previous:
                        transitions.append({
                            "Period": period, "Mode": mode,
                            "Date": str(date.date()), "DeepActive": active,
                            "RelativeROC20": state.get("relative_roc20"),
                            "QQQTarget": context["target_weights"]["QQQ"],
                        })
                    previous = active
                print(summary_rows[-1], flush=True)
            if period == "CURRENT":
                for mode in (
                    "BASE_COMPOSITE", "FIXED35", "REL10_CAP20",
                    "REL10_CAP20_INDEPENDENT",
                ):
                    row, _ = run(
                        data_dir, dates, mode, proxy=proxy, cost_multiplier=3.0
                    )
                    summary_rows.append({"Period": "CURRENT_COST3", "Mode": mode, **row})
            if period == "DOTCOM_PROXY":
                for mode in (
                    "BASE_COMPOSITE", "FIXED35", "REL10_CAP20",
                    "REL10_CAP20_INDEPENDENT",
                ):
                    row, _ = run(
                        data_dir, dates, mode, proxy=proxy, cost_multiplier=3.0
                    )
                    summary_rows.append({"Period": "DOTCOM_COST3", "Mode": mode, **row})
                bil_path = data_dir / "BIL.csv"
                bil = pd.read_csv(bil_path)
                for field in ("Open", "High", "Low", "Close"):
                    bil[field] = 100.0
                bil.to_csv(bil_path, index=False)
                for mode in (
                    "BASE_COMPOSITE", "FIXED35", "REL10_CAP20",
                    "REL10_CAP20_INDEPENDENT",
                ):
                    row, _ = run(data_dir, dates, mode, proxy=proxy)
                    summary_rows.append({"Period": "DOTCOM_ZERO_CASH", "Mode": mode, **row})

    RESULT_DIR.mkdir(exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(
        RESULT_DIR / "strategy28_dotcom_fast_tier_summary.csv", index=False
    )
    pd.DataFrame(events).to_csv(
        RESULT_DIR / "strategy28_dotcom_fast_tier_events.csv", index=False
    )
    pd.DataFrame(transitions).to_csv(
        RESULT_DIR / "strategy28_dotcom_fast_tier_transitions.csv", index=False
    )


if __name__ == "__main__":
    main()
