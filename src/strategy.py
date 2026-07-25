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


class BASIC_BANG_DIV(BaseStrategy):
    """RSI regime strategy with monthly asymmetric-band rebalancing."""

    RISK_ASSET = "QQQ"
    SAFE_ASSET = "BND"
    GOLD_ASSET = "GLD"
    SPLIT_DAYS = 5

    def __init__(self):
        self.bull_weights = {
            self.RISK_ASSET: 0.60,
            self.SAFE_ASSET: 0.30,
            self.GOLD_ASSET: 0.10,
        }
        self.bear_weights = {
            self.RISK_ASSET: 0.30,
            self.SAFE_ASSET: 0.60,
            self.GOLD_ASSET: 0.10,
        }
        self.current_target = self.bull_weights.copy()
        self.state = "BULL"
        self.last_checked_month = None

        self.lower_threshold = -0.05
        self.upper_threshold = 0.05

    @staticmethod
    def _signal(rebalance, target, days, reason=None):
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        current_month = date.to_period("M")
        risk_rsi = market[self.RISK_ASSET]["RSI14"]

        # RSI 80 이상에서는 즉시 방어 비중으로 전환한다.
        if risk_rsi is not None and risk_rsi >= 80 and self.state != "BEAR":
            self.state = "BEAR"
            self.current_target = self.bear_weights.copy()
            self.last_checked_month = current_month
            return self._signal(
                True,
                self.current_target,
                1,
                f"RSI_OVERBOUGHT({risk_rsi:.1f})_TAKE_PROFIT",
            )

        # RSI 30 이하에서는 Portfolio의 내장 분할 리밸런싱으로 5일에 걸쳐 복귀한다.
        if risk_rsi is not None and risk_rsi <= 30 and self.state != "BULL":
            self.state = "BULL"
            self.current_target = self.bull_weights.copy()
            self.last_checked_month = current_month
            return self._signal(
                True,
                self.current_target,
                self.SPLIT_DAYS,
                f"RSI_OVERSOLD_SPLIT_{self.SPLIT_DAYS}_DAYS({risk_rsi:.1f})",
            )

        # 월 1회 현재 구조의 Portfolio.weights()로 목표 비중 이탈을 검사한다.
        if current_month != self.last_checked_month:
            self.last_checked_month = current_month
            prices = {ticker: market[ticker]["Close"] for ticker in self.current_target}
            weights = portfolio.weights(prices)
            for ticker, target_weight in self.current_target.items():
                weight_diff = weights.get(ticker, 0.0) - target_weight
                if weight_diff <= self.lower_threshold or weight_diff >= self.upper_threshold:
                    return self._signal(
                        True,
                        self.current_target,
                        1,
                        f"ASYMMETRIC_BAND_{ticker}(diff:{weight_diff:+.3f})",
                    )

        return self._signal(False, self.current_target, 1)
