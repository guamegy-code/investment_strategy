"""Production strategy and the static benchmarks used to evaluate it."""

from abc import ABC, abstractmethod
from enum import Enum


class BaseStrategy(ABC):
    @abstractmethod
    def evaluate(self, date, market, portfolio):
        """Return a rebalance decision, target weights, and execution days."""


class AllocationState(Enum):
    BULL = "BULL"
    CAUTION = "CAUTION"
    BEAR = "BEAR"
    RECOVERY = "RECOVERY"


class DynamicRiskAllocationStrategy(BaseStrategy):
    """Final QQQ regime strategy with adaptive BND/BIL allocation."""

    STATE_WEIGHTS = {
        AllocationState.BULL: (0.70, 0.10),
        AllocationState.CAUTION: (0.70, 0.15),
        AllocationState.BEAR: (0.00, 0.20),
        AllocationState.RECOVERY: (0.50, 0.15),
    }

    BEAR_ENTRY_SCORE = 5
    BEAR_CONFIRMATION_DAYS = 10
    STRUCTURAL_DRAWDOWN = -0.08
    CAUTION_ENTER_SCORE = 5
    CAUTION_CONFIRMATION_DAYS = 3
    BEAR_RECOVERY_SCORE = 3
    RECOVERY_CONFIRMATION_DAYS = 2
    BULL_CONFIRMATION_DAYS = 3
    SAFE_MOMENTUM_PERIOD = 40
    SAFE_SWITCH_BUFFER = 0.25

    def __init__(self):
        self.state = None
        self.target = None
        self.safe_asset = None
        self.last_rebalance_month = None
        self.last_safe_selection_month = None
        self.risk_off_score = 0
        self.recovery_score = 0
        self._candidate = None
        self._candidate_days = 0

    @staticmethod
    def _valid(*values):
        return all(value is not None and value == value for value in values)

    def _scores(self, qqq):
        # 단기 상태 점수는 6개 조건으로 구성한다. 하락 조건은
        # risk_off_score, 반대의 상승 조건은 recovery_score에 합산된다.
        close = qqq["Close"]
        ema20 = qqq["EMA20"]
        ema55 = qqq["EMA55"]
        ema200 = qqq["EMA200"]
        roc5 = qqq["ROC5"]
        roc20 = qqq["ROC20"]
        slope5 = qqq["EMA20_SLOPE5"]
        if not self._valid(close, ema20, ema55, ema200, roc5, roc20, slope5):
            return 0, 0

        risk_off = sum((
            close < ema20,
            close < ema55,
            ema20 < ema55,
            roc5 < 0,
            roc20 < 0,
            slope5 < 0,
        ))
        recovery = sum((
            close > ema20,
            close > ema55,
            ema20 > ema55,
            roc5 > 0,
            roc20 > 0,
            slope5 > 0,
        ))
        return int(risk_off), int(recovery)

    def _is_structural_bear(self, qqq):
        # 단기 약세만으로 BEAR에 진입하지 않는다. 이동평균 역배열,
        # 60일 하락, 장기 EMA 하락, 최근 고점 대비 -8% 이하를 모두 요구한다.
        close = qqq["Close"]
        ema20 = qqq["EMA20"]
        ema55 = qqq["EMA55"]
        ema200 = qqq["EMA200"]
        roc60 = qqq.get("ROC60")
        slope200 = qqq.get("EMA200_SLOPE20")
        drawdown120 = qqq.get("DRAWDOWN120")
        if not self._valid(
            close, ema20, ema55, ema200, roc60, slope200, drawdown120
        ):
            return False
        return (
            self.risk_off_score >= self.BEAR_ENTRY_SCORE
            and close < ema20 < ema55 < ema200
            and roc60 < 0
            and slope200 < 0
            and drawdown120 <= self.STRUCTURAL_DRAWDOWN
        )

    def _bull_reentry_allowed(self, qqq):
        # BEAR 이후의 짧은 반등을 걸러내기 위한 중기 추세 복귀 조건이다.
        close = qqq.get("Close")
        ema55 = qqq.get("EMA55")
        roc60 = qqq.get("ROC60")
        return self._valid(close, ema55, roc60) and close > ema55 and roc60 > 0

    def _desired_state(self, qqq):
        self.risk_off_score, self.recovery_score = self._scores(qqq)
        structural_bear = self._is_structural_bear(qqq)

        # 최초 상태: 구조적 하락이면 BEAR, 단기 약세 점수가 높으면 CAUTION,
        # 어느 조건에도 해당하지 않으면 BULL로 시작한다.
        if self.state is None:
            if structural_bear:
                return AllocationState.BEAR
            if self.risk_off_score >= self.CAUTION_ENTER_SCORE:
                return AllocationState.CAUTION
            return AllocationState.BULL

        # BULL: 구조적 하락은 BEAR 후보, 단기 약세 5점 이상은 CAUTION 후보.
        if self.state == AllocationState.BULL:
            if structural_bear:
                return AllocationState.BEAR
            if self.risk_off_score >= self.CAUTION_ENTER_SCORE:
                return AllocationState.CAUTION
            return self.state

        # CAUTION: 구조적 하락이 확인되면 BEAR, 상승 점수 4점 이상이면 BULL.
        if self.state == AllocationState.CAUTION:
            if structural_bear:
                return AllocationState.BEAR
            if self.recovery_score >= 4:
                return AllocationState.BULL
            return self.state

        # BEAR: 상승 조건 6개 중 3개 이상 회복되면 RECOVERY 후보가 된다.
        if self.state == AllocationState.BEAR:
            if self.recovery_score >= self.BEAR_RECOVERY_SCORE:
                return AllocationState.RECOVERY
            return self.state

        # RECOVERY: 구조적 하락이 재발하면 BEAR로 돌아간다. BULL 복귀에는
        # 상승 점수 4점과 종가>EMA55, ROC60>0을 함께 요구한다.
        if structural_bear:
            return AllocationState.BEAR
        if self.recovery_score >= 4 and self._bull_reentry_allowed(qqq):
            return AllocationState.BULL
        if self.risk_off_score >= self.CAUTION_ENTER_SCORE:
            return AllocationState.CAUTION
        return self.state

    def _confirmation_days(self, desired):
        # 하루짜리 노이즈를 줄이기 위해 후보 상태가 아래 기간만큼
        # 연속으로 유지될 때 실제 상태를 변경한다.
        if desired == AllocationState.BEAR:
            return self.BEAR_CONFIRMATION_DAYS
        if desired == AllocationState.CAUTION:
            return self.CAUTION_CONFIRMATION_DAYS
        if desired == AllocationState.RECOVERY:
            return self.RECOVERY_CONFIRMATION_DAYS
        if desired == AllocationState.BULL:
            return self.BULL_CONFIRMATION_DAYS
        return 1

    def _confirm(self, desired):
        if desired == self.state:
            self._candidate = None
            self._candidate_days = 0
            return False
        if desired != self._candidate:
            self._candidate = desired
            self._candidate_days = 1
        else:
            self._candidate_days += 1
        return self._candidate_days >= self._confirmation_days(desired)

    @staticmethod
    def _execution_days(state):
        return {
            AllocationState.BEAR: 1,
            AllocationState.CAUTION: 2,
            AllocationState.RECOVERY: 3,
            AllocationState.BULL: 5,
        }[state]

    def _select_safe_asset(self, market):
        roc_column = f"ROC{self.SAFE_MOMENTUM_PERIOD}"
        bnd_roc = market["BND"].get(roc_column)
        bil_roc = market["BIL"].get(roc_column)
        if not self._valid(bnd_roc, bil_roc):
            return self.safe_asset or "BND"
        if self.safe_asset is None:
            return "BND" if bnd_roc >= bil_roc else "BIL"
        if (
            self.safe_asset == "BND"
            and bil_roc > bnd_roc + self.SAFE_SWITCH_BUFFER
        ):
            return "BIL"
        if (
            self.safe_asset == "BIL"
            and bnd_roc > bil_roc + self.SAFE_SWITCH_BUFFER
        ):
            return "BND"
        return self.safe_asset

    def _target_for_state(self):
        qqq_weight, gold_weight = self.STATE_WEIGHTS[self.state]
        target = {
            "QQQ": qqq_weight,
            "BND": 0.0,
            "BIL": 0.0,
            "GLD": gold_weight,
        }
        target[self.safe_asset] = 1.0 - qqq_weight - gold_weight
        return target

    def _monthly_band_rebalance(self, date, market, portfolio):
        month = date.to_period("M")
        if month == self.last_rebalance_month:
            return False
        self.last_rebalance_month = month
        prices = {ticker: market[ticker]["Close"] for ticker in self.target}
        weights = portfolio.weights(prices)
        return any(
            abs(weights.get(ticker, 0.0) - target_weight) >= 0.05
            for ticker, target_weight in self.target.items()
        )

    def _signal(self, rebalance, reason, days=None):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": days or self._execution_days(self.state),
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        previous_safe_asset = self.safe_asset
        if month != self.last_safe_selection_month:
            self.safe_asset = self._select_safe_asset(market)
            self.last_safe_selection_month = month
        safe_changed = (
            previous_safe_asset is not None
            and self.safe_asset != previous_safe_asset
        )

        desired = self._desired_state(market["QQQ"])
        if self.state is None:
            self.state = desired
            self.target = self._target_for_state()
            self.last_rebalance_month = month
            return self._signal(True, "INITIAL")

        rebalance = False
        reason = None
        if self._confirm(desired):
            previous_state = self.state
            self.state = desired
            self.last_rebalance_month = month
            self._candidate = None
            self._candidate_days = 0
            rebalance = True
            reason = (
                f"{previous_state.value}->{self.state.value}"
                f"(risk_off={self.risk_off_score},recovery={self.recovery_score})"
            )
        elif self._monthly_band_rebalance(date, market, portfolio):
            rebalance = True
            reason = "MONTHLY_5PCT_BAND"

        desired_target = self._target_for_state()
        target_changed = desired_target != self.target
        self.target = desired_target
        if safe_changed:
            rotation = f"SAFE_ROTATION_{previous_safe_asset}->{self.safe_asset}"
            reason = f"{reason}|{rotation}" if reason else rotation

        if rebalance or target_changed:
            days = None if rebalance else 1
            return self._signal(True, reason, days)
        return self._signal(False, None)


