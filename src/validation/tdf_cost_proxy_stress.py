"""Stress selected TDF allocation candidates for execution costs and proxy drag."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from backtest import Backtest
from config import COMMISSION, DATA_DIR, SLIPPAGE
from performance import Performance
from strategy_dsl import DeclarativeStrategy, load_strategy_definition
from validation.tdf_state_allocation_grid import allocation_definition


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies" / "06_profit_band_tdf2050.yaml"
RESULT_DIR = ROOT / "results"
TICKERS = ("QQQ", "TDF2050_PROXY", "BND", "BIL")


def definitions():
    return {
        "Production allocation": load_strategy_definition(SOURCE),
        "Strong QQQ 65% / Recovery QQQ 55%": allocation_definition(
            0.65, 0.55, 0.40
        ),
        "Strong QQQ 60% / Recovery QQQ 55%": allocation_definition(
            0.60, 0.55, 0.40
        ),
    }


def run(definition, *, data_dir=DATA_DIR, cost_multiplier=1.0):
    strategy = DeclarativeStrategy(definition)
    history, trades, rebalances = Backtest(
        strategy,
        data_dir=data_dir,
        tickers=strategy.required_tickers,
        commission=COMMISSION * cost_multiplier,
        slippage=SLIPPAGE * cost_multiplier,
    ).run_all()
    return history, len(trades), len(rebalances)


def metrics(history):
    performance = Performance(history)
    return {
        "CAGR": performance.cagr(),
        "MDD": performance.mdd(),
        "Sharpe": performance.sharpe_ratio(),
        "Calmar": performance.calmar_ratio(),
    }


def write_proxy_stress_data(directory, annual_drag):
    destination = Path(directory)
    for ticker in TICKERS:
        frame = pd.read_csv(DATA_DIR / f"{ticker}.csv")
        if ticker == "TDF2050_PROXY":
            factor = (1 - annual_drag) ** (pd.RangeIndex(len(frame)) / 252)
            for column in ("Close", "High", "Low", "Open"):
                frame[column] = frame[column] * factor
            frame = frame[["Date", "Close", "High", "Low", "Open", "Volume"]]
        frame.to_csv(destination / f"{ticker}.csv", index=False)


def main():
    cost_rows = []
    proxy_rows = []
    for name, definition in definitions().items():
        for multiplier in (1.0, 2.0, 3.0):
            history, trades, rebalances = run(
                definition, cost_multiplier=multiplier
            )
            cost_rows.append({
                "Candidate": name,
                "CostMultiplier": multiplier,
                **metrics(history),
                "TransactionCosts": history["TransactionCosts"].iloc[-1],
                "Trades": trades,
                "Rebalances": rebalances,
            })
        for drag in (0.0, 0.005, 0.010):
            with TemporaryDirectory() as directory:
                write_proxy_stress_data(directory, drag)
                history, trades, rebalances = run(
                    definition, data_dir=Path(directory)
                )
            proxy_rows.append({
                "Candidate": name,
                "AnnualTDFProxyDrag": drag,
                **metrics(history),
                "TransactionCosts": history["TransactionCosts"].iloc[-1],
                "Trades": trades,
                "Rebalances": rebalances,
            })

    costs = pd.DataFrame(cost_rows)
    proxy = pd.DataFrame(proxy_rows)
    RESULT_DIR.mkdir(exist_ok=True)
    costs.to_csv(RESULT_DIR / "tdf_cost_stress.csv", index=False)
    proxy.to_csv(RESULT_DIR / "tdf_proxy_drag_stress.csv", index=False)
    for title, frame in (("Cost stress", costs), ("TDF proxy drag", proxy)):
        display = frame.copy()
        for column in ("CAGR", "MDD", "TransactionCosts"):
            display[column] = display[column].map(lambda value: f"{value:.2%}")
        if "AnnualTDFProxyDrag" in display:
            display["AnnualTDFProxyDrag"] = display["AnnualTDFProxyDrag"].map(
                lambda value: f"{value:.2%}"
            )
        for column in ("Sharpe", "Calmar"):
            display[column] = display[column].map(lambda value: f"{value:.3f}")
        print(title)
        print(display.to_string(index=False))


if __name__ == "__main__":
    main()
