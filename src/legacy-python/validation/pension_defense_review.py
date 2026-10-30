"""Causal defense-asset experiments and actual Korean ETF implementation replay.

Source decisions are fixed before any alternative defense portfolio is run.
The research ledger trades at opens using information already available then.
On a defense-only switch, risk share counts are preserved exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, PROJECT_ROOT, RESEARCH_DATA_DIR
from pension_downside_review import StableDelayBacktest
from pension_structure_review import prepare as prepare_source
from portfolio import Portfolio
from result_paths import research_result_path
from strategy_dsl import load_strategy_definition
from strategy15_isolated_gold_overlay import RecordingStrategy
from strategy30_31_yaml_parity import STRATEGY30
from strategy32_parity_review import STRATEGY32

LOCK_PATH = PROJECT_ROOT / "validation/pension_defense_lock_20261025.json"
LOCK = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
CACHE = RESEARCH_DATA_DIR / "pension_defense"
END = LOCK["source_period"][1]


def read_price(path):
    frame = pd.read_csv(path, index_col="Date", parse_dates=True).loc[:END]
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError(f"Invalid dates: {path}")
    if frame[["Open", "Close"]].isna().any().any() or (frame[["Open", "Close"]] <= 0).any().any():
        raise ValueError(f"Invalid prices: {path}")
    return frame


def source_schedule():
    dependency_paths = [Path(__file__), LOCK_PATH, STRATEGY30, STRATEGY32,
        PROJECT_ROOT/"tmp/BAA10Y.csv", PROJECT_ROOT/"tmp/XLF.csv"]
    dependency_paths += [DATA_DIR/(s+".csv") for s in ("QQQ", "BIL", "SPY", "KRW=X")]
    dependency_paths += [PROJECT_ROOT/"src/legacy-python"/s for s in
        ("config.py", "backtest.py", "portfolio.py", "strategy_dsl.py", "indicators.py", "downloader.py",
         "validation/pension_structure_review.py", "validation/pension_downside_review.py",
         "validation/strategy28_failed_dip_regime.py", "validation/strategy30_xlf_conditional_review.py")]
    key = {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in dependency_paths}
    cache_manifest = research_result_path("pension_defense_source_cache.json")
    if cache_manifest.exists():
        saved = json.loads(cache_manifest.read_text(encoding="utf-8"))
        valid = saved["Key"] == key and all(
            (PROJECT_ROOT/name).exists() and hashlib.sha256((PROJECT_ROOT/name).read_bytes()).hexdigest() == checksum
            for name, checksum in saved["Artifacts"].items())
        if valid:
            result = pd.read_csv(research_result_path("pension_defense_source30.csv"), index_col="Date", parse_dates=True)
            result["Rebalance"] = result.Rebalance.astype(bool)
            return result
    schedules, histories = {}, {}
    with TemporaryDirectory(prefix="pension-defense-source-", dir=PROJECT_ROOT / "tmp") as temp:
        directory = prepare_source(Path(temp), proxy=False)
        for number, path in ((30, STRATEGY30), (32, STRATEGY32)):
            points = {}
            strategy = RecordingStrategy(load_strategy_definition(path),
                lambda date, point: points.__setitem__(date, point))
            history, _, _ = StableDelayBacktest(strategy, data_dir=directory,
                tickers=strategy.required_tickers, start_date=LOCK["source_period"][0],
                end_date=END, commission=LOCK["commission"], slippage=LOCK["slippage"]).run_all()
            frame = pd.DataFrame({date: {"RiskTarget": point["target"]["QQQ"],
                "Rebalance": point["rebalance"]}
                for date, point in points.items()}).T.sort_index()
            frame["RiskTarget"] = frame.RiskTarget.astype(float)
            frame["Rebalance"] = frame.Rebalance.astype(bool)
            frame["TrendMode"] = history.NotificationContext.map(lambda context: context["state_values"]["trend_mode"])
            frame.index.name = "Date"
            schedules[number], histories[number] = frame, history.Portfolio
            frame.to_csv(research_result_path(f"pension_defense_source{number}.csv"))
    # The same research product mapping is used only after verifying equivalence.
    pd.testing.assert_frame_equal(schedules[30], schedules[32])
    pd.testing.assert_series_equal(histories[30], histories[32])
    previous = pd.read_csv(research_result_path("pension_structure_current_current_base30_daily.csv"), index_col=0, parse_dates=True)
    pd.testing.assert_series_equal(histories[30], previous.Portfolio, check_exact=False,
                                   check_names=False, rtol=1e-12, atol=1e-12)
    artifacts = [research_result_path(f"pension_defense_source{n}.csv") for n in (30, 32)]
    cache_manifest.write_text(json.dumps({"Key": key, "BaselineReproduced": True,
        "Artifacts": {str(p.relative_to(PROJECT_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts}}, indent=2), encoding="utf-8")
    return schedules[30]


def feature_frame(prices):
    frame = pd.DataFrame(index=prices.index)
    frame["Close"] = prices.Close
    frame["EMA200"] = prices.Close.ewm(span=200, adjust=False, min_periods=200).mean()
    frame["VOL60"] = prices.Close.pct_change(fill_method=None).rolling(60, min_periods=60).std()
    for horizon in LOCK["rotation"]["lookbacks"]:
        frame[f"ROC{horizon}"] = prices.Close.pct_change(horizon, fill_method=None)
    return frame


def select_mix(features, date, cash, reference, groups):
    scores = {}
    cash_row = features[reference].loc[date]
    if not cash_row[["ROC60", "ROC120", "ROC252"]].notna().all():
        raise ValueError("Comparison cash history is not ready")
    best = {}
    for asset, group in groups.items():
        row = features[asset].loc[date]
        if not row[["Close", "EMA200", "VOL60", "ROC60", "ROC120", "ROC252"]].notna().all():
            continue
        score = sum(weight*(row[f"ROC{h}"]-cash_row[f"ROC{h}"])
                    for h, weight in zip(LOCK["rotation"]["lookbacks"], LOCK["rotation"]["score_weights"]))
        if row.Close <= row.EMA200 or row.ROC60 <= cash_row.ROC60 or row.VOL60 <= 0 or score <= 0:
            continue
        scores[asset] = float(score)
        if group not in best or score > scores[best[group]]:
            best[group] = asset
    selected = sorted(best.values(), key=lambda asset: (-scores[asset], asset))[:2]
    inverse = {asset: 1/features[asset].at[date, "VOL60"] for asset in selected}
    mix = {}
    for asset in selected:
        capacity = .3 if groups[asset] == "GOLD" else .5
        mix[asset] = min(capacity, inverse[asset]/sum(inverse.values()))
    mix[cash] = 1-sum(mix.values())
    return mix


def load_market(universe):
    if universe == "LONG":
        raw = {s: read_price(DATA_DIR / (s+".csv")) for s in ("QQQ", "BIL", "GLD", "IEF", "SHY", "KRW=X")}
        fx = raw.pop("KRW=X")
        calendar = raw["QQQ"].index.intersection(fx.index)
        prices = {}
        for symbol, frame in raw.items():
            frame = frame.reindex(calendar).copy()
            frame["Open"] *= fx.Open.reindex(calendar)
            frame["Close"] *= fx.Close.reindex(calendar)
            prices[symbol] = frame[["Open", "Close"]]
        prices["SHORT"] = read_price(CACHE / "153130.KS.csv").reindex(calendar, method="ffill")[["Open", "Close"]]
        prices["ZERO"] = pd.DataFrame(1., index=calendar, columns=["Open", "Close"])
        return prices, {"QQQ": 1.}, fx.reindex(calendar), LOCK["long_period"]
    mapping = {"RISK1": DATA_DIR/"379810.KS.csv", "RISK2": DATA_DIR/"0015B0.KS.csv",
        "MM": CACHE/"488770.KS.csv", "SHORT": CACHE/"153130.KS.csv",
        "GOLD": CACHE/"411060.KS.csv", "GOVT3": DATA_DIR/"114100.KS.csv"}
    prices = {symbol: read_price(path)[["Open", "Close"]] for symbol, path in mapping.items()}
    # Preserve each asset's real pre-inception feature history. Never truncate
    # the short-bond/gold warm-up to the newer money-market ETF's inception.
    return prices, {"RISK1": .5, "RISK2": .5}, None, LOCK["domestic_period"]


def profile_policy(profile, features, date, fx_features=None):
    if profile == "USD_CASH":
        return {"BIL": 1.}
    if profile in ("KRW_SHORT", "SHORT"):
        return {"SHORT": 1.}
    if profile == "KRW_ZERO":
        return {"ZERO": 1.}
    if profile == "MM":
        return {"MM": 1.}
    if profile == "FX_SWITCH":
        row = fx_features.loc[date]
        return {"BIL" if row.Close > row.EMA200 and row.ROC60 > 0 else "SHORT": 1.}
    if profile == "ROTATE":
        return select_mix(features, date, "MM", "SHORT", LOCK["rotation"]["domestic_candidates"])
    cash = "BIL" if profile == "ROTATE_USD" else "SHORT"
    return select_mix(features, date, cash, cash, LOCK["rotation"]["long_candidates"])


class DefensePortfolio(Portfolio):
    """Hold risk units constant when switching only defense assets."""
    def __init__(self, risk, **kwargs):
        super().__init__(**kwargs)
        self.risk = tuple(risk)

    def trade(self, ticker, shares, price, date=None):
        if ticker != "ZERO":
            return super().trade(ticker, shares, price, date)
        commission, slippage = self.commission, self.slippage
        self.commission = self.slippage = 0.
        try:
            super().trade(ticker, shares, price, date)
        finally:
            self.commission, self.slippage = commission, slippage

    def execute(self, prices, risk_target, risk_mix, defense_mix, *, defense_only, date):
        weights = self.weights(prices)
        actual = sum(weights.get(asset, 0.) for asset in self.risk)
        target = {asset: 0. for asset in prices}
        if defense_only:
            for asset in self.risk:
                target[asset] = weights.get(asset, 0.)
            risk_target = actual
        else:
            shares = ({asset: weights.get(asset, 0.)/actual for asset in self.risk}
                      if actual > .7 and risk_target > .7 else risk_mix)
            for asset in self.risk:
                target[asset] = risk_target * shares[asset]
        for asset, share in defense_mix.items():
            target[asset] += (1-risk_target)*share
        before = {asset: self.positions.get(asset, 0.) for asset in self.risk}
        self.start_rebalance(target, days=1, date=date, reason="DEFENSE_ONLY" if defense_only else "SOURCE_EVENT")
        self.update(prices, date=date)
        if defense_only:
            for asset in self.risk:
                if abs(before[asset]-self.positions.get(asset, 0.)) > 1e-10:
                    raise AssertionError("Defense-only switch changed risk units")


def simulate(prices, risk_mix, schedule, profile, dates, *, cost=1., delay=0, fx=None):
    risk = tuple(risk_mix)
    full_calendar = prices[next(asset for asset in prices if asset not in risk)].index
    calendar = full_calendar
    for asset in prices:
        calendar = calendar.intersection(prices[asset].dropna().index)
    calendar = calendar[(calendar >= dates[0]) & (calendar <= dates[1])]
    if calendar.empty:
        raise ValueError("No actual-price overlap")
    features = {asset: feature_frame(frame) for asset, frame in prices.items() if asset not in risk and asset != "ZERO"}
    fx_features = feature_frame(fx) if fx is not None else None
    portfolio = DefensePortfolio(risk, commission=LOCK["commission"]*cost, slippage=LOCK["slippage"]*cost)
    capital = LOCK["numerical_ledger_capital_krw"]
    portfolio.cash = float(capital)
    last_source, last_month, mix, pending = None, None, None, None
    activity = []
    anchor = full_calendar[full_calendar < calendar[0]][-1]
    history_rows = [{"Date": anchor, "Portfolio": 1., "TransactionCosts": 0., "RiskWeight": 0.}]
    for i, date in enumerate(calendar):
        available = schedule.index[schedule.index < date]
        if not len(available):
            raise ValueError("No completed US decision before local open")
        asof = available[-1]
        point = schedule.loc[asof]
        arrived = schedule.loc[(schedule.index > last_source) & (schedule.index <= asof)] if last_source is not None else schedule.loc[:asof]
        source_event = bool(arrived.Rebalance.astype(bool).any()) if last_source is not None else True
        month = date.to_period("M")
        prior = full_calendar[full_calendar < date][-1]
        monthly = last_month != month
        old_mix = mix
        if monthly:
            mix = profile_policy(profile, features, prior, fx_features)
            last_month = month
        defense_switch = old_mix is not None and mix != old_mix
        if source_event:
            same_core_order = (pending is not None and not pending["DefenseOnly"]
                               and abs(pending["RiskTarget"]-float(point.RiskTarget)) < 1e-12)
            due = pending["Due"] if same_core_order else i+delay
            pending = {"Due": due, "RiskTarget": float(point.RiskTarget),
                       "Mix": mix.copy(), "DefenseOnly": False,
                       "SourceDate": asof, "PolicyDate": prior}
        elif defense_switch:
            if pending is not None:
                # A cash selection may update the destination, but must never
                # cancel a queued stock exit/reentry or restart its deadline.
                pending["Mix"], pending["PolicyDate"] = mix.copy(), prior
            else:
                pending = {"Due": i+delay, "RiskTarget": float(point.RiskTarget),
                           "Mix": mix.copy(), "DefenseOnly": True,
                           "SourceDate": asof, "PolicyDate": prior}
        opens = {asset: float(frame.at[date, "Open"]) for asset, frame in prices.items()}
        executed = None
        if pending is not None and pending["Due"] <= i:
            executed = pending
            portfolio.execute(opens, pending["RiskTarget"], risk_mix, pending["Mix"],
                defense_only=pending["DefenseOnly"], date=date)
            pending = None
        closes = {asset: float(frame.at[date, "Close"]) for asset, frame in prices.items()}
        weights = portfolio.weights(closes)
        history_rows.append({"Date": date, "Portfolio": portfolio.value(closes)/capital,
            "TransactionCosts": portfolio.total_costs/capital,
            "RiskWeight": sum(weights.get(asset, 0.) for asset in risk)})
        activity.append({"Date": date, "USSourceDate": asof, "PolicyAsOf": prior,
            "SourceEvent": source_event, "DefenseSwitch": defense_switch,
            "Executed": executed is not None,
            "DefenseOnly": bool(executed and executed["DefenseOnly"]),
            "Allocation": json.dumps(mix, sort_keys=True)})
        last_source = asof
    history = pd.DataFrame(history_rows).set_index("Date")
    trades = pd.DataFrame(portfolio.trades)
    if len(trades):
        for column in ("Shares", "Cash", "Fee"):
            trades[column] /= capital
    activity = pd.DataFrame(activity)
    assert (pd.to_datetime(activity.USSourceDate) < pd.to_datetime(activity.Date)).all()
    assert (pd.to_datetime(activity.PolicyAsOf) < pd.to_datetime(activity.Date)).all()
    return history, trades, activity, len(portfolio.rebalances)


def metrics(history, trades, rebalances):
    years = (history.index[-1]-history.index[0]).days/365.25
    values = history.Portfolio
    return {"CAGR": (values.iloc[-1]/values.iloc[0])**(1/years)-1,
        "Return": values.iloc[-1]/values.iloc[0]-1, "MDD": (values/values.cummax()-1).min(),
        "Trades": len(trades), "TradeDates": pd.to_datetime(trades.Date).nunique() if len(trades) else 0,
        "Rebalances": rebalances, "Costs": history.TransactionCosts.iloc[-1],
        "MeanRiskWeight": history.RiskWeight.iloc[1:].mean(),
        "Start": str(history.index[1].date()), "End": str(history.index[-1].date()), "Rows": len(history)-1}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=("LONG", "DOMESTIC"), required=True)
    parser.add_argument("--profiles", nargs="+")
    parser.add_argument("--cost", type=float, default=1.)
    parser.add_argument("--delay", type=int, default=0)
    parser.add_argument("--stem", required=True)
    args = parser.parse_args()
    prices, risk, fx, dates = load_market(args.universe)
    allowed = LOCK["long_profiles" if args.universe == "LONG" else "domestic_profiles"]
    profiles = args.profiles or allowed
    if not set(profiles).issubset(allowed):
        raise ValueError("Unknown profile")
    schedule = source_schedule()
    source_paths = [LOCK_PATH, Path(__file__), STRATEGY30, STRATEGY32,
        PROJECT_ROOT/"src/legacy-python/portfolio.py", PROJECT_ROOT/"src/legacy-python/validation/pension_downside_review.py"]
    source_paths.extend(CACHE.glob("*.csv"))
    source_paths.extend(DATA_DIR/(s+".csv") for s in ("QQQ", "BIL", "GLD", "IEF", "SHY", "KRW=X", "379810.KS", "0015B0.KS", "114100.KS"))
    research_result_path(args.stem+"_manifest.json").write_text(json.dumps({
        "Arguments": vars(args), "Design": LOCK,
        "InputsAndCode": [{"File": str(path.relative_to(PROJECT_ROOT)),
            "SHA256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in source_paths],
        "Source30And32VerifiedEqual": True,
    }, indent=2), encoding="utf-8")
    rows, windows = [], []
    for profile in profiles:
        history, trades, activity, rebalances = simulate(prices, risk, schedule, profile, dates,
            cost=args.cost, delay=args.delay, fx=fx)
        key = {"Universe": args.universe, "Profile": profile, "CostMultiple": args.cost, "ExtraDelay": args.delay}
        row = {**key, **metrics(history, trades, rebalances)}
        rows.append(row)
        for start, end in LOCK["subperiods"]:
            sample = history.loc[start:end]
            if len(sample) < 250:
                continue
            years = (sample.index[-1]-sample.index[0]).days/365.25
            windows.append({**key, "Start": start, "End": end,
                "CAGR": (sample.Portfolio.iloc[-1]/sample.Portfolio.iloc[0])**(1/years)-1,
                "MDD": (sample.Portfolio/sample.Portfolio.cummax()-1).min()})
        history.to_csv(research_result_path(f"{args.stem}_{profile.lower()}_daily.csv"))
        trades.to_csv(research_result_path(f"{args.stem}_{profile.lower()}_trades.csv"), index=False)
        activity.to_csv(research_result_path(f"{args.stem}_{profile.lower()}_activity.csv"), index=False)
        pd.DataFrame(rows).to_csv(research_result_path(args.stem+"_summary.csv"), index=False)
        pd.DataFrame(windows).to_csv(research_result_path(args.stem+"_windows.csv"), index=False)
        print(row, flush=True)


if __name__ == "__main__":
    main()
