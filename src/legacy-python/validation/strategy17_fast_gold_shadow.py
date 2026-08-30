"""Screen fast-entry exceptions for strategy 17 without changing production.

Portfolio results are valued in KRW while QQQ/GLD signals use their local
(USD) price series.  The production slow entry remains unchanged; candidates
only add a separately confirmed fast-entry rule.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtest import Backtest
from config import COMMISSION, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "strategies" / "17_band_7030_tdf_state_bil_gld_overlay.yaml"
START_DATE = "2013-01-04"


@dataclass(frozen=True)
class Profile:
    name: str
    qqq_weakness: str | None = None
    confirmation_days: int = 0


PROFILES = (
    Profile("BASELINE"),
    Profile("CLOSE_BELOW_EMA20_C3", "QQQ.close < QQQ.ema20", 3),
    Profile("CLOSE_BELOW_EMA20_C5", "QQQ.close < QQQ.ema20", 5),
    Profile(
        "EMA20_AND_NEGATIVE_ROC20_C3",
        "QQQ.close < QQQ.ema20 and QQQ.roc20 < 0",
        3,
    ),
    Profile(
        "EMA20_AND_NEGATIVE_ROC20_C5",
        "QQQ.close < QQQ.ema20 and QQQ.roc20 < 0",
        5,
    ),
    Profile("RISK_OFF_SCORE_3_C3", "variables.risk_off_score >= 3", 3),
    Profile("RISK_OFF_SCORE_3_C5", "variables.risk_off_score >= 3", 5),
)
DETAIL_PROFILE = PROFILES[2]


def candidate_definition(profile: Profile):
    definition = deepcopy(load_strategy_definition(SOURCE))
    if profile.qqq_weakness is None:
        return definition

    definition["variables"]["gld_fast_signal"] = (
        f"({profile.qqq_weakness}) "
        "and GLD.close > GLD.ema55 "
        "and GLD.roc20 > QQQ.roc20 "
        "and GLD.roc60 > QQQ.roc60"
    )
    definition["state"]["gold_overlay"]["rules"].insert(
        0,
        {
            "when": (
                "state.gold_overlay == 'OFF' "
                "and state.market_mode in ['BULL', 'CAUTION', 'RECOVERY'] "
                "and not variables.structural_bear "
                "and variables.gld_fast_signal"
            ),
            "set": "ON",
            "confirm": profile.confirmation_days,
        },
    )
    return definition


def krw_valued_usd_signal_strategy(definition):
    strategy = DeclarativeStrategy(definition)
    strategy.foreign_asset_tickers = strategy.holding_tickers
    strategy.valuation_currency = "KRW"
    strategy.valuation_fx_ticker = "KRW=X"
    strategy.valuation_signal_currency = "LOCAL"
    strategy.required_tickers = tuple(
        dict.fromkeys((*strategy.required_tickers, "KRW=X"))
    )
    return strategy


def run(profile, *, cost_multiple=1.0, signal_delay_days=0):
    strategy = krw_valued_usd_signal_strategy(candidate_definition(profile))
    history, trades, rebalances = Backtest(
        strategy,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiple,
        slippage=SLIPPAGE * cost_multiple,
        signal_delay_days=signal_delay_days,
        start_date=START_DATE,
    ).run_all()
    return history, trades, rebalances


def metrics(history):
    performance = Performance(history)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Sharpe": performance.sharpe_ratio(),
    }


def rolling_comparison(baseline, candidate, years):
    common = baseline.index.intersection(candidate.index)
    first_end = common.min() + pd.DateOffset(years=years)
    endpoints = (
        pd.Series(common[common >= first_end], index=common[common >= first_end])
        .groupby(common[common >= first_end].to_period("M"))
        .last()
    )
    rows = []
    for end in endpoints:
        start = end - pd.DateOffset(years=years)
        base_window = baseline.loc[(baseline.index >= start) & (baseline.index <= end)]
        candidate_window = candidate.loc[
            (candidate.index >= start) & (candidate.index <= end)
        ]
        base_metrics = metrics(base_window)
        candidate_metrics = metrics(candidate_window)
        rows.append({
            "CAGRGap": candidate_metrics["CAGR"] - base_metrics["CAGR"],
            "MDDImprovement": candidate_metrics["MDD"] - base_metrics["MDD"],
        })
    report = pd.DataFrame(rows)
    return {
        "Windows": len(report),
        "MeanCAGRGap": report["CAGRGap"].mean(),
        "WorstCAGRGap": report["CAGRGap"].min(),
        "CAGRWinShare": (report["CAGRGap"] > 0).mean(),
        "MeanMDDImprovement": report["MDDImprovement"].mean(),
        "MDDNoWorseShare": (report["MDDImprovement"] >= 0).mean(),
        "BothWinShare": (
            (report["CAGRGap"] > 0) & (report["MDDImprovement"] >= 0)
        ).mean(),
    }


def main():
    baseline = None
    base_history = None
    detail_history = None
    print("Profile,CAGR,MDD,Sharpe,Trades,Rebalances,CAGRGap,MDDImprovement")
    for profile in PROFILES:
        history, trades, rebalances = run(profile)
        result = metrics(history)
        if baseline is None:
            baseline = result
            base_history = history
        if profile == DETAIL_PROFILE:
            detail_history = history
        print(
            f"{profile.name},{result['CAGR']:.8f},{result['MDD']:.8f},"
            f"{result['Sharpe']:.6f},{len(trades)},{len(rebalances)},"
            f"{result['CAGR'] - baseline['CAGR']:.8f},"
            f"{result['MDD'] - baseline['MDD']:.8f}"
        )

    print("\nRollingYears,Windows,MeanCAGRGap,WorstCAGRGap,CAGRWinShare,"
          "MeanMDDImprovement,MDDNoWorseShare,BothWinShare")
    for years in (3, 5):
        report = rolling_comparison(base_history, detail_history, years)
        print(
            f"{years},{report['Windows']},{report['MeanCAGRGap']:.8f},"
            f"{report['WorstCAGRGap']:.8f},{report['CAGRWinShare']:.6f},"
            f"{report['MeanMDDImprovement']:.8f},"
            f"{report['MDDNoWorseShare']:.6f},{report['BothWinShare']:.6f}"
        )

    print("\nStress,CAGRGap,MDDImprovement,SharpeGap,TradeDelta,RebalanceDelta")
    for label, cost_multiple, delay in (
        ("COST_5X", 5.0, 0),
        ("DELAY_2D", 1.0, 2),
    ):
        base_run = run(
            PROFILES[0], cost_multiple=cost_multiple, signal_delay_days=delay
        )
        detail_run = run(
            DETAIL_PROFILE,
            cost_multiple=cost_multiple,
            signal_delay_days=delay,
        )
        base_result = metrics(base_run[0])
        detail_result = metrics(detail_run[0])
        print(
            f"{label},{detail_result['CAGR'] - base_result['CAGR']:.8f},"
            f"{detail_result['MDD'] - base_result['MDD']:.8f},"
            f"{detail_result['Sharpe'] - base_result['Sharpe']:.8f},"
            f"{len(detail_run[1]) - len(base_run[1])},"
            f"{len(detail_run[2]) - len(base_run[2])}"
        )


if __name__ == "__main__":
    main()
