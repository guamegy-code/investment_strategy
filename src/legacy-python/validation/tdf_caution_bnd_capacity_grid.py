"""Tune BND capacity separately in normal and strong CAUTION."""

from pathlib import Path

import pandas as pd

from performance import Performance
from validation.tdf_bnd_improvement_candidates import run
from validation.tdf_caution_tiered_allocation import tiered_definition


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
CAPACITIES = (0.0, 0.5, 1.0)


def capacity_definition(caution_capacity, strong_capacity):
    definition = tiered_definition(0.40, 0.40, 0.0)
    definition["state"]["bnd_capacity"] = {
        "initial": "100%",
        "rules": [
            {
                "when": (
                    "state.market_mode == 'CAUTION' and "
                    "state.safe_tdf_share == 0"
                ),
                "set": f"{strong_capacity * 100}%",
            },
            {
                "when": "state.market_mode == 'CAUTION'",
                "set": f"{caution_capacity * 100}%",
            },
            {"otherwise": True, "set": "100%"},
        ],
    }
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


def main():
    definitions = {
        f"CAUTION BND {caution:.0%} / STRONG BND {strong:.0%}": (
            capacity_definition(caution, strong)
        )
        for caution in CAPACITIES
        for strong in CAPACITIES
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
    summary.to_csv(
        RESULT_DIR / "tdf_caution_bnd_capacity_grid.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "tdf_caution_bnd_capacity_grid_rolling.csv", index=False
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
