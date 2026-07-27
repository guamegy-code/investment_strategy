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


class RETIREMENT_7030_BAND(BaseStrategy):
    """
    70/30 퇴직연금 하이브리드 리밸런싱 전략 (5% 밴드)
    - 위험자산(QQQ) 70%, 안전자산(BND 등) 30%
    - 매일 비중을 체크하여 목표 비중에서 ±5% 이탈 시 즉각 원복 (수익실현 or 저점매수)
    """

    RISK_ASSET = "QQQ"
    SAFE_ASSET = "BND"  # IRP용 원화 파킹 ETF인 경우 해당 티커(예: KODEX CD금리 등)로 변경

    def __init__(self):
        self.target_weights = {
            self.RISK_ASSET: 0.70,
            self.SAFE_ASSET: 0.30,
        }
        
        # 5% 임계치 (비중이 65% 이하로 떨어지거나 75% 이상으로 올라갈 때)
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
        # 1. 매일(daily) 현재 포트폴리오의 비중을 계산
        prices = {ticker: market[ticker]["Close"] for ticker in self.target_weights}
        weights = portfolio.weights(prices)

        # 2. 각 자산의 목표 비중 이탈 여부 확인
        # (두 종목이므로 RISK_ASSET 하나만 검사해도 되지만, 확장성을 위해 loop 유지)
        for ticker, target_weight in self.target_weights.items():
            current_weight = weights.get(ticker, 0.0)
            weight_diff = current_weight - target_weight

            # 3. 목표 비중에서 ±5% 이상 이탈했는지 검사
            if weight_diff <= self.lower_threshold or weight_diff >= self.upper_threshold:
                
                # 리포팅을 위한 액션 태깅
                # 비중이 기준치 이하로 떨어졌다면(-0.05 이하) 싸진 자산을 줍는 것(BUY_DIP)
                # 비중이 기준치 이상으로 올라갔다면(+0.05 이상) 비싸진 자산을 파는 것(TAKE_PROFIT)
                action = "BUY_DIP" if weight_diff <= self.lower_threshold else "TAKE_PROFIT"
                
                return self._signal(
                    True,
                    self.target_weights,
                    5,  # 즉시 1일 만에 리밸런싱 (필요시 기존 코드처럼 분할 매수로 변경 가능)
                    f"5%_BAND_BREAK_{action}_{ticker}(diff:{weight_diff:+.3f})"
                )

        # 4. 임계치 이내라면 기존 홀딩 유지
        return self._signal(False, self.target_weights, 1)


class ASYMMETRIC_TREND_BAND(BaseStrategy):
    """
    비대칭 밴드 + 이동평균선(SMA) 추세 필터 결합 전략
    - 상승장(Uptrend): 익절은 무한대기(Let profits run), 추매는 예민하게(-5%)
    - 하락장(Downtrend): 익절은 타이트하게(+5%), 추매는 신중하게(-10%)
    """

    RISK_ASSET = "QLD"
    SAFE_ASSET = "QQQ"
    MID_ASSET = "GLD"

    def __init__(self):
        self.target_weights = {
            self.RISK_ASSET: 0.40,
            self.MID_ASSET: 0.30,
            self.SAFE_ASSET: 0.30,
        }
        

    def _signal(self, rebalance, target, days, reason=None):
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        # 1. 현재 가격 및 비중 계산 (데이터 누락 예외 처리 방어 로직 추가)
        prices = {}
        for ticker in self.target_weights:
            # market 데이터에 해당 티커가 없거나 "Close" 키가 없는 경우 (상장 전 등)
            if ticker not in market or "Close" not in market[ticker]:
                return self._signal(False, self.target_weights, 1)
            
            price = market[ticker]["Close"]
            
            # 결측치(None 또는 NaN)인 경우 패스 (price != price는 NaN을 판별하는 파이썬 표준 팁)
            if price is None or price != price:
                return self._signal(False, self.target_weights, 1)
                
            prices[ticker] = price

        weights = portfolio.weights(prices)
        
        current_risk_weight = weights.get(self.RISK_ASSET, 0.0)
        weight_diff = current_risk_weight - self.target_weights[self.RISK_ASSET]

        # 2. 추세 판단 (55~200일 이동평균선 사용)
        current_price = market[self.RISK_ASSET]["Close"]
        base = market[self.RISK_ASSET].get("EMA55", current_price) # 없으면 현재가로 대체(항상 상승장 취급)
        
        is_uptrend = current_price >= base

        # 3. 시장 상태에 따른 비대칭 임계치(Threshold) 설정
        if is_uptrend:
            # 상승장: 수익을 자르지 않음(상단 임계치 무한대), 하락 시 5% 빠지면 줍줍
            upper_threshold = float('inf') 
            lower_threshold = -0.015
            trend_status = "UPTREND"
        else:
            # 하락장: 데드캣 바운스 시 즉시 현금화(+5%), 하락 시 천천히 줍줍(-10%)
            upper_threshold = 0.015
            lower_threshold = -0.06
            trend_status = "DOWNTREND"

        # 4. 리밸런싱 시그널 판단
        if weight_diff <= lower_threshold:
            # 하단 밴드 이탈 -> 싸진 위험자산 매수
            
            print(f"Weight diff: {weight_diff:.3f}, Lower threshold: {lower_threshold:.3f}")

            return self._signal(
                True,
                self.target_weights,
                1,
                f"{trend_status}_BUY_DIP_{self.RISK_ASSET}(diff:{weight_diff:+.3f})"
            )
            
        elif weight_diff >= upper_threshold:
            # 상단 밴드 이탈 -> 비싸진 위험자산 매도 (상승장에서는 절대 발동하지 않음)

            print(f"Weight diff: {weight_diff:.3f}, Upper threshold: {upper_threshold:.3f}")

            return self._signal(
                True,
                self.target_weights,
                1,
                f"{trend_status}_TAKE_PROFIT_{self.RISK_ASSET}(diff:{weight_diff:+.3f})"
            )

        # 5. 밴드 내에 있으면 그대로 홀딩 (존버 모드)
        return self._signal(False, self.target_weights, 1)

