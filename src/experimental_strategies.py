"""Experimental strategies kept outside the production OOS-locked module."""

import math

from strategy import ASYMMETRIC_TREND_BAND_ADD_DEFENSE2


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
