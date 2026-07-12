"""
performance.py

백테스트 성과 분석 모듈 (v2.2)

계산:
- CAGR
- MDD
- Sharpe Ratio
- Volatility
"""

import numpy as np
import pandas as pd

from config import (
    RISK_FREE_RATE,
    TRADING_DAYS,
)


class Performance:

    def __init__( 
        self,
        history: pd.DataFrame
    ):
        self.history = history.copy()

        if "Portfolio" not in self.history.columns:
            raise ValueError("Portfolio column이 없습니다.")

        self.returns = self.history["Portfolio"].pct_change().dropna()
        

    # ==================================================
    # CAGR
    # ==================================================

    def cagr(self):

        """
        연평균 성장률
        """

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

        """
        최대 낙폭
        """

        portfolio = self.history["Portfolio"]

        peak = portfolio.cummax()
        drawdown = (portfolio - peak) / peak

        return drawdown.min()



    # ==================================================
    # 변동성
    # ==================================================

    def volatility(self):

        """
        연환산 변동성
        """
        return self.returns.std() * np.sqrt(TRADING_DAYS)


    # ==================================================
    # Sharpe Ratio
    # ==================================================

    def sharpe_ratio(self):

        """
        연환산 샤프지수
        """

        excess_return = self.returns.mean() * TRADING_DAYS - RISK_FREE_RATE
        annual_volatility = self.volatility()

        if annual_volatility == 0:
            return 0
    
        return excess_return / annual_volatility


    # ==================================================
    # 전체 결과
    # ==================================================

    def summary(self):

        """
        성과 요약
        """
        return {
            "CAGR": self.cagr(),
            "MDD": self.mdd(),
            "Volatility": self.volatility(),
            "Sharpe": self.sharpe_ratio(),
            "Start": self.history["Portfolio"].iloc[0],
            "End": self.history["Portfolio"].iloc[-1],
        }