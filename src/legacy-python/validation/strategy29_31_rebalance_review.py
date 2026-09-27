"""Inspect 26/29/31 rebalance timing and research-only 31 allocation variants."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest
from config import COMMISSION, DATA_DIR, EXTENDED_DATA_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy30_31_yaml_parity import (
    ROOT, STRATEGY29, STRATEGY31, DOTCOM, GFC, add_credit_observation,
    apply_credit_lag, build_data,
)
from tdf_proxy import build_proxy_frame


STRATEGY26 = ROOT / "strategies/26_band_7030_tdf_valuation_defense.yaml"
START, END = "2012-01-03", "2026-07-31"


def run(definition, directory, dates=(START, END)):
    strategy = DeclarativeStrategy(deepcopy(definition))
    history, trades, rebalances = Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=dates[0], end_date=dates[1],
        commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()
    return history, trades, rebalances


def metric_row(name, result):
    history, trades, rebalances = result
    perf = Performance(history)
    return {
        "name": name, "cagr": round(100 * float(perf.cagr()), 3),
        "mdd": round(100 * float(perf.mdd()), 3),
        "trades": len(trades), "rebalances": len(rebalances),
        "end_value": round(float(history.Portfolio.iloc[-1]), 5),
    }


def rebalance_frame(rebalances):
    return pd.DataFrame([{
        "signal": str(row["Date"].date()),
        "execution": str(row.get("ExecutionDate", pd.NaT).date())
        if pd.notna(row.get("ExecutionDate")) else None,
        "qqq_pre": round(float(row.get("PreWeights", {}).get("QQQ", 0)), 3),
        "qqq_target": round(float(row["Target"].get("QQQ", 0)), 3),
        "tdf_target": round(float(row["Target"].get("TDF2050_PROXY", 0)), 3),
        "reason": row.get("Reason"),
    } for row in rebalances])


def annual_gap(a, b):
    aligned = pd.concat([a.Portfolio.rename("29"), b.Portfolio.rename("31")], axis=1)
    yearly = aligned.groupby(aligned.index.year)
    return pd.DataFrame([{
        "year": year,
        "29": round(100 * (part["29"].iloc[-1] / part["29"].iloc[0] - 1), 2),
        "31": round(100 * (part["31"].iloc[-1] / part["31"].iloc[0] - 1), 2),
    } for year, part in yearly])


def candidate(base, name):
    definition = deepcopy(base)
    definition["strategy"]["id"] += f"-{name}-research"
    if "qqq_cap75" in name:
        definition["rebalance"][0]["when"] = (
            definition["rebalance"][0]["when"].strip()
            + " or weight_deviation('QQQ') >= 5%"
        )
        if name == "qqq_cap75":
            return definition
        name = name.removeprefix("qqq_cap75_")
    if name.startswith("release_"):
        release = definition["state"]["credit_guard"]["rules"][1]
        release["confirm"] = 10 if "spread" in name else 5
        if name == "release_no_ema55":
            release["when"] = release["when"].replace("and QQQ.ema55 > QQQ.ema200", "")
        elif name == "release_no_spy":
            release["when"] = release["when"].replace("and SPY.close > SPY.ema200", "")
        elif name.startswith("release_spread"):
            cutoff = int(name.removeprefix("release_spread")) / 100
            release["when"] = release["when"].replace("and QQQ.ema55 > QQQ.ema200", "")
            release["when"] = release["when"].strip() + f" and BAA10Y.close < {cutoff}"
    elif name.startswith("tier_"):
        cap = int(name.split("_")[1])
        guard_index = next(i for i, rule in enumerate(definition["target"])
                           if rule.get("when") == "state.credit_guard == 'TRUE'")
        definition["target"].insert(guard_index, {
            "when": (
                "state.credit_guard == 'TRUE' and state.deep_guard == 'FALSE' "
                "and state.trend_mode == 'BULL' and QQQ.close > QQQ.ema200 "
                "and QQQ.roc60 > 0 and SPY.close > SPY.ema200"
            ),
            "weights": {
                "QQQ": f"{cap}%", "TDF2050_PROXY": "30%",
                "BIL": f"{70-cap}%",
            },
        })
    else:
        raise ValueError(name)
    return definition


def first_normal_rebalance(rebalances):
    return next((str(row["ExecutionDate"].date()) for row in rebalances
                 if row["Date"] >= pd.Timestamp("2023-01-01")
                 and row["Target"].get("QQQ") == .70), None)


def interval_return(history, start, end):
    part = history.loc[start:end, "Portfolio"]
    return round(100 * (part.iloc[-1] / part.iloc[0] - 1), 3)


def make_proxy(directory, proxy_ticker="SPY"):
    build_data(directory, dotcom=True)
    apply_credit_lag(directory, 1)
    add_credit_observation(directory)
    spy = pd.read_csv(DATA_DIR / "SPY.csv", index_col="Date", parse_dates=True)
    qqq = pd.read_csv(EXTENDED_DATA_DIR / "QQQ.csv", index_col="Date", parse_dates=True)
    bnd = pd.read_csv(EXTENDED_DATA_DIR / "BND.csv", index_col="Date", parse_dates=True)
    tdf = build_proxy_frame(
        {"SPY": spy, "VXUS": spy if proxy_ticker == "SPY" else qqq, "BND": bnd},
        observation_lag=1,
    )
    tdf.to_csv(directory / "TDF2050_PROXY.csv")


def main():
    with TemporaryDirectory(prefix="strategy31-review-", dir=ROOT / "tmp") as temp:
        directory = Path(temp)
        build_data(directory, dotcom=False)
        apply_credit_lag(directory, 1)
        add_credit_observation(directory)
        (directory / "TDF2050_PROXY.csv").write_bytes(
            (DATA_DIR / "TDF2050_PROXY.csv").read_bytes()
        )
        defs = {
            "26": load_strategy_definition(STRATEGY26),
            "29": load_strategy_definition(STRATEGY29),
            "31": load_strategy_definition(STRATEGY31),
        }
        cap_only = "--cap-only" in sys.argv
        results = {name: run(definition, directory) for name, definition in defs.items()
                   if not cap_only or name == "31"}
        print("METRICS")
        for name, result in results.items():
            print(metric_row(name, result), flush=True)
        if not cap_only:
            print("ANNUAL")
            print(annual_gap(results["29"][0], results["31"][0]).to_string(index=False))
        if not cap_only:
            for name, result in results.items():
                print(f"REBALANCES {name}")
                print(rebalance_frame(result[2]).to_string(index=False))
        variants = (("qqq_cap75", "qqq_cap75_release_spread250") if cap_only else (
            "release_confirm5", "release_no_ema55", "release_no_spy",
            "release_spread220", "release_spread250", "tier_60", "tier_70",
            "qqq_cap75", "qqq_cap75_release_spread250"))
        print("VARIANTS_CURRENT")
        for name in variants:
            result = run(candidate(defs["31"], name), directory)
            print(metric_row(name, result),
                  "normal_execution", first_normal_rebalance(result[2]),
                  "jan_apr_2023", interval_return(result[0], "2023-01-24", "2023-04-13"),
                  flush=True)
        for label, dates in (("DOTCOM", DOTCOM), ("GFC", GFC)):
            with TemporaryDirectory(prefix="strategy31-proxy-review-", dir=ROOT / "tmp") as proxy_temp:
                proxy_dir = Path(proxy_temp)
                make_proxy(proxy_dir)
                print(f"VARIANTS_{label}")
                for name, definition in (("31", defs["31"]),) + tuple(
                    (variant, candidate(defs["31"], variant)) for variant in variants
                ):
                    definition = deepcopy(definition)
                    definition["parameters"].update(
                        valuation_arm_score=30.0, valuation_breakdown_points=4.0
                    )
                    print(metric_row(name, run(definition, proxy_dir, dates)), flush=True)
        return results


if __name__ == "__main__":
    main()
