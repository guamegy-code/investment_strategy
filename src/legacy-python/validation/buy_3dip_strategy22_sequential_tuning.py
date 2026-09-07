"""22번 전략을 목표 비중, 임계값, 연속형 비중 순서로 튜닝한다."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MODULE_DIR.parents[1]
sys.path.insert(0, str(MODULE_DIR))

from buy_3dip_composite_valuation import load_scores  # noqa: E402
from buy_3dip_parameter_search import _trade, load_prices  # noqa: E402
from backtest import Backtest  # noqa: E402
from performance import Performance  # noqa: E402
from strategy_dsl import DeclarativeStrategy, load_strategy_definition  # noqa: E402


END_DATE = "2026-07-31"
DEVELOPMENT_END = "2020-12-31"
RECENT_START = "2021-01-01"
BASE_TARGETS = (0.70, 0.87, 0.90, 1.00)
OUTPUT = PROJECT_ROOT / "tmp" / "buy_3dip_strategy22_sequential_tuning.csv"


@dataclass(frozen=True)
class DiscreteProfile:
    mild_entry: float = 65.0
    high_entry: float = 75.0
    mild_exit: float = 60.0
    high_exit: float = 65.0
    mild_stage0: float = 0.60
    mild_stage1: float = 0.84
    high_stage0: float = 0.55
    high_stage1: float = 0.82


@dataclass(frozen=True)
class ContinuousProfile:
    start: float
    cap: float
    max_stage0_cut: float
    max_stage1_cut: float


BASELINE_22 = DiscreteProfile()


def _discrete_level(score: float, old: int, profile: DiscreteProfile) -> int:
    """완충구간을 유지하면서 월간 밸류에이션 상태를 갱신한다."""
    if not np.isfinite(score):
        return old
    if old == 2:
        if score < profile.mild_exit:
            return 0
        if score < profile.high_exit:
            return 1
        return 2
    if old == 1:
        if score >= profile.high_entry:
            return 2
        if score < profile.mild_exit:
            return 0
        return 1
    if score >= profile.high_entry:
        return 2
    if score >= profile.mild_entry:
        return 1
    return 0


def _discrete_target(stage: int, level: int, profile: DiscreteProfile) -> float:
    if stage >= 2 or level == 0:
        return BASE_TARGETS[stage]
    if level == 1:
        return profile.mild_stage0 if stage == 0 else profile.mild_stage1
    return profile.high_stage0 if stage == 0 else profile.high_stage1


def _continuous_target(stage: int, score: float, profile: ContinuousProfile) -> float:
    if stage >= 2 or not np.isfinite(score):
        return BASE_TARGETS[stage]
    intensity = float(np.clip((score - profile.start) / (profile.cap - profile.start), 0.0, 1.0))
    cut = profile.max_stage0_cut if stage == 0 else profile.max_stage1_cut
    return BASE_TARGETS[stage] - cut * intensity


def simulate(
    data: pd.DataFrame,
    profile: DiscreteProfile | ContinuousProfile,
) -> dict[str, float]:
    """다음 거래일 시가 체결과 실제 거래비용을 포함해 전략을 재현한다."""
    q_open = data["QQQ_Open"].to_numpy(float)
    q_close = data["QQQ_Close"].to_numpy(float)
    b_open = data["BIL_Open"].to_numpy(float)
    b_close = data["BIL_Close"].to_numpy(float)
    scores = data["all4"].to_numpy(float)
    values = np.empty(len(data))

    cash, q_shares, b_shares = 1.0, 0.0, 0.0
    stage, level, peak = 0, 0, 0.0
    current_target, pending_target = BASE_TARGETS[0], BASE_TARGETS[0]
    prior_month = None
    rebalances = 0

    for index, date in enumerate(data.index):
        if pending_target is not None:
            total = cash + q_shares * q_open[index] + b_shares * b_open[index]
            q_delta = total * pending_target / q_open[index] - q_shares
            b_delta = total * (1 - pending_target) / b_open[index] - b_shares
            if q_delta < 0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta < 0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            if q_delta > 0:
                cash, q_shares = _trade(cash, q_shares, q_open[index], q_delta)
            if b_delta > 0:
                cash, b_shares = _trade(cash, b_shares, b_open[index], b_delta)
            current_target = pending_target
            pending_target = None
            rebalances += 1

        month = date.to_period("M")
        if month != prior_month:
            if isinstance(profile, DiscreteProfile):
                new_level = _discrete_level(scores[index], level, profile)
                desired = _discrete_target(stage, new_level, profile)
                level = new_level
            else:
                desired = _continuous_target(stage, scores[index], profile)
            if abs(desired - current_target) > 1e-10:
                pending_target = desired
            prior_month = month

        old_stage = stage
        price = q_close[index]
        if stage == 3 and price >= peak * (1 - 0.175):
            stage = 2
        elif stage == 2 and price <= peak * (1 - 0.325):
            stage = 3
        elif stage == 2 and price >= peak * (1 - 0.085):
            stage = 1
        elif stage == 1 and price <= peak * (1 - 0.20):
            stage = 2
        elif stage == 1 and price >= peak * 1.075:
            stage = 0
        elif stage == 0 and peak > 0 and price <= peak * 0.90:
            stage = 1

        changed = stage != old_stage
        if peak == 0:
            peak = price
        elif changed and stage == 0:
            peak = price
        elif stage == 0 and price > peak:
            peak = price

        if changed:
            if isinstance(profile, DiscreteProfile):
                desired = _discrete_target(stage, level, profile)
            else:
                desired = _continuous_target(stage, scores[index], profile)
            if abs(desired - current_target) > 1e-10:
                pending_target = desired

        values[index] = cash + q_shares * q_close[index] + b_shares * b_close[index]

    years = (data.index[-1] - data.index[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1 / years) - 1
    mdd = float((values / np.maximum.accumulate(values) - 1).min())
    return {
        "CAGR": cagr,
        "MDD": mdd,
        "Calmar": cagr / abs(mdd),
        "Rebalances": rebalances,
    }


def evaluate(data: pd.DataFrame, profile) -> dict[str, float]:
    full = simulate(data, profile)
    development = simulate(data.loc[:DEVELOPMENT_END], profile)
    recent = simulate(data.loc[RECENT_START:], profile)
    return {
        **full,
        "Dev_CAGR": development["CAGR"],
        "Dev_MDD": development["MDD"],
        "Recent_CAGR": recent["CAGR"],
        "Recent_MDD": recent["MDD"],
    }


def acceptable(candidate: dict[str, float], baseline: dict[str, float]) -> bool:
    """수익률을 우선하되 MDD와 구간별 성과의 허용 가능한 열위만 인정한다."""
    return (
        candidate["CAGR"] > baseline["CAGR"]
        and candidate["MDD"] >= baseline["MDD"] - 0.0025
        and candidate["Dev_CAGR"] >= baseline["Dev_CAGR"] - 0.001
        and candidate["Dev_MDD"] >= baseline["Dev_MDD"] - 0.0025
        and candidate["Recent_CAGR"] >= baseline["Recent_CAGR"] - 0.001
        and candidate["Recent_MDD"] >= baseline["Recent_MDD"] - 0.0025
    )


def search(data, experiment: str, candidates, baseline_profile):
    baseline = evaluate(data, baseline_profile)
    rows = []
    for profile in candidates:
        metrics = evaluate(data, profile)
        rows.append({"experiment": experiment, **asdict(profile), **metrics})
    frame = pd.DataFrame(rows)
    mask = [acceptable(row, baseline) for row in frame.to_dict("records")]
    eligible = frame.loc[mask].sort_values(["CAGR", "Calmar"], ascending=False)
    winner = None if eligible.empty else eligible.iloc[0]
    print(f"\n[{experiment}] 기준선 {baseline}")
    print(f"탐색 {len(frame)}개, 통과 {len(eligible)}개")
    if winner is not None:
        print(eligible.head(10).to_string(index=False))
    return frame, winner, baseline


def allocation_candidates(profile: DiscreteProfile):
    for mild0, high0, mild1, high1 in product(
        (0.55, 0.575, 0.60, 0.625, 0.65),
        (0.50, 0.525, 0.55, 0.575, 0.60),
        (0.82, 0.83, 0.84, 0.85, 0.87),
        (0.77, 0.79, 0.81, 0.82, 0.83, 0.85),
    ):
        if high0 <= mild0 and high1 <= mild1:
            yield replace(
                profile,
                mild_stage0=mild0,
                high_stage0=high0,
                mild_stage1=mild1,
                high_stage1=high1,
            )


def threshold_candidates(profile: DiscreteProfile):
    for mild_entry, high_entry, mild_gap, high_gap in product(
        (55.0, 60.0, 65.0, 70.0, 75.0),
        (65.0, 70.0, 75.0, 80.0, 85.0, 90.0),
        (2.5, 5.0, 10.0, 15.0),
        (5.0, 10.0, 15.0, 20.0),
    ):
        mild_exit = mild_entry - mild_gap
        high_exit = high_entry - high_gap
        if high_entry > mild_entry and high_exit >= mild_exit:
            yield replace(
                profile,
                mild_entry=mild_entry,
                high_entry=high_entry,
                mild_exit=mild_exit,
                high_exit=high_exit,
            )


def continuous_candidates(discrete_winner: DiscreteProfile):
    stage0_cut = BASE_TARGETS[0] - discrete_winner.high_stage0
    stage1_cut = BASE_TARGETS[1] - discrete_winner.high_stage1
    cuts0 = sorted({max(0.05, stage0_cut + delta) for delta in (-0.05, -0.025, 0, 0.025, 0.05)})
    cuts1 = sorted({max(0.01, stage1_cut + delta) for delta in (-0.04, -0.02, 0, 0.02, 0.04)})
    for start, width, cut0, cut1 in product(
        (50.0, 55.0, 60.0, 65.0, 70.0),
        (10.0, 15.0, 20.0, 25.0),
        cuts0,
        cuts1,
    ):
        yield ContinuousProfile(start, start + width, cut0, cut1)


def _profile_from_row(row, profile_type):
    return profile_type(**{field: row[field] for field in profile_type.__dataclass_fields__})


def verify_discrete_with_dsl(profile: DiscreteProfile, start: str, end: str):
    """실제 전략 정의와 백테스트 엔진으로 계단형 후보를 교차 검증한다."""
    definition = deepcopy(
        load_strategy_definition(PROJECT_ROOT / "strategies" / "22_buy_3dip_composite_valuation.yaml")
    )
    parameters = definition["parameters"]
    parameters.update(
        {
            "valuation_mild_entry": profile.mild_entry,
            "valuation_high_entry": profile.high_entry,
            "valuation_mild_exit": profile.mild_exit,
            "valuation_high_exit": profile.high_exit,
        }
    )
    definition["target"][2]["weights"] = {"QQQ": profile.high_stage1, "BIL": 1 - profile.high_stage1}
    definition["target"][3]["weights"] = {"QQQ": profile.mild_stage1, "BIL": 1 - profile.mild_stage1}
    definition["target"][5]["weights"] = {"QQQ": profile.high_stage0, "BIL": 1 - profile.high_stage0}
    definition["target"][6]["weights"] = {"QQQ": profile.mild_stage0, "BIL": 1 - profile.mild_stage0}
    strategy = DeclarativeStrategy(definition)
    history, _, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        start_date=start,
        end_date=end,
    ).run_all()
    performance = Performance(history)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Calmar": performance.calmar_ratio(),
        "Rebalances": len(rebalances),
    }


def main():
    scores = load_scores()
    data = load_prices(end_date=END_DATE).join(scores[["all4"]], how="left")

    # 긴 격자 탐색을 반복하지 않고 이미 선정한 후보만 DSL로 재검증할 때 사용한다.
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-only":
        best_allocation = DiscreteProfile(
            mild_stage0=0.55,
            mild_stage1=0.87,
            high_stage0=0.50,
            high_stage1=0.85,
        )
        best_discrete = DiscreteProfile(
            mild_entry=65.0,
            high_entry=75.0,
            mild_exit=60.0,
            high_exit=60.0,
            mild_stage0=0.55,
            mild_stage1=0.87,
            high_stage0=0.50,
            high_stage1=0.85,
        )
        for label, profile in (
            ("22번", BASELINE_22),
            ("목표 비중 승자", best_allocation),
            ("추천 계단형", best_discrete),
        ):
            print(label, "전체", verify_discrete_with_dsl(profile, "2012-01-03", END_DATE))
            print(label, "개발", verify_discrete_with_dsl(profile, "2012-01-03", DEVELOPMENT_END))
            print(label, "최근", verify_discrete_with_dsl(profile, RECENT_START, END_DATE))
        return

    allocations, allocation_winner, baseline = search(
        data, "1_목표비중", allocation_candidates(BASELINE_22), BASELINE_22
    )
    best_allocation = (
        BASELINE_22
        if allocation_winner is None
        else _profile_from_row(allocation_winner, DiscreteProfile)
    )

    thresholds, threshold_winner, allocation_metrics = search(
        data, "2_임계값", threshold_candidates(best_allocation), best_allocation
    )
    best_discrete = (
        best_allocation
        if threshold_winner is None
        else _profile_from_row(threshold_winner, DiscreteProfile)
    )

    continuous, continuous_winner, discrete_metrics = search(
        data, "3_연속형", continuous_candidates(best_discrete), best_discrete
    )

    pd.concat([allocations, thresholds, continuous], ignore_index=True, sort=False).to_csv(
        OUTPUT, index=False
    )
    print("\n22번 최초 기준선", baseline)
    print("1단계 승자", best_allocation, allocation_metrics)
    print("2단계 승자", best_discrete, discrete_metrics)
    if continuous_winner is None:
        print("3단계에서 2단계 승자를 이긴 후보 없음")
    else:
        print("3단계 승자", _profile_from_row(continuous_winner, ContinuousProfile))
        print({key: continuous_winner[key] for key in ("CAGR", "MDD", "Calmar", "Rebalances", "Dev_CAGR", "Dev_MDD", "Recent_CAGR", "Recent_MDD")})
    print("\n실제 DSL 교차 검증")
    for label, profile in (("22번", BASELINE_22), ("추천 계단형", best_discrete)):
        print(label, "전체", verify_discrete_with_dsl(profile, "2012-01-03", END_DATE))
        print(label, "개발", verify_discrete_with_dsl(profile, "2012-01-03", DEVELOPMENT_END))
        print(label, "최근", verify_discrete_with_dsl(profile, RECENT_START, END_DATE))
    print("결과 파일", OUTPUT)


if __name__ == "__main__":
    main()
