"""Check whether strategy 21's threshold choice is stable near its optimum.

This is a robustness check, not a new optimization.  It replays the fine local
grid used for strategy 21 and compares the chosen parameters with nearby
choices across non-overlapping periods.
"""

from __future__ import annotations

from buy_3dip_joint_threshold_search import Parameters, candidates, load_prices, simulate


STRATEGY_21 = Parameters(0.10, 0.20, 0.325, 0.075, 0.085, 0.175)
WINDOWS = {
    "2012-2014": ("2012-01-03", "2014-12-31"),
    "2015-2017": ("2015-01-01", "2017-12-31"),
    "2018-2020": ("2018-01-01", "2020-12-31"),
    "2021-2023": ("2021-01-01", "2023-12-31"),
    "2024-2026": ("2024-01-01", None),
}


def label(parameters: Parameters) -> str:
    return (
        f"-{parameters.entry_b:.2%}/-{parameters.entry_c:.2%}/-{parameters.entry_d:.2%} "
        f"| +{parameters.recovery_1_gain:.2%}/-{parameters.recovery_2_drawdown:.2%}"
        f"/-{parameters.recovery_3_drawdown:.2%}"
    )


def main() -> None:
    data = load_prices()
    baseline_full = simulate(data, STRATEGY_21)
    rows = []
    for parameters in candidates():
        full = simulate(data, parameters)
        row = {"parameters": parameters, **full}
        for name, (start, end) in WINDOWS.items():
            period = data.loc[start:end]
            result = simulate(period, parameters)
            baseline = simulate(period, STRATEGY_21)
            row[f"{name}_CAGR_delta"] = result["CAGR"] - baseline["CAGR"]
            row[f"{name}_MDD_delta"] = result["MDD"] - baseline["MDD"]
        rows.append(row)

    # The chosen configuration should remain close to the local efficient
    # frontier rather than rely on a single isolated grid point.
    near = [
        row
        for row in rows
        if row["CAGR"] >= baseline_full["CAGR"] - 0.0025
        and row["MDD"] >= baseline_full["MDD"] - 0.0025
    ]
    both_better = [
        row
        for row in rows
        if row["CAGR"] > baseline_full["CAGR"]
        and row["MDD"] > baseline_full["MDD"]
    ]

    print("strategy_21", label(STRATEGY_21), baseline_full)
    print(f"candidates={len(rows)} near_frontier={len(near)} both_better={len(both_better)}")
    print("\nWindow deltas versus strategy 21 (CAGR / MDD, percentage points)")
    for name, (start, end) in WINDOWS.items():
        baseline = simulate(data.loc[start:end], STRATEGY_21)
        print(f"{name}: baseline CAGR={baseline['CAGR']:.3%}, MDD={baseline['MDD']:.3%}")
        for metric in ("CAGR", "MDD"):
            values = [row[f"{name}_{metric}_delta"] * 100 for row in near]
            print(f"  near-frontier {metric} delta range: {min(values):+.3f}pp to {max(values):+.3f}pp")

    print("\nNear-frontier candidates")
    for row in sorted(near, key=lambda value: (value["Calmar"], value["CAGR"]), reverse=True):
        print(
            label(row["parameters"]),
            f"CAGR={row['CAGR']:.3%} MDD={row['MDD']:.3%} Calmar={row['Calmar']:.3f}",
        )


if __name__ == "__main__":
    main()
