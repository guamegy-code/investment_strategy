"""
portfolio.py

포트폴리오 관리 모듈 (v2.2)

역할:
- 보유 ETF 수량 관리
- 현금 관리
- 평가금액 계산
- 목표 비중 리밸런싱
- 백테스트 기록 저장
"""

from dataclasses import dataclass, field
from typing import Dict, List

from config import (
    COMMISSION,
    SLIPPAGE,
)


@dataclass
class Portfolio:

    initial_cash: float
    tickers: List[str]
    cash: float = field(init=False)
    holdings: Dict[str, float] = field(init=False)
    history: list = field(default_factory=list)
    trades: list = field(default_factory=list)


    def __post_init__(self):

        # 초기 현금
        self.cash = self.initial_cash

        # ETF 보유 수량
        self.holdings = { ticker: 0.0 for ticker in self.tickers }


    # ==================================================
    # 현재 총 자산
    # ==================================================

    def total_value(
        self,
        prices: Dict[str, float]
    ) -> float:
        """
        현재 포트폴리오 평가금액
        """

        value = self.cash

        for ticker, shares in self.holdings.items():
            value += shares * prices[ticker]

        return value


    # ==================================================
    # 현재 비중
    # ==================================================

    def current_weights(
        self,
        prices: Dict[str, float]
    ) -> Dict[str, float]:
        """
        현재 ETF별 비중 계산
        """

        total = self.total_value(prices)

        if total == 0:
            return { ticker: 0 for ticker in self.tickers }

        weights = {}

        for ticker in self.tickers:
            value = self.holdings[ticker] * prices[ticker]
            weights[ticker] = value / total

        return weights


    # ==================================================
    # 리밸런싱
    # ==================================================

    def rebalance(
        self,
        prices: Dict[str, float],
        target_weights: Dict[str, float],
        date=None
    ):
        """
        목표 비중으로 리밸런싱
        현재 버전은 소수점 ETF 매매 허용
        """

        total_before = self.total_value(prices)

        new_holdings = {}

        total_invested = 0

        for ticker in self.tickers:
            target_weight = target_weights.get(ticker, 0)
            target_amount = total_before * target_weight

            price = prices[ticker]
            shares = target_amount / price
            new_holdings[ticker] = shares
            total_invested += shares * price


        # 거래비용 반영

        trading_cost = total_before * (COMMISSION + SLIPPAGE)
        self.holdings = new_holdings
        self.cash = total_before - total_invested - trading_cost

        self.trades.append(
            {
                "Date": date,
                "Type": "Rebalance",
                "Cost": trading_cost,
                "Value": total_before,
            }
        )


    # ==================================================
    # 일별 기록
    # ==================================================

    def record(
        self,
        date,
        prices: Dict[str, float]
    ):
        """
        날짜별 포트폴리오 저장
        """

        row = {
            "Date": date,
            "Portfolio": self.total_value(prices),
            "Cash": self.cash,
        }

        for ticker in self.tickers:
            row[ticker] = self.holdings[ticker] * prices[ticker]
            row[f"{ticker}_holdings"] = self.holdings[ticker]

        self.history.append(row)


    # ==================================================
    # 결과 반환
    # ==================================================

    def get_history(self):
        return self.history


    def get_trades(self):
        return self.trades