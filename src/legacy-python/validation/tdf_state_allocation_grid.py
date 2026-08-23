"""Grid-search strong-CAUTION and RECOVERY allocation levels.

The production TDF strategy is the common base: VXUS2 state transitions,
75% CAUTION QQQ cap, strong-CAUTION TDF removal, and a 50% BND capacity cap.
"""

import argparse
from copy import deepcopy
from pathlib import Path

import pandas as pd

from performance import Performance
from strategy_dsl import load_strategy_definition
from validation.tdf_bnd_improvement_candidates import run


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
RESULT_DIR = ROOT / "results"
STRONG_QQQ_WEIGHTS = (0.60, 0.65, 0.70)
RECOVERY_QQQ_WEIGHTS = (0.45, 0.50, 0.55)
RECOVERY_TDF_SHARES = (0.00, 0.20, 0.40)


def weights(qqq):
    safe = f"(1 - ({qqq}))"
    tdf = f"round({safe} * state.safe_tdf_share, 10)"
    bnd = (
        f"round({safe} * (1 - state.safe_tdf_share) * "
        "state.bnd_capacity * state.bond_share, 10)"
    )
    return {
        "QQQ": qqq,
        "TDF2050_PROXY": tdf,
        "BND": bnd,
        "BIL": f"round(max(0, 1 - ({qqq}) - ({tdf}) - ({bnd})), 10)",
    }


def allocation_definition(strong_qqq, recovery_qqq, recovery_tdf):
    definition = deepcopy(load_strategy_definition(SOURCE))
    definition["state"]["risk_weight"]["rules"][1]["set"] = (
        f"{recovery_qqq * 100}%"
    )
    safe_rules = definition["state"]["safe_tdf_share"]["rules"]
    safe_rules.insert(
        -1,
        {
            "when": "state.market_mode == 'RECOVERY'",
            "set": f"{recovery_tdf * 100}%",
        },
    )
    definition["target"].insert(
        0,
        {
            "when": (
                "state.market_mode == 'CAUTION' and "
                "state.safe_tdf_share == 0"
            ),
            "weights": weights(f"{strong_qqq:.2f}"),
        },
    )
    return definition


def period_metrics(history, start, end):
    performance = Performance(history.loc[start:end])
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Volatility": performance.volatility(),
        "Sharpe": performance.sharpe_ratio(),
        "Calmar": performance.calmar_ratio(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strong", type=float, nargs="*", default=STRONG_QQQ_WEIGHTS,
        help="strong-CAUTION QQQ weights to include",
    )
    parser.add_argument("--output-suffix", default="")
    args = parser.parse_args(argv)
    definitions = {
        (
            f"Strong QQQ {strong:.0%} / Recovery QQQ {recovery:.0%} "
            f"/ Recovery TDF {tdf:.0%}"
        ): allocation_definition(strong, recovery, tdf)
        for strong in args.strong
        for recovery in RECOVERY_QQQ_WEIGHTS
        for tdf in RECOVERY_TDF_SHARES
    }
    runs = {name: run(definition) for name, definition in definitions.items()}
    common_start = max(history.index.min() for history, _, _ in runs.values())
    common_end = min(history.index.max() for history, _, _ in runs.values())

    rows = []
    rolling_rows = []
    for name, (history, trades, rebalances) in runs.items():
        sample = history.loc[common_start:common_end]
        full = period_metrics(history, common_start, common_end)
        later = period_metrics(history, "2021-01-01", common_end)
        rows.append({
            "Candidate": name,
            "Start": common_start.strftime("%Y-%m-%d"),
            "End": common_end.strftime("%Y-%m-%d"),
            **full,
            "LaterCAGR": later["CAGR"],
            "LaterMDD": later["MDD"],
            "TransactionCosts": (
                sample["TransactionCosts"].iloc[-1]
                - sample["TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })
        for year in range(2012, 2024):
            start = pd.Timestamp(year, 1, 1)
            end = pd.Timestamp(year + 2, 12, 31)
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                **period_metrics(history, start, end),
            })

    summary = pd.DataFrame(rows).sort_values(
        ["Calmar", "CAGR"], ascending=False
    )
    rolling = pd.DataFrame(rolling_rows)
    RESULT_DIR.mkdir(exist_ok=True)
    suffix = f"_{args.output_suffix}" if args.output_suffix else ""
    summary.to_csv(
        RESULT_DIR / f"tdf_state_allocation_grid{suffix}.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / f"tdf_state_allocation_grid_rolling{suffix}.csv",
        index=False,
    )
    display = summary.copy()
    for column in (
        "CAGR", "MDD", "Volatility", "LaterCAGR", "LaterMDD",
        "TransactionCosts",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    for column in ("Sharpe", "Calmar"):
        display[column] = display[column].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
