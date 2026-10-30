"""Offline, research-only downside overlays on the actual Strategy 30/32 YAML.

Uses the existing next-open execution engine and immutable input snapshots.
No production definitions are modified. The matched-exposure control uses an
ex-post scale for attribution only; it is not an independently tradable rule.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from shutil import copy2
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest import Backtest
from config import COMMISSION, DATA_DIR, SLIPPAGE
from downloader import _align_macro_to_qqq_sessions, _load_fred_series
from downside_signal_screen import build_features, load_inputs
from performance import Performance
from result_paths import research_result_path
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy28_failed_dip_regime import CURRENT, ROOT, build_data
from strategy30_31_yaml_parity import STRATEGY30, add_credit_observation
from strategy30_xlf_conditional_review import add_xlf
from strategy32_parity_review import STRATEGY32

VARIANTS = ("BASE", "PRICE_SLOW", "TERM_SLOW", "TERM_VVIX_SLOW",
            "TERM_FAST", "TERM_VOLUME_FAST", "TERM_MEMORY_FAST", "TERM_PRICE_FAST")
EVENTS = {
    "2015": ("2015-07-20", "2016-04-29"),
    "2018_Q4": ("2018-09-20", "2019-04-30"),
    "COVID": ("2020-02-19", "2020-08-31"),
    "2020_SEP": ("2020-09-02", "2020-12-31"),
    "2022": ("2021-11-19", "2023-12-29"),
    "2025": ("2025-02-19", "2025-10-31"),
}


def prepare(directory, credit_lag):
    build_data(directory, dotcom=False)
    path = directory / "QQQ.csv"
    qqq = pd.read_csv(path, index_col="Date", parse_dates=True)
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    qqq["BAA_SPREAD"] = _align_macro_to_qqq_sessions(
        baa, qqq.index, lag_sessions=credit_lag)
    qqq.to_csv(path, index_label="Date")
    add_credit_observation(directory)
    add_xlf(directory)
    copy2(DATA_DIR / "KRW=X.csv", directory / "KRW=X.csv")
    return qqq


def feature_frame(directory, qqq, vol_lag):
    frames, _ = load_inputs()
    # Use the SAME QQQ/SPY price vintage as the baseline and execution engine.
    frames["QQQ"] = qqq
    frames["SPY"] = pd.read_csv(directory / "SPY.csv", index_col="Date", parse_dates=True)
    features = build_features(frames, vol_lag=vol_lag)
    features["close"] = qqq.Close
    features["ema200"] = qqq.Close.ewm(span=200, adjust=False, min_periods=200).mean()
    features["long_bear"] = (qqq.Close < features.ema200) & (features.ema200.diff(20) < 0)
    return features


def cap_schedule(features, variant, *, cap=.8, price_drop=-.03):
    if variant not in VARIANTS:
        raise ValueError(variant)
    if not 0 < cap < .9:
        raise ValueError("The initial cap must be below the 90% recovery step")
    active, recovery_days, released_at = False, 0, -1000
    active_cap = cap
    rows = []
    for i, (date, row) in enumerate(features.iterrows()):
        needed = ["qqq_return5", "close", "qqq_ema20"]
        if variant.startswith("TERM"):
            needed += ["vix9d_vix", "vix_vix3m"]
        if variant == "TERM_VVIX_SLOW":
            needed += ["vvix_change5", "vvix_prior_q80"]
        if variant == "TERM_VOLUME_FAST":
            needed += ["down_volume_share5"]
        available = bool(row[needed].notna().all())
        entry = available and row.qqq_return5 < price_drop
        if variant.startswith("TERM"):
            entry = entry and row.vix9d_vix > 1.05 and row.vix_vix3m > 1
        if variant == "TERM_VVIX_SLOW":
            entry = entry and bool(row.vol_of_vol_stress)
        if variant == "TERM_VOLUME_FAST":
            entry = entry and row.down_volume_share5 > .65
        if variant == "BASE":
            entry = False
        entered = bool(entry and not active)
        if entry:
            if not active:
                active_cap = (.65 if variant == "TERM_MEMORY_FAST" and row.long_bear
                              and i - released_at <= 20 else cap)
            active, recovery_days = True, 0
        recovered = available and row.close > row.qqq_ema20 and row.qqq_return5 > 0
        if variant.endswith("FAST") and variant != "TERM_PRICE_FAST":
            recovered = recovered and row.vix9d_vix <= 1.05 and row.vix_vix3m <= 1
        if active and not entry:
            # Missing input holds defense and interrupts recovery confirmation.
            recovery_days = recovery_days + 1 if recovered else 0
            if recovery_days >= (2 if variant.endswith("FAST") else 3):
                active, released_at, recovery_days = False, i, 0
        effective_cap = (.9 if active and recovery_days == 1 and variant.endswith("FAST")
                         else active_cap if active else 1.)
        rows.append({"Cap": effective_cap, "Entry": entered,
                     "Available": available, "Active": active})
    return pd.DataFrame(rows, index=features.index)


class DownsideOverlay:
    """Cap the base target while preserving stronger defenses and no-top-up."""
    def __init__(self, base, schedule, *, scale=1.):
        self.base, self.schedule, self.scale = base, schedule, scale
        self.previous_cap = 1.
        self.previous_binding = False
        self.notification_context = None

    def __getattr__(self, name):
        return getattr(self.base, name)

    def evaluate(self, date, market, portfolio):
        signal = self.base.evaluate(date, market, portfolio)
        target = dict(signal["target"])
        original = target["QQQ"]
        cap = float(self.schedule.at[date, "Cap"])
        target["QQQ"] = min(original * self.scale, cap)
        target["BIL"] += original - target["QQQ"]
        binding = target["QQQ"] < original - 1e-12
        current = portfolio.weights({ticker: market[ticker]["Close"] for ticker in ("QQQ", "BIL")})
        deviation = max(abs(current.get(t, 0.) - w) for t, w in target.items())
        cap_changed = abs(cap - self.previous_cap) > 1e-12
        # An invisible cap change must not generate trades under stronger defense.
        transition = (binding != self.previous_binding or
                      cap_changed and (binding or self.previous_binding))
        if binding:
            rebalance = transition or deviation >= .075 or current.get("QQQ", 0.) - target["QQQ"] <= -.04
        else:
            rebalance = bool(signal["rebalance"]) or transition
        context = deepcopy(self.base.notification_context)
        context["target_weights"] = target.copy()
        context["downside_overlay"] = {"cap": cap, "binding": binding,
                                       "base_qqq": original}
        self.notification_context = context
        self.previous_cap, self.previous_binding = cap, binding
        return {**signal, "target": target, "rebalance": rebalance,
                "reason": "DOWNSIDE_TRANSITION" if transition else signal.get("reason")}


class StableDelayBacktest(Backtest):
    """Keep an identical pending order's deadline during delay stress tests.

    The production engine replaces every queued signal, even an identical one.
    Daily deviation signals can consequently postpone the order indefinitely.
    This research adapter changes only that repeated-identical-order behavior.
    """
    def _queue_rebalance(self, signal, signal_date):
        queued = self.delayed_rebalance
        target = signal.target_weights if hasattr(signal, "target_weights") else signal["target"]
        days = signal.execution_days if hasattr(signal, "execution_days") else signal["days"]
        if queued and self._delay_for_signal() > 0 and queued["days"] == days:
            tickers = set(queued["target"]) | set(target)
            if all(abs(queued["target"].get(t, 0.) - target.get(t, 0.)) < 1e-12 for t in tickers):
                return
        super()._queue_rebalance(signal, signal_date)


def evaluate(directory, number, schedule, *, cost=1., execution_delay=0,
             currency="USD", scale=1., direct=False):
    base = DeclarativeStrategy(load_strategy_definition(STRATEGY30 if number == 30 else STRATEGY32))
    if currency == "KRW":
        base.foreign_asset_tickers = base.holding_tickers
        base.valuation_currency = "KRW"
        base.valuation_fx_ticker = "KRW=X"
        base.valuation_signal_currency = "LOCAL"
        base.required_tickers = tuple(dict.fromkeys((*base.required_tickers, "KRW=X")))
    strategy = base if direct else DownsideOverlay(base, schedule, scale=scale)
    history, trades, rebalances = StableDelayBacktest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=CURRENT[0], end_date=CURRENT[1], signal_delay_days=execution_delay,
        commission=COMMISSION * cost, slippage=SLIPPAGE * cost).run_all()
    perf = Performance(history)
    targets = history.NotificationContext.map(lambda x: x["target_weights"]["QQQ"])
    metrics = {"CAGR": perf.cagr(), "MDD": perf.mdd(), "Trades": len(trades),
               "Rebalances": len(rebalances), "MeanQQQTarget": targets.mean(),
               "Start": str(history.index[0].date()), "End": str(history.index[-1].date()),
               "Rows": len(history), "ActiveDays": int(schedule.Active.sum()),
               "Entries": int(schedule.Entry.sum()),
               "UnavailableDays": int((~schedule.Available).sum()),
               "MeanQQQActual": history.Weights.map(lambda x: x.get("QQQ", 0.)).mean(),
               "TransactionCosts": float(history.TransactionCosts.iloc[-1])}
    # Trade amount / previous close NAV measures gross traded notional, both legs.
    if len(trades):
        prior_nav = history.Portfolio.shift(1)
        amounts = trades["Value"] if "Value" in trades else trades["Shares"] * trades["Price"]
        dates = pd.to_datetime(trades.Date)
        metrics["AnnualGrossTurnover"] = float((np.abs(amounts.to_numpy()) /
            prior_nav.reindex(dates).to_numpy()).sum() / ((history.index[-1] - history.index[0]).days / 365.25))
    return metrics, history, trades


def event_rows(history, baseline):
    rows = []
    for name, (start, end) in EVENTS.items():
        values = history.Portfolio.loc[start:end]
        reference = baseline.Portfolio.loc[start:end]
        trough = reference.idxmin()  # retrospective attribution, never an input
        peak_value = values.iloc[0]
        recovered = values.loc[trough:]
        recovery = recovered[recovered >= peak_value]
        rows.append({"Event": name, "Return": values.iloc[-1] / peak_value - 1,
                     "MDD": (values / values.cummax() - 1).min(),
                     "RecoverySessionsFromBaseTrough": (int(values.index.get_loc(recovery.index[0]) -
                         values.index.get_loc(trough)) if len(recovery) else None),
                     "RecoveredInWindow": bool(len(recovery)),
                     "AdvantageToBaseTrough": (values.loc[trough] / peak_value) /
                         (reference.loc[trough] / reference.iloc[0]) - 1,
                     "AdvantageAfterBaseTrough": (values.iloc[-1] / values.loc[trough]) /
                         (reference.iloc[-1] / reference.loc[trough]) - 1})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--strategies", type=int, nargs="+", choices=(30, 32), default=[30, 32])
    parser.add_argument("--cost", type=float, default=1.)
    parser.add_argument("--execution-delay", type=int, default=0)
    parser.add_argument("--data-lag", type=int, default=1)
    parser.add_argument("--currency", choices=("USD", "KRW"), default="USD")
    parser.add_argument("--cap", type=float, default=.8)
    parser.add_argument("--price-drop", type=float, default=-.03)
    parser.add_argument("--matched-control", action="store_true")
    parser.add_argument("--stem", default="pension_downside")
    args = parser.parse_args()
    summaries, events, windows, all_histories = [], [], [], {}
    with TemporaryDirectory(prefix="pension-downside-", dir=ROOT / "tmp") as temp:
        directory = Path(temp)
        qqq = prepare(directory, args.data_lag)
        complete_features = feature_frame(directory, qqq, args.data_lag)
        for cutoff in ("2018-12-31", "2020-03-31", "2022-06-30"):
            prefix = feature_frame(directory, qqq.loc[:cutoff], args.data_lag)
            pd.testing.assert_frame_equal(prefix, complete_features.loc[:cutoff])
        features = complete_features.loc[CURRENT[0]:CURRENT[1]]
        if features.empty:
            raise ValueError("No common observations")
        inventory = [{"File": path.name, "SHA256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in directory.glob("*.csv")]
        _, raw_inventory = load_inputs()
        research_result_path(args.stem + "_manifest.json").write_text(json.dumps({
            "Arguments": vars(args), "Inputs": inventory, "PublicCache": raw_inventory,
            "Commission": COMMISSION, "Slippage": SLIPPAGE,
            "Execution": "next open plus execution_delay sessions",
            "DelayAdapter": "Identical pending orders retain their original deadline",
            "SourceCodeSHA256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "Definitions": {str(n): hashlib.sha256(p.read_bytes()).hexdigest()
                            for n, p in ((30, STRATEGY30), (32, STRATEGY32))},
        }, indent=2), encoding="utf-8")
        for number in args.strategies:
            baseline = None
            means = {}
            variants = list(dict.fromkeys(("BASE", *args.variants)))
            if args.matched_control:
                if "TERM_FAST" not in variants:
                    raise ValueError("Matched control requires TERM_FAST")
                variants.append("MATCHED_EXPOST")
            for variant in variants:
                schedule = cap_schedule(features, "BASE" if variant == "MATCHED_EXPOST" else variant,
                                        cap=args.cap, price_drop=args.price_drop)
                # Prefix invariance checks the entire sequential state machine.
                if variant != "MATCHED_EXPOST":
                    for cutoff in ("2018-12-31", "2020-03-31", "2022-06-30"):
                        pd.testing.assert_frame_equal(schedule.loc[:cutoff], cap_schedule(
                            features.loc[:cutoff], variant, cap=args.cap, price_drop=args.price_drop))
                scale = means["TERM_FAST"] / means["BASE"] if variant == "MATCHED_EXPOST" else 1.
                metrics, history, trades = evaluate(directory, number, schedule, cost=args.cost,
                    execution_delay=args.execution_delay, currency=args.currency, scale=scale)
                if variant == "BASE":
                    baseline = history
                    if args.cost == 1 and args.execution_delay == 0 and args.data_lag == 1 and args.currency == "USD":
                        direct, direct_history, _ = evaluate(directory, number, schedule, direct=True)
                        pd.testing.assert_series_equal(history.Portfolio, direct_history.Portfolio)
                        assert metrics["Trades"] == direct["Trades"]
                means[variant] = metrics["MeanQQQTarget"]
                key = {"Strategy": number, "Variant": variant, "Currency": args.currency,
                       "CostMultiple": args.cost, "ExtraExecutionDelay": args.execution_delay,
                       "DataLag": args.data_lag, "InitialCap": args.cap, "PriceDrop": args.price_drop,
                       "ExposureScale": scale}
                summaries.append({**key, **metrics})
                events.extend({**key, **row} for row in event_rows(history, baseline))
                for start, end in (("2012-01-03", "2016-12-30"), ("2017-01-01", "2020-12-31"),
                                   ("2021-01-01", "2023-12-31"), ("2024-01-01", CURRENT[1])):
                    sample = history.loc[start:end]
                    perf = Performance(sample)
                    windows.append({**key, "WindowStart": start, "WindowEnd": end,
                                    "CAGR": perf.cagr(), "MDD": perf.mdd()})
                all_histories[f"{number}_{variant}"] = history.Portfolio
                export = history[["Portfolio"]].copy()
                export["QQQTarget"] = history.NotificationContext.map(lambda x: x["target_weights"]["QQQ"])
                export = export.join(schedule)
                export.to_csv(research_result_path(f"{args.stem}_{number}_{variant.lower()}_daily.csv"))
                trades.to_csv(research_result_path(f"{args.stem}_{number}_{variant.lower()}_trades.csv"), index=False)
                print({"Strategy": number, "Variant": variant, **metrics}, flush=True)
                # Checkpoint completed runs so an interrupted matrix is reviewable.
                for suffix, rows in (("summary", summaries), ("events", events), ("windows", windows)):
                    pd.DataFrame(rows).to_csv(research_result_path(args.stem + "_" + suffix + ".csv"), index=False)
        pd.DataFrame(all_histories).to_csv(research_result_path(args.stem + "_equity.csv"))


if __name__ == "__main__":
    main()
