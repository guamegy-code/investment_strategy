"""Compare selective-rebalance SafeBlend and VXUS extensions."""

from __future__ import annotations

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, RESULT_DIR, START_DATE
from performance import Performance
from strategy import (
    RetirementAllocationLegacyStrategy,
    RetirementAllocationSafeBlendStrategy,
    RetirementAllocationSPYStrategy,
    RetirementAllocationVXUSStrategy,
    RetirementAllocationStrategy,
    SafeBlendAllocationStrategy,
    STATIC_RETIREMENT_7030,
    VXUSSubstitutionStrategy,
)


COMMON_END_DATE = "2026-07-31"
FIXED_WINDOWS = (
    ("FULL", START_DATE, COMMON_END_DATE),
    ("EARLY", "2012-01-01", "2017-12-31"),
    ("MID", "2018-01-01", "2021-12-31"),
    ("RECENT", "2022-01-01", COMMON_END_DATE),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", COMMON_END_DATE),
)
VARIANTS = (
    ("RETIREMENT", RetirementAllocationLegacyStrategy),
    ("SELECTIVE_RETIREMENT", RetirementAllocationStrategy),
    ("SAFE_BLEND", SafeBlendAllocationStrategy),
    ("SELECTIVE_SAFE_BLEND", RetirementAllocationSafeBlendStrategy),
    ("VXUS", VXUSSubstitutionStrategy),
    ("SELECTIVE_VXUS", RetirementAllocationVXUSStrategy),
    ("SELECTIVE_SPY", RetirementAllocationSPYStrategy),
    ("STATIC_7030", STATIC_RETIREMENT_7030),
)
PAIRS = (
    ("RETIREMENT", "SELECTIVE_RETIREMENT"),
    ("SAFE_BLEND", "SELECTIVE_SAFE_BLEND"),
    ("VXUS", "SELECTIVE_VXUS"),
    ("SELECTIVE_VXUS", "SELECTIVE_SPY"),
)


