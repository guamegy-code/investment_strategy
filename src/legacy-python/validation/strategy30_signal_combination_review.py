"""Research-only controlled signal combinations against deployed Strategy 30.

Every variant begins with the deployed YAML. Portfolio targets, release rules,
and rebalance rules stay fixed; only credit/deep entry conditions differ.
"""

from __future__ import annotations

import argparse
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import RESULT_DIR
from downloader import _load_fred_series, _load_hy_oas_observations
from strategy28_failed_dip_regime import CURRENT, DOTCOM, GFC, ROOT
from strategy30_31_yaml_parity import add_credit_observation
from strategy30_hy_oas_review import STRATEGY30, prepare, run_one
from strategy30_unknown_crash_stress import SCENARIOS, EVENT_START, EVENT_END, inject
from strategy_dsl import load_strategy_definition

HY = "BAMLH0A0HYM2"
VARIANTS = ("BASE30", "MARKET", "FAST_CREDIT", "MARKET_FAST",
            "PRICE70", "MARKET_PRICE70", "SEQUENTIAL")
MARKET_CONFIRM = "(SPY.drawdown120 <= -0.15 and SPY.close < SPY.ema200)"
DEEP_CONFIRM = "(SPY.drawdown120 <= -0.20 and QQQ.drawdown120 <= -0.20)"
BAA_FAST = "(BAA10Y.close >= 2 and variables.baa_change5 >= 0.15)"
HY_FAST = f"({HY}.close >= 4 and variables.hy_change5 >= 0.25)"


def definition_for(variant: str) -> dict:
    if variant not in VARIANTS:
        raise ValueError(variant)
    definition = deepcopy(load_strategy_definition(STRATEGY30))
    if variant == "BASE30":
        return definition
    definition["strategy"]["id"] += f"-{variant.lower()}-research"
    credit_entry = definition["state"]["credit_guard"]["rules"][0]
    deep_entry = definition["state"]["deep_guard"]["rules"][0]
    if variant in ("MARKET", "MARKET_FAST", "MARKET_PRICE70"):
        old = "state.relative_weak_age < 20"
        assert credit_entry["when"].count(old) == 1
        credit_entry["when"] = credit_entry["when"].replace(
            old, f"({old} or {MARKET_CONFIRM})"
        )
        old = "variables.relative_roc20 <= -10"
        assert deep_entry["when"].count(old) == 1
        deep_entry["when"] = deep_entry["when"].replace(
            old, f"({old} or {DEEP_CONFIRM})"
        )
    if variant in ("FAST_CREDIT", "MARKET_FAST", "SEQUENTIAL"):
        definition["assets"]["observations"].append(HY)
        definition["variables"]["baa_change5"] = (
            "BAA10Y.close - BAA10Y.close / (1 + BAA10Y.roc5 / 100)"
        )
        definition["variables"]["hy_change5"] = (
            f"{HY}.close - {HY}.close / (1 + {HY}.roc5 / 100)"
        )
        if variant == "SEQUENTIAL":
            # The age state is evaluated after credit_guard, so a same-session
            # observation first becomes eligible on the next QQQ session.
            definition["state"]["credit_accel_age"] = {
                "initial": 11,
                "rules": [
                    {"when": f"{BAA_FAST} or {HY_FAST}", "set": 0},
                    {"when": "state.credit_accel_age < 11",
                     "set": "=state.credit_accel_age + 1"},
                ],
            }
            old = ("state.relative_weak_age < 20 and BAA10Y.close >= 2 "
                   "and variables.credit_change20 >= 0.30")
            assert credit_entry["when"].count(old) == 1
            delayed = ("state.credit_accel_age <= 10 and "
                       "(BAA10Y.close >= 2 or BAMLH0A0HYM2.close >= 4) and "
                       "QQQ.drawdown60 <= -0.08 and SPY.drawdown60 <= -0.06")
            credit_entry["when"] = credit_entry["when"].replace(
                old, f"(({old}) or ({delayed}))"
            )
        else:
            old = "BAA10Y.close >= 2 and variables.credit_change20 >= 0.30"
            assert credit_entry["when"].count(old) == 1
            credit_entry["when"] = credit_entry["when"].replace(
                old, f"(({old}) or {BAA_FAST} or {HY_FAST})"
            )
    if variant in ("PRICE70", "MARKET_PRICE70"):
        definition["state"]["shock_guard"] = {
            "initial": "FALSE",
            "rules": [
                {"when": "state.shock_guard == 'FALSE' and QQQ.roc5 <= -10 "
                         "and SPY.roc5 <= -6", "set": "TRUE"},
                {"when": "state.shock_guard == 'TRUE' and QQQ.close > QQQ.ema20 "
                         "and SPY.close > SPY.ema20 and QQQ.roc5 > 0",
                 "set": "FALSE", "confirm": 3},
            ],
        }
        # Insert after all stricter existing defenses, preserving their priority.
        position = next(i for i, rule in enumerate(definition["target"])
                        if rule.get("when") == "state.trend_mode == 'RECOVERY'")
        definition["target"].insert(position, {
            "when": "state.shock_guard == 'TRUE'",
            "weights": {"QQQ": "70%", "BIL": "30%"},
        })
    return definition


