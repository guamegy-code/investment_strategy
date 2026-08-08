"""Experimental strategies kept outside the production OOS-locked module."""

import math

from strategy import (
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2,
    AllocationState,
    RetirementAllocationStrategy,
)


class StagedBearRetirementStrategy(RetirementAllocationStrategy):
    """Experimental 70% -> 30% -> 0% risk-off implementation.

    The first confirmed BEAR transition retains 30% in the risk asset.  It
    moves to 0% only when the parent's structural-bear condition remains true
    for an additional configurable number of trading days.  ``None`` keeps
    the 30% floor throughout BEAR.
    """

    STAGE1_RISK_WEIGHT = 0.30

    def __init__(self, full_exit_confirmation_days=5, *args, **kwargs):
        if (
            full_exit_confirmation_days is not None
            and full_exit_confirmation_days < 1
        ):
            raise ValueError("full_exit_confirmation_days must be positive or None")
        self.full_exit_confirmation_days = full_exit_confirmation_days
        self._continued_bear_days = 0
        self._full_bear_defense = False
        self._stage2_activated_today = False
        super().__init__(*args, **kwargs)

    def _desired_state(self, qqq):
        desired = super()._desired_state(qqq)
        self._stage2_activated_today = False
        if self.state != AllocationState.BEAR:
            self._continued_bear_days = 0
            self._full_bear_defense = False
            return desired

        if (
            not self._full_bear_defense
            and self.full_exit_confirmation_days is not None
            and self._is_structural_bear(qqq)
        ):
            self._continued_bear_days += 1
            if self._continued_bear_days >= self.full_exit_confirmation_days:
                self._full_bear_defense = True
                self._stage2_activated_today = True
        return desired

    def _index_target_for_state(self):
        target = super()._index_target_for_state()
        if self.state != AllocationState.BEAR or self._full_bear_defense:
            return target
        target[self.RISK_ASSET] = self.STAGE1_RISK_WEIGHT
        target[self.BOND_ASSET] = 0.0
        target[self.CASH_ASSET] = 0.0
        target[self.safe_asset] = 1.0 - self.STAGE1_RISK_WEIGHT
        return target

    def evaluate(self, date, market, portfolio):
        previous_state = self.state
        signal = super().evaluate(date, market, portfolio)
        if previous_state != AllocationState.BEAR and self.state == AllocationState.BEAR:
            suffix = "BEAR_STAGE1_30"
            signal["reason"] = (
                f"{signal['reason']}|{suffix}" if signal["reason"] else suffix
            )
        if self._stage2_activated_today:
            signal["reason"] = (
                f"BEAR_STAGE2_0(continued={self._continued_bear_days})"
            )
        return signal


class StateOnlyTransitionRetirementStrategy(RetirementAllocationStrategy):
    """Update equal-target BULL/CAUTION states without forcing a trade."""

    def __init__(
        self,
        suppress_bull_to_caution=True,
        suppress_caution_to_bull=True,
        *args,
        **kwargs,
    ):
        self.suppressed_transitions = set()
        if suppress_bull_to_caution:
            self.suppressed_transitions.add(
                (AllocationState.BULL, AllocationState.CAUTION)
            )
        if suppress_caution_to_bull:
            self.suppressed_transitions.add(
                (AllocationState.CAUTION, AllocationState.BULL)
            )
        super().__init__(*args, **kwargs)

    def _is_suppressed_transition(self, previous_state, current_state):
        return (previous_state, current_state) in self.suppressed_transitions

    def _outside_band(self, market, portfolio):
        prices = {ticker: market[ticker]["Close"] for ticker in self.target}
        weights = portfolio.weights(prices)
        return any(
            abs(weights.get(ticker, 0.0) - target_weight) >= 0.05
            for ticker, target_weight in self.target.items()
        )

    def evaluate(self, date, market, portfolio):
        previous_state = self.state
        previous_target = self.target.copy() if self.target is not None else None
        signal = super().evaluate(date, market, portfolio)
        if not self._is_suppressed_transition(previous_state, self.state):
            return signal
        if previous_target != signal["target"]:
            return signal
        if signal["reason"] and "SAFE_ROTATION" in signal["reason"]:
            return signal
        if self._outside_band(market, portfolio):
            signal["reason"] = f"{signal['reason']}|MONTHLY_5PCT_BAND"
            return signal
        return self._signal(
            False,
            signal["target"],
            self._execution_days(self.state),
            f"STATE_ONLY_{previous_state.value}->{self.state.value}",
        )


