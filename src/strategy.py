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
                "days": 3,
                "reason": reason
            }

        # -----------------------------
        # 아무것도 안 함
        # -----------------------------
        return {
            "rebalance": False,
            "target": self.current_target,
            "days": 3,
            "reason": None,
        }
    
class Strategy2(BaseStrategy):

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
        day = 5

        # -----------------------------
        # 1. RSI 과열
        # -----------------------------
        if rsi >= 75:
            target = {
                "QQQ": 0.6,
                "BND": 0.3,
                "GLD": 0.1,
            }
            day = 5
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
            day = 1
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
                "days": day,
                "reason": reason
            }

        # -----------------------------
        # 아무것도 안 함
        # -----------------------------
        return {
            "rebalance": False,
            "target": self.current_target,
            "days": day,
            "reason": None,
        }

from enum import Enum

class MarketState(Enum):
    BULL = "BULL"
    OVERHEATED = "OVERHEATED"
    BEAR = "BEAR"
    CRASH = "CRASH"

class TrendStrategy(BaseStrategy):

    def __init__(self):
        self.state = None
        self.target = None

    # ==================================================
    # 목표 비중
    # ==================================================
    def allocation(self, state):
        if state == MarketState.BULL:
            return {
                "QQQ": 0.6,
                "BND": 0.4,
                "GLD": 0
            }
        elif state == MarketState.OVERHEATED:
            return {
                "QQQ": 0.6,
                "BND": 0.4,
                "GLD": 0
            }
        elif state == MarketState.BEAR:
            return {
                "QQQ": 0.7,
                "BND": 0.3,
                "GLD": 0,
            }
        else:
            return {
                "QQQ": 0.7,
                "BND": 0.3,
                "GLD": 0,
            }

    # ==================================================
    # 시장 상태 판단
    # ==================================================
    def detect_state(self, market):
        qqq = market["QQQ"]
        close = qqq["Close"]
        ma200 = qqq["MA200"]
        rsi = qqq["RSI14"]
        macd = qqq["MACD"]
        signal = qqq["MACD_SIGNAL"]
        atr = qqq["ATR"]
        atr_ma = qqq["ATR60"]

        # --------------------------------------------------
        # 강한 하락장
        # --------------------------------------------------
        if (close < ma200 and macd < signal and atr > atr_ma * 1.5):
            return MarketState.CRASH

        # --------------------------------------------------
        # 약세장
        # --------------------------------------------------
        if (close < ma200 and macd < signal):
            return MarketState.BEAR

        # --------------------------------------------------
        # 과열
        # --------------------------------------------------
        if rsi >= 75:
            return MarketState.OVERHEATED

        # --------------------------------------------------
        # 기본
        # --------------------------------------------------
        return MarketState.BULL

    # ==================================================
    # 전략 평가
    # ==================================================
    def evaluate(self, date, market, portfolio):
        new_state = self.detect_state(
            market
        )

        # 최초 투자
        if self.state is None:
            self.state = new_state
            self.target = self.allocation(new_state)
            return {
                "rebalance": True,
                "target": self.target,
                "days": 1,
                "reason": "INITIAL",
            }

        # 상태 변화
        if new_state != self.state:
            old_state = self.state
            self.state = new_state
            self.target = self.allocation(new_state)
            return {
                "rebalance": True,
                "target": self.target,
                "days": 3,
                "reason": (f"{old_state.value}" f" -> " f"{new_state.value}"),
            }

        # 유지
        return {
            "rebalance": False,
            "target": self.target,
            "days": 5,
            "reason": None,
        }