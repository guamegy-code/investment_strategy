"""
strategy.py

전략 기본 클래스
"""

from abc import ABC, abstractmethod


class BaseStrategy(ABC):

    @abstractmethod
    def evaluate(self, date, market, portfolio):
        """
        Returns
        -------
        {
            "rebalance": bool,
            "target": dict,
            "reason": str | None
        }
        """
        pass


class Strategy1(BaseStrategy):

    def __init__(self):
        self.current_target = {
            "QQQ": 0.6,
            "BND": 0.3,
            "GLD": 0.1,
        }
        self.last_rebalance_month = None

    def evaluate(self, date, market, portfolio):
        qqq = market["QQQ"]

        close = qqq["Close"]
        ma200 = qqq["MA200"]
        rsi = qqq["RSI14"]

        target = {
            "QQQ": 0.6,
            "BND": 0.3,
            "GLD": 0.1,
        }

        reason = None

        # -----------------------------
        # 1. RSI 과열
        # -----------------------------
        if rsi >= 75:
            target = {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }
            reason = "RSI_OVERBOUGHT"
        
        # -----------------------------
        # 2. MA200 하향 이탈
        # -----------------------------
        elif close < ma200:
            target = {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }
            reason = "MA200_BREAKDOWN"

        # -----------------------------
        # 목표 비중 변경 여부
        # -----------------------------
        if target != self.current_target:
            self.current_target = target
            self.last_rebalance_month = date.to_period("M")
            return {
                "rebalance": True,
                "target": target,
                "reason": reason,
            }

        # -----------------------------
        # 월말 리밸런싱
        # -----------------------------
        current_month = date.to_period("M")
        if current_month != self.last_rebalance_month:
            self.last_rebalance_month = current_month
            return {
                "rebalance": True,
                "target": self.current_target,
                "reason": "MONTHLY",
            }

        # -----------------------------
        # 아무것도 안 함
        # -----------------------------
        return {
            "rebalance": False,
            "target": self.current_target,
            "reason": None,
        }
    


class Strategy2(BaseStrategy):

    def __init__(self):
        self.current_target = {
            "QQQ": 0.7,
            "BND": 0.3,
            "GLD": 0        
        }
        self.last_rebalance_month = None

    def evaluate(self, date, market, portfolio):
        qqq = market["QQQ"]

        close = qqq["Close"]
        ma55 = qqq["MA55"]
        rsi = qqq["RSI14"]

        target = {
            "QQQ": 0.7,
            "BND": 0.3,
            "GLD": 0        
        }

        reason = None

        # -----------------------------
        # 1. RSI 과열
        # -----------------------------
        if rsi >= 75:
            target = {
                "QQQ": 0.6,
                "BND": 0.4,
                "GLD": 0        
            }
            reason = "RSI_OVERBOUGHT"
        
        # -----------------------------
        # 2. MA55 하향 이탈
        # -----------------------------
        elif close < ma55:
            target = {
                "QQQ": 0.7,
                "BND": 0.3,
                "GLD": 0
            }
            reason = "MA55_BREAKDOWN"

        # -----------------------------
        # 목표 비중 변경 여부
        # -----------------------------
        if target != self.current_target:
            self.current_target = target
            self.last_rebalance_month = date.to_period("M")
            return {
                "rebalance": True,
                "target": target,
                "reason": reason,
            }

        # -----------------------------
        # 월말 리밸런싱
        # -----------------------------
        current_month = date.to_period("M")
        if current_month != self.last_rebalance_month:
            self.last_rebalance_month = current_month
            return {
                "rebalance": True,
                "target": self.current_target,
                "reason": "MONTHLY",
            }

        # -----------------------------
        # 아무것도 안 함
        # -----------------------------
        return {
            "rebalance": False,
            "target": self.current_target,
            "reason": None,
        }