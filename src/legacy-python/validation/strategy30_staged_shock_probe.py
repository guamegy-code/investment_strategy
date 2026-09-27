"""Research-only staged QQQ/SPY crash guard for the 30 candidate.

Three predeclared entry/weight profiles share the same gradual recovery and
20-session cooldown. Synthetic scenarios and historical windows are compared.
"""

import argparse
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT, RESULT_DIR, build_data, summarize
from strategy28_mixed_tuning import MixedTuning, apply_credit_lag
from strategy30_unknown_crash_stress import SCENARIOS, EVENT_END, inject
from backtest import Backtest
from config import COMMISSION, SLIPPAGE


PROFILES = {
    "FAST_65_35": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .65, "deep_cap": .35},
    "FAST_70_50": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .70, "deep_cap": .50},
    "SLOW_65_35": {"entry": (5, -10, -6), "deep": (5, -12, -7), "first_cap": .65, "deep_cap": .35},
    "FAST_65_35_CALM": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .65, "deep_cap": .35, "pre_dd_min": -.05},
    "FAST_70_50_CALM": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .70, "deep_cap": .50, "pre_dd_min": -.05},
    "FAST_65_35_CALM_SHORT": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .65, "deep_cap": .35, "pre_dd_min": -.05, "stage1_max": 5},
    "FAST_70_50_CALM_SHORT": {"entry": (3, -7, -4), "deep": (5, -12, -7), "first_cap": .70, "deep_cap": .50, "pre_dd_min": -.05, "stage1_max": 5},
}


class StagedShockGuard(MixedTuning):
    def __init__(self, profile, *, cape_proxy=False):
        super().__init__(cape_proxy=cape_proxy)
        self.profile = PROFILES[profile]
        self.guard_stage = 0
        self.stage1_age = 0
        self.cooldown = 0
        self.safe_days = 0
        self.shock_activations = 0

    @property
    def required_market_fields(self):
        fields = super().required_market_fields
        for ticker in ("QQQ", "SPY"):
            fields[ticker] = tuple(sorted(set(fields.get(ticker, ())) | {"ROC3"}))
        fields["QQQ"] = tuple(sorted(set(fields["QQQ"]) | {"PRE_DD20_5"}))
        return fields

    def evaluate(self, date, market, portfolio):
        signal = super().evaluate(date, market, portfolio)
        q, spy = market["QQQ"], market["SPY"]
        previous = self.guard_stage
        if self.guard_stage == 1:
            self.stage1_age += 1
        self.cooldown = max(0, self.cooldown - 1)
        e_period, e_q, e_s = self.profile["entry"]
        d_period, d_q, d_s = self.profile["deep"]
        entry = float(q[f"ROC{e_period}"]) <= e_q and float(spy[f"ROC{e_period}"]) <= e_s
        if "pre_dd_min" in self.profile:
            entry = entry and float(q["PRE_DD20_5"]) > self.profile["pre_dd_min"]
        deep = float(q[f"ROC{d_period}"]) <= d_q and float(spy[f"ROC{d_period}"]) <= d_s
        if self.guard_stage == 0 and self.cooldown == 0 and entry:
            self.guard_stage = 1
            self.stage1_age = 1
            self.shock_activations += 1
            self.safe_days = 0
        if self.guard_stage == 1 and deep:
            self.guard_stage = 2
            self.stage1_age = 0
            self.safe_days = 0
        elif self.guard_stage == 2:
            safe = (
                float(q["Close"]) > float(q["EMA20"])
                and float(spy["Close"]) > float(spy["EMA20"])
                and float(q["ROC5"]) > 0
                and float(spy["ROC5"]) > 0
            )
            self.safe_days = self.safe_days + 1 if safe else 0
            if self.safe_days >= 3:
                self.guard_stage = 1
                self.stage1_age = 1
                self.safe_days = 0
        elif self.guard_stage == 1:
            safe = (
                float(q["Close"]) > float(q["EMA20"])
                and float(spy["Close"]) > float(spy["EMA20"])
                and float(q["ROC3"]) > 0
                and float(spy["ROC3"]) > 0
            )
            self.safe_days = self.safe_days + 1 if safe else 0
            if self.safe_days >= 3 or self.stage1_age >= self.profile.get("stage1_max", 10**9):
                self.guard_stage = 0
                self.stage1_age = 0
                self.cooldown = 20
                self.safe_days = 0
        cap = (
            self.profile["deep_cap"] if self.guard_stage == 2 else
            self.profile["first_cap"] if self.guard_stage == 1 else 1.0
        )
        target = dict(signal["target"])
        if target["QQQ"] > cap:
            target["BIL"] += target["QQQ"] - cap
            target["QQQ"] = cap
        changed = self.guard_stage != previous
        if target != signal["target"]:
            current = self._weights(portfolio, market)
            deviation = max(abs(current[t] - target[t]) for t in target)
            signal["rebalance"] = changed or deviation >= .075 or current["QQQ"] < target["QQQ"] - .04
        else:
            signal["rebalance"] = signal["rebalance"] or changed
        signal["target"] = target
        if changed:
            signal["reason"] = "STAGED_SHOCK_GUARD"
        self.target = target
        self.notification_context = deepcopy(self.notification_context)
        self.notification_context["target_weights"] = deepcopy(target)
        self.notification_context["staged_shock"] = {
            "stage": self.guard_stage, "cooldown": self.cooldown,
            "qqq_roc3": float(q["ROC3"]), "spy_roc3": float(spy["ROC3"]),
        }
        return signal


