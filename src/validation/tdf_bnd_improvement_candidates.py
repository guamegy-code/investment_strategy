"""Compare focused improvements to the TDF40/BIL60 safe sleeve.

The four market-state transitions and the validated selective CAUTION-to-BULL
rebalance suppression remain unchanged. Candidates isolate profit-band handling
in CAUTION and alternative monthly BND signals inside the BIL capacity.
"""

from copy import deepcopy
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.conditional_bnd_safe_sleeve import (
    baseline_definition,
    conditional_definition,
    hysteresis_definition,
)


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
BENCHMARK_SOURCE = ROOT / "strategies" / "07_band_7030_bnd.yaml"
COMMON_START = pd.Timestamp("2011-03-29")

MULTI_HORIZON_SCORE = (
    "count(BND.roc20 > BIL.roc20, "
    "BND.roc60 > BIL.roc60 + 1.50, "
    "BND.close > BND.ema55)"
)


def release_caution_profit_band(definition):
    """Keep the QQQ profit band in BULL, but restore 70% QQQ in CAUTION."""
    result = deepcopy(definition)
    old = "state.market_mode in ['BULL', 'CAUTION']"
    new = "state.market_mode == 'BULL'"
    for rule in result["target"]:
        if "when" in rule:
            rule["when"] = rule["when"].replace(old, new)
    for rule in result["rebalance"]:
        rule["when"] = rule["when"].replace(old, new)
    return result


def cap_caution_profit_band(definition, caution_cap):
    """Preserve the band in CAUTION only while QQQ is below a tighter cap."""
    result = deepcopy(definition)
    shared_mode = "state.market_mode in ['BULL', 'CAUTION']"
    capped_mode = (
        "(state.market_mode == 'BULL' or "
        f"(state.market_mode == 'CAUTION' and "
        f"portfolio.weight.QQQ <= {caution_cap}))"
    )
    result["target"][0]["when"] = result["target"][0]["when"].replace(
        shared_mode, capped_mode
    )

    upper_rule = result["rebalance"][0]
    upper_rule["when"] = (
        "(state.market_mode == 'BULL' and "
        "portfolio.weight.QQQ >= parameters.upper_risk_weight) or "
        "(state.market_mode == 'CAUTION' and "
        f"portfolio.weight.QQQ > {caution_cap})"
    )
    for rule in result["rebalance"][1:]:
        rule["when"] = rule["when"].replace(shared_mode, capped_mode)
    return result


def multi_horizon_definition(confirm=1, defensive_only=False):
    definition = conditional_definition("1 == 0", 1.0)
    entry = f"state.bond_share == 0 and {MULTI_HORIZON_SCORE} >= 2"
    exit_condition = f"state.bond_share > 0 and {MULTI_HORIZON_SCORE} <= 1"
    if defensive_only:
        defensive = "state.market_mode in ['CAUTION', 'BEAR', 'RECOVERY']"
        entry = f"{entry} and {defensive}"
        exit_condition = (
            f"{exit_condition} or (state.bond_share > 0 and "
            "state.market_mode == 'BULL')"
        )
    definition["state"]["bond_share"] = {
        "initial": "0%",
        "check": "monthly",
        "rules": [
            {"when": entry, "set": "100%", "confirm": confirm},
            {"when": exit_condition, "set": "0%", "confirm": confirm},
        ],
    }
    return definition


