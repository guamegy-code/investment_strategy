"""Reduce churn in the strong-CAUTION TDF exit signal."""

from pathlib import Path

import pandas as pd

from performance import Performance
from validation.tdf_bnd_improvement_candidates import run
from validation.tdf_caution_safe_sleeve_grid import caution_definition


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"


def signal_definition(check, exit_score, entry_confirm=1):
    definition = caution_definition(0.40, 1.0)
    definition["state"]["safe_tdf_share"] = {
        "initial": "40%",
        "check": check,
        "rules": [
            {
                "when": (
                    "state.safe_tdf_share > 0 and "
                    "state.market_mode == 'CAUTION' and "
                    "variables.risk_off_score >= 6"
                ),
                "set": "0%",
                "confirm": entry_confirm,
            },
            {
                "when": (
                    "state.safe_tdf_share == 0 and ("
                    "state.market_mode != 'CAUTION' or "
                    f"variables.risk_off_score <= {exit_score})"
                ),
                "set": "40%",
            },
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
        "Baseline CAUTION TDF 40%": caution_definition(0.40, 1.0),
        "Daily score 6 / exit 5": signal_definition("daily", 5),
        "Daily score 6 / exit 4": signal_definition("daily", 4),
        "Daily score 6 / exit 3": signal_definition("daily", 3),
        "Daily score 6 confirm 3 / exit 4": signal_definition(
            "daily", 4, entry_confirm=3
        ),
        "Weekly score 6 / exit 5": signal_definition("weekly", 5),
        "Weekly score 6 / exit 4": signal_definition("weekly", 4),
        "Weekly score 6 confirm 2 / exit 4": signal_definition(
            "weekly", 4, entry_confirm=2
        ),
        "Monthly score 6 / exit 4": signal_definition("monthly", 4),
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
        RESULT_DIR / "tdf_caution_signal_hysteresis.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "tdf_caution_signal_hysteresis_rolling.csv", index=False
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
