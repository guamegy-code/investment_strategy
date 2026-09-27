"""Reproduce strategy 30's pre-adoption credit guard numeric study."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategy_dsl import load_strategy_definition
from strategy30_31_yaml_parity import DOTCOM, GFC, ROOT, STRATEGY30
from strategy31_numeric_tuning import (
    DATA_DIR, prepare_current, run, run_krw, guard_summary, make_proxy, metric_row,
)


PROFILES = {
    "base": {},
    "cap_55": {"cap": 55},
    "cap_60": {"cap": 60},
    "cap_50": {"cap": 50},
    "release_97_5_credit_3": {"ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_55_release_97_5_credit_3": {"cap": 55, "ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_60_release_97_5_credit_3": {"cap": 60, "ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_50_release_97_5_credit_3": {"cap": 50, "ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_45_release_97_5_credit_3": {"cap": 45, "ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_40_release_97_5_credit_3": {"cap": 40, "ema": 0.97, "confirm": 5, "credit": 3.0},
    "cap_40_release_97_5_credit_2_9": {"cap": 40, "ema": 0.97, "confirm": 5, "credit": 2.9},
    "cap_40_release_97_5_credit_3_1": {"cap": 40, "ema": 0.97, "confirm": 5, "credit": 3.1},
    "cap_65_release_97_5_credit_2_9": {"ema": 0.97, "confirm": 5, "credit": 2.9},
    "cap_65_release_97_5_credit_3_1": {"ema": 0.97, "confirm": 5, "credit": 3.1},
}


def definition_for(name):
    knobs = PROFILES[name]
    definition = deepcopy(load_strategy_definition(STRATEGY30))
    definition["strategy"]["id"] += f"-{name}-research"
    if name == "base":
        definition["strategy"]["version"] = 1
    # Reconstruct the pre-adoption rules so the historical base remains stable.
    release = definition["state"]["credit_guard"]["rules"][1]
    adopted_ema = "QQQ.ema55 > QQQ.ema200 * 0.97"
    adopted_credit = "and BAA10Y.close < 3.0"
    assert adopted_ema in release["when"] and adopted_credit in release["when"]
    assert release["confirm"] == 5
    release["when"] = release["when"].replace(adopted_ema, "QQQ.ema55 > QQQ.ema200")
    release["when"] = release["when"].replace(adopted_credit, "").rstrip() + "\n"
    release["confirm"] = 10
    first = next(rule for rule in definition["target"]
                 if rule.get("when") == "state.credit_guard == 'TRUE'")
    assert first["weights"] == {"QQQ": "50%", "BIL": "50%"}
    first["weights"] = {"QQQ": "65%", "BIL": "35%"}
    if "ema" in knobs:
        release["when"] = release["when"].replace(
            "QQQ.ema55 > QQQ.ema200",
            f"QQQ.ema55 > QQQ.ema200 * {knobs['ema']}",
        )
        release["confirm"] = knobs["confirm"]
        release["when"] = release["when"].rstrip() + f" and BAA10Y.close < {knobs['credit']}\n"
    if "cap" in knobs:
        cap = knobs["cap"]
        first["weights"] = {"QQQ": f"{cap}%", "BIL": f"{100-cap}%"}
    return definition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", choices=PROFILES,
                        default=list(PROFILES))
    parser.add_argument("--krw-only", action="store_true")
    parser.add_argument("--proxy-only", action="store_true")
    parser.add_argument("--period", choices=("DOTCOM", "GFC", "BOTH"), default="BOTH")
    args = parser.parse_args()
    if not args.proxy_only:
        with TemporaryDirectory(prefix="strategy30-numeric-", dir=ROOT / "tmp") as temp:
            directory = Path(temp)
            prepare_current(directory)
            if args.krw_only:
                (directory / "KRW=X.csv").write_bytes((DATA_DIR / "KRW=X.csv").read_bytes())
            for name in args.profiles:
                result = (run_krw if args.krw_only else run)(definition_for(name), directory)
                print("CURRENT_KRW" if args.krw_only else "CURRENT", metric_row(name, result),
                      guard_summary(result[0]), flush=True)
    if args.proxy_only:
        for period, dates in (("DOTCOM", DOTCOM), ("GFC", GFC)):
            if args.period != "BOTH" and period != args.period:
                continue
            with TemporaryDirectory(prefix="strategy30-numeric-proxy-", dir=ROOT / "tmp") as temp:
                directory = Path(temp)
                make_proxy(directory)
                for name in args.profiles:
                    definition = definition_for(name)
                    definition["parameters"].update(
                        valuation_arm_score=30.0, valuation_breakdown_points=4.0
                    )
                    result = run(definition, directory, dates)
                    print(period, metric_row(name, result), guard_summary(result[0]), flush=True)


if __name__ == "__main__":
    main()
