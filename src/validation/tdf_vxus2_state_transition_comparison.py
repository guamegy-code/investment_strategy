"""Measure the effect of VXUS2 market-state rules on TDF/BND allocation."""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from performance import Performance
from strategy_dsl import load_strategy_definition
from validation.conditional_bnd_safe_sleeve import hysteresis_definition
from validation.tdf_bnd_improvement_candidates import (
    cap_caution_profit_band,
    run,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
LEGACY_SOURCE = ROOT / "strategies" / "04_profit_band_vxus.yaml"
VXUS2_SOURCE = ROOT / "strategies" / "05_profit_band_vxus_v2.yaml"
STATE_VARIABLES = (
    "risk_off_score",
    "recovery_score",
    "structural_bear",
    "bull_reentry_allowed",
)


def with_market_state_rules(definition, source):
    result = deepcopy(definition)
    for name in STATE_VARIABLES:
        result["variables"][name] = deepcopy(source["variables"][name])
    result["state"]["market_mode"] = deepcopy(source["state"]["market_mode"])
    if "structural_drawdown" in source.get("parameters", {}):
        result["parameters"]["structural_drawdown"] = source["parameters"][
            "structural_drawdown"
        ]
    else:
        result["parameters"].pop("structural_drawdown", None)
    return result


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


def state_statistics(history, start, end):
    states = history.loc[start:end, "StrategyState"]
    counts = states.value_counts()
    transitions = int((states != states.shift()).sum() - 1)
    return {
        "BullDays": int(counts.get("BULL", 0)),
        "CautionDays": int(counts.get("CAUTION", 0)),
        "BearDays": int(counts.get("BEAR", 0)),
        "RecoveryDays": int(counts.get("RECOVERY", 0)),
        "StateTransitions": transitions,
    }


def main():
    legacy = load_strategy_definition(LEGACY_SOURCE)
    vxus2 = load_strategy_definition(VXUS2_SOURCE)
    current = hysteresis_definition()
    cap75 = cap_caution_profit_band(current, 0.75)
    definitions = {
        "TDF/BND current allocation + legacy VXUS states": (
            with_market_state_rules(current, legacy)
        ),
        "TDF/BND current allocation + VXUS2 states": (
            with_market_state_rules(current, vxus2)
        ),
        "TDF/BND CAUTION cap 75% + legacy VXUS states": (
            with_market_state_rules(cap75, legacy)
        ),
        "TDF/BND CAUTION cap 75% + VXUS2 states": (
            with_market_state_rules(cap75, vxus2)
        ),
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
            **state_statistics(history, common_start, common_end),
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
        RESULT_DIR / "tdf_vxus2_state_transition_comparison.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "tdf_vxus2_state_transition_rolling.csv", index=False
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
