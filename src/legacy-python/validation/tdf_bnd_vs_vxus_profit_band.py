"""Compare TDF/BND candidates with both VXUS profit-band strategies."""

from pathlib import Path

import pandas as pd

from backtest import Backtest
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.conditional_bnd_safe_sleeve import (
    baseline_definition,
    hysteresis_definition,
)
from validation.tdf_bnd_improvement_candidates import cap_caution_profit_band


ROOT = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT / "results"
SOURCES = {
    "RetirementAllocationProfitBandVXUS": (
        ROOT / "strategies" / "04_profit_band_vxus.yaml"
    ),
    "RetirementAllocationProfitBandVXUS2": (
        ROOT / "strategies" / "05_profit_band_vxus_v2.yaml"
    ),
    "Retirement 70/30 Band": (
        ROOT / "strategies" / "07_band_7030_bnd.yaml"
    ),
}


def run(definition):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy, tickers=strategy.required_tickers
    ).run_all()
    return history, len(trades), len(rebalances)


def period_metrics(history, start, end):
    sample = history.loc[start:end]
    performance = Performance(sample)
    return {
        "CumulativeReturn": sample["Portfolio"].iloc[-1]
        / sample["Portfolio"].iloc[0]
        - 1,
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Volatility": performance.volatility(),
        "Sharpe": performance.sharpe_ratio(),
        "Sortino": performance.sortino_ratio(),
        "Calmar": performance.calmar_ratio(),
    }


def drawdown_period(history, start, end):
    portfolio = history.loc[start:end, "Portfolio"]
    drawdown = portfolio / portfolio.cummax() - 1
    trough = drawdown.idxmin()
    peak = portfolio.loc[:trough].idxmax()
    return peak, trough


def main():
    definitions = {
        name: load_strategy_definition(source)
        for name, source in SOURCES.items()
    }
    definitions.update({
        "TDF40/BIL60 baseline": baseline_definition(),
        "TDF40/BIL60 + BND hysteresis": hysteresis_definition(),
        "TDF/BND + CAUTION cap 75%": cap_caution_profit_band(
            hysteresis_definition(), 0.75
        ),
        "TDF/BND + CAUTION cap 76.25%": cap_caution_profit_band(
            hysteresis_definition(), 0.7625
        ),
    })
    runs = {name: run(definition) for name, definition in definitions.items()}
    common_start = max(history.index.min() for history, _, _ in runs.values())
    common_end = min(history.index.max() for history, _, _ in runs.values())

    rows = []
    rolling_rows = []
    for name, (history, trades, rebalances) in runs.items():
        full = period_metrics(history, common_start, common_end)
        later = period_metrics(history, "2021-01-01", common_end)
        sample = history.loc[common_start:common_end]
        peak, trough = drawdown_period(history, common_start, common_end)
        rows.append({
            "Candidate": name,
            "Start": common_start.strftime("%Y-%m-%d"),
            "End": common_end.strftime("%Y-%m-%d"),
            **full,
            "LaterCAGR": later["CAGR"],
            "LaterMDD": later["MDD"],
            "MaxDrawdownPeak": peak.strftime("%Y-%m-%d"),
            "MaxDrawdownTrough": trough.strftime("%Y-%m-%d"),
            "TransactionCosts": (
                sample["TransactionCosts"].iloc[-1]
                - sample["TransactionCosts"].iloc[0]
            ),
            "Trades": trades,
            "Rebalances": rebalances,
        })
        for year in range(2012, 2024):
            window_start = pd.Timestamp(year, 1, 1)
            window_end = pd.Timestamp(year + 2, 12, 31)
            rolling_rows.append({
                "Candidate": name,
                "Window": f"{year}-{year + 2}",
                **period_metrics(history, window_start, window_end),
            })

    summary = pd.DataFrame(rows).sort_values(
        ["Calmar", "CAGR"], ascending=False
    )
    rolling = pd.DataFrame(rolling_rows)
    RESULT_DIR.mkdir(exist_ok=True)
    summary.to_csv(RESULT_DIR / "tdf_bnd_vs_vxus_profit_band.csv", index=False)
    rolling.to_csv(
        RESULT_DIR / "tdf_bnd_vs_vxus_profit_band_rolling.csv", index=False
    )

    display = summary.copy()
    for column in (
        "CumulativeReturn", "CAGR", "MDD", "Volatility", "LaterCAGR",
        "LaterMDD", "TransactionCosts",
    ):
        display[column] = display[column].map(lambda value: f"{value:.2%}")
    for column in ("Sharpe", "Sortino", "Calmar"):
        display[column] = display[column].map(lambda value: f"{value:.3f}")
    print(display.to_string(index=False))


if __name__ == "__main__":
    main()