def _run(factory, start_date, end_date):
    strategy = factory()
    if end_date is None or pd.Timestamp(end_date) > pd.Timestamp(COMMON_END_DATE):
        end_date = COMMON_END_DATE
    with contextlib.redirect_stdout(io.StringIO()):
        history, trades, rebalances = Backtest(
            strategy,
            data_dir=DATA_DIR,
            tickers=strategy.required_tickers,
            start_date=start_date,
            end_date=end_date,
        ).run_all()
    metrics = Performance(history).summary()
    reasons = [event.get("Reason") or "" for event in rebalances]
    return {
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "Calmar": metrics["Calmar"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "CautionToBullOrders": sum(
            reason.startswith("CAUTION->BULL") for reason in reasons
        ),
    }


def _rolling_windows():
    final_year = pd.Timestamp(
        pd.read_csv(DATA_DIR / "QQQ.csv", usecols=["Date"])["Date"].max()
    ).year
    return tuple(
        (
            f"ROLLING_3Y_{year}_{year + 2}",
            f"{year}-01-01",
            f"{year + 2}-12-31",
        )
        for year in range(pd.Timestamp(START_DATE).year, final_year - 1)
    )


def _window_report(windows):
    return pd.DataFrame([
        {"Window": window, "Variant": label, **_run(factory, start, end)}
        for window, start, end in windows
        for label, factory in VARIANTS
    ])


def _pair_comparison(fixed, rolling):
    full = fixed.loc[fixed["Window"] == "FULL"].set_index("Variant")
    rows = []
    for parent, candidate in PAIRS:
        parent_rolling = rolling.loc[
            rolling["Variant"] == parent
        ].set_index("Window")
        candidate_rolling = rolling.loc[
            rolling["Variant"] == candidate
        ].set_index("Window")
        cagr_gap = candidate_rolling["CAGR"] - parent_rolling["CAGR"]
        mdd_gap = candidate_rolling["MDD"] - parent_rolling["MDD"]
        sharpe_gap = candidate_rolling["Sharpe"] - parent_rolling["Sharpe"]
        rows.append({
            "Parent": parent,
            "Candidate": candidate,
            "CAGRGap": full.at[candidate, "CAGR"] - full.at[parent, "CAGR"],
            "MDDImprovement": full.at[candidate, "MDD"] - full.at[parent, "MDD"],
            "SharpeGap": (
                full.at[candidate, "Sharpe"] - full.at[parent, "Sharpe"]
            ),
            "TransactionCostChange": (
                full.at[candidate, "TransactionCosts"]
                - full.at[parent, "TransactionCosts"]
            ),
            "TradeChange": full.at[candidate, "Trades"] - full.at[parent, "Trades"],
            "RebalanceChange": (
                full.at[candidate, "Rebalances"]
                - full.at[parent, "Rebalances"]
            ),
            "CautionToBullOrderChange": (
                full.at[candidate, "CautionToBullOrders"]
                - full.at[parent, "CautionToBullOrders"]
            ),
            "RollingWindows": len(candidate_rolling),
            "RollingCAGRWins": int((cagr_gap > 0.0).sum()),
            "RollingMDDWins": int((mdd_gap > 0.0).sum()),
            "RollingSharpeWins": int((sharpe_gap > 0.0).sum()),
            "RollingBothWins": int(
                ((cagr_gap > 0.0) & (mdd_gap > 0.0)).sum()
            ),
            "WorstRollingCAGRGap": cagr_gap.min(),
            "WorstRollingMDDImprovement": mdd_gap.min(),
        })
    return pd.DataFrame(rows)


def _markdown_report(fixed, comparison):
    full = fixed.loc[fixed["Window"] == "FULL"]
    lines = [
        "# 선택적 리밸런싱 확장 전략 비교",
        "",
        "Retirement, SafeBlend, VXUS 세 전략에서 CAUTION→BULL의 동일 목표 주문만 "
        "선택적으로 생략한 결과를 각 직접 부모와 비교했다.",
        "",
        "## 전체기간 성과",
        "",
        "| 전략 | CAGR | MDD | Sharpe | 거래 | 리밸런싱 | CAUTION→BULL 주문 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in full.iterrows():
        lines.append(
            f"| {row['Variant']} | {row['CAGR']:.2%} | {row['MDD']:.2%} | "
            f"{row['Sharpe']:.3f} | {int(row['Trades'])} | "
            f"{int(row['Rebalances'])} | {int(row['CautionToBullOrders'])} |"
        )
    lines.extend([
        "",
        "## 직접 부모 대비",
        "",
        "| 후보 | CAGR 차이 | MDD 개선 | Sharpe 차이 | 거래 변화 | 롤링 CAGR 승 | 롤링 MDD 승 | 동시 승 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for _, row in comparison.iterrows():
        lines.append(
            f"| {row['Candidate']} | {row['CAGRGap']:.3%} | "
            f"{row['MDDImprovement']:.3%} | {row['SharpeGap']:.4f} | "
            f"{int(row['TradeChange'])} | {int(row['RollingCAGRWins'])}/"
            f"{int(row['RollingWindows'])} | {int(row['RollingMDDWins'])}/"
            f"{int(row['RollingWindows'])} | {int(row['RollingBothWins'])}/"
            f"{int(row['RollingWindows'])} |"
        )
    return "\n".join(lines) + "\n"


def run_selective_extension_validation():
    fixed = _window_report(FIXED_WINDOWS)
    rolling = _window_report(_rolling_windows())
    comparison = _pair_comparison(fixed, rolling)
    fixed.to_csv(
        RESULT_DIR / "selective_rebalance_extensions_fixed.csv", index=False
    )
    rolling.to_csv(
        RESULT_DIR / "selective_rebalance_extensions_rolling.csv", index=False
    )
    comparison.to_csv(
        RESULT_DIR / "selective_rebalance_extensions_comparison.csv", index=False
    )
    (RESULT_DIR / "selective_rebalance_extensions.md").write_text(
        _markdown_report(fixed, comparison), encoding="utf-8"
    )
    return {"fixed": fixed, "rolling": rolling, "comparison": comparison}


if __name__ == "__main__":
    reports = run_selective_extension_validation()
    print("Full-period comparison")
    print(
        reports["fixed"].loc[
            reports["fixed"]["Window"] == "FULL"
        ].to_string(index=False)
    )
    print("\nDirect-parent comparison")
    print(reports["comparison"].to_string(index=False))
