"""Test conditional BND use inside the TDF2050/BIL safe sleeve.

The baseline keeps 40% of the non-QQQ sleeve in TDF2050 and 60% in BIL.
Candidates replace part or all of the BIL allocation with BND only when a
monthly bond-relative-strength or bond-trend condition is favorable.
"""

from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.tdf_bil_retirement_insight import definition_for


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
BASE_CONFIG = {
    "canonical_qqq": 0.70,
    "recovery_qqq": 0.50,
    "upper_qqq": 0.775,
    "effective_cap": False,
}
TDF_SAFE_SHARE = 0.40
BOND_CAPACITY = 0.60

SIGNALS = {
    "relative": "BND.roc40 > BIL.roc40 + 0.20",
    "strict": (
        "BND.roc40 > BIL.roc40 + 1.50 and "
        "BND.close > BND.ema55"
    ),
    "trend": (
        "BND.close > BND.ema55 and BND.ema20 > BND.ema55 and "
        "BND.roc40 > 0"
    ),
    "strict_defense": (
        "state.market_mode in ['BEAR', 'RECOVERY'] and "
        "BND.roc40 > BIL.roc40 + 1.50 and BND.close > BND.ema55"
    ),
}


def _weights(qqq):
    safe = f"(1 - ({qqq}))"
    tdf = f"round({safe} * {TDF_SAFE_SHARE}, 10)"
    bnd = (
        f"round({safe} * {BOND_CAPACITY} * state.bond_share, 10)"
    )
    return {
        "QQQ": qqq,
        "TDF2050_PROXY": tdf,
        "BND": bnd,
        "BIL": f"round(max(0, 1 - ({qqq}) - ({tdf}) - ({bnd})), 10)",
    }


def conditional_definition(signal, replacement_share):
    definition = definition_for(BASE_CONFIG)
    definition["assets"]["required"] = [
        "QQQ", "TDF2050_PROXY", "BND", "BIL"
    ]
    definition["state"]["safe_tdf_share"] = {"initial": "0%", "rules": []}
    definition["state"]["bond_share"] = {
        "initial": "0%",
        "check": "monthly",
        "rules": [
            {"when": signal, "set": f"{replacement_share * 100}%"},
            {"otherwise": True, "set": "0%"},
        ],
    }
    band_condition = definition["target"][0]["when"]
    definition["target"] = [
        {"when": band_condition, "weights": _weights("portfolio.weight.QQQ")},
        {"weights": _weights("state.risk_weight")},
    ]
    definition["rebalance"] = [
        rule for rule in definition["rebalance"]
        if "changed(state.safe_tdf_share)" not in rule["when"]
    ]
    definition["rebalance"].insert(
        -1, {"when": "changed(state.bond_share)", "days": 1}
    )
    return definition


def baseline_definition():
    return conditional_definition("1 == 0", 0.0)


def hysteresis_definition(entry_gap=1.50, exit_gap=0.20):
    definition = conditional_definition("1 == 0", 1.0)
    definition["state"]["bond_share"] = {
        "initial": "0%",
        "check": "monthly",
        "rules": [
            {
                "when": (
                    "state.bond_share == 0 and "
                    f"BND.roc40 > BIL.roc40 + {entry_gap} and "
                    "BND.close > BND.ema55"
                ),
                "set": "100%",
            },
            {
                "when": (
                    "state.bond_share > 0 and "
                    f"BND.roc40 <= BIL.roc40 + {exit_gap}"
                ),
                "set": "0%",
            },
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


def main():
    candidates = {"Baseline TDF40/BIL60": run(baseline_definition())}
    for signal_name, signal in SIGNALS.items():
        for replacement in (0.50, 1.00):
            name = f"{signal_name} / BND replace {replacement:.0%}"
            candidates[name] = run(
                conditional_definition(signal, replacement)
            )
    candidates["hysteresis 1.5/0.2 / BND replace 100%"] = run(
        hysteresis_definition()
    )
    benchmark = run(
        load_strategy_definition(
            ROOT / "strategies" / "07_band_7030_bnd.yaml"
        )
    )[0]
    common_start = pd.Timestamp("2011-03-29")
    common_end = min(history.index.max() for history, _, _ in candidates.values())
    benchmark_cagr, benchmark_mdd, _ = metrics(
        benchmark, common_start, common_end
    )

    rows, rolling_rows = [], []
    for name, (history, trades, rebalances) in candidates.items():
        cagr, mdd, calmar = metrics(history, common_start, common_end)
        later_cagr, later_mdd, _ = metrics(history, "2021-01-01", common_end)
        sample = history.loc[common_start:common_end]
        bnd_weights = sample["Weights"].map(lambda weights: weights.get("BND", 0.0))
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
    summary.to_csv(RESULT_DIR / "conditional_bnd_summary.csv", index=False)
    rolling.to_csv(RESULT_DIR / "conditional_bnd_rolling.csv", index=False)

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
