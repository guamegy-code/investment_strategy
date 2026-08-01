"""
runner.py

여러 전략을 실행하고 성과를 비교
"""

import pandas as pd
from backtest import Backtest
from config import DATA_DIR
from performance import Performance


class Runner:

    def __init__(self, data_dir=DATA_DIR, tickers=None, backtest_options=None):
        self.strategies = []
        self.results = []
        self.data_dir = data_dir
        self.tickers = tickers
        self.backtest_options = backtest_options or {}

    # ==================================================
    # 전략 등록
    # ==================================================
    def add_strategy(self, strategy):
        self.strategies.append(strategy)

    # ==================================================
    # 전체 실행
    # ==================================================

    def run(self):
        self.results = []

        for strategy in self.strategies:
            backtest = Backtest(
                strategy,
                data_dir=self.data_dir,
                tickers=self.tickers,
                **self.backtest_options,
            )
            history, trades, rebalances = backtest.run_all()
            performance = Performance(history)
            summary = performance.summary()
            summary["Strategy"] = strategy.__class__.__name__

            self.results.append({
                "strategy": strategy,
                "history": history,
                "trades": trades,
                "rebalances": rebalances,
                "market_data": backtest.data,
                "summary": summary,
            })

        return self.results

    # ==================================================
    # 결과 비교
    # ==================================================

    def summary(self):
        rows = []

        for result in self.results:
            rows.append(result["summary"])

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        columns = [
            "Strategy",
            "CAGR",
            "MDD",
            "Volatility",
            "Sharpe",
            "Sortino",
            "Calmar",
            "TransactionCosts",
            "Start",
            "End",
        ]
        return df[columns]
