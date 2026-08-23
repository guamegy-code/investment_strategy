"""Tune only the CAUTION safe sleeve under the VXUS2 state rules."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from performance import Performance
from validation.conditional_bnd_safe_sleeve import hysteresis_definition
from validation.tdf_bnd_improvement_candidates import (
    cap_caution_profit_band,
    run,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
TDF_SHARES = (0.0, 0.2, 0.4, 0.6)
BND_CAPACITIES = (0.0, 0.5, 1.0)


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


def caution_definition(tdf_share, bnd_capacity):
    definition = cap_caution_profit_band(hysteresis_definition(), 0.75)
    definition = deepcopy(definition)
    definition["state"]["safe_tdf_share"] = {
        "initial": "40%",
        "rules": [
            {
                "when": "state.market_mode == 'CAUTION'",
                "set": f"{tdf_share * 100}%",
            },
            {"otherwise": True, "set": "40%"},
        ],
    }
    definition["state"]["bnd_capacity"] = {
        "initial": "100%",
        "rules": [
            {
                "when": "state.market_mode == 'CAUTION'",
                "set": f"{bnd_capacity * 100}%",
            },
            {"otherwise": True, "set": "100%"},
        ],
    }
    for rule in definition["target"]:
        qqq = rule["weights"]["QQQ"]
        rule["weights"] = weights(qqq)
    definition["rebalance"].insert(
        -1,
        {
            "when": (
                "changed(state.safe_tdf_share) or "
                "changed(state.bnd_capacity)"
            ),
            "days": 1,
        },
    )
    return definition


def period_metrics(history, start, end):
    sample = history.loc[start:end]
    performance = Performance(sample)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Volatility": performance.volatility(),
        "Sharpe": performance.sharpe_ratio(),
        "Calmar": performance.calmar_ratio(),
    }


def main():
    definitions = {
        f"CAUTION TDF {tdf:.0%} / BND capacity {bnd:.0%}": (
            caution_definition(tdf, bnd)
        )
        for tdf in TDF_SHARES
        for bnd in BND_CAPACITIES
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
    summary.to_csv(RESULT_DIR / "tdf_caution_safe_sleeve_grid.csv", index=False)
    rolling.to_csv(
        RESULT_DIR / "tdf_caution_safe_sleeve_grid_rolling.csv", index=False
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