class STATIC_703010_BAND(BaseStrategy):
    """Static QQQ 70 / BND 20 / GLD 10 benchmark with a 5% band."""

    def __init__(self):
        self.target = {"QQQ": 0.70, "BND": 0.20, "GLD": 0.10}
        self.last_rebalance_month = None

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        reason = None
        rebalance = False
        if self.last_rebalance_month is None:
            rebalance = True
            reason = "INITIAL"
        elif month != self.last_rebalance_month:
            prices = {ticker: market[ticker]["Close"] for ticker in self.target}
            weights = portfolio.weights(prices)
            rebalance = any(
                abs(weights.get(ticker, 0.0) - target_weight) >= 0.05
                for ticker, target_weight in self.target.items()
            )
            if rebalance:
                reason = "MONTHLY_5PCT_BAND"
        self.last_rebalance_month = month
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": 1,
            "reason": reason,
        }


class STATIC_70_BIL20_GLD10(STATIC_703010_BAND):
    def __init__(self):
        super().__init__()
        self.target = {"QQQ": 0.70, "BIL": 0.20, "GLD": 0.10}


class STATIC_70_BND10_BIL10_GLD10(STATIC_703010_BAND):
    def __init__(self):
        super().__init__()
        self.target = {
            "QQQ": 0.70,
            "BND": 0.10,
            "BIL": 0.10,
            "GLD": 0.10,
        }


