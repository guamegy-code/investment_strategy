"""Closed-loop pension product allocation; separate historical and active-fund evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, EXTENDED_DATA_DIR, PROJECT_ROOT, RESEARCH_DATA_DIR
from pension_downside_review import StableDelayBacktest
from pension_structure_review import prepare
from portfolio import Portfolio
from result_paths import research_result_path
from strategy_dsl import DeclarativeStrategy, ProductMappedStrategy, load_strategy_definition
from strategy30_31_yaml_parity import STRATEGY30
from strategy32_parity_review import STRATEGY32

LOCK_PATH = PROJECT_ROOT / "validation/pension_risk_allocation_lock_20261026.json"
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
ASSETS = ("R1", "R2", "CASH")


def read_price(path):
    frame = pd.read_csv(path, index_col="Date", parse_dates=True)[["Open", "Close"]]
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Invalid calendar: {path}")
    if not np.isfinite(frame.to_numpy()).all() or (frame <= 0).any().any():
        raise ValueError(f"Invalid prices: {path}")
    return frame


def case_prices(case, stress=None):
    spec = LOCK["cases"][case]
    cache = RESEARCH_DATA_DIR / "pension_proxy"
    paths = {
        "R1": (DATA_DIR if case == "DOMESTIC" else cache) / (spec["core"] + ".csv"),
        "R2": (DATA_DIR if case == "DOMESTIC" else cache) / (spec["alternative"] + ".csv"),
        "CASH": (RESEARCH_DATA_DIR / "pension_defense/488770.KS.csv" if case == "DOMESTIC" else
                 (EXTENDED_DATA_DIR if spec["period"] == "PROXY" else DATA_DIR) / "BIL.csv"),
    }
    prices = {s: read_price(path).loc[:spec["end"]] for s, path in paths.items()}
    if stress:
        if case != "DOMESTIC":
            raise ValueError("Idiosyncratic scenarios are domestic only")
        shocks = LOCK["conditional_stress"]
        knots = shocks["alternative_only_factors"][stress]
        frame = prices["R2"].copy()
        dates = frame.index[frame.index >= shocks["start"]]
        factors = np.interp(np.arange(len(dates)), [p[0] for p in knots], [p[1] for p in knots])
        frame.loc[dates] = frame.loc[dates].mul(factors, axis=0)
        prices["R2"] = frame
    return prices, paths


def risk_features(prices, lookback=None):
    lookback = lookback or LOCK["risk_mix"]["volatility_lookback"]
    closes = pd.concat({s: prices[s].Close for s in ("R1", "R2")}, axis=1, join="inner")
    returns = closes.pct_change(fill_method=None)
    sigma = returns.rolling(lookback, min_periods=lookback).std()
    share = (sigma.R1 / (sigma.R1 + sigma.R2)).clip(upper=LOCK["risk_mix"]["alternative_share_cap"])
    share = share.where((sigma.R1 > 0) & (sigma.R2 > 0))
    return share.rename("AlternativeShare")


def cppi_cap(nav, peak):
    if not np.isfinite(nav) or nav <= 0 or peak < nav:
        raise ValueError("Invalid account NAV/peak")
    rule = LOCK["cppi"]
    return float(np.clip(rule["multiplier"] * (1 - (1-rule["drawdown_budget"]) * peak/nav), 0, 1))


def product_mapper(source):
    definition = {
        "strategy": {"id": "pension-product-risk-research", "name": "Pension research", "version": 1,
                     "dsl_version": 1, "enabled": True},
        "source": "research-source", "products": {"QQQ": {"R1": .5, "R2": .5}, "BIL": {"CASH": 1.}},
    }
    return ProductMappedStrategy(definition, source)


def source_bundle(directory, case, number):
    spec = LOCK["cases"][case]
    source = DeclarativeStrategy(load_strategy_definition(STRATEGY30 if number == 30 else STRATEGY32))
    proxy = spec["period"] == "PROXY"
    if proxy:
        source.parameters["valuation_arm_score"] = 30.
        source.parameters["valuation_breakdown_points"] = 4.
    warm = LOCK["source_warm_start"]["DOTCOM" if case.startswith("DOTCOM") else "GFC" if proxy else "CURRENT"]
    engine = StableDelayBacktest(source, data_dir=directory, tickers=source.required_tickers,
                                start_date=warm, end_date=spec["end"])
    columns = engine.market_data.columns
    markets = {values[0]: engine.get_market(dict(zip(columns, values[1:])))
               for values in engine.market_data.itertuples(index=True, name=None)}
    prior = engine.data.index[engine.data.index < spec["start"]]
    if prior.empty:
        raise ValueError("No source initialization session")
    engine.data, engine.market_data = engine.data.loc[prior], engine.market_data.loc[prior]
    engine.run_all()
    return {"Source": source, "Date": prior[-1], "InitialTarget": dict(source.target),
            "Markets": {d: m for d, m in markets.items() if d > prior[-1]}}


def execute_order(portfolio, opens, order, date):
    """A stock-composition-only order cannot silently trade the cash ETF."""
    target = dict(order["Target"])
    cash_before = portfolio.positions.get("CASH", 0.)
    if order["MixOnly"]:
        actual = portfolio.weights(opens)
        risk = actual.get("R1", 0.) + actual.get("R2", 0.)
        target = {"R1": risk*(1-order["Share"]), "R2": risk*order["Share"],
                  "CASH": actual.get("CASH", 0.)}
    if min(target.values()) < -1e-12 or sum(target.values()) > 1+1e-10:
        raise AssertionError("Invalid target")
    portfolio.start_rebalance(target, days=1, date=date, reason=order["Reason"])
    portfolio.update(opens, date=date)
    if order["MixOnly"] and abs(portfolio.positions.get("CASH", 0.)-cash_before) > 1e-8:
        raise AssertionError("Stock-only order changed cash-ETF shares")
    if portfolio.cash < -1e-6 or min(portfolio.positions.values(), default=0.) < -1e-8:
        raise AssertionError("Borrowing/short position")


def simulate(prices, bundle, profile, start, end, *, cost=1., delay=0):
    if profile not in LOCK["profiles"] or delay not in (0, 1):
        raise ValueError("Unknown profile/delay")
    source = product_mapper(deepcopy(bundle["Source"]))
    standalone = profile in ("EQUITY50", "CPPI_ONLY")
    pure_mix = profile in ("MONTHLY50", "RISK_BALANCE", "EQUITY50", "CPPI_ONLY")
    has_cap = profile in ("CPPI_CAP", "CPPI_ONLY")
    full_calendar = prices["CASH"].index
    calendar = full_calendar
    for frame in prices.values():
        calendar = calendar.intersection(frame.index)
    calendar = calendar[(calendar >= start) & (calendar <= end)]
    if calendar.empty:
        raise ValueError("No execution overlap")
    anchor = full_calendar[full_calendar < calendar[0]][-1]
    features = risk_features(prices)
    data = {s: f.to_dict("index") for s, f in prices.items()}
    indexes = {s: f.index for s, f in prices.items()}
    source_dates = sorted(d for d in bundle["Markets"] if d < calendar[-1])
    cursor, last_source = 0, bundle["Date"]
    initial_risk = float(bundle["InitialTarget"]["QQQ"])
    point = {"target": {"R1": initial_risk*.5, "R2": initial_risk*.5, "CASH": 1-initial_risk}}
    portfolio = Portfolio(commission=LOCK["commission"]*cost, slippage=LOCK["slippage"]*cost)
    capital = float(LOCK["numerical_capital"])
    portfolio.cash = capital
    peak, prior_nav, share, month, queued = capital, capital, .5, None, {}
    history = [{"Date": anchor, "Portfolio": 1., "TransactionCosts": 0., "RiskWeight": 0.,
                "AlternativeShare": 0., "Cap": 1., "SourceRisk": initial_risk}]
    activities = []

    for i, date in enumerate(calendar):
        prior = calendar[i-1] if i else anchor
        source_event = i == 0
        # Evaluate before today's execution. Product marks never come from the
        # future local close, even when several US sessions passed over a KRX closure.
        if not standalone:
            while cursor < len(source_dates) and source_dates[cursor] < date:
                d = source_dates[cursor]
                market = dict(bundle["Markets"][d])
                for asset in ASSETS:
                    pos = indexes[asset].searchsorted(d, side="right")-1
                    if pos < 0:
                        raise ValueError("Source attempted to use pre-inception product prices")
                    mark_date = indexes[asset][pos]
                    if mark_date >= date:
                        raise AssertionError("Future product mark")
                    market[asset] = {"Close": data[asset][mark_date]["Close"]}
                point = source.evaluate(d, market, portfolio)
                if point["days"] != 1:
                    raise ValueError("This research design requires one-session orders")
                source_event |= bool(point["rebalance"])
                last_source = d
                cursor += 1
        monthly = month != date.to_period("M")
        if monthly:
            if profile == "RISK_BALANCE":
                available = features.loc[:prior]
                estimate = available.iloc[-1] if len(available) else np.nan
                share = float(estimate) if pd.notna(estimate) else LOCK["risk_mix"]["missing_history_share"]
            elif pure_mix:
                share = .5
            month = date.to_period("M")
        core_target = 1. if standalone else float(point["target"]["R1"]+point["target"]["R2"])
        if not pure_mix:
            share = float(point["target"]["R2"])/core_target if core_target > 0 else .5
        cap = cppi_cap(prior_nav, peak) if has_cap else 1.
        target_risk = min(core_target, cap)
        if i:
            previous_closes = {s: data[s][prior]["Close"] for s in ASSETS}
            weights = portfolio.weights(previous_closes)
            actual_risk = weights.get("R1", 0.)+weights.get("R2", 0.)
        else:
            actual_risk = 0.
        cap_event = has_cap and abs(actual_risk-target_risk) > LOCK["cppi"]["trade_band"]
        full_order = (i == 0) or (source_event and not standalone) or cap_event
        mix_only = not full_order and monthly and pure_mix and actual_risk > 1e-10
        reason = "SOURCE" if full_order else "MONTHLY_MIX"
        if cap_event:
            reason = "CPPI"
        if full_order or mix_only:
            order = {"Target": {"R1": target_risk*(1-share), "R2": target_risk*share,
                                 "CASH": 1-target_risk}, "Share": share, "MixOnly": mix_only,
                     "Reason": reason, "DecisionDate": date, "PolicyDate": prior,
                     "SourceDate": last_source, "Due": i+delay}
            # An existing older order must not be refreshed with today's data.
            # Multiple fresh components are composed above, before submission.
            queued[i+delay] = order
        order = queued.pop(i, None)
        opens = {s: data[s][date]["Open"] for s in ASSETS}
        if order is not None:
            if not order["PolicyDate"] < date or not order["SourceDate"] < date:
                raise AssertionError("Noncausal execution")
            execute_order(portfolio, opens, order, date)
        closes = {s: data[s][date]["Close"] for s in ASSETS}
        nav = portfolio.value(closes)
        weights = portfolio.weights(closes)
        risk = weights.get("R1", 0.)+weights.get("R2", 0.)
        history.append({"Date": date, "Portfolio": nav/capital, "TransactionCosts": portfolio.total_costs/capital,
                        "RiskWeight": risk, "AlternativeShare": weights.get("R2", 0.)/risk if risk else 0.,
                        "Cap": cap, "SourceRisk": core_target})
        activities.append({"Date": date, "USSourceDate": last_source, "PolicyAsOf": prior,
                           "SourceEvent": source_event, "CapEvent": cap_event, "Monthly": monthly,
                           "Executed": order is not None, "MixOnly": bool(order and order["MixOnly"]),
                           "OrderPolicyDate": order["PolicyDate"] if order else None,
                           "OrderDecisionDate": order["DecisionDate"] if order else None,
                           "RawSourceTarget": json.dumps(point["target"], sort_keys=True)})
        prior_nav, peak = nav, max(peak, nav)
    trades = pd.DataFrame(portfolio.trades)
    if len(trades):
        for column in ("Shares", "Cash", "Fee"):
            trades[column] /= capital
    return pd.DataFrame(history).set_index("Date"), trades, pd.DataFrame(activities), len(portfolio.rebalances)


def metrics(history, trades, rebalances):
    nav = history.Portfolio
    years = (nav.index[-1]-nav.index[0]).days/365.25
    dd = nav/nav.cummax()-1
    underwater = dd < -1e-10
    groups = (~underwater).cumsum()
    longest = int(underwater.groupby(groups).sum().max())
    return {"CAGR": float((nav.iloc[-1]/nav.iloc[0])**(1/years)-1), "Return": float(nav.iloc[-1]-1),
            "MDD": float(dd.min()), "TradeDates": int(pd.to_datetime(trades.Date).nunique()) if len(trades) else 0,
            "Trades": len(trades), "Rebalances": rebalances, "Costs": float(history.TransactionCosts.iloc[-1]),
            "MeanRisk": float(history.RiskWeight.iloc[1:].mean()),
            "LongestUnderwaterSessions": longest, "BelowFloorSessions": int((dd < -.2).sum()),
            "NearCashSessions": int((history.RiskWeight.iloc[1:] < .01).sum()),
            "Rows": len(history)-1, "Start": str(history.index[1].date()), "End": str(history.index[-1].date())}


def export(stem, key, history, trades, activity, rebalances):
    profile = key["Profile"].lower()
    for suffix, frame in (("daily", history), ("trades", trades), ("activity", activity)):
        frame.to_csv(research_result_path(f"{stem}_{profile}_{suffix}.csv"), index=suffix == "daily")
    return {**key, **metrics(history, trades, rebalances)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", choices=LOCK["cases"], default=list(LOCK["cases"]))
    parser.add_argument("--profiles", nargs="+", choices=LOCK["profiles"], default=LOCK["profiles"])
    parser.add_argument("--source", type=int, choices=(30, 32), default=30)
    parser.add_argument("--cost", type=float, default=1.)
    parser.add_argument("--delay", type=int, choices=(0, 1), default=0)
    parser.add_argument("--stress", choices=LOCK["conditional_stress"]["alternative_only_factors"])
    parser.add_argument("--prefix-check", action="store_true")
    parser.add_argument("--stem", required=True)
    args = parser.parse_args()
    summaries, bundles, prepared, checks = [], {}, {}, []
    with TemporaryDirectory(prefix="pension-risk-", dir=PROJECT_ROOT/"tmp") as temp:
        for period in {LOCK["cases"][case]["period"] for case in args.cases}:
            directory = Path(temp)/period
            directory.mkdir()
            prepare(directory, proxy=period == "PROXY")
            prepared[period] = directory
        for case in args.cases:
            spec = LOCK["cases"][case]
            bundle_key = (spec["period"], spec["start"], spec["end"], args.source)
            if bundle_key not in bundles:
                bundles[bundle_key] = source_bundle(prepared[spec["period"]], case, args.source)
            bundle = bundles[bundle_key]
            prices, paths = case_prices(case, args.stress)
            stem = f"{args.stem}_{case.lower()}"
            for profile in args.profiles:
                history, trades, activity, rebalances = simulate(prices, bundle, profile, spec["start"], spec["end"],
                                                               cost=args.cost, delay=args.delay)
                key = {"Case": case, "Source": args.source, "Profile": profile, "Currency": spec["currency"],
                       "CostMultiple": args.cost, "ExtraDelay": args.delay, "ConditionalStress": args.stress or "NONE"}
                row = export(stem, key, history, trades, activity, rebalances)
                summaries.append(row)
                pd.DataFrame(summaries).to_csv(research_result_path(args.stem+"_summary.csv"), index=False)
                print(row, flush=True)
                if args.prefix_check:
                    cutoff = history.index[max(2, len(history)//2)]
                    prefix_bundle = {**bundle, "Markets": {d: m for d, m in bundle["Markets"].items() if d <= cutoff}}
                    prefix = simulate({s: f.loc[:cutoff] for s, f in prices.items()}, prefix_bundle, profile,
                                      spec["start"], str(cutoff.date()), cost=args.cost, delay=args.delay)[0]
                    pd.testing.assert_frame_equal(prefix, history.loc[:cutoff], check_exact=False, rtol=1e-12, atol=1e-12)
                    checks.append({**key, "Cutoff": str(cutoff.date()), "Passed": True})
            inputs = [LOCK_PATH, Path(__file__), STRATEGY30, STRATEGY32, *paths.values(),
                      PROJECT_ROOT/"src/legacy-python/strategy_dsl.py", PROJECT_ROOT/"src/legacy-python/portfolio.py",
                      PROJECT_ROOT/"src/legacy-python/backtest.py", PROJECT_ROOT/"src/legacy-python/strategy_domain.py",
                      Path(prepare.__code__.co_filename), PROJECT_ROOT/"src/legacy-python/validation/pension_downside_review.py"]
            inputs += [DATA_DIR/(s+".csv") for s in ("QQQ", "BIL", "SPY", "KRW=X", "shiller_cape")]
            inputs += [EXTENDED_DATA_DIR/(s+".csv") for s in ("QQQ", "BIL")]
            inputs += [PROJECT_ROOT/"tmp"/(s+".csv") for s in ("BAA10Y", "XLF")]
            manifest = {"Arguments": vars(args), "Design": LOCK,
                        "InputsSHA256": {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
                        "PreparedSHA256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in prepared[spec["period"]].glob("*.csv")},
                        "SourceInitializationDate": str(bundle["Date"].date()), "PrefixChecks": checks}
            research_result_path(stem+"_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
