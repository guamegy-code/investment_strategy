"""
runner.py

여러 전략을 실행하고 성과를 비교
"""

import pandas as pd
from backtest import Backtest
from performance import Performance


class Runner:

    def __init__(self):
        self.strategies = []
        self.results = []

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
            backtest = Backtest(strategy)
            history, trades, rebalances = backtest.run_all()
            performance = Performance(history)
            summary = performance.summary()
            summary["Strategy"] = strategy.__class__.__name__

            self.results.append({
                "strategy": strategy,
                "history": history,
                "trades": trades,
                "rebalances": rebalances,
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
            "Start",
            "End",
        ]
        return df[columns]