class STATIC_70_BND5_BIL15_GLD10(STATIC_703010_BAND):
    def __init__(self):
        super().__init__()
        self.target = {
            "QQQ": 0.70,
            "BND": 0.05,
            "BIL": 0.15,
            "GLD": 0.10,
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

        if current_month != self.last_checked_month:
            self.last_checked_month = current_month
            prices = {
                ticker: market[ticker]["Close"]
                for ticker in self.current_target
            }
            weights = portfolio.weights(prices)
            for ticker, target_weight in self.current_target.items():
                weight_diff = weights.get(ticker, 0.0) - target_weight
                if (
                    weight_diff <= self.lower_threshold
                    or weight_diff >= self.upper_threshold
                ):
                    return self._signal(
                        True,
                        self.current_target,
                        1,
                        f"ASYMMETRIC_BAND_{ticker}(diff:{weight_diff:+.3f})",
                    )

        return self._signal(False, self.current_target, 1)


class RETIREMENT_7030_BAND(BaseStrategy):
    """Daily 5% band strategy targeting QQQ 70 / BND 30."""

    def __init__(self):
        self.target_weights = {"QQQ": 0.70, "BND": 0.30}
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
        prices = {
            ticker: market[ticker]["Close"] for ticker in self.target_weights
        }
        weights = portfolio.weights(prices)
        for ticker, target_weight in self.target_weights.items():
            weight_diff = weights.get(ticker, 0.0) - target_weight
            if (
                weight_diff <= self.lower_threshold
                or weight_diff >= self.upper_threshold
            ):
                action = (
                    "BUY_DIP"
                    if weight_diff <= self.lower_threshold
                    else "TAKE_PROFIT"
                )
                return self._signal(
                    True,
                    self.target_weights,
                    5,
                    f"5%_BAND_BREAK_{action}_{ticker}(diff:{weight_diff:+.3f})",
                )
        return self._signal(False, self.target_weights, 1)


class ASYMMETRIC_TREND_BAND(BaseStrategy):
    """QLD 40 / GLD 30 / QQQ 30 with an EMA55 asymmetric band."""

    def __init__(self):
        self.target_weights = {"QLD": 0.40, "GLD": 0.30, "QQQ": 0.30}

    @staticmethod
    def _signal(rebalance, target, days, reason=None):
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        prices = {}
        for ticker in self.target_weights:
            if ticker not in market or "Close" not in market[ticker]:
                return self._signal(False, self.target_weights, 1)
            price = market[ticker]["Close"]
            if price is None or price != price:
                return self._signal(False, self.target_weights, 1)
            prices[ticker] = price

        weights = portfolio.weights(prices)
        risk_weight = weights.get("QLD", 0.0)
        weight_diff = risk_weight - self.target_weights["QLD"]
        current_price = market["QLD"]["Close"]
        ema55 = market["QLD"].get("EMA55", current_price)

        if current_price >= ema55:
            upper_threshold = float("inf")
            lower_threshold = -0.015
            trend = "UPTREND"
        else:
            upper_threshold = 0.015
            lower_threshold = -0.06
            trend = "DOWNTREND"

        if weight_diff <= lower_threshold:
            return self._signal(
                True,
                self.target_weights,
                1,
                f"{trend}_BUY_DIP_QLD(diff:{weight_diff:+.3f})",
            )
        if weight_diff >= upper_threshold:
            return self._signal(
                True,
                self.target_weights,
                1,
                f"{trend}_TAKE_PROFIT_QLD(diff:{weight_diff:+.3f})",
            )
        return self._signal(False, self.target_weights, 1)
