"""Check whether the CAUTION QQQ cap result is stable near its optimum."""

from pathlib import Path

import pandas as pd

from performance import Performance
from validation.conditional_bnd_safe_sleeve import hysteresis_definition
from validation.tdf_bnd_improvement_candidates import (
    COMMON_START,
    cap_caution_profit_band,
    run,
)


ROOT = Path(__file__).resolve().parents[2]
CAPS = (0.740, 0.745, 0.750, 0.755, 0.760, 0.7625, 0.765, 0.770, 0.775)


def metrics(history, start, end):
    performance = Performance(history.loc[start:end])
    return performance.cagr(), performance.mdd(), performance.calmar_ratio()


def main():
    runs = {
        cap: run(cap_caution_profit_band(hysteresis_definition(), cap))
        for cap in CAPS
    }
    common_end = min(history.index.max() for history, _, _ in runs.values())
    rows = []
    for cap, (history, trades, rebalances) in runs.items():
        cagr, mdd, calmar = metrics(history, COMMON_START, common_end)
        later_cagr, later_mdd, _ = metrics(history, "2021-01-01", common_end)
        sample = history.loc[COMMON_START:common_end]
        rows.append({
            "CautionCap": cap,
            "CAGR": cagr,
            "MDD": mdd,
            "Calmar": calmar,
            "LaterCAGR": later_cagr,
            "LaterMDD": later_mdd,
            "TransactionCosts": (
                sample["TransactionCosts"].iloc[-1]
                - sample["TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })
    summary = pd.DataFrame(rows).sort_values("CautionCap")
    summary.to_csv(
        ROOT / "results" / "tdf_bnd_caution_cap_sensitivity.csv", index=False
    )
    display = summary.copy()
    for column in (
        "CautionCap", "CAGR", "MDD", "LaterCAGR", "LaterMDD",
        "TransactionCosts",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
