"""Fixed-design component removal and independent pension trend experiments.

All runs are offline. USD signal prices are preserved during KRW translation.
Independent sleeves own separate capital; their NAVs are summed, never averaged
as fixed daily portfolio weights. No existing YAML or raw input is changed.
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

from config import COMMISSION, DATA_DIR, SLIPPAGE
from downloader import _align_macro_to_qqq_sessions, _load_fred_series
from pension_downside_review import StableDelayBacktest
from performance import Performance
from result_paths import research_result_path
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy28_failed_dip_regime import ROOT, build_data
from strategy30_31_yaml_parity import STRATEGY30, add_credit_observation
from strategy30_xlf_conditional_review import add_xlf
from strategy32_parity_review import STRATEGY32

LOCK_PATH = ROOT / "validation/pension_structure_lock_20261025.json"
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
VARIANTS = tuple(LOCK["variants"])
HORIZONS = tuple(LOCK["independent_sleeves"]["horizons"])


def definition_for(variant):
    """Disable a component at its source so target priority cannot reactivate it."""
    if variant not in VARIANTS[:7]:
        raise ValueError(variant)
    definition = deepcopy(load_strategy_definition(STRATEGY32 if variant == "BASE32" else STRATEGY30))
    if variant.startswith("DROP_"):
        definition["strategy"]["id"] += "-" + variant.lower() + "-research"
        for state, value in LOCK["ablation_states"][variant].items():
            definition["state"][state] = {"initial": value, "rules": []}
    return definition


def trend_features(qqq, bil):
    frame = pd.DataFrame(index=qqq.index)
    cash = bil.Close.reindex(qqq.index)
    for horizon in HORIZONS:
        frame[f"SMA{horizon}"] = qqq.Close.rolling(horizon, min_periods=horizon).mean()
        frame[f"EXCESS{horizon}"] = (qqq.Close / qqq.Close.shift(horizon)) / (cash / cash.shift(horizon)) - 1
    return frame


def prepare(directory, *, proxy):
    build_data(directory, dotcom=proxy)
    path = directory / "QQQ.csv"
    qqq = pd.read_csv(path, index_col="Date", parse_dates=True)
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    qqq["BAA_SPREAD"] = _align_macro_to_qqq_sessions(baa, qqq.index, lag_sessions=LOCK["macro_lag_sessions"])
    qqq.to_csv(path, index_label="Date")
    add_credit_observation(directory)
    add_xlf(directory)
    bil = pd.read_csv(directory / "BIL.csv", index_col="Date", parse_dates=True)
    features = trend_features(qqq, bil)
    for cutoff in ("2003-12-31", "2008-10-31", "2018-12-31", "2020-03-31", "2022-06-30"):
        if qqq.index.min() < pd.Timestamp(cutoff) < qqq.index.max():
            pd.testing.assert_frame_equal(features.loc[:cutoff], trend_features(qqq.loc[:cutoff], bil.loc[:cutoff]))
    qqq = qqq.join(features)
    qqq.to_csv(path, index_label="Date")
    copy2(DATA_DIR / "KRW=X.csv", directory / "KRW=X.csv")
    return directory


class BinaryTrend:
    STRATEGY_VERSION = "research-1"
    required_tickers = ("QQQ", "BIL")
    holding_tickers = ("QQQ", "BIL")
    valuation_signal_currency = "LOCAL"

    def __init__(self, family, horizon=0):
        self.family, self.horizon = family, horizon
        self.strategy_id = f"pension-{family}-{horizon}-research"
        self.previous = None
        self.notification_context = None
        fields = ("Close",) if family == "BUY_HOLD" else ("Close", f"{family}{horizon}")
        self.required_market_fields = {"QQQ": fields, "BIL": ("Close",)}

    def evaluate(self, date, market, portfolio):
        qqq = market["QQQ"]
        if self.family == "BUY_HOLD":
            target = 1.
        else:
            observed = float(qqq[f"{self.family}{self.horizon}"])
            if not np.isfinite(observed):
                raise ValueError("Signal unavailable on comparison calendar")
            target = float(qqq["Close"] > observed) if self.family == "SMA" else float(observed > 0)
        changed = self.previous is None or target != self.previous
        self.previous = target
        weights = {"QQQ": target, "BIL": 1-target}
        self.notification_context = {"target_weights": weights, "state_values": {"signal": target}}
        return {"rebalance": changed, "target": weights, "days": 1,
                "reason": f"{self.family}_{self.horizon}"}


def translated(strategy, currency):
    if currency == "KRW":
        strategy.foreign_asset_tickers = strategy.holding_tickers
        strategy.valuation_currency = "KRW"
        strategy.valuation_fx_ticker = "KRW=X"
        strategy.valuation_signal_currency = "LOCAL"
        strategy.required_tickers = tuple(dict.fromkeys((*strategy.required_tickers, "KRW=X")))
    return strategy


def run_strategy(directory, strategy, dates, calendar, *, cost, delay):
    engine = StableDelayBacktest(strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1], commission=COMMISSION*cost,
        slippage=SLIPPAGE*cost, signal_delay_days=delay)
    if calendar is not None:
        if len(calendar.difference(engine.data.index)):
            raise ValueError("Candidate cannot use the baseline calendar")
        engine.data = engine.data.loc[calendar]
        engine.market_data = engine.market_data.loc[calendar]
    return engine.run_all()


def aggregate_sleeves(histories):
    """Sum independently funded accounts; allow their capital shares to drift."""
    dates = histories[0].index
    if any(not h.index.equals(dates) for h in histories):
        raise ValueError("Independent sleeve dates differ")
    navs = pd.concat([h.Portfolio for h in histories], axis=1)
    result = pd.DataFrame({"Portfolio": navs.mean(axis=1)}, index=dates)
    exposure = sum(h.Portfolio * h.Weights.map(lambda x: x.get("QQQ", 0.)) for h in histories)
    targets = sum(h.Portfolio * h.NotificationContext.map(lambda x: x["target_weights"]["QQQ"]) for h in histories)
    capital = navs.sum(axis=1)
    result["QQQActual"] = exposure / capital
    result["QQQTarget"] = targets / capital
    result["TransactionCosts"] = sum(h.TransactionCosts for h in histories) / len(histories)
    return result


def export_history(history):
    if "QQQActual" in history:
        return history.copy()
    result = history[["Portfolio", "TransactionCosts"]].copy()
    result["QQQActual"] = history.Weights.map(lambda x: x.get("QQQ", 0.))
    result["QQQTarget"] = history.NotificationContext.map(lambda x: x["target_weights"]["QQQ"])
    return result


def metrics(history, trades, rebalances):
    perf = Performance(history)
    years = (history.index[-1] - history.index[0]).days / 365.25
    turnover = 0.
    if len(trades):
        notional = trades.Shares.abs() * trades.Price
        prior = history.Portfolio.shift(1).reindex(pd.to_datetime(trades.Date)).to_numpy()
        turnover = float(np.sum(notional.to_numpy() / prior) / years)
    return {"CAGR": perf.cagr(), "MDD": perf.mdd(), "Sharpe": perf.sharpe_ratio(),
            "Rebalances": len(rebalances), "Trades": len(trades),
            "TradeDates": int(pd.to_datetime(trades.Date).nunique()) if len(trades) else 0,
            "MeanQQQActual": history.QQQActual.mean(), "AnnualGrossTurnover": turnover,
            "Start": str(history.index[0].date()), "End": str(history.index[-1].date()),
            "Rows": len(history), "TransactionCosts": history.TransactionCosts.iloc[-1]}


def source_manifest(directory, args):
    sources = [STRATEGY30, STRATEGY32, LOCK_PATH, Path(__file__)]
    return {"Arguments": vars(args), "Lock": LOCK,
        "CodeAndDefinitions": [{"File": str(p.relative_to(ROOT)), "SHA256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in sources],
        "PreparedInputs": [{"File": p.name, "SHA256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in directory.glob("*.csv")],
        "DelayAdapter": "Repeated identical pending orders keep their original execution deadline"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", choices=LOCK["periods"], default=list(LOCK["periods"]))
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--cost", type=float, default=1.)
    parser.add_argument("--delay", type=int, default=0)
    parser.add_argument("--currency", choices=("USD", "KRW"), default="USD")
    parser.add_argument("--stem", default="pension_structure")
    args = parser.parse_args()
    summaries, windows, events = [], [], []
    for period in args.periods:
        if args.currency == "KRW" and period != "CURRENT":
            raise ValueError("KRW proxy histories are unavailable for the old crisis periods")
        dates = LOCK["periods"][period]
        with TemporaryDirectory(prefix="pension-structure-", dir=ROOT / "tmp") as temp:
            directory = prepare(Path(temp), proxy=period != "CURRENT")
            research_result_path(args.stem + "_" + period.lower() + "_manifest.json").write_text(
                json.dumps(source_manifest(directory, args), indent=2), encoding="utf-8")
            calendar, baseline, all_equity = None, None, {}
            for variant in dict.fromkeys(("BASE30", *args.variants)):
                strategy = translated(DeclarativeStrategy(definition_for(variant)), args.currency) if variant in VARIANTS[:7] else None
                if strategy is not None:
                    if period != "CURRENT":
                        strategy.parameters["valuation_arm_score"] = 30.
                        strategy.parameters["valuation_breakdown_points"] = 4.
                    h, trades, rebalances = run_strategy(directory, strategy, dates, calendar, cost=args.cost, delay=args.delay)
                    history = export_history(h)
                    # Capture evidence that the disabled state stayed disabled.
                    if variant.startswith("DROP_"):
                        for name, value in LOCK["ablation_states"][variant].items():
                            assert h.NotificationContext.map(lambda x: x["state_values"][name] == value).all(), name
                else:
                    family = variant.split("_")[0]
                    components, ledger, rebalances = [], [], []
                    horizons = (0,) if variant == "BUY_HOLD" else HORIZONS
                    for horizon in horizons:
                        member = BinaryTrend("BUY_HOLD" if variant == "BUY_HOLD" else family, horizon)
                        h, t, r = run_strategy(directory, translated(member, args.currency), dates, calendar, cost=args.cost, delay=args.delay)
                        components.append(h)
                        t = t.copy()
                        if len(t):
                            t["Shares"] /= len(horizons)
                            t["Fee"] /= len(horizons)
                            t["Cash"] /= len(horizons)
                            t["Sleeve"] = horizon
                        ledger.append(t)
                        rebalances.extend(r)
                        export_history(h).to_csv(research_result_path(f"{args.stem}_{period.lower()}_{variant.lower()}_{horizon}_sleeve.csv"))
                    history = aggregate_sleeves(components)
                    trades = pd.concat(ledger, ignore_index=True)
                if variant == "BASE30":
                    calendar, baseline = history.index, history
                if not history.index.equals(calendar):
                    raise AssertionError("Unequal comparison calendar")
                key = {"Period": period, "Variant": variant, "Currency": args.currency,
                       "CostMultiple": args.cost, "ExtraDelay": args.delay}
                row = {**key, **metrics(history, trades, rebalances)}
                row["CAGRDelta"] = row["CAGR"] - Performance(baseline).cagr()
                row["MDDDelta"] = row["MDD"] - Performance(baseline).mdd()
                summaries.append(row)
                spans = LOCK["current_subperiods"] if period == "CURRENT" else [dates]
                for start, end in spans:
                    sample, reference = history.loc[start:end], baseline.loc[start:end]
                    perf, ref = Performance(sample), Performance(reference)
                    windows.append({**key, "Start": start, "End": end, "CAGR": perf.cagr(),
                                    "MDD": perf.mdd(), "CAGRDelta": perf.cagr()-ref.cagr(), "MDDDelta": perf.mdd()-ref.mdd()})
                crisis = ({"DOTCOM": ("2000-03-27", "2002-10-07")} if period == "DOTCOM_PROXY" else
                          {"GFC": ("2007-10-31", "2009-03-09")} if period == "GFC_PROXY" else
                          {"2015": ("2015-07-20", "2016-04-29"), "2018": ("2018-09-20", "2019-04-30"),
                           "COVID": ("2020-02-19", "2020-08-31"), "2022": ("2021-11-19", "2023-12-29"),
                           "2025": ("2025-02-19", "2025-10-31")})
                for name, (start, end) in crisis.items():
                    values, ref = history.Portfolio.loc[start:end], baseline.Portfolio.loc[start:end]
                    events.append({**key, "Event": name, "Return": values.iloc[-1]/values.iloc[0]-1,
                        "MDD": (values/values.cummax()-1).min(), "RelativeReturn":
                        (values.iloc[-1]/values.iloc[0])/(ref.iloc[-1]/ref.iloc[0])-1})
                history.to_csv(research_result_path(f"{args.stem}_{period.lower()}_{variant.lower()}_daily.csv"))
                trades.to_csv(research_result_path(f"{args.stem}_{period.lower()}_{variant.lower()}_trades.csv"), index=False)
                all_equity[variant] = history.Portfolio
                for suffix, rows in (("summary", summaries), ("windows", windows), ("events", events)):
                    pd.DataFrame(rows).to_csv(research_result_path(args.stem + "_" + suffix + ".csv"), index=False)
                print(row, flush=True)
            pd.DataFrame(all_equity).to_csv(research_result_path(args.stem + "_" + period.lower() + "_equity.csv"))


if __name__ == "__main__":
    main()
