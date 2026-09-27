"""Research-only financial-lead and volatility-normalized crash overlays for 28.

Signals use closing data; Backtest executes a rebalance at the next open.
XLF prices are cached in ignored tmp/XLF.csv, not committed with this script.
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


class PredictiveOverlay(FastTierOverlay):
    def __init__(self, mode, *, cape_proxy=False, entry_delay=0):
        super().__init__("REL10_CAP20_INDEPENDENT", cape_proxy=cape_proxy)
        self.research_mode = mode
        self.entry_delay = entry_delay
        self.pending_fin = None
        self.pending_shock = None
        self.finance_active = False
        self.shock_active = False
        self.finance_safe = 0
        self.shock_safe = 0
        self.finance_activations = 0
        self.shock_activations = 0
        self.previous_overlay_target = None

    @property
    def required_market_fields(self):
        fields = super().required_market_fields
        fields["QQQ"] = tuple(sorted(set(fields["QQQ"]) | {
            "FIN_REL20", "QQQ_ROC5_Z", "VIX_ROC5", "VIX_CLOSE", "BAA_CHANGE5"
        }))
        return fields

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        q, spy = market["QQQ"], market["SPY"]
        spread, change20 = float(q["BAA_SPREAD"]), float(q["BAA_CHANGE20"])
        fin_trigger = (
            float(q["FIN_REL20"]) <= -8 and spread >= 1.8 and change20 >= .2
        )
        shock_trigger = (
            float(q["QQQ_ROC5_Z"]) <= -2.5
            and (float(q["VIX_ROC5"]) >= 30 or float(q["BAA_CHANGE5"]) >= .2)
        )
        old_fin, old_shock = self.finance_active, self.shock_active
        # Schedule the alert when first observed, then activate after N trading
        # sessions even if the one-day condition disappears in the meantime.
        if self.research_mode in ("FIN", "BOTH") and not self.finance_active:
            if self.pending_fin is None and fin_trigger:
                self.pending_fin = self.entry_delay
            elif self.pending_fin is not None:
                self.pending_fin -= 1
        if self.research_mode in ("SHOCK", "BOTH") and not self.shock_active:
            if self.pending_shock is None and shock_trigger:
                self.pending_shock = self.entry_delay
            elif self.pending_shock is not None:
                self.pending_shock -= 1
        if self.pending_fin is not None and self.pending_fin <= 0 and not self.finance_active:
            self.finance_active = True
            self.finance_activations += 1
            self.finance_safe = 0
            self.pending_fin = None
        if self.pending_shock is not None and self.pending_shock <= 0 and not self.shock_active:
            self.shock_active = True
            self.shock_activations += 1
            self.shock_safe = 0
            self.pending_shock = None
        if self.finance_active:
            safe = (float(q["FIN_REL20"]) > -2 and change20 <= 0
                    and float(spy["Close"]) > float(spy["EMA55"]))
            self.finance_safe = self.finance_safe + 1 if safe else 0
            if self.finance_safe >= 5:
                self.finance_active = False
        if self.shock_active:
            safe = (float(q["Close"]) > float(q["EMA55"])
                    and float(q["VIX_CLOSE"]) < 30)
            self.shock_safe = self.shock_safe + 1 if safe else 0
            if self.shock_safe >= 10:
                self.shock_active = False
        cap = 0.35 if self.finance_active or self.shock_active else 1.0
        target = dict(signal["target"])
        if target["QQQ"] > cap:
            target["BIL"] += target["QQQ"] - cap
            target["QQQ"] = cap
        changed = ((old_fin != self.finance_active or old_shock != self.shock_active)
                   and target != self.previous_overlay_target)
        if target != signal["target"]:
            current = self._weights(portfolio, market)
            deviation = max(abs(current[t] - target[t]) for t in target)
            signal["rebalance"] = changed or deviation >= .075 or current["QQQ"] < target["QQQ"] - .04
        else:
            signal["rebalance"] = signal["rebalance"] or changed
        signal["target"] = target
        if changed:
            signal["reason"] = "PREDICTIVE_SHOCK"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["predictive_shock"] = {
            "finance": self.finance_active, "shock": self.shock_active,
            "fin_rel20": float(q["FIN_REL20"]),
            "qqq_roc5_z": float(q["QQQ_ROC5_Z"]),
            "vix_roc5": float(q["VIX_ROC5"]),
        }
        self.previous_overlay_target = deepcopy(target)
        return signal


def add_data(directory):
    q = pd.read_csv(directory / "QQQ.csv", index_col="Date", parse_dates=True)
    spy = pd.read_csv(directory / "SPY.csv", index_col="Date", parse_dates=True)
    xlf_path = ROOT / "tmp" / "XLF.csv"
    if not xlf_path.is_file():
        raise RuntimeError("Research requires ignored tmp/XLF.csv (adjusted XLF OHLC)")
    xlf = pd.read_csv(xlf_path, index_col="Date", parse_dates=True)
    vix = pd.read_csv(ROOT / "data" / "VIX.csv", index_col="Date", parse_dates=True)
    relative = xlf["Close"].div(spy["Close"])
    q["FIN_REL20"] = (100 * relative.pct_change(20)).reindex(q.index)
    five = 100 * q["Close"].pct_change(5)
    baseline_vol = five.shift(1).rolling(60).std()
    q["QQQ_ROC5_Z"] = five / baseline_vol
    vix_close = vix["Close"].reindex(q.index).ffill()
    q["VIX_CLOSE"] = vix_close
    q["VIX_ROC5"] = 100 * vix_close.pct_change(5)
    q["BAA_CHANGE5"] = q["BAA_SPREAD"].diff(5)
    q.to_csv(directory / "QQQ.csv", index_label="Date")


def run(directory, dates, mode, proxy, delay=0, cost=1):
    s = PredictiveOverlay(mode, cape_proxy=proxy, entry_delay=delay)
    history, trades, rebalances = Backtest(
        s, data_dir=directory, tickers=s.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost, slippage=SLIPPAGE * cost,
    ).run_all()
    return s, history, summarize(history, trades, rebalances, s)


def main():
    summaries, events, transitions = [], [], []
    for period, dates, proxy in (
        ("CURRENT", CURRENT, False), ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    ):
        with TemporaryDirectory(prefix="predictive-shock-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=proxy)
            add_data(directory)
            for mode, delay, cost in (
                ("BASE", 0, 1), ("FIN", 0, 1), ("SHOCK", 0, 1),
                ("BOTH", 0, 1), ("FIN", 2, 1), ("FIN", 5, 1),
                ("FIN", 0, 3),
            ):
                s, h, metrics = run(directory, dates, mode, proxy, delay, cost)
                label = f"{mode}_D{delay}_C{cost}"
                summaries.append({"Period": period, "Mode": label, **metrics,
                                  "FinanceAlerts": s.finance_activations,
                                  "ShockAlerts": s.shock_activations,
                                  "DeepAlerts": s.deep_activations})
                spans = (
                    {k: v for k, v in EVENTS.items() if k != "DOTCOM"}
                    if period == "CURRENT" else
                    {"DOTCOM": EVENTS["DOTCOM"]} if period == "DOTCOM_PROXY" else
                    {"GFC": ("2007-10-31", "2009-03-09")}
                )
                for event, span in spans.items():
                    events.append({"Period": period, "Mode": label, "Event": event,
                                   **event_result(h, *span)})
                prior = None
                for date, ctx in h.NotificationContext.items():
                    state = ctx["predictive_shock"]
                    pair = (state["finance"], state["shock"])
                    if pair != prior and any(pair + (prior or (False, False))):
                        transitions.append({
                            "Period": period, "Mode": label, "Date": str(date.date()),
                            "Finance": pair[0], "Shock": pair[1],
                            "FinRel20": state["fin_rel20"],
                            "QQQZ5": state["qqq_roc5_z"],
                            "VIXChange5": state["vix_roc5"],
                            "QQQTarget": ctx["target_weights"]["QQQ"],
                        })
                    prior = pair
                print(summaries[-1], flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summaries).to_csv(RESULT_DIR / "strategy28_predictive_shock_summary.csv", index=False)
    pd.DataFrame(events).to_csv(RESULT_DIR / "strategy28_predictive_shock_events.csv", index=False)
    pd.DataFrame(transitions).to_csv(RESULT_DIR / "strategy28_predictive_shock_transitions.csv", index=False)


if __name__ == "__main__":
    main()
