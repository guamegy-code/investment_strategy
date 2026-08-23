"""Search state-specific TDF/BIL safe-sleeve allocations.

QQQ targets, four-state transition conditions, confirmation periods, and the
77.5% profit band stay fixed.  TDF shares within the non-QQQ sleeve vary by
BULL, CAUTION, BEAR, and RECOVERY, without monthly momentum switching.
"""

from itertools import product
from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.tdf_bil_retirement_insight import definition_for


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_SOURCE = ROOT / "strategies" / "07_band_7030_bnd.yaml"
BASE_CONFIG = {
    "canonical_qqq": 0.70,
    "recovery_qqq": 0.50,
    "upper_qqq": 0.775,
    "effective_cap": False,
}


def state_mix_definition(bull, caution, bear, recovery):
    definition = definition_for(BASE_CONFIG)
    definition["state"]["safe_tdf_share"] = {
        "initial": f"{bull * 100}%",
        "rules": [
            {"when": "state.market_mode == 'BEAR'", "set": f"{bear * 100}%"},
            {
                "when": "state.market_mode == 'RECOVERY'",
                "set": f"{recovery * 100}%",
            },
            {
                "when": "state.market_mode == 'CAUTION'",
                "set": f"{caution * 100}%",
            },
            {"when": "state.market_mode == 'BULL'", "set": f"{bull * 100}%"},
        ],
    }
    # A change between BULL and CAUTION normally preserves the profit band;
    # the safe sleeve still needs one trade to apply its new fixed state mix.
    return definition


def run_definition(definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return history, len(trades), len(rebalances)


def metrics(history, start, end):
    period = history.loc[start:end]
    performance = Performance(period)
    return performance.cagr(), performance.mdd(), performance.calmar_ratio()


def main():
    benchmark, _, _ = run_definition(load_strategy_definition(BENCHMARK_SOURCE))
    candidates = {}
    counts = {}
    for bull, caution, recovery in product(
        (0.75, 1.00), (0.25, 0.50, 0.75), (0.25, 0.50, 0.75)
    ):
        bear = 0.0
        name = (
            f"B{bull:.0%}_C{caution:.0%}_BE{bear:.0%}_R{recovery:.0%}"
        )
        history, trades, rebalances = run_definition(
            state_mix_definition(bull, caution, bear, recovery)
        )
        candidates[name] = history
        counts[name] = (trades, rebalances)

    common_start = pd.Timestamp("2011-03-29")
    common_end = min(history.index.max() for history in candidates.values())
    benchmark_cagr, benchmark_mdd, _ = metrics(
        benchmark, common_start, common_end
    )
    rows = []
    rolling_rows = []
    for name, history in candidates.items():
        cagr, mdd, calmar = metrics(history, common_start, common_end)
        later_cagr, later_mdd, _ = metrics(history, "2021-01-01", common_end)
        for year in range(2012, 2024):
            start = pd.Timestamp(year=year, month=1, day=1)
            end = pd.Timestamp(year=year + 2, month=12, day=31)
            candidate_cagr, candidate_mdd, _ = metrics(history, start, end)
            window_benchmark_cagr, window_benchmark_mdd, _ = metrics(
                benchmark, start, end
            )
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                "CAGRGap": candidate_cagr - window_benchmark_cagr,
                "MDDImprovement": candidate_mdd - window_benchmark_mdd,
            })
        trades, rebalances = counts[name]
        costs = (
            history.loc[common_start:common_end, "TransactionCosts"].iloc[-1]
            - history.loc[common_start:common_end, "TransactionCosts"].iloc[0]
        )
        rows.append({
            "Candidate": name,
            "CAGR": cagr,
            "MDD": mdd,
            "Calmar": calmar,
            "CAGRGap": cagr - benchmark_cagr,
            "MDDImprovement": mdd - benchmark_mdd,
            "LaterCAGR": later_cagr,
            "LaterMDD": later_mdd,
            "TransactionCosts": costs,
            "Trades": trades,
            "Rebalances": rebalances,
        })

    rolling = pd.DataFrame(rolling_rows)
    summary = pd.DataFrame(rows)
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
    summary = summary.merge(pd.DataFrame(robustness), on="Candidate")
    summary = summary.sort_values(
        ["BothWins", "Calmar", "CAGR"], ascending=False
    )
    summary.to_csv(ROOT / "results" / "tdf_bil_state_mix_summary.csv", index=False)
    rolling.to_csv(ROOT / "results" / "tdf_bil_state_mix_rolling.csv", index=False)

    display = summary.head(10).copy()
    for column in (
        "CAGR", "MDD", "CAGRGap", "MDDImprovement", "LaterCAGR",
        "LaterMDD", "TransactionCosts", "WorstCAGRGap",
        "WorstMDDImprovement",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    display["Calmar"] = display["Calmar"].map(lambda value: f"{value:.2f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
