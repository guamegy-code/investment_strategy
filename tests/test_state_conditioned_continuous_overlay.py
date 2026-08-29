import sys
import unittest
from pathlib import Path

import pandas as pd


sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "src" / "legacy-python"),
)

from validation.state_conditioned_continuous_overlay import (  # noqa: E402
    OverlayProfile,
    StateConditionedContinuousOverlayStrategy,
    caution_severity,
)


class FakeBaseline:
    holding_tickers = ("QQQ", "TDF2050_PROXY", "BIL")
    observation_tickers = ("SPY",)
    required_tickers = (*holding_tickers, *observation_tickers)
    required_market_fields = {
        "QQQ": ("Close", "EMA55", "VOL60"),
    }
    risk_asset_tickers = ("QQQ",)


class FakePortfolio:
    def __init__(self, weights):
        self._weights = weights

    def weights(self, _prices):
        return self._weights.copy()


def market(close=90.0, ema55=100.0, volatility=0.30):
    return {
        "QQQ": {"Close": close, "EMA55": ema55, "VOL60": volatility},
        "TDF2050_PROXY": {"Close": 100.0},
        "BIL": {"Close": 100.0},
        "SPY": {"Close": 100.0},
    }


def point(
    state="CAUTION",
    target=None,
    safe_tdf_share=0.0,
    rebalance=False,
):
    return {
        "state": state,
        "target": target or {
            "QQQ": 0.70,
            "TDF2050_PROXY": 0.0,
            "BIL": 0.30,
        },
        "rebalance": rebalance,
        "days": 2,
        "reason": None,
        "risk_off_score": 6,
        "recovery_score": 0,
        "safe_tdf_share": safe_tdf_share,
    }


class StateConditionedContinuousOverlayTests(unittest.TestCase):
    def strategy(self, schedule, adjustment=0.10):
        return StateConditionedContinuousOverlayStrategy(
            OverlayProfile("TEST", adjustment),
            schedule,
            FakeBaseline(),
            lambda _row: None,
        )

    def test_severity_uses_fixed_trend_and_volatility_scales(self):
        self.assertAlmostEqual(caution_severity(market()["QQQ"]), 1.0)
        self.assertEqual(
            caution_severity(market(close=105.0, volatility=0.15)["QQQ"]),
            0.0,
        )
        self.assertAlmostEqual(
            caution_severity(market(close=95.0, volatility=0.225)["QQQ"]),
            0.5,
        )

    def test_overlay_reduces_only_qqq_and_preserves_bil_sleeve(self):
        date = pd.Timestamp("2024-01-02")
        strategy = self.strategy({date: point()})

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio({"QQQ": 0.70, "TDF2050_PROXY": 0.0, "BIL": 0.30}),
        )

        self.assertAlmostEqual(signal["target"]["QQQ"], 0.60)
        self.assertAlmostEqual(signal["target"]["TDF2050_PROXY"], 0.0)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.40)
        self.assertAlmostEqual(sum(signal["target"].values()), 1.0)

    def test_overlay_preserves_tdf_sleeve(self):
        date = pd.Timestamp("2024-01-02")
        schedule = {
            date: point(
                target={"QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0},
                safe_tdf_share=1.0,
            )
        }
        strategy = self.strategy(schedule)

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio({"QQQ": 0.70, "TDF2050_PROXY": 0.30, "BIL": 0.0}),
        )

        self.assertAlmostEqual(signal["target"]["QQQ"], 0.60)
        self.assertAlmostEqual(signal["target"]["TDF2050_PROXY"], 0.40)
        self.assertAlmostEqual(signal["target"]["BIL"], 0.0)

    def test_overlay_is_inactive_outside_caution(self):
        date = pd.Timestamp("2024-01-02")
        baseline_target = {
            "QQQ": 0.50,
            "TDF2050_PROXY": 0.50,
            "BIL": 0.0,
        }
        strategy = self.strategy({
            date: point(
                state="RECOVERY",
                target=baseline_target,
                safe_tdf_share=1.0,
            )
        })

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio(baseline_target),
        )

        self.assertEqual(signal["target"], baseline_target)
        self.assertEqual(strategy.adjustment, 0.0)

    def test_overlay_specific_rebalance_is_limited_to_one_check_per_week(self):
        first = pd.Timestamp("2024-01-02")
        second = pd.Timestamp("2024-01-03")
        next_week = pd.Timestamp("2024-01-09")
        schedule = {date: point() for date in (first, second, next_week)}
        strategy = self.strategy(schedule)
        portfolio = FakePortfolio(
            {"QQQ": 0.70, "TDF2050_PROXY": 0.0, "BIL": 0.30}
        )

        first_signal = strategy.evaluate(first, market(), portfolio)
        second_signal = strategy.evaluate(second, market(), portfolio)
        next_week_signal = strategy.evaluate(next_week, market(), portfolio)

        self.assertTrue(first_signal["rebalance"])
        self.assertFalse(second_signal["rebalance"])
        self.assertTrue(next_week_signal["rebalance"])

    def test_zero_capacity_reproduces_shadow_target(self):
        date = pd.Timestamp("2024-01-02")
        baseline_target = {
            "QQQ": 0.73,
            "TDF2050_PROXY": 0.27,
            "BIL": 0.0,
        }
        strategy = self.strategy(
            {date: point(target=baseline_target, safe_tdf_share=1.0)},
            adjustment=0.0,
        )

        signal = strategy.evaluate(
            date,
            market(),
            FakePortfolio(baseline_target),
        )

        self.assertEqual(signal["target"], baseline_target)
        self.assertFalse(signal["rebalance"])


if __name__ == "__main__":
    unittest.main()
