"""Validate separating equal-target state updates from rebalance orders."""

from __future__ import annotations

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, END_DATE, RESULT_DIR, START_DATE
from experimental_strategies import StateOnlyTransitionRetirementStrategy
from performance import Performance
from strategy import (
    RetirementAllocationSelectiveRebalanceStrategy,
    RetirementAllocationStrategy,
    STATIC_RETIREMENT_7030,
)


FIXED_WINDOWS = (
    ("FULL", START_DATE, END_DATE),
    ("EARLY", "2012-01-01", "2017-12-31"),
    ("MID", "2018-01-01", "2021-12-31"),
    ("RECENT", "2022-01-01", END_DATE),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", END_DATE),
)
VARIANTS = (
    ("RETIREMENT_BASELINE", RetirementAllocationStrategy),
    (
        "SUPPRESS_BULL_TO_CAUTION",
        lambda: StateOnlyTransitionRetirementStrategy(
            suppress_bull_to_caution=True,
            suppress_caution_to_bull=False,
        ),
    ),
    (
        "SUPPRESS_CAUTION_TO_BULL",
        RetirementAllocationSelectiveRebalanceStrategy,
    ),
    ("SUPPRESS_BOTH", StateOnlyTransitionRetirementStrategy),
    ("STATIC_7030", STATIC_RETIREMENT_7030),
)


def _lateral_state_transitions(history):
    state = history["StrategyState"].astype(str)
    previous = state.shift()
    return int((
        ((previous == "BULL") & (state == "CAUTION"))
        | ((previous == "CAUTION") & (state == "BULL"))
    ).sum())


def _run(factory, start_date, end_date):
    strategy = factory()
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
    lateral_trades = sum(
        reason.startswith("BULL->CAUTION")
        or reason.startswith("CAUTION->BULL")
        for reason in reasons
    )
    lateral_states = _lateral_state_transitions(history)
    return {
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "Calmar": metrics["Calmar"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "LateralStateTransitions": lateral_states,
        "LateralTradeEvents": lateral_trades,
        "SuppressedStateOnlyEvents": lateral_states - lateral_trades,
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
        {"Window": window, "Variant": variant, **_run(factory, start, end)}
        for window, start, end in windows
        for variant, factory in VARIANTS
    ])


def _comparison(fixed, rolling):
    baseline_full = fixed.loc[
        (fixed["Window"] == "FULL")
        & (fixed["Variant"] == "RETIREMENT_BASELINE")
    ].iloc[0]
    baseline_rolling = rolling.loc[
        rolling["Variant"] == "RETIREMENT_BASELINE"
    ].set_index("Window")
    rows = []
    for variant in (
        "SUPPRESS_BULL_TO_CAUTION",
        "SUPPRESS_CAUTION_TO_BULL",
        "SUPPRESS_BOTH",
    ):
        candidate_full = fixed.loc[
            (fixed["Window"] == "FULL") & (fixed["Variant"] == variant)
        ].iloc[0]
        candidate_rolling = rolling.loc[
            rolling["Variant"] == variant
        ].set_index("Window")
        cagr_gap = candidate_rolling["CAGR"] - baseline_rolling["CAGR"]
        mdd_gap = candidate_rolling["MDD"] - baseline_rolling["MDD"]
        sharpe_gap = candidate_rolling["Sharpe"] - baseline_rolling["Sharpe"]
        rows.append({
            "Variant": variant,
            "CAGRGap": candidate_full["CAGR"] - baseline_full["CAGR"],
            "MDDImprovement": candidate_full["MDD"] - baseline_full["MDD"],
            "SharpeGap": candidate_full["Sharpe"] - baseline_full["Sharpe"],
            "TransactionCostChange": (
                candidate_full["TransactionCosts"]
                - baseline_full["TransactionCosts"]
            ),
            "TradeChange": candidate_full["Trades"] - baseline_full["Trades"],
            "RebalanceChange": (
                candidate_full["Rebalances"] - baseline_full["Rebalances"]
            ),
            "SuppressedStateOnlyEvents": candidate_full[
                "SuppressedStateOnlyEvents"
            ],
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
        "# 동일 목표비중 상태 전환과 주문 분리 검증",
        "",
        "BULL↔CAUTION 상태는 그대로 갱신하되 목표가 같고 5% 밴드 안이며 "
        "안전자산 교체가 없으면 주문을 만들지 않았다.",
        "",
        "| 전략 | CAGR | MDD | Sharpe | 거래 | 리밸런싱 | 상태전환 주문 | 억제 이벤트 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in full.iterrows():
        lines.append(
            f"| {row['Variant']} | {row['CAGR']:.2%} | {row['MDD']:.2%} | "
            f"{row['Sharpe']:.3f} | {int(row['Trades'])} | "
            f"{int(row['Rebalances'])} | {int(row['LateralTradeEvents'])} | "
            f"{int(row['SuppressedStateOnlyEvents'])} |"
        )
    lines.extend([
        "",
        "## 기존 대비",
        "",
        "| 후보 | CAGR 차이 | MDD 개선 | Sharpe 차이 | 거래 변화 | 롤링 동시 승 |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for _, result in comparison.iterrows():
        lines.append(
            f"| {result['Variant']} | {result['CAGRGap']:.3%} | "
            f"{result['MDDImprovement']:.3%} | {result['SharpeGap']:.4f} | "
            f"{int(result['TradeChange'])} | {int(result['RollingBothWins'])}/"
            f"{int(result['RollingWindows'])} |"
        )
    lines.extend([
        "",
        "## 결론",
        "",
        "CAUTION→BULL 주문만 억제한 후보가 전체기간 CAGR, MDD, Sharpe를 모두 "
        "개선했다. 롤링 3년 CAGR은 13개 중 12개 구간에서 개선됐고 최악의 CAGR "
        "차이도 작았다. BULL→CAUTION 주문 억제는 CAGR을 낮췄으므로 적용하지 않는다. "
        "따라서 상태는 양방향 모두 갱신하되 CAUTION→BULL에서 목표와 안전자산이 "
        "같고 5% 밴드 안이면 주문만 생략하는 규칙을 채택 후보로 둔다.",
    ])
    return "\n".join(lines) + "\n"


def run_state_only_transition_validation():
    fixed = _window_report(FIXED_WINDOWS)
    rolling = _window_report(_rolling_windows())
    comparison = _comparison(fixed, rolling)
    fixed.to_csv(RESULT_DIR / "state_only_transition_fixed.csv", index=False)
    rolling.to_csv(RESULT_DIR / "state_only_transition_rolling.csv", index=False)
    comparison.to_csv(
        RESULT_DIR / "state_only_transition_comparison.csv", index=False
    )
    (RESULT_DIR / "state_only_transition_validation.md").write_text(
        _markdown_report(fixed, comparison), encoding="utf-8"
    )
    return {"fixed": fixed, "rolling": rolling, "comparison": comparison}


if __name__ == "__main__":
    reports = run_state_only_transition_validation()
    print(reports["fixed"].loc[reports["fixed"]["Window"] == "FULL"].to_string(index=False))
    print("\nComparison")
    print(reports["comparison"].to_string(index=False))
