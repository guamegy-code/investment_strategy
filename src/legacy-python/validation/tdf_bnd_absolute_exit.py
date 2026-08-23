"""Test an absolute BND trend exit on the strongest state-allocation candidates."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from performance import Performance
from strategy_dsl import load_strategy_definition
from validation.tdf_bnd_improvement_candidates import run
from validation.tdf_state_allocation_grid import allocation_definition


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
RESULT_DIR = ROOT / "results"


def absolute_exit(definition):
    result = deepcopy(definition)
    exit_rule = result["state"]["bond_share"]["rules"][1]
    exit_rule["when"] = (
        f"({exit_rule['when']}) or BND.close < BND.ema55"
    )
    return result


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
        "Production allocation": load_strategy_definition(SOURCE),
        "Strong QQQ 70% / Recovery QQQ 55%": allocation_definition(
            0.70, 0.55, 0.40
        ),
        "Strong QQQ 65% / Recovery QQQ 55%": allocation_definition(
            0.65, 0.55, 0.40
        ),
        "Strong QQQ 60% / Recovery QQQ 55%": allocation_definition(
            0.60, 0.55, 0.40
        ),
    }
    definitions.update({
        f"{name} + BND absolute exit": absolute_exit(definition)
        for name, definition in tuple(definitions.items())
    })
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
    summary.to_csv(RESULT_DIR / "tdf_bnd_absolute_exit.csv", index=False)
    rolling.to_csv(
        RESULT_DIR / "tdf_bnd_absolute_exit_rolling.csv", index=False
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
