"""Sensitivity of the strong-CAUTION TDF exit confirmation period."""

from pathlib import Path

import pandas as pd

from performance import Performance
from validation.tdf_bnd_improvement_candidates import run
from validation.tdf_caution_selective_safe_sleeve import selective_definition


ROOT = Path(__file__).resolve().parents[2]
CONFIRMATIONS = range(1, 11)


def main():
    rows = []
    runs = {
        confirm: run(selective_definition(0.0, 6, confirm))
        for confirm in CONFIRMATIONS
    }
    common_start = max(history.index.min() for history, _, _ in runs.values())
    common_end = min(history.index.max() for history, _, _ in runs.values())
    for confirm, (history, trades, rebalances) in runs.items():
        sample = history.loc[common_start:common_end]
        performance = Performance(sample)
        later = Performance(history.loc["2021-01-01":common_end])
        rows.append({
            "ConfirmationDays": confirm,
            "CAGR": performance.cagr(),
            "MDD": performance.mdd(),
            "Volatility": performance.volatility(),
            "Sharpe": performance.sharpe_ratio(),
            "Calmar": performance.calmar_ratio(),
            "LaterCAGR": later.cagr(),
            "LaterMDD": later.mdd(),
            "TransactionCosts": (
                sample["TransactionCosts"].iloc[-1]
                - sample["TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })
    summary = pd.DataFrame(rows).sort_values("ConfirmationDays")
    summary.to_csv(
        ROOT / "results" / "tdf_caution_confirmation_sensitivity.csv",
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
