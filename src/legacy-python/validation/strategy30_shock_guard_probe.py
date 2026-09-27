"""Research-only abrupt shared-crash guard for the proposed 30 composite."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT, RESULT_DIR, build_data, summarize
from strategy28_mixed_tuning import MixedTuning, apply_credit_lag
from strategy30_unknown_crash_stress import SCENARIOS, EVENT_END, inject
from backtest import Backtest
from config import COMMISSION, SLIPPAGE


class SharedShockGuard(MixedTuning):
    def __init__(self, *, cape_proxy=False):
        super().__init__(cape_proxy=cape_proxy)
        self.shock_active = False
        self.shock_recovery_days = 0
        self.shock_activations = 0

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        q, spy = market["QQQ"], market["SPY"]
        previous = self.shock_active
        trigger = float(q["ROC5"]) <= -12 and float(spy["ROC5"]) <= -7
        if trigger and not self.shock_active:
            self.shock_active = True
            self.shock_activations += 1
            self.shock_recovery_days = 0
        if self.shock_active:
            recovered = (
                float(q["Close"]) > float(q["EMA20"])
                and float(spy["Close"]) > float(spy["EMA20"])
                and float(q["ROC5"]) > 0
                and float(spy["ROC5"]) > 0
            )
            self.shock_recovery_days = self.shock_recovery_days + 1 if recovered else 0
            if self.shock_recovery_days >= 3:
                self.shock_active = False
                self.shock_recovery_days = 0
        target = dict(signal["target"])
        if self.shock_active and target["QQQ"] > .35:
            target["BIL"] += target["QQQ"] - .35
            target["QQQ"] = .35
        changed = self.shock_active != previous
        if target != signal["target"]:
            current = self._weights(portfolio, market)
            deviation = max(abs(current[t] - target[t]) for t in target)
            signal["rebalance"] = changed or deviation >= .075 or current["QQQ"] < target["QQQ"] - .04
        else:
            signal["rebalance"] = signal["rebalance"] or changed
        signal["target"] = target
        if changed:
            signal["reason"] = "SHARED_SHOCK_GUARD"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["shock_guard"] = {
            "active": self.shock_active,
            "qqq_roc5": float(q["ROC5"]),
            "spy_roc5": float(spy["ROC5"]),
        }
        return signal


def run(directory, dates, proxy):
    strategy = SharedShockGuard(cape_proxy=proxy)
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    event = history.loc["2024-01-02":"2024-12-31", "Portfolio"]
    metrics = summarize(history, trades, rebalances, strategy)
    metrics["ShockAlerts"] = strategy.shock_activations
    if len(event):
        metrics["EventReturn"] = float(event.iloc[-1] / event.iloc[0] - 1)
        metrics["EventMDD"] = float((event / event.cummax() - 1).min())
        previous = False
        dates = []
        for date, context in history.loc[event.index, "NotificationContext"].items():
            active = context["shock_guard"]["active"]
            if active and not previous:
                dates.append(date)
            previous = active
        metrics["EventShockAlerts"] = len(dates)
        metrics["FirstEventShockAlert"] = str(dates[0].date()) if dates else None
        metrics["LossAtEventShockAlert"] = (
            float(event.loc[dates[0]] / event.iloc[0] - 1) if dates else None
        )
    return metrics, history


def main():
    rows = []
    for name, scenario in SCENARIOS.items():
        with TemporaryDirectory(prefix="shock-guard-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=False)
            apply_credit_lag(directory, 1)
            inject(directory, scenario)
            metrics, _ = run(directory, (CURRENT[0], str(EVENT_END.date())), False)
            row = {"Period": "SCENARIO", "Scenario": name, **metrics}
            rows.append(row)
            print(row, flush=True)
    for period, dates, proxy in (
        ("CURRENT", CURRENT, False),
        ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    ):
        with TemporaryDirectory(prefix="shock-guard-history-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=proxy)
            apply_credit_lag(directory, 1)
            metrics, _ = run(directory, dates, proxy)
            row = {"Period": period, "Scenario": "HISTORICAL", **metrics}
            rows.append(row)
            print(row, flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(RESULT_DIR / "strategy30_shock_guard_probe_summary.csv", index=False)


if __name__ == "__main__":
    main()