def run(definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return history, len(trades), len(rebalances)


def metrics(history, start, end):
    sample = history.loc[start:end]
    performance = Performance(sample)
    return performance.cagr(), performance.mdd(), performance.calmar_ratio()


def candidate_definitions():
    current_hysteresis = hysteresis_definition()
    multi = multi_horizon_definition()
    multi_confirmed = multi_horizon_definition(confirm=2)
    defensive_confirmed = multi_horizon_definition(confirm=2, defensive_only=True)
    candidates = {
        "Baseline TDF40/BIL60": baseline_definition(),
        "Current BND hysteresis": current_hysteresis,
        "A Caution band release": release_caution_profit_band(
            baseline_definition()
        ),
        "A + current BND hysteresis": release_caution_profit_band(
            current_hysteresis
        ),
        "B Multi-horizon BND": multi,
        "A+B Caution release + multi-horizon": release_caution_profit_band(
            multi
        ),
        "C Multi-horizon confirm 2": multi_confirmed,
        "A+C Caution release + confirm 2": release_caution_profit_band(
            multi_confirmed
        ),
        "C Defensive-only confirm 2": defensive_confirmed,
        "A+C Defensive-only + Caution release": release_caution_profit_band(
            defensive_confirmed
        ),
    }
    for cap in (0.725, 0.750, 0.7625):
        label = f"Caution cap {cap:.2%}"
        candidates[label] = cap_caution_profit_band(
            baseline_definition(), cap
        )
        candidates[f"{label} + BND hysteresis"] = cap_caution_profit_band(
            current_hysteresis, cap
        )
    return candidates


def main():
    candidates = {
        name: run(definition)
        for name, definition in candidate_definitions().items()
    }
    benchmark = run(load_strategy_definition(BENCHMARK_SOURCE))[0]
    common_end = min(history.index.max() for history, _, _ in candidates.values())
    benchmark_cagr, benchmark_mdd, _ = metrics(
        benchmark, COMMON_START, common_end
    )

    rows = []
    rolling_rows = []
    for name, (history, trades, rebalances) in candidates.items():
        cagr, mdd, calmar = metrics(history, COMMON_START, common_end)
        later_cagr, later_mdd, _ = metrics(history, "2021-01-01", common_end)
        sample = history.loc[COMMON_START:common_end]
        bnd_weights = sample["Weights"].map(
            lambda weights: weights.get("BND", 0.0)
        )
        for year in range(2012, 2024):
            start = pd.Timestamp(year, 1, 1)
            end = pd.Timestamp(year + 2, 12, 31)
            candidate_cagr, candidate_mdd, _ = metrics(history, start, end)
            window_cagr, window_mdd, _ = metrics(benchmark, start, end)
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                "CAGRGap": candidate_cagr - window_cagr,
                "MDDImprovement": candidate_mdd - window_mdd,
            })
        rows.append({
            "Candidate": name,
            "CAGR": cagr,
            "MDD": mdd,
            "Calmar": calmar,
            "CAGRGap": cagr - benchmark_cagr,
            "MDDImprovement": mdd - benchmark_mdd,
            "LaterCAGR": later_cagr,
            "LaterMDD": later_mdd,
            "AverageBNDWeight": bnd_weights.mean(),
            "BNDExposureDays": int((bnd_weights > 1e-8).sum()),
            "TransactionCosts": (
                sample["TransactionCosts"].iloc[-1]
                - sample["TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })

    rolling = pd.DataFrame(rolling_rows)
    robustness = []
    for name, group in rolling.groupby("Candidate"):
        both = (group["CAGRGap"] > 0) & (group["MDDImprovement"] > 0)
        robustness.append({
            "Candidate": name,
            "CAGRWins": int((group["CAGRGap"] > 0).sum()),
            "MDDWins": int((group["MDDImprovement"] > 0).sum()),
            "BothWins": int(both.sum()),
            "WorstCAGRGap": group["CAGRGap"].min(),
            "WorstMDDImprovement": group["MDDImprovement"].min(),
        })
    summary = pd.DataFrame(rows).merge(
        pd.DataFrame(robustness), on="Candidate"
    ).sort_values(["BothWins", "Calmar", "CAGR"], ascending=False)

    RESULT_DIR.mkdir(exist_ok=True)
    summary.to_csv(
        RESULT_DIR / "tdf_bnd_improvement_candidates_summary.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "tdf_bnd_improvement_candidates_rolling.csv", index=False
    )

    display = summary.copy()
    for column in (
        "CAGR", "MDD", "CAGRGap", "MDDImprovement", "LaterCAGR",
        "LaterMDD", "AverageBNDWeight", "TransactionCosts",
        "WorstCAGRGap", "WorstMDDImprovement",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
