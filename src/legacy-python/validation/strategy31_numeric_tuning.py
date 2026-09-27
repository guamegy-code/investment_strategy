"""Reproduce strategy 31's pre-adoption numeric sensitivity study.

The historical baseline is reconstructed from the adopted production YAML.
Use --proxy to evaluate synthetic dot-com and financial-crisis paths.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest import Backtest
from config import COMMISSION, DATA_DIR, SLIPPAGE
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from strategy29_31_rebalance_review import START, END, make_proxy, metric_row, run
from strategy30_31_yaml_parity import (
    DOTCOM, GFC, ROOT, STRATEGY31, add_credit_observation,
    apply_credit_lag, build_data,
)


PROFILES = {
    "base": {},
    "spread_2_15": {"spread": 2.15},
    "spread_2_25": {"spread": 2.25},
    "spread_2_35": {"spread": 2.35},
    "spread_2_40": {"spread": 2.40},
    "spread_2_45": {"spread": 2.45},
    "change_0_4": {"change": 0.4},
    "weak_7_5": {"weak": -7.5},
    "weak_7": {"weak": -7},
    "weak_6_5": {"weak": -6.5},
    "weak_6": {"weak": -6},
    "weak_6_25": {"weak": -6.25},
    "weak_8": {"weak": -8},
    "memory_10": {"memory": 10},
    "memory_15": {"memory": 15},
    "cap_70": {"cap": 70},
    "cap_55": {"cap": 55},
    "cap_45": {"cap": 45},
    "cap_40": {"cap": 40},
    "cap_60": {"cap": 60},
    "cap_65": {"cap": 65},
    "release_ema_97": {"release_ema": 0.97},
    "release_ema_98": {"release_ema": 0.98},
    "release_ema_99": {"release_ema": 0.99},
    "release_ema_97_confirm_5": {"release_ema": 0.97, "release_confirm": 5},
    "release_ema_98_confirm_5": {"release_ema": 0.98, "release_confirm": 5},
    "release_ema_97_confirm_3": {"release_ema": 0.97, "release_confirm": 3},
    "release_ema_97_confirm_6": {"release_ema": 0.97, "release_confirm": 6},
    "release_ema_97_confirm_7": {"release_ema": 0.97, "release_confirm": 7},
    "release_ema_97_confirm_8": {"release_ema": 0.97, "release_confirm": 8},
    "release_ema_975_confirm_5": {"release_ema": 0.975, "release_confirm": 5},
    "cap_40_release_ema_97_confirm_5": {"cap": 40, "release_ema": 0.97, "release_confirm": 5},
    "cap_45_release_ema_97_confirm_5": {"cap": 45, "release_ema": 0.97, "release_confirm": 5},
    "cap_35_release_ema_97_confirm_5": {"cap": 35, "release_ema": 0.97, "release_confirm": 5},
    "cap_30_release_ema_97_confirm_5": {"cap": 30, "release_ema": 0.97, "release_confirm": 5},
    "cap_40_release_ema_97_confirm_5_deep_8": {"cap": 40, "release_ema": 0.97, "release_confirm": 5, "deep": -8},
    "release_ema_97_confirm_5_credit_2_5": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 2.5},
    "release_ema_97_confirm_5_credit_3": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 3.0},
    "release_ema_97_confirm_5_credit_2_75": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 2.75},
    "release_ema_97_confirm_5_credit_3_25": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 3.25},
    "release_ema_97_confirm_5_credit_2_9": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 2.9},
    "release_ema_97_confirm_5_credit_3_1": {"release_ema": 0.97, "release_confirm": 5, "release_credit": 3.1},
    "cap_40_release_ema_97_confirm_5_credit_2_5": {"cap": 40, "release_ema": 0.97, "release_confirm": 5, "release_credit": 2.5},
    "cap_40_release_ema_97_confirm_5_credit_3": {"cap": 40, "release_ema": 0.97, "release_confirm": 5, "release_credit": 3.0},
    "cap_45_release_ema_97_confirm_5_credit_3": {"cap": 45, "release_ema": 0.97, "release_confirm": 5, "release_credit": 3.0},
    "band_10": {"band": 10},
    "spread_2_25_cap_60": {"spread": 2.25, "cap": 60},
    "spread_2_25_change_0_4": {"spread": 2.25, "change": 0.4},
}


def definition_for(name):
    definition = deepcopy(load_strategy_definition(STRATEGY31))
    definition["strategy"]["id"] += f"-{name}-research"
    if name == "base":
        definition["strategy"]["version"] = 1
    # Restore the historical baseline before applying a research profile.
    # Fail clearly if the production rules change so the comparison cannot
    # silently drift away from the results recorded in the research note.
    release = definition["state"]["credit_guard"]["rules"][1]
    adopted_ema = "QQQ.ema55 > QQQ.ema200 * 0.97"
    adopted_credit = "and BAA10Y.close < 3.0"
    assert adopted_ema in release["when"] and adopted_credit in release["when"]
    assert release["confirm"] == 5
    release["when"] = release["when"].replace(adopted_ema, "QQQ.ema55 > QQQ.ema200")
    release["when"] = release["when"].replace(adopted_credit, "").rstrip() + "\n"
    release["confirm"] = 10
    first_target = next(rule for rule in definition["target"]
                        if rule.get("when") == "state.credit_guard == 'TRUE'")
    assert first_target["weights"] == {
        "QQQ": "40%", "TDF2050_PROXY": "30%", "BIL": "30%",
    }
    first_target["weights"] = {
        "QQQ": "50%", "TDF2050_PROXY": "30%", "BIL": "20%",
    }
    knobs = PROFILES[name]
    age = definition["state"]["relative_weak_age"]["rules"][0]
    age_rules = definition["state"]["relative_weak_age"]
    entry = definition["state"]["credit_guard"]["rules"][0]
    if "memory" in knobs:
        days = knobs["memory"]
        age_rules["initial"] = days
        age_rules["rules"][1]["when"] = age_rules["rules"][1]["when"].replace("< 20", f"< {days}")
        entry["when"] = entry["when"].replace("state.relative_weak_age < 20", f"state.relative_weak_age < {days}")
    if "weak" in knobs:
        age["when"] = age["when"].replace("<= -5", f"<= {knobs['weak']}")
    if "spread" in knobs:
        entry["when"] = entry["when"].replace(
            "BAA10Y.close >= 2", f"BAA10Y.close >= {knobs['spread']}"
        )
    if "change" in knobs:
        entry["when"] = entry["when"].replace(
            ">= 0.30", f">= {knobs['change']}"
        )
    if "release_ema" in knobs:
        release = definition["state"]["credit_guard"]["rules"][1]
        release["when"] = release["when"].replace(
            "QQQ.ema55 > QQQ.ema200",
            f"QQQ.ema55 > QQQ.ema200 * {knobs['release_ema']}",
        )
    if "release_confirm" in knobs:
        definition["state"]["credit_guard"]["rules"][1]["confirm"] = knobs["release_confirm"]
    if "release_credit" in knobs:
        release = definition["state"]["credit_guard"]["rules"][1]
        release["when"] = release["when"].strip() + f" and BAA10Y.close < {knobs['release_credit']}\n"
    if "deep" in knobs:
        deep_entry = definition["state"]["deep_guard"]["rules"][0]
        deep_entry["when"] = deep_entry["when"].replace("<= -10", f"<= {knobs['deep']}")
    if "cap" in knobs:
        cap = knobs["cap"]
        first = next(rule for rule in definition["target"]
                     if rule.get("when") == "state.credit_guard == 'TRUE'")
        first["weights"] = {
            "QQQ": f"{cap}%", "TDF2050_PROXY": "30%", "BIL": f"{70-cap}%",
        }
    if "band" in knobs:
        definition["rebalance"][0]["when"] = definition["rebalance"][0]["when"].replace(
            "7.5%", f"{knobs['band']}%"
        )
    return definition


def guard_summary(history):
    states = history.NotificationContext.map(lambda x: x["state_values"])
    first = states.map(lambda x: x["credit_guard"] == "TRUE")
    deep = states.map(lambda x: x["deep_guard"] == "TRUE")
    onset = first & ~first.shift(1, fill_value=False)
    return {
        "guard_days": int(first.sum()),
        "deep_days": int(deep.sum()),
        "first_guard": str(onset[onset].index[0].date()) if onset.any() else None,
        "last_guard": str(first[first].index[-1].date()) if first.any() else None,
        "guard_episodes": int(onset.sum()),
    }


def prepare_current(directory):
    build_data(directory, dotcom=False)
    apply_credit_lag(directory, 1)
    add_credit_observation(directory)
    (directory / "TDF2050_PROXY.csv").write_bytes(
        (DATA_DIR / "TDF2050_PROXY.csv").read_bytes()
    )


def run_krw(definition, directory):
    strategy = DeclarativeStrategy(deepcopy(definition))
    strategy.foreign_asset_tickers = strategy.holding_tickers
    strategy.valuation_currency = "KRW"
    strategy.valuation_fx_ticker = "KRW=X"
    strategy.valuation_signal_currency = "LOCAL"
    strategy.required_tickers = tuple(dict.fromkeys((*strategy.required_tickers, "KRW=X")))
    return Backtest(
        strategy, data_dir=directory, tickers=strategy.required_tickers,
        start_date=START, end_date=END, commission=COMMISSION, slippage=SLIPPAGE,
    ).run_all()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profiles", nargs="+", choices=PROFILES,
        default=["base", "weak_6_5", "release_ema_97_confirm_5_credit_3",
                 "cap_45_release_ema_97_confirm_5_credit_3",
                 "cap_40_release_ema_97_confirm_5_credit_3"],
    )
    parser.add_argument("--proxy", action="store_true")
    parser.add_argument("--proxy-only", action="store_true")
    parser.add_argument("--tdf-equity-proxy", choices=("SPY", "QQQ"), default="SPY")
    parser.add_argument("--krw-only", action="store_true")
    parser.add_argument("--period", choices=("DOTCOM", "GFC", "BOTH"), default="BOTH")
    args = parser.parse_args()
    if args.krw_only:
        with TemporaryDirectory(prefix="strategy31-numeric-krw-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare_current(directory)
            (directory / "KRW=X.csv").write_bytes((DATA_DIR / "KRW=X.csv").read_bytes())
            for name in args.profiles:
                result = run_krw(definition_for(name), directory)
                print("CURRENT_KRW", metric_row(name, result), guard_summary(result[0]), flush=True)
        return
    if not args.proxy_only:
        with TemporaryDirectory(prefix="strategy31-numeric-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare_current(directory)
            for name in args.profiles:
                result = run(definition_for(name), directory)
                print("CURRENT", metric_row(name, result), guard_summary(result[0]), flush=True)
    if args.proxy or args.proxy_only:
        for period, dates in (("DOTCOM", DOTCOM), ("GFC", GFC)):
            if args.period != "BOTH" and period != args.period:
                continue
            with TemporaryDirectory(prefix="strategy31-numeric-proxy-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                make_proxy(directory, proxy_ticker=args.tdf_equity_proxy)
                for name in args.profiles:
                    definition = definition_for(name)
                    definition["parameters"].update(
                        valuation_arm_score=30.0, valuation_breakdown_points=4.0
                    )
                    result = run(definition, directory, dates)
                    print(period, metric_row(name, result), guard_summary(result[0]), flush=True)


if __name__ == "__main__":
    main()