def add_roc3(directory):
    for ticker in ("QQQ", "SPY"):
        path = directory / f"{ticker}.csv"
        df = pd.read_csv(path, index_col="Date", parse_dates=True)
        df["ROC3"] = df["Close"].pct_change(3) * 100
        if ticker == "QQQ":
            if "DRAWDOWN20" not in df:
                df["DRAWDOWN20"] = df["Close"] / df["Close"].rolling(20).max() - 1
            df["PRE_DD20_5"] = df["DRAWDOWN20"].shift(5)
        df.to_csv(path, index_label="Date")


def run(directory, dates, profile, proxy):
    strategy = StagedShockGuard(profile, cape_proxy=proxy)
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    row = summarize(history, trades, rebalances, strategy)
    row["GuardAlerts"] = strategy.shock_activations
    event = history.loc["2024-01-02":"2024-12-31", "Portfolio"]
    if len(event):
        row["EventReturn"] = float(event.iloc[-1] / event.iloc[0] - 1)
        row["EventMDD"] = float((event / event.cummax() - 1).min())
        prior, activations = 0, []
        for date, context in history.loc[event.index, "NotificationContext"].items():
            stage = context["staged_shock"]["stage"]
            if stage > 0 and prior == 0:
                activations.append(date)
            prior = stage
        row["EventGuardAlerts"] = len(activations)
        row["FirstGuard"] = str(activations[0].date()) if activations else None
        row["LossAtFirstGuard"] = (
            float(event.loc[activations[0]] / event.iloc[0] - 1) if activations else None
        )
    return row, history


def main(*, gated_only=False, short_only=False):
    rows, transitions = [], []
    cases = [
        ("SCENARIO", name, (CURRENT[0], str(EVENT_END.date())), False, scenario)
        for name, scenario in SCENARIOS.items()
        if name in ("ORIGINAL_2024", "FAST_SHARED_SILENT", "FAST_GROWTH_LATE_CREDIT",
                    "REBOUND_RE_DROP", "FAST_V_RECOVERY")
    ]
    cases.extend((period, "HISTORICAL", dates, proxy, None) for period, dates, proxy in (
        ("CURRENT", CURRENT, False), ("DOTCOM_PROXY", DOTCOM, True),
        ("GFC_PROXY", GFC, True),
    ))
    for period, name, dates, proxy, scenario in cases:
        with TemporaryDirectory(prefix="staged-shock-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            build_data(directory, dotcom=proxy)
            apply_credit_lag(directory, 1)
            if scenario is not None:
                inject(directory, scenario)
            add_roc3(directory)
            profiles = (
                ("FAST_65_35_CALM_SHORT", "FAST_70_50_CALM_SHORT") if short_only else
                ("FAST_65_35_CALM", "FAST_70_50_CALM") if gated_only else
                ("FAST_65_35", "FAST_70_50", "SLOW_65_35")
            )
            for profile in profiles:
                metrics, history = run(directory, dates, profile, proxy)
                row = {"Period": period, "Scenario": name, "Profile": profile, **metrics}
                rows.append(row)
                previous = None
                for date, context in history.NotificationContext.items():
                    stage = context["staged_shock"]["stage"]
                    if stage != previous:
                        transitions.append({
                            "Period": period, "Scenario": name, "Profile": profile,
                            "Date": str(date.date()), "Stage": stage,
                            "QQQTarget": context["target_weights"]["QQQ"],
                        })
                    previous = stage
                print(row, flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_short" if short_only else "_gated" if gated_only else ""
    pd.DataFrame(rows).to_csv(RESULT_DIR / f"strategy30_staged_shock{suffix}_summary.csv", index=False)
    pd.DataFrame(transitions).to_csv(RESULT_DIR / f"strategy30_staged_shock{suffix}_transitions.csv", index=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gated-only", action="store_true")
    parser.add_argument("--short-only", action="store_true")
    args = parser.parse_args()
    main(gated_only=args.gated_only, short_only=args.short_only)