class ASYMMETRIC_TREND_BAND_ADD_DEFENSE2_TUNED(
    ASYMMETRIC_TREND_BAND_ADD_DEFENSE2
):
    """Pension-compliant DEFENSE2 candidate with an earlier trailing stop.

    Normal and defensive allocations, trend rules, asymmetric bands, and
    recovery behavior remain unchanged.  The only tuned trading parameter is
    the drawdown that activates defense: 15.5% instead of 25%.
    """

    MIN_SAFE_ASSET_WEIGHT = 0.30
    DEFENSIVE_DRAWDOWN = -0.155

    def __init__(self, defensive_drawdown=None):
        super().__init__()
        self.defensive_drawdown = (
            self.DEFENSIVE_DRAWDOWN
            if defensive_drawdown is None
            else float(defensive_drawdown)
        )
        self._validate_pension_allocations()

    def _validate_pension_allocations(self):
        for label, target in (
            ("normal", self.target_weights),
            ("defensive", self.defensive_weights),
        ):
            if target[self.SAFE_ASSET] < self.MIN_SAFE_ASSET_WEIGHT:
                raise ValueError(
                    f"{label} safe-asset weight must be at least "
                    f"{self.MIN_SAFE_ASSET_WEIGHT:.0%}"
                )
            if abs(sum(target.values()) - 1.0) > 1e-9:
                raise ValueError(f"{label} target weights must sum to 100%")

    def evaluate(self, date, market, portfolio):
        prices = {}
        for ticker in self.target_weights:
            if ticker not in market or "Close" not in market[ticker]:
                return self._signal(
                    False, self.target_weights, 1,
                    f"Missing data for {ticker}",
                )
            price = market[ticker]["Close"]
            if price is None or math.isnan(float(price)):
                return self._signal(
                    False, self.target_weights, 1,
                    f"NaN price for {ticker}",
                )
            prices[ticker] = price

        qqq = market[self.RISK_ASSET]
        current_price = qqq["Close"]
        ma55 = qqq.get("EMA55", current_price)
        ma200 = qqq.get("EMA200", current_price)
        is_uptrend = ma55 >= ma200
        current_risk_weight = portfolio.weights(prices).get(
            self.RISK_ASSET, 0.0
        )

        if self.is_defensive_mode and is_uptrend:
            self.is_defensive_mode = False
            if current_risk_weight <= self.target_weights[self.RISK_ASSET]:
                return self._signal(
                    True,
                    self.target_weights,
                    1,
                    f"TREND_RECOVERY_NORMAL_MODE_{self.RISK_ASSET}",
                )

        active_target = (
            self.defensive_weights
            if self.is_defensive_mode
            else self.target_weights
        )
        weight_diff = (
            current_risk_weight - active_target[self.RISK_ASSET]
        )

        if is_uptrend:
            upper_threshold = float("inf")
            lower_threshold = -0.03
            trend_status = "UPTREND"
        else:
            upper_threshold = 0.03
            lower_threshold = -0.06
            trend_status = "DOWNTREND"

        rsi = qqq.get("RSI14", 50)
        disparity60 = qqq.get("DISPARITY60", 100)
        if rsi > 95 and disparity60 >= 110:
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True, active_target, 1,
                f"EXTREME_OVERBOUGHT_TAKE_PROFIT_{self.RISK_ASSET}",
            )
        if rsi <= 20 and disparity60 <= 90:
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True, active_target, 1,
                f"EXTREME_OVERSELL_BUY_{self.RISK_ASSET}",
            )

        self.highest_price = max(self.highest_price, current_price)
        self.lowest_price = min(self.lowest_price, current_price)
        drawdown_from_peak = (
            current_price / self.highest_price - 1.0
            if self.highest_price > 0.0
            else 0.0
        )
        if (
            drawdown_from_peak <= self.defensive_drawdown
            and not self.is_defensive_mode
        ):
            self.is_defensive_mode = True
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True,
                self.defensive_weights,
                1,
                f"TRAILING_STOP_DEFENSIVE_MODE_{self.RISK_ASSET}"
                f"({self.defensive_drawdown:.1%})",
            )

        if weight_diff <= lower_threshold:
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True, active_target, 1,
                f"{trend_status}_BUY_DIP_{self.RISK_ASSET}",
            )
        if weight_diff >= upper_threshold:
            self.highest_price = current_price
            self.lowest_price = current_price
            return self._signal(
                True, active_target, 1,
                f"{trend_status}_TAKE_PROFIT_{self.RISK_ASSET}",
            )
        return self._signal(False, active_target, 1)
