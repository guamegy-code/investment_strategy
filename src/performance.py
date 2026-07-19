"""
performance.py

백테스트 성과 분석
"""

import numpy as np
import pandas as pd

from config import (
    RISK_FREE_RATE,
    TRADING_DAYS,
)


class Performance:

    def __init__(self, history):
        self.history = history.copy()
        self.returns = self.history["Portfolio"].pct_change().dropna()
    

    # ==================================================
    # CAGR
    # ==================================================
    def cagr(self):
        start_value = self.history["Portfolio"].iloc[0]
        end_value = self.history["Portfolio"].iloc[-1]
        years = (self.history.index[-1] - self.history.index[0]).days / 365.25
        if years == 0:
            return 0
        return ((end_value / start_value) ** (1 / years) - 1)


    # ==================================================
    # MDD
    # ==================================================
    def mdd(self):
        portfolio = self.history["Portfolio"]
        peak = portfolio.cummax()
        drawdown = (portfolio - peak) / peak
        return drawdown.min()


    # ==================================================
    # 변동성
    # ==================================================
    def volatility(self):
        return self.returns.std() * np.sqrt(TRADING_DAYS)


    # ==================================================
    # Sharpe Ratio
    # ==================================================
    def sharpe_ratio(self):
        excess_return = self.returns.mean() * TRADING_DAYS - RISK_FREE_RATE
        annual_volatility = self.volatility()
        if annual_volatility == 0:
            return 0
        return excess_return / annual_volatility


    # ==================================================
    # 결과 요약
    # ==================================================
    def summary(self):
        return {
            "CAGR": self.cagr(),
            "MDD": self.mdd(),
            "Volatility": self.volatility(),
            "Sharpe": self.sharpe_ratio(),
            "Start": self.history["Portfolio"].iloc[0],
            "End": self.history["Portfolio"].iloc[-1],
        }