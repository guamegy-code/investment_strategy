"""Production strategy and the static benchmarks used to evaluate it."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import Enum
from math import exp


def _validated_asset_mix(risk_asset, risk_assets):
    """Return a positive risk-sleeve mix whose weights sum to one."""
    if risk_assets is None:
        return {risk_asset: 1.0}
    if not isinstance(risk_assets, Mapping) or not risk_assets:
        raise ValueError("risk_assets must be a non-empty asset-to-weight mapping")
    mix = {ticker: float(weight) for ticker, weight in risk_assets.items()}
    if any(not ticker or weight <= 0.0 for ticker, weight in mix.items()):
        raise ValueError("risk asset names and weights must be positive")
    if abs(sum(mix.values()) - 1.0) > 1e-9:
        raise ValueError("risk asset weights must sum to 1.0")
    return mix


def _allocate_sleeve(total_weight, asset_mix):
    """Allocate a total weight while keeping the rounded result exact."""
    items = list(asset_mix.items())
    target = {}
    allocated = 0.0
    for index, (ticker, share) in enumerate(items):
        weight = (
            round(total_weight - allocated, 10)
            if index == len(items) - 1
            else round(total_weight * share, 10)
        )
        target[ticker] = weight
        allocated += weight
    return target


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

    SIGNAL_ASSET = "QQQ"
    RISK_ASSET = "QQQ"
    BOND_ASSET = "BND"
    CASH_ASSET = "BIL"
    DIVERSIFIER_ASSET = "GLD"

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
        bnd_roc = market[self.BOND_ASSET].get(roc_column)
        bil_roc = market[self.CASH_ASSET].get(roc_column)
        if not self._valid(bnd_roc, bil_roc):
            return self.safe_asset or self.BOND_ASSET
        if self.safe_asset is None:
            return self.BOND_ASSET if bnd_roc >= bil_roc else self.CASH_ASSET
        if (
            self.safe_asset == self.BOND_ASSET
            and bil_roc > bnd_roc + self.SAFE_SWITCH_BUFFER
        ):
            return self.CASH_ASSET
        if (
            self.safe_asset == self.CASH_ASSET
            and bnd_roc > bil_roc + self.SAFE_SWITCH_BUFFER
        ):
            return self.BOND_ASSET
        return self.safe_asset

    def _target_for_state(self):
        qqq_weight, gold_weight = self.STATE_WEIGHTS[self.state]
        target = {
            self.RISK_ASSET: qqq_weight,
            self.BOND_ASSET: 0.0,
            self.CASH_ASSET: 0.0,
            self.DIVERSIFIER_ASSET: gold_weight,
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

        desired = self._desired_state(market[self.SIGNAL_ASSET])
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


class PensionRiskAllocationStrategy(DynamicRiskAllocationStrategy):
    """Retirement strategy with configurable products and no gold allocation.

    ``signal_asset`` drives regime detection but is never traded when it differs
    from ``risk_asset``. This keeps QQQ signals while trading a pension ETF.
    The combined ``risk_assets`` sleeve is capped at 70%; its mapping defines
    how that sleeve is split. ``bond_asset`` and ``cash_asset`` must be replaced
    with products classified as safe assets by the pension provider. The
    default US ETFs are backtest proxies, not Korean pension products.
    """

    MAX_RISK_WEIGHT = 0.70
    STATE_RISK_WEIGHTS = {
        AllocationState.BULL: 0.70,
        AllocationState.CAUTION: 0.70,
        AllocationState.BEAR: 0.00,
        AllocationState.RECOVERY: 0.50,
    }

    def __init__(
        self,
        signal_asset=None,
        risk_asset="QQQ",
        risk_assets=None,
        bond_asset="BND",
        cash_asset="BIL",
    ):
        self.risk_assets = _validated_asset_mix(risk_asset, risk_assets)
        trade_assets = (*self.risk_assets, bond_asset, cash_asset)
        if len(set(trade_assets)) != len(trade_assets):
            raise ValueError("risk, bond, and cash assets must be different")
        self.RISK_ASSET = next(iter(self.risk_assets))
        self.SIGNAL_ASSET = signal_asset or self.RISK_ASSET
        if self.SIGNAL_ASSET in {bond_asset, cash_asset}:
            raise ValueError("signal asset cannot be a bond or cash asset")
        self.BOND_ASSET = bond_asset
        self.CASH_ASSET = cash_asset
        super().__init__()

    @property
    def required_tickers(self):
        """Market-data identifiers needed to run this strategy."""
        return tuple(dict.fromkeys((
            self.SIGNAL_ASSET,
            *self.risk_assets,
            self.BOND_ASSET,
            self.CASH_ASSET,
        )))

    def _target_for_state(self):
        risk_weight = self.STATE_RISK_WEIGHTS[self.state]
        if not 0.0 <= risk_weight <= self.MAX_RISK_WEIGHT:
            raise ValueError("retirement risk-asset weight must be between 0% and 70%")
        target = _allocate_sleeve(risk_weight, self.risk_assets)
        target.update({self.BOND_ASSET: 0.0, self.CASH_ASSET: 0.0})
        target[self.safe_asset] = round(1.0 - risk_weight, 10)
        return target


class _PensionSafeBlendMixin:
    """Blend BND and BIL in 25-point steps without changing risk weights."""

    SAFE_BLEND_INNER_THRESHOLD = 0.25
    SAFE_BLEND_OUTER_THRESHOLD = 1.00

    def __init__(self, *args, **kwargs):
        self.safe_asset_mix = None
        super().__init__(*args, **kwargs)

    def _select_safe_asset(self, market):
        """Set a 25-point BND/BIL blend from their 40-session momentum gap."""
        roc_column = f"ROC{self.SAFE_MOMENTUM_PERIOD}"
        bnd_roc = market[self.BOND_ASSET].get(roc_column)
        bil_roc = market[self.CASH_ASSET].get(roc_column)
        if not self._valid(bnd_roc, bil_roc):
            if self.safe_asset_mix is None:
                selected = self.safe_asset or self.BOND_ASSET
                self.safe_asset_mix = {
                    self.BOND_ASSET: float(selected == self.BOND_ASSET),
                    self.CASH_ASSET: float(selected == self.CASH_ASSET),
                }
            return max(self.safe_asset_mix, key=self.safe_asset_mix.get)

        spread = float(bnd_roc) - float(bil_roc)
        if spread >= self.SAFE_BLEND_OUTER_THRESHOLD:
            bond_share = 1.00
        elif spread >= self.SAFE_BLEND_INNER_THRESHOLD:
            bond_share = 0.75
        elif spread >= -self.SAFE_BLEND_INNER_THRESHOLD:
            bond_share = 0.50
        elif spread > -self.SAFE_BLEND_OUTER_THRESHOLD:
            bond_share = 0.25
        else:
            bond_share = 0.00
        self.safe_asset_mix = {
            self.BOND_ASSET: bond_share,
            self.CASH_ASSET: 1.0 - bond_share,
        }
        return (
            self.BOND_ASSET
            if bond_share >= 0.50
            else self.CASH_ASSET
        )

    def _target_for_state(self):
        target = super()._target_for_state()
        target.pop(None, None)
        safe_weight = 1.0 - sum(
            target.get(ticker, 0.0) for ticker in self.risk_assets
        )
        safe_mix = self.safe_asset_mix or {
            self.BOND_ASSET: float(self.safe_asset == self.BOND_ASSET),
            self.CASH_ASSET: float(self.safe_asset == self.CASH_ASSET),
        }
        if not any(safe_mix.values()):
            safe_mix = {self.BOND_ASSET: 1.0, self.CASH_ASSET: 0.0}
        bond_weight = round(safe_weight * safe_mix[self.BOND_ASSET], 10)
        target[self.BOND_ASSET] = bond_weight
        target[self.CASH_ASSET] = round(safe_weight - bond_weight, 10)
        return target


class PensionBlendedRiskAllocationStrategy(
    _PensionSafeBlendMixin,
    PensionRiskAllocationStrategy,
):
    """Pension strategy with a 25-point BND/BIL momentum ladder."""


class _PensionVXUSSubstitutionMixin:
    """Replace only the available BND sleeve with capped VXUS exposure."""

    ALTERNATIVE_RISK_ASSET = "VXUS"

    @property
    def required_tickers(self):
        return tuple(dict.fromkeys((
            *super().required_tickers,
            self.ALTERNATIVE_RISK_ASSET,
        )))

    @property
    def risk_asset_tickers(self):
        """All assets counted as risk assets in reports and charts."""
        return (*self.risk_assets, self.ALTERNATIVE_RISK_ASSET)

    def _target_for_state(self):
        target = super()._target_for_state()
        target[self.ALTERNATIVE_RISK_ASSET] = 0.0
        risk_weight = sum(
            target.get(ticker, 0.0) for ticker in self.risk_assets
        )
        if risk_weight < self.MAX_RISK_WEIGHT and target[self.BOND_ASSET] > 0.0:
            available_risk_capacity = self.MAX_RISK_WEIGHT - risk_weight
            vxus_weight = min(target[self.BOND_ASSET], available_risk_capacity)
            target[self.ALTERNATIVE_RISK_ASSET] = round(vxus_weight, 10)
            target[self.BOND_ASSET] = round(
                target[self.BOND_ASSET] - vxus_weight, 10
            )
        combined_risk = risk_weight + target[self.ALTERNATIVE_RISK_ASSET]
        if combined_risk > self.MAX_RISK_WEIGHT + 1e-9:
            raise ValueError("combined QQQ and VXUS weight exceeds 70%")
        return target


class PensionVXUSSubstitutionStrategy(
    _PensionVXUSSubstitutionMixin,
    PensionRiskAllocationStrategy,
):
    """Use VXUS for spare risk capacity when BND is selected below 70% QQQ.

    VXUS remains a risk asset. It replaces only enough BND to keep the
    combined QQQ and VXUS target at or below the pension risk-asset cap.
    BIL is never replaced.
    """



class PensionBlendedVXUSSubstitutionStrategy(
    _PensionVXUSSubstitutionMixin,
    PensionBlendedRiskAllocationStrategy,
):
    """VXUS substitution combined with the 25-point BND/BIL ladder."""


class DownsideTrendOverlayStrategy(BaseStrategy):
    """Keep QQQ near 70% and reduce it continuously during negative trends."""

    MIN_QQQ_WEIGHT = 0.20
    MAX_QQQ_WEIGHT = 0.70
    MAX_GLD_WEIGHT = 0.20
    TARGET_QQQ_VOLATILITY = 0.25
    REBALANCE_BAND = 0.05
    EXECUTION_DAYS = 3

    def __init__(self):
        self.target = None
        self.last_signal_month = None
        self.trend_score = 0.0
        self.volatility_multiplier = 1.0

    @staticmethod
    def _valid(*values):
        return all(value is not None and value == value for value in values)

    @staticmethod
    def _clip(value, lower, upper):
        return max(lower, min(value, upper))

    @classmethod
    def _scaled_signal(cls, value, scale):
        return cls._clip(value / scale, -1.0, 1.0)

    def _qqq_weight(self, qqq):
        close = qqq.get("Close")
        ema200 = qqq.get("EMA200")
        roc60 = qqq.get("ROC60")
        roc120 = qqq.get("ROC120")
        roc252 = qqq.get("ROC252")
        volatility = qqq.get("VOL60")
        if not self._valid(close, ema200, roc60, roc120, roc252, volatility):
            self.trend_score = 0.0
            self.volatility_multiplier = 1.0
            return self.MAX_QQQ_WEIGHT

        signals = (
            self._scaled_signal(roc60, 15.0),
            self._scaled_signal(roc120, 25.0),
            self._scaled_signal(roc252, 40.0),
            self._scaled_signal(close / ema200 - 1.0, 0.15),
        )
        self.trend_score = sum(signals) / len(signals)
        self.volatility_multiplier = self._clip(
            self.TARGET_QQQ_VOLATILITY / max(float(volatility), 0.01),
            0.25,
            1.0,
        )
        volatility_stress = 1.0 / self.volatility_multiplier
        reduction = self._clip(
            max(0.0, -self.trend_score) * volatility_stress,
            0.0,
            1.0,
        )
        tactical_range = self.MAX_QQQ_WEIGHT - self.MIN_QQQ_WEIGHT
        return self.MAX_QQQ_WEIGHT - tactical_range * reduction

    def _safe_weights(self, market, remaining):
        scores = {}
        for ticker in ("BND", "BIL", "GLD"):
            roc60 = market[ticker].get("ROC60")
            roc120 = market[ticker].get("ROC120")
            volatility = market[ticker].get("VOL60")
            if not self._valid(roc60, roc120, volatility):
                scores[ticker] = 0.0
                continue
            momentum = (float(roc60) + float(roc120)) / 200.0
            scores[ticker] = momentum - 0.25 * float(volatility)

        maximum = max(scores.values())
        scaled = {
            ticker: exp((score - maximum) / 0.05)
            for ticker, score in scores.items()
        }
        total = sum(scaled.values())
        safe = {
            ticker: remaining * value / total
            for ticker, value in scaled.items()
        }

        gold_cap = min(self.MAX_GLD_WEIGHT, remaining)
        if safe["GLD"] > gold_cap:
            excess = safe["GLD"] - gold_cap
            safe["GLD"] = gold_cap
            non_gold = safe["BND"] + safe["BIL"]
            if non_gold > 0:
                safe["BND"] += excess * safe["BND"] / non_gold
                safe["BIL"] += excess * safe["BIL"] / non_gold
            else:
                safe["BND"] += excess / 2.0
                safe["BIL"] += excess / 2.0
        return safe

    def _desired_target(self, market):
        qqq_weight = self._qqq_weight(market["QQQ"])
        target = {"QQQ": qqq_weight}
        target.update(self._safe_weights(market, 1.0 - qqq_weight))
        return target

    def _signal(self, rebalance, reason):
        return {
            "rebalance": rebalance,
            "target": self.target.copy(),
            "days": self.EXECUTION_DAYS,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        month = date.to_period("M")
        if self.target is None:
            self.target = self._desired_target(market)
            self.last_signal_month = month
            return self._signal(True, "INITIAL_DOWNSIDE_OVERLAY")
        if month == self.last_signal_month:
            return self._signal(False, None)

        self.last_signal_month = month
        desired = self._desired_target(market)
        prices = {ticker: market[ticker]["Close"] for ticker in desired}
        current = portfolio.weights(prices)
        self.target = desired
        rebalance = any(
            abs(current.get(ticker, 0.0) - weight) >= self.REBALANCE_BAND
            for ticker, weight in desired.items()
        )
        return self._signal(
            rebalance,
            "MONTHLY_DOWNSIDE_OVERLAY_5PCT_BAND" if rebalance else None,
        )


class _MarketRegimeObserver:
    """Observe DynamicRiskAllocationStrategy states without changing weights."""

    def __init__(self):
        self.classifier = DynamicRiskAllocationStrategy()

    def update(self, qqq):
        desired = self.classifier._desired_state(qqq)
        if self.classifier.state is None:
            self.classifier.state = desired
        elif self.classifier._confirm(desired):
            self.classifier.state = desired
            self.classifier._candidate = None
            self.classifier._candidate_days = 0
        return self.classifier.state


class _StaticRegimeBandStrategy(BaseStrategy):
    """Fixed allocation with a 5% band and read-only market-regime tracking."""

    SIGNAL_ASSET = "QQQ"
    RISK_ASSET = "QQQ"
    TARGET = {}

    def __init__(self):
        self.target = self.TARGET.copy()
        self.last_rebalance_month = None
        self._regime_observer = _MarketRegimeObserver()
        self.state = None
        self.risk_off_score = 0
        self.recovery_score = 0

    def evaluate(self, date, market, portfolio):
        self.state = self._regime_observer.update(market[self.SIGNAL_ASSET])
        classifier = self._regime_observer.classifier
        self.risk_off_score = classifier.risk_off_score
        self.recovery_score = classifier.recovery_score

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


class STATIC_70_BND10_BIL10_GLD10(_StaticRegimeBandStrategy):
    """Selected benchmark splitting defensive assets between BND and BIL."""

    TARGET = {
        "QQQ": 0.70,
        "BND": 0.10,
        "BIL": 0.10,
        "GLD": 0.10,
    }


class STATIC_PENSION_7030(_StaticRegimeBandStrategy):
    """Configurable 70/30 benchmark with a separate regime signal asset."""

    def __init__(
        self,
        signal_asset=None,
        risk_asset="QQQ",
        risk_assets=None,
        bond_asset="BND",
        cash_asset="BIL",
    ):
        self.risk_assets = _validated_asset_mix(risk_asset, risk_assets)
        trade_assets = (*self.risk_assets, bond_asset, cash_asset)
        if len(set(trade_assets)) != len(trade_assets):
            raise ValueError("risk, bond, and cash assets must be different")
        self.RISK_ASSET = next(iter(self.risk_assets))
        self.SIGNAL_ASSET = signal_asset or self.RISK_ASSET
        if self.SIGNAL_ASSET in {bond_asset, cash_asset}:
            raise ValueError("signal asset cannot be a bond or cash asset")
        self.BOND_ASSET = bond_asset
        self.CASH_ASSET = cash_asset
        self.TARGET = _allocate_sleeve(0.70, self.risk_assets)
        self.TARGET.update({self.BOND_ASSET: 0.15, self.CASH_ASSET: 0.15})
        super().__init__()

    @property
    def required_tickers(self):
        """Market-data identifiers needed to run this benchmark."""
        return tuple(dict.fromkeys((
            self.SIGNAL_ASSET,
            *self.risk_assets,
            self.BOND_ASSET,
            self.CASH_ASSET,
        )))




import math


class ASYMMETRIC_TREND_BAND_ADD_DEFENSE(BaseStrategy):
    """
    비대칭 밴드 + 이동평균선(SMA) 추세 필터 + MDD 방어 + 동적 타겟 추적 결합 전략
    - 상승장(Uptrend): 최고 도달 비중을 새로운 타겟으로 갱신(Trailing Target). 고점 기준 -3% 하락 시 줍줍.
    - 하락장(Downtrend): 추세 이탈 시 60% 기본 비중으로 리셋하여 수익 확정. 반등 시 타이트하게 익절(+3%).
    """

    RISK_ASSET = "QQQ"
    SAFE_ASSET = "BND"
    MID_ASSET = "GLD"

    def __init__(self):
        # 고정값이 아닌 시작(Base) 비중으로 정의
        self.base_weights = {
            self.RISK_ASSET: 0.60,
            self.MID_ASSET: 0.10,
            self.SAFE_ASSET: 0.30,
        }
        
        self.defensive_weights = {
            self.RISK_ASSET: 0.30,
            self.MID_ASSET: 0.10,
            self.SAFE_ASSET: 0.70,
        }
        
        # [핵심 추가] 시장 상황에 따라 유연하게 갱신되는 위험 자산 추적 비중
        self.trailing_risk_target = self.base_weights[self.RISK_ASSET]
        
        self.is_defensive_mode = False
        self.highest_price = 0.0
        self.lowest_price = float('inf')
        
        # 장기 추세 변곡점 판단용 플래그
        self.was_uptrend = True

    def _signal(self, rebalance, target, days, reason=None):
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        # 1. 무결성 검증
        prices = {}
        for ticker in self.base_weights:
            if ticker not in market or "Close" not in market[ticker]:
                return self._signal(False, self.base_weights, 1, f"Missing data for {ticker}")
            
            price = market[ticker]["Close"]
            if price is None or math.isnan(float(price)):
                return self._signal(False, self.base_weights, 1, f"NaN price for {ticker}")
            prices[ticker] = price

        current_price = market[self.RISK_ASSET]["Close"]
        ma55 = market[self.RISK_ASSET].get("EMA55", current_price)
        ma200 = market[self.RISK_ASSET].get("EMA200", current_price)

        is_uptrend = ma55 >= ma200

        weights = portfolio.weights(prices)
        current_risk_weight = weights.get(self.RISK_ASSET, 0.0)

        # ========================================================
        # [핵심 로직] Trailing Target Weight (동적 목표 비중 갱신)
        # ========================================================
        if is_uptrend:
            # 상승장: 자연스럽게 불어난 비중을 새로운 목표 비중으로 승격시킴
            if current_risk_weight > self.trailing_risk_target:
                self.trailing_risk_target = current_risk_weight
        else:
            # 하락장 전환 시: 쌓아둔 막대한 수익을 확정(Take Profit)하고 기본 비중(60%)으로 안전하게 리셋
            if self.was_uptrend:
                print(f"[{date.date()}] 하락장(역배열) 전환! 수익 실현 및 목표 비중 60% 리셋")
                self.trailing_risk_target = self.base_weights[self.RISK_ASSET]
        
        self.was_uptrend = is_uptrend

        # 실시간 동적 타겟 딕셔너리 생성
        dynamic_target_weights = self.base_weights.copy()
        dynamic_target_weights[self.RISK_ASSET] = self.trailing_risk_target
        # 늘어난 위험 자산 비중만큼 안전 자산(BND) 비중을 삭감 (최소 0% 제한)
        dynamic_target_weights[self.SAFE_ASSET] = max(0.0, 1.0 - dynamic_target_weights[self.RISK_ASSET] - dynamic_target_weights[self.MID_ASSET])

        # ========================================================
        # 2. 극한 하락 방어(Risk-Off) 로직
        # ========================================================
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price
            
        drawdown_from_peak = (current_price - self.highest_price) / self.highest_price if self.highest_price > 0 else 0.0

        if drawdown_from_peak <= -0.25 and not self.is_defensive_mode:
            self.is_defensive_mode = True
            # 폭락 시 동적 타겟도 60%로 리셋하여 복귀 시 무리한 매수 방지
            self.trailing_risk_target = self.base_weights[self.RISK_ASSET] 
            print(f"[{date.date()}] 최고점 대비 {drawdown_from_peak:.1%} 하락! 방어 모드(QQQ 20%) 대피")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(True, self.defensive_weights, 1, "TRAILING_STOP_DEFENSIVE_MODE")

        if self.is_defensive_mode and is_uptrend:
            self.is_defensive_mode = False
            if current_risk_weight > dynamic_target_weights[self.RISK_ASSET]:
                print(f"[{date.date()}] 방어 모드 해제 및 리밸런싱 스킵 (수익방치)")
            else:
                print(f"[{date.date()}] 방어 모드 해제 -> 상승장 타겟 비중 복귀")
                return self._signal(True, dynamic_target_weights, 1, "TREND_RECOVERY_NORMAL_MODE")

        # 현재 활성화된 타겟 비중 결정 및 비중 차이 계산
        active_target_weights = self.defensive_weights if self.is_defensive_mode else dynamic_target_weights
        weight_diff = current_risk_weight - active_target_weights[self.RISK_ASSET]

        # ========================================================
        # 3. 비대칭 밴드 임계치 및 극단적 지표 대응
        # ========================================================
        if is_uptrend:
            upper_threshold = 0.1 #float('inf') 
            lower_threshold = -0.03
            trend_status = "UPTREND"
        else:
            upper_threshold = 0.03
            lower_threshold = -0.09
            trend_status = "DOWNTREND"

        rsi = market[self.RISK_ASSET].get("RSI14", 50)
        disparity60 = market[self.RISK_ASSET].get("DISPARITY60", 100)

        if rsi > 95 and disparity60 >= 110:
            if is_uptrend and current_risk_weight > active_target_weights[self.RISK_ASSET]:
                pass # 상승장 수익 방치
            else:
                print(f"[{date.date()}] 극단적 과매수 익절 발동")
                self.highest_price = current_price
                self.lowest_price = current_price
                return self._signal(True, active_target_weights, 1, "EXTREME_OVERBOUGHT")

        if rsi <= 20 and disparity60 <= 90:
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(True, active_target_weights, 1, "EXTREME_OVERSELL_BUY")

        # ========================================================
        # 4. 밴드 이탈 리밸런싱 (진정한 고점 기준 줍줍)
        # ========================================================
        if weight_diff <= lower_threshold:
            print(f"[{date.date()}] {trend_status} 매수(Buy Dip) 발동 | 타겟({active_target_weights[self.RISK_ASSET]:.1%}) 대비 차이: {weight_diff:+.3f}")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(True, active_target_weights, 1, f"{trend_status}_BUY_DIP")
            
        elif weight_diff >= upper_threshold:
            print(f"[{date.date()}] {trend_status} 상단 밴드 이탈 매도 | 비중차이: {weight_diff:+.3f}")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(True, active_target_weights, 1, f"{trend_status}_TAKE_PROFIT")

        return self._signal(False, active_target_weights, 1)

class ASYMMETRIC_TREND_BAND_ADD_DEFENSE2(BaseStrategy):

    """
    비대칭 밴드 + 이동평균선(SMA) 추세 필터 + MDD 방어(Risk-Off) 결합 전략
    - 상승장(Uptrend): 익절은 무한대기(Let profits run), 추매는 예민하게(-3%)
    - 하락장(Downtrend): 익절은 타이트하게(+3%), 추매는 신중하게(-6%)
    - 극한 하락(-25% MDD): 위험 자산(QQQ) 비중을 20%로 강제 축소하여 방어 모드 전환
    - 추세 회복(Recovery): 방어 모드에서 상승장 전환 시, 이미 비중이 크다면 강제 매도 없이 스킵
    """

    RISK_ASSET = "QQQ"
    SAFE_ASSET = "BND"
    MID_ASSET = "GLD"

    def __init__(self):
        # 기본 타겟 비중 (상승장 및 평시)
        self.target_weights = {
            self.RISK_ASSET: 0.65,
            self.MID_ASSET: 0.05,
            self.SAFE_ASSET: 0.30,
        }
        
        # [추가] 극한 하락장 방어용(Risk-Off) 타겟 비중
        self.defensive_weights = {
            self.RISK_ASSET: 0.30,  # 60% -> 30% 대폭 축소
            self.MID_ASSET: 0.10,
            self.SAFE_ASSET: 0.90,  # 채권/현금 비중 30% -> 70% 확대
        }
        
        # 방어 모드 상태 플래그
        self.is_defensive_mode = False

        # 트레일링 스탑/익절용 최고/최저가 추적 변수
        self.highest_price = 0.0
        self.lowest_price = float('inf')

    def _signal(self, rebalance, target, days, reason=None):
        """매매 시그널을 생성하여 백테스트 엔진에 전달하는 포맷"""
        return {
            "rebalance": rebalance,
            "target": target.copy(),
            "days": days,
            "reason": reason,
        }

    def evaluate(self, date, market, portfolio):
        """
        매일(또는 주기적으로) 호출되어 리밸런싱 여부를 판단하는 핵심 로직
        """
        # 1. 현재 가격 및 데이터 무결성 검증 방어 로직
        prices = {}
        for ticker in self.target_weights:
            if ticker not in market or "Close" not in market[ticker]:
                return self._signal(False, self.target_weights, 1, f"Missing data for {ticker}")
            
            price = market[ticker]["Close"]
            if price is None or math.isnan(float(price)):
                return self._signal(False, self.target_weights, 1, f"NaN price for {ticker}")
                
            prices[ticker] = price

        # 2. 현재 가격 및 이평선 조회 (55~200일 이동평균선 사용)
        current_price = market[self.RISK_ASSET]["Close"]
        ma20 = market[self.RISK_ASSET].get("EMA20", current_price)
        ma55 = market[self.RISK_ASSET].get("EMA55", current_price)   # EMA55로 수정
        ma200 = market[self.RISK_ASSET].get("EMA200", current_price)


        is_uptrend = ma55 >= ma200

        # ========================================================
        # [수정됨] 3. 현재 포트폴리오 비중 선행 계산
        # (추세 회복 시 비중을 체크하기 위해 위로 끌어올림)
        # ========================================================
        weights = portfolio.weights(prices)
        current_risk_weight = weights.get(self.RISK_ASSET, 0.0)

        # ========================================================
        # 4. [회피 로직] 상승장 전환 시 방어 모드 해제 -> 기본 비중 복귀
        # ========================================================
        if self.is_defensive_mode and is_uptrend:
            self.is_defensive_mode = False
            
            # [요청하신 핵심 로직] 비중을 60%로 늘리려는데, 이미 60%를 초과한 상태라면 스킵!
            if current_risk_weight > self.target_weights[self.RISK_ASSET]:
                print(f"[{date.date()}] 추세 회복(EMA55>=EMA200) 방어 모드 해제! 단, 현재비중({current_risk_weight:.1%})이 목표({self.target_weights[self.RISK_ASSET]:.1%})보다 커서 매도 리밸런싱 스킵(수익방치)")
                # 시그널을 반환하지 않고 아래로 흘려보내어 자연스럽게 홀딩(False)되도록 함
            else:
                print(f"[{date.date()}] 추세 회복(EMA55>=EMA200) -> 방어 모드 해제 및 기본 비중 복귀 매수")
                return self._signal(
                    True, 
                    self.target_weights, 
                    1, 
                    f"TREND_RECOVERY_NORMAL_MODE_{self.RISK_ASSET}"
                )

        # 5. 현재 모드에 따른 목표 비중 선택 및 비중 차이(Weight Diff) 계산
        active_target_weights = self.defensive_weights if self.is_defensive_mode else self.target_weights
        weight_diff = current_risk_weight - active_target_weights[self.RISK_ASSET]

        # 6. 시장 상태에 따른 비대칭 임계치(Threshold) 설정
        if is_uptrend:
            upper_threshold = float('inf') 
            lower_threshold = -0.03
            trend_status = "UPTREND"
        else:
            upper_threshold = 0.03
            lower_threshold = -0.06
            trend_status = "DOWNTREND"

        # 7. 극단적 과매수/과매도 강제 리밸런싱 조건
        rsi = market[self.RISK_ASSET].get("RSI14", 50)
        disparity60 = market[self.RISK_ASSET].get("DISPARITY60", 100)

        if rsi > 95 and disparity60 >= 110:

            print(f"[{date.date()}] 극단적 과매수 익절 발동 (RSI: {rsi:.1f}, Disp60: {disparity60:.1f}) -> 현재:{current_price:.2f}, 현재비중:{current_risk_weight:.1%} 목표 비중 복귀")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True,
                active_target_weights,
                1,
                f"EXTREME_OVERBOUGHT_TAKE_PROFIT_{self.RISK_ASSET}(rsi:{rsi:.1f},disp:{disparity60:.1f})"
            )

        if rsi <= 20 and disparity60 <= 90:
            print(f"[{date.date()}] 극단적 과매도 줍줍 발동 (RSI: {rsi:.1f}, Disp60: {disparity60:.1f}) -> 목표 비중 복귀")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True,
                active_target_weights,
                1,
                f"EXTREME_OVERSELL_BUY_{self.RISK_ASSET}(rsi:{rsi:.1f},disp:{disparity60:.1f})"
            )

        # 8. 트레일링 스탑 - 고점 대비 -25% 하락 시 방어 모드 발동
        if current_price > self.highest_price:
            self.highest_price = current_price
        if current_price < self.lowest_price:
            self.lowest_price = current_price
            
        drawdown_from_peak = (current_price - self.highest_price) / self.highest_price if self.highest_price > 0 else 0.0

        # 고점 대비 -25% 이상 폭락 시 방어 비중(QQQ 30%)으로 대피
        if drawdown_from_peak <= -0.25 and not self.is_defensive_mode:
            self.is_defensive_mode = True
            print(f"[{date.date()}] 최고점 대비 {drawdown_from_peak:.1%} 하락 발동! -> 방어 모드(QQQ 20%)로 긴급 대피")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True, 
                self.defensive_weights, 
                1, 
                f"TRAILING_STOP_DEFENSIVE_MODE_{self.RISK_ASSET}(-25%)"
            )

        # 9. 비중 이탈(Weight Diff) 밴드 확인 및 리밸런싱 시그널 반환
        if weight_diff <= lower_threshold:
            print(f"[{date.date()}] {trend_status} 매수(Buy Dip) 발동 | 비중차이: {weight_diff:+.3f} -> 현재:{current_price:.2f}, 현재비중:{current_risk_weight:.1%}")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True,
                active_target_weights,
                1,
                f"{trend_status}_BUY_DIP_{self.RISK_ASSET}(diff:{weight_diff:+.3f})"
            )
            
        elif weight_diff >= upper_threshold:
            print(f"[{date.date()}] {trend_status} 상단 밴드 이탈 매도 | 비중차이: {weight_diff:+.3f} -> 현재:{current_price:.2f}, 현재비중:{current_risk_weight:.1%}")
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True,
                active_target_weights,
                1,
                f"{trend_status}_TAKE_PROFIT_{self.RISK_ASSET}(diff:{weight_diff:+.3f})"
            )

        # 10. 임계치 이탈이 없다면 그대로 홀딩 (존버 모드)
        return self._signal(False, active_target_weights, 1)