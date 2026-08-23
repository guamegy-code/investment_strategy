"""Compare selected TDF candidates before and after 2021 without overlap."""

from pathlib import Path

import pandas as pd

from performance import Performance
from strategy_dsl import load_strategy_definition
from validation.tdf_bnd_improvement_candidates import run
from validation.tdf_state_allocation_grid import allocation_definition


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
RESULT_DIR = ROOT / "results"


def period_metrics(history, start, end):
    sample = history.loc[start:end]
    performance = Performance(sample)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
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
    runs = {name: run(definition)[0] for name, definition in definitions.items()}
    common_start = max(history.index.min() for history in runs.values())
    common_end = min(history.index.max() for history in runs.values())
    rows = []
    for name, history in runs.items():
        rows.append({
            "Candidate": name,
            **{
                f"Pre2021{key}": value
                for key, value in period_metrics(
                    history, common_start, "2020-12-31"
                ).items()
            },
            **{
                f"Post2021{key}": value
                for key, value in period_metrics(
                    history, "2021-01-01", common_end
                ).items()
            },
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(RESULT_DIR / "tdf_period_split_comparison.csv", index=False)
    display = summary.copy()
    for column in display.columns:
        if column != "Candidate":
            display[column] = display[column].map(lambda value: f"{value:.2%}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