def event_metrics(history: pd.DataFrame, credit: pd.Series, deep: pd.Series,
                  start: str, end: str) -> dict:
    value = history.loc[start:end, "Portfolio"]
    active = credit.loc[start:end]
    strong = deep.loc[start:end]
    if value.empty:
        raise ValueError(f"No event dates: {start}..{end}")
    first = active.index[active & ~active.shift(1, fill_value=False)]
    first_deep = strong.index[strong & ~strong.shift(1, fill_value=False)]
    return {
        "Return": float(value.iloc[-1] / value.iloc[0] - 1),
        "MDD": float((value / value.cummax() - 1).min()),
        "FirstCredit": str(first[0].date()) if len(first) else "",
        "FirstDeep": str(first_deep[0].date()) if len(first_deep) else "",
        "LossAtFirstCredit": (
            float(value.loc[first[0]] / value.iloc[0] - 1) if len(first) else None
        ),
        "CreditDays": int(active.sum()),
        "DeepDays": int(strong.sum()),
    }


def run_history(baa: pd.Series, hy: pd.Series, periods, variants):
    summaries, events, transitions = [], [], []
    spans = {
        "CURRENT": {"2018_Q4": ("2018-09-20", "2019-04-30"),
                    "COVID": ("2020-02-19", "2020-08-31"),
                    "2022_BEAR": ("2021-11-19", "2023-01-19"),
                    "2025": ("2025-02-19", "2025-04-08")},
        "DOTCOM_PROXY": {"DOTCOM": ("2000-03-27", "2002-10-07")},
        "GFC_PROXY": {"GFC": ("2007-10-31", "2009-03-09")},
    }
    for period, dates, proxy in (("CURRENT", CURRENT, False),
                                 ("DOTCOM_PROXY", DOTCOM, True),
                                 ("GFC_PROXY", GFC, True)):
        if period not in periods:
            continue
        with TemporaryDirectory(prefix="strategy30-combination-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare(directory, baa, hy, proxy=proxy, lag=1)
            for variant in variants:
                metrics, history, credit, deep = run_one(
                    directory, definition_for(variant), dates, proxy=proxy, cost=1.0
                )
                summaries.append({"Period": period, "Variant": variant, **metrics})
                for event, (start, end) in spans[period].items():
                    events.append({"Period": period, "Variant": variant,
                                   "Event": event,
                                   **event_metrics(history, credit, deep, start, end)})
                changed = (credit.ne(credit.shift(1, fill_value=False)) |
                           deep.ne(deep.shift(1, fill_value=False)))
                for date in history.index[changed]:
                    transitions.append({"Period": period, "Variant": variant,
                                        "Date": str(date.date()),
                                        "Credit": bool(credit.loc[date]),
                                        "Deep": bool(deep.loc[date])})
                print(summaries[-1], flush=True)
    return summaries, events, transitions


def run_stress(baa: pd.Series, hy: pd.Series, variants, scenarios):
    rows = []
    for name, scenario in SCENARIOS.items():
        if name not in scenarios:
            continue
        with TemporaryDirectory(prefix="strategy30-combination-stress-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare(directory, baa, hy, proxy=False, lag=1)
            inject(directory, scenario)
            # The injection changes QQQ's lagged BAA_SPREAD. Rebuild the exact
            # market observation seen by the deployed DSL from that same path.
            add_credit_observation(directory)
            for variant in variants:
                metrics, history, credit, deep = run_one(
                    directory, definition_for(variant),
                    (CURRENT[0], str(EVENT_END.date())), proxy=False, cost=1.0,
                )
                row = {"Scenario": name, "Variant": variant,
                       **event_metrics(history, credit, deep,
                                       str(EVENT_START.date()), str(EVENT_END.date())),
                       "TradesWholeBacktest": metrics["Trades"]}
                rows.append(row)
                print(row, flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--periods", nargs="+", default=["CURRENT", "DOTCOM_PROXY", "GFC_PROXY"])
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=VARIANTS)
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--output-stem", default="strategy30_signal_combination")
    args = parser.parse_args()
    baa = _load_fred_series(ROOT / "tmp/BAA10Y.csv")
    hy = _load_hy_oas_observations()
    summary, events, transitions = run_history(baa, hy, args.periods, args.variants)
    stress = run_stress(baa, hy, args.variants, args.scenarios) if args.stress else []
    RESULT_DIR.mkdir(exist_ok=True)
    for suffix, rows in (("summary", summary), ("events", events),
                         ("transitions", transitions), ("stress", stress)):
        if rows:
            pd.DataFrame(rows).to_csv(
                RESULT_DIR / f"{args.output_stem}_{suffix}.csv", index=False
            )


if __name__ == "__main__":
    main()
