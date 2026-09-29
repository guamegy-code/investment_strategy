"""Compare HY OAS with the exact deployed Strategy 30, in memory only.

The YAML on disk is never changed. Baseline and variants share the same
portfolio logic, release timing, rebalance conditions, and cost assumptions.
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest  # noqa: E402
from config import COMMISSION, DATA_DIR, SLIPPAGE  # noqa: E402
from downloader import _load_fred_series, _load_hy_oas_observations  # noqa: E402
from indicators import Indicator  # noqa: E402
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT, RESULT_DIR, build_data  # noqa: E402
from strategy28_hy_oas_review import attach_credit_features, rolling_metrics  # noqa: E402
from strategy30_31_yaml_parity import STRATEGY30, add_credit_observation  # noqa: E402

HY = "BAMLH0A0HYM2"
VARIANTS = ("BASE30", "HY_ENTRY", "BAA_OR_HY", "BAA_AND_HY", "FULL_HY")


def _replace_once(value: str, old: str, new: str) -> str:
    if value.count(old) != 1:
        raise ValueError(f"Strategy 30 rule changed: expected one {old!r}")
    return value.replace(old, new)


def definition_for(variant: str, *, hy_level: float = 4.0,
                   hy_change: float = 0.50) -> dict:
    definition = deepcopy(load_strategy_definition(STRATEGY30))
    if variant not in VARIANTS:
        raise ValueError(variant)
    if variant == "BASE30":
        return definition
    definition["strategy"]["id"] += f"-{variant.lower()}-research"
    definition["assets"]["observations"].append(HY)
    entry = definition["state"]["credit_guard"]["rules"][0]
    baa_gate = "BAA10Y.close >= 2 and variables.credit_change20 >= 0.30"
    hy_gate = f"{HY}.close >= {hy_level:g} and variables.hy_change20 >= {hy_change:.2f}"
    if variant in ("BAA_OR_HY", "BAA_AND_HY"):
        definition["variables"]["hy_change20"] = (
            f"{HY}.close - {HY}.close / (1 + {HY}.roc20 / 100)"
        )
        join = "or" if variant == "BAA_OR_HY" else "and"
        entry["when"] = _replace_once(
            entry["when"], baa_gate,
            f"(({baa_gate}) {join} ({hy_gate}))",
        )
    else:
        definition["variables"]["credit_change20"] = (
            f"{HY}.close - {HY}.close / (1 + {HY}.roc20 / 100)"
        )
        entry["when"] = _replace_once(
            entry["when"], "BAA10Y.close >= 2", f"{HY}.close >= {hy_level:g}"
        )
        entry["when"] = _replace_once(
            entry["when"], ">= 0.30", f">= {hy_change:.2f}"
        )
    if variant == "FULL_HY":
        release = definition["state"]["credit_guard"]["rules"][1]
        release["when"] = _replace_once(
            release["when"], "BAA10Y.close < 3.0", f"{HY}.close < 4.5"
        )
        deep = definition["state"]["deep_guard"]["rules"][1]
        deep["when"] = _replace_once(
            deep["when"],
            "BAA10Y.close < 2 or state.deep_credit_peak - BAA10Y.close >= 0.50",
            f"{HY}.close < 4 or state.deep_credit_peak - {HY}.close >= 0.75",
        )
        for rule in definition["state"]["deep_credit_peak"]["rules"]:
            rule["when"] = rule["when"].replace("BAA10Y.close", f"{HY}.close")
            rule["set"] = rule["set"].replace("BAA10Y.close", f"{HY}.close")
    return definition


def prepare(directory: Path, baa: pd.Series, hy: pd.Series, *, proxy: bool, lag: int):
    build_data(directory, dotcom=proxy)
    attach_credit_features(directory, baa, hy, lag)
    add_credit_observation(directory)
    qqq = pd.read_csv(directory / "QQQ.csv", index_col="Date", parse_dates=True)
    frame = pd.DataFrame(index=qqq.index)
    for field in ("Open", "High", "Low", "Close"):
        frame[field] = qqq["HY_OAS_LEVEL"]
    frame["Volume"] = 0
    frame = Indicator.add_indicators(frame.dropna(subset=["Close"]))
    frame.index.name = "Date"
    frame.to_csv(directory / f"{HY}.csv")


def run_one(directory, definition, dates, *, proxy: bool, cost: float, krw: bool = False):
    strategy = DeclarativeStrategy(definition)
    if proxy:
        strategy.parameters["valuation_arm_score"] = 30.0
        strategy.parameters["valuation_breakdown_points"] = 4.0
    if krw:
        strategy.foreign_asset_tickers = strategy.holding_tickers
        strategy.valuation_currency = "KRW"
        strategy.valuation_fx_ticker = "KRW=X"
        strategy.valuation_signal_currency = "LOCAL"
        strategy.required_tickers = tuple(dict.fromkeys((*strategy.required_tickers, "KRW=X")))
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION * cost, slippage=SLIPPAGE * cost,
    ).run_all()
    perf = Performance(history)
    states = history.NotificationContext.map(lambda x: x["state_values"])
    credit = states.map(lambda x: x["credit_guard"] == "TRUE")
    deep = states.map(lambda x: x["deep_guard"] == "TRUE")
    metrics = {
        "CAGR": perf.cagr(), "MDD": perf.mdd(),
        "Sharpe": perf.sharpe_ratio(), "Calmar": perf.calmar_ratio(),
        "Trades": len(trades), "Rebalances": len(rebalances),
        "CreditEpisodes": int((credit & ~credit.shift(1, fill_value=False)).sum()),
        "CreditDays": int(credit.sum()), "DeepDays": int(deep.sum()),
    }
    return metrics, history, credit, deep


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=("CURRENT", "DOTCOM_PROXY", "GFC_PROXY", "ALL"), default="ALL")
    parser.add_argument("--lags", type=int, nargs="+", default=[1])
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--cost", type=float, default=1.0)
    parser.add_argument("--hy-level", type=float, default=4.0)
    parser.add_argument("--hy-change", type=float, default=0.50)
    parser.add_argument("--krw", action="store_true")
    parser.add_argument("--output-stem", default="strategy30_hy_oas")
    args = parser.parse_args()
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    periods = (("CURRENT", CURRENT, False),
               ("DOTCOM_PROXY", DOTCOM, True), ("GFC_PROXY", GFC, True))
    summary, transitions, events, rolling = [], [], [], []
    for period, dates, proxy in periods:
        if args.period not in ("ALL", period):
            continue
        if args.krw and period != "CURRENT":
            continue
        for lag in args.lags:
            with TemporaryDirectory(prefix="strategy30-hy-oas-", dir=ROOT / "tmp") as temporary:
                directory = Path(temporary)
                prepare(directory, baa, hy, proxy=proxy, lag=lag)
                if args.krw:
                    copy2(DATA_DIR / "KRW=X.csv", directory / "KRW=X.csv")
                for variant in args.variants:
                    definition = definition_for(
                        variant, hy_level=args.hy_level, hy_change=args.hy_change
                    )
                    metrics, history, credit, deep = run_one(
                        directory, definition, dates, proxy=proxy, cost=args.cost,
                        krw=args.krw,
                    )
                    key = {"Period": period, "LagSessions": lag,
                           "CostMultiple": args.cost, "Variant": variant,
                           "HyLevel": args.hy_level, "HyChange20": args.hy_change,
                           "Currency": "KRW" if args.krw else "USD"}
                    summary.append({**key, **metrics})
                    for date in history.index[credit.ne(credit.shift(1, fill_value=False)) |
                                              deep.ne(deep.shift(1, fill_value=False))]:
                        transitions.append({**key, "Date": str(date.date()),
                                            "CreditGuard": bool(credit.loc[date]),
                                            "DeepGuard": bool(deep.loc[date])})
                    spans = ({"DOTCOM": ("2000-03-27", "2002-10-07")}
                             if period == "DOTCOM_PROXY" else
                             {"GFC": ("2007-10-31", "2009-03-09")}
                             if period == "GFC_PROXY" else
                             {"2018_Q4": ("2018-09-20", "2019-04-30"),
                              "COVID": ("2020-02-19", "2020-08-31"),
                              "2022_BEAR": ("2021-11-19", "2023-01-19"),
                              "2025": ("2025-02-19", "2025-04-08")})
                    for event, (start, end) in spans.items():
                        window = history.loc[start:end, "Portfolio"]
                        events.append({**key, "Event": event,
                                       "Return": float(window.iloc[-1] / window.iloc[0] - 1),
                                       "MDD": float((window / window.cummax() - 1).min())})
                    if lag == 1 and args.cost == 1.0:
                        for years in (3, 5):
                            rolling.extend({**key, **row} for row in rolling_metrics(history, years))
                    print(summary[-1], flush=True)
    RESULT_DIR.mkdir(exist_ok=True)
    for suffix, rows in (("summary", summary), ("transitions", transitions),
                         ("events", events), ("rolling", rolling)):
        pd.DataFrame(rows).to_csv(RESULT_DIR / f"{args.output_stem}_{suffix}.csv", index=False)


if __name__ == "__main__":
    main()
