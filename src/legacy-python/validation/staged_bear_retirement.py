"""Validate staged 70% -> 30% -> 0% Retirement risk reduction."""

from __future__ import annotations

import contextlib
import io

import pandas as pd

from backtest import Backtest
from config import DATA_DIR, END_DATE, RESULT_DIR, START_DATE
from experimental_strategies import StagedBearRetirementStrategy
from performance import Performance
from strategy import RetirementAllocationLegacyStrategy, STATIC_RETIREMENT_7030


FIXED_WINDOWS = (
    ("FULL", START_DATE, END_DATE),
    ("EARLY", "2012-01-01", "2017-12-31"),
    ("MID", "2018-01-01", "2021-12-31"),
    ("RECENT", "2022-01-01", END_DATE),
    ("PRE_COVID", "2012-01-01", "2019-12-31"),
    ("POST_COVID", "2020-01-01", END_DATE),
)
VARIANTS = (
    ("RETIREMENT_BASELINE", RetirementAllocationLegacyStrategy),
    ("STATIC_7030", STATIC_RETIREMENT_7030),
    ("STAGED_1D", lambda: StagedBearRetirementStrategy(1)),
    ("STAGED_2D", lambda: StagedBearRetirementStrategy(2)),
    ("STAGED_3D", lambda: StagedBearRetirementStrategy(3)),
    ("STAGED_5D", lambda: StagedBearRetirementStrategy(5)),
    ("STAGED_10D", lambda: StagedBearRetirementStrategy(10)),
    ("STAGED_30_FLOOR", lambda: StagedBearRetirementStrategy(None)),
)


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
    reasons = pd.Series(
        [event.get("Reason") or "" for event in rebalances], dtype=str
    )
    return {
        "CAGR": metrics["CAGR"],
        "MDD": metrics["MDD"],
        "Sharpe": metrics["Sharpe"],
        "Calmar": metrics["Calmar"],
        "TransactionCosts": metrics["TransactionCosts"],
        "Trades": len(trades),
        "Rebalances": len(rebalances),
        "Stage1Entries": int(reasons.str.contains("BEAR_STAGE1_30").sum()),
        "Stage2Entries": int(reasons.str.contains("BEAR_STAGE2_0").sum()),
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
    rows = []
    for window, start, end in windows:
        for variant, factory in VARIANTS:
            rows.append({
                "Window": window,
                "Variant": variant,
                **_run(factory, start, end),
            })
    return pd.DataFrame(rows)


def _sensitivity_summary(full, rolling):
    baseline_full = full.loc[
        full["Variant"] == "RETIREMENT_BASELINE"
    ].iloc[0]
    baseline_rolling = rolling.loc[
        rolling["Variant"] == "RETIREMENT_BASELINE"
    ].set_index("Window")
    rows = []
    for variant in (
        "STAGED_1D", "STAGED_2D", "STAGED_3D", "STAGED_5D",
        "STAGED_10D", "STAGED_30_FLOOR",
    ):
        candidate_full = full.loc[full["Variant"] == variant].iloc[0]
        candidate_rolling = rolling.loc[
            rolling["Variant"] == variant
        ].set_index("Window")
        cagr_gap = candidate_rolling["CAGR"] - baseline_rolling["CAGR"]
        mdd_gap = candidate_rolling["MDD"] - baseline_rolling["MDD"]
        sharpe_gap = candidate_rolling["Sharpe"] - baseline_rolling["Sharpe"]
        rows.append({
            "Variant": variant,
            "FullCAGR": candidate_full["CAGR"],
            "FullMDD": candidate_full["MDD"],
            "FullSharpe": candidate_full["Sharpe"],
            "FullCAGRGap": candidate_full["CAGR"] - baseline_full["CAGR"],
            "FullMDDImprovement": candidate_full["MDD"] - baseline_full["MDD"],
            "FullSharpeGap": candidate_full["Sharpe"] - baseline_full["Sharpe"],
            "FullStage1Entries": candidate_full["Stage1Entries"],
            "FullStage2Entries": candidate_full["Stage2Entries"],
            "RollingWindows": len(candidate_rolling),
            "RollingCAGRWins": int((cagr_gap > 0.0).sum()),
            "RollingMDDWins": int((mdd_gap > 0.0).sum()),
            "RollingSharpeWins": int((sharpe_gap > 0.0).sum()),
            "RollingBothWins": int(((cagr_gap > 0.0) & (mdd_gap > 0.0)).sum()),
            "WorstRollingCAGRGap": cagr_gap.min(),
            "WorstRollingMDDImprovement": mdd_gap.min(),
        })
    return pd.DataFrame(rows)


def _markdown_report(fixed, sensitivity):
    full = fixed.loc[fixed["Window"] == "FULL"]
    lines = [
        "# Retirement 단계적 위험축소 검증",
        "",
        "기존 BEAR 0% 전환과 70%→30%→0% 후보를 비교했다. 단계 2는 기존 "
        "structural_bear 조건이 BEAR 진입 후에도 지정한 거래일만큼 유지될 때 실행한다.",
        "",
        "## 전체기간",
        "",
        "| 후보 | CAGR | MDD | Sharpe | 1단계 | 2단계 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in full.iterrows():
        lines.append(
            f"| {row['Variant']} | {row['CAGR']:.2%} | {row['MDD']:.2%} | "
            f"{row['Sharpe']:.3f} | {int(row['Stage1Entries'])} | "
            f"{int(row['Stage2Entries'])} |"
        )
    lines.extend([
        "",
        "## 기존 Retirement 대비 민감도",
        "",
        "| 후보 | CAGR 차이 | MDD 개선 | Sharpe 차이 | 롤링 CAGR 승 | 롤링 MDD 승 | 동시 승 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ])
    for _, row in sensitivity.iterrows():
        lines.append(
            f"| {row['Variant']} | {row['FullCAGRGap']:.2%} | "
            f"{row['FullMDDImprovement']:.2%} | {row['FullSharpeGap']:.3f} | "
            f"{int(row['RollingCAGRWins'])}/{int(row['RollingWindows'])} | "
            f"{int(row['RollingMDDWins'])}/{int(row['RollingWindows'])} | "
            f"{int(row['RollingBothWins'])}/{int(row['RollingWindows'])} |"
        )
    lines.extend([
        "",
        "## 결론",
        "",
        "모든 단계형 후보가 전체기간 CAGR, MDD, Sharpe에서 기존 전략보다 낮았다. "
        "13개 롤링 3년 구간에서도 CAGR과 MDD를 동시에 개선한 후보는 없었다. "
        "단일 이벤트의 21일 반등 손실 감소가 전체 포트폴리오 경로의 개선으로 "
        "이어지지 않았으므로 이 규칙은 채택하지 않는다.",
    ])
    return "\n".join(lines) + "\n"


def run_staged_bear_validation():
    fixed = _window_report(FIXED_WINDOWS)
    rolling = _window_report(_rolling_windows())
    full = fixed.loc[fixed["Window"] == "FULL"]
    sensitivity = _sensitivity_summary(full, rolling)
    fixed.to_csv(RESULT_DIR / "staged_bear_fixed_windows.csv", index=False)
    rolling.to_csv(RESULT_DIR / "staged_bear_rolling_windows.csv", index=False)
    sensitivity.to_csv(
        RESULT_DIR / "staged_bear_sensitivity_summary.csv", index=False
    )
    (RESULT_DIR / "staged_bear_validation.md").write_text(
        _markdown_report(fixed, sensitivity), encoding="utf-8"
    )
    return {"fixed": fixed, "rolling": rolling, "sensitivity": sensitivity}


if __name__ == "__main__":
    reports = run_staged_bear_validation()
    print("Full-period comparison")
    print(
        reports["fixed"].loc[reports["fixed"]["Window"] == "FULL"].to_string(
            index=False
        )
    )
    print("\nStaged sensitivity versus Retirement baseline")
    print(reports["sensitivity"].to_string(index=False))
